import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from oss_mentor.collector.github_client import GitHubResponse
from oss_mentor.datasets.github_history import CachedGitHubSource, write_snapshot
from oss_mentor.datasets.git_history import collect_git_history
from oss_mentor.datasets.git_objects import changed_files, object_hash, parse_tree
from oss_mentor.datasets.identity_map import IdentityMap
from oss_mentor.datasets.normalization import normalize_records, IDENTITY_FIELDS
from oss_mentor.datasets.snapshot import validate_snapshot
from oss_mentor.storage.datasets import DatasetStore


class ObjectFixture:
    """Synthetic Git objects, with no Git executable or external repository needed."""
    def __init__(self):
        self.objects, self.assets = {}, {}

    def add(self, kind, data):
        sha = object_hash(kind, data)
        self.objects[kind, sha] = data
        return sha

    def tree(self, entries):
        data = b"".join(b"100644 " + name.encode() + b"\0" + bytes.fromhex(sha) for name, sha in sorted(entries.items()))
        return self.add("tree", data)

    def commit(self, tree, parents, timestamp, author=b"Synthetic Author <author@example.invalid>"):
        content = b"tree " + tree.encode() + b"\n"
        content += b"".join(b"parent " + parent.encode() + b"\n" for parent in parents)
        content += b"author " + author + b" " + str(timestamp).encode() + b" +0000\n"
        content += b"committer Synthetic Committer <committer@example.invalid> " + str(timestamp).encode() + b" +0000\n\nSynthetic test\n"
        return self.add("commit", content)

    def read(self, kind, sha):
        data = self.objects[kind, sha]
        self.assets[f"git_objects/{kind}/{sha}.bin"] = data
        return data

    def select(self, anchor, limit):
        return self.selection[:limit]


class GitHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reader = ObjectFixture()
        self.license = self.reader.add("blob", b"Synthetic MIT: Permission is hereby granted, free of charge\n")
        code1 = self.reader.add("blob", b"def example():\n    return 1\n")
        code2 = self.reader.add("blob", b"def example():\n    return 2\n")
        tree1 = self.reader.tree({"LICENSE": self.license, "old.py": code1})
        tree2 = self.reader.tree({"LICENSE": self.license, "new.py": code1})
        tree3 = self.reader.tree({"LICENSE": self.license, "new.py": code2})
        timestamp = 1640995200
        first = self.reader.commit(tree1, [], timestamp)
        second = self.reader.commit(tree2, [first], timestamp + 60)
        third = self.reader.commit(tree3, [second], timestamp + 120)
        self.anchor = self.reader.commit(tree3, [third, first], timestamp + 180)
        self.reader.selection = [self.anchor, third, second, first]
        rows = []
        for sha, tree, seconds in ((self.anchor, tree3, 180), (third, tree3, 120), (second, tree2, 60), (first, tree1, 0)):
            rows.append({"sha": sha, "author": None if sha == third else {"id": 10}, "committer": {"id": 99},
                         "commit": {"tree": {"sha": tree},
                                    "committer": {"date": datetime.fromtimestamp(timestamp + seconds, timezone.utc).isoformat()}}})
        repository = {"id": 100, "private": False, "license": {"spdx_id": "MIT"}, "full_name": "synthetic/project",
                      "language": "Python", "archived": False, "html_url": "https://github.com/synthetic/project"}
        self.client = Mock(api_base="https://api.github.com", api_version="2026-03-10")
        def get(path, params=None):
            return GitHubResponse(url="https://api.github.com" + path, status_code=200, headers={},
                                  payload=rows if path.endswith("/commits") else repository,
                                  fetched_at=datetime(2026, 10, 15, tzinfo=timezone.utc))
        self.client.get.side_effect = get
        self.api_rows = rows
        self.source = CachedGitHubSource(self.client, self.root / "cache", "synthetic/project", allow_network=True)
        self.identities = IdentityMap(self.root / "identities.json", 100)

    def collect(self, source=None, identities=None):
        return collect_git_history(self.reader, source or self.source, identities or self.identities,
                                   anchor=self.anchor, until="2022-02-01T00:00:00Z", commit_limit=4)

    def test_working_data_excludes_merge_and_preserves_rename_and_unknown_identity(self):
        manifest, records, assets = self.collect()
        self.assertFalse(manifest["frozen"])
        self.assertEqual(manifest["dataset_lifecycle"], "working")
        self.assertEqual(len(records["contributions"]), 3)
        self.assertNotIn(self.anchor, {r["commit_sha"] for r in records["contributions"]})
        self.assertEqual(len(records["files"]), 1)
        self.assertEqual(len(records["file_aliases"]), 1)
        self.assertEqual({r["identity_status"] for r in records["developers"]}, {"verified", "unconfirmed"})
        report = write_snapshot(self.root / "working", manifest, records, assets)
        self.assertEqual(report["status"], "source_verified")
        self.assertEqual(validate_snapshot(self.root / "working"), report)

    def test_identity_mapping_and_offline_rebuild_are_stable(self):
        expected = self.collect()
        self.identities.save()
        loaded = IdentityMap(self.identities.path, 100)
        source = CachedGitHubSource(self.client, self.root / "cache", "synthetic/project", allow_network=False)
        self.client.get.reset_mock()
        self.assertEqual(self.collect(source, loaded), expected)
        self.client.get.assert_not_called()

    def test_tampered_git_object_fails_before_publishing(self):
        manifest, records, assets = self.collect()
        location = f"git_objects/commit/{self.anchor}.bin"
        assets[location] += b"tamper"
        with self.assertRaisesRegex(ValueError, "object hash"):
            write_snapshot(self.root / "bad", manifest, records, assets)
        self.assertFalse((self.root / "bad").exists())

    def test_committer_cannot_be_substituted_for_author(self):
        manifest, records, assets = self.collect()
        row = records["contributions"][0]
        row["git_author_hash"] = hashlib.sha256(b"Synthetic Committer <committer@example.invalid>").hexdigest()
        with self.assertRaisesRegex(ValueError, "Author/committer"):
            write_snapshot(self.root / "bad", manifest, records, assets)

    def test_experiment_freeze_is_not_performed_by_collector(self):
        manifest, records, assets = self.collect()
        manifest["frozen"] = True
        with self.assertRaisesRegex(ValueError, "working data"):
            write_snapshot(self.root / "bad", manifest, records, assets)

    def test_existing_file_keys_are_not_rewritten_when_rename_links_two_known_paths(self):
        old = self.identities.file("old.py")
        new = self.identities.file("new.py")
        self.assertNotEqual(old, new)
        self.identities.file("new.py", previous_path="old.py", commit_sha=self.anchor)
        self.identities.save()
        loaded = IdentityMap(self.identities.path, 100)
        self.assertEqual(loaded.file("old.py"), old)
        self.assertEqual(loaded.file("new.py"), new)
        self.assertEqual(len(loaded.data["rename_conflicts"]), 1)

    def test_similarity_without_identical_blob_does_not_merge_paths(self):
        old = {"old.py": {"sha": "1" * 40, "type": "blob", "mode": "100644"}}
        new = {"new.py": {"sha": "2" * 40, "type": "blob", "mode": "100644"}}
        self.assertEqual({r["status"] for r in changed_files(old, new)}, {"added", "removed"})

    def test_same_second_project_commits_remain_distinct(self):
        records = {kind: [] for kind in IDENTITY_FIELDS}
        base = {"developer_key": "d", "repository_key": "r", "behavior_type": "contribution",
                "event_at": "2022-01-01T00:00:00Z", "source": "github_git"}
        records["project_behaviors"] = [dict(base, commit_sha="1" * 40), dict(base, commit_sha="2" * 40)]
        normalized, _ = normalize_records(records)
        self.assertEqual(len(normalized["project_behaviors"]), 2)

    def test_overlapping_metadata_pages_do_not_duplicate_contributions(self):
        original_get = self.client.get.side_effect
        next_url = "https://api.github.com/repos/synthetic/project/commits?page=2"
        def get(path, params=None):
            if path == next_url:
                payload, headers = self.api_rows[1:], {}
            elif path.endswith("/commits"):
                payload, headers = self.api_rows[:2], {"link": f'<{next_url}>; rel="next"'}
            else:
                return original_get(path, params)
            return GitHubResponse("https://api.github.com" + path if path.startswith("/") else path,
                                  200, headers, payload, datetime(2026, 10, 15, tzinfo=timezone.utc))
        self.client.get.side_effect = get
        manifest, records, _ = self.collect()
        self.assertEqual(manifest["source_provenance"]["duplicate_api_commits_removed"], 1)
        self.assertEqual(len(records["contributions"]), 3)

    def test_working_database_import_is_repeatable_and_does_not_freeze(self):
        manifest, records, assets = self.collect()
        working = self.root / "working"
        write_snapshot(working, manifest, records, assets)
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("PRAGMA foreign_keys = ON")
        migration = Path(__file__).resolve().parents[2] / "db/sqlite/011_midterm_datasets.sql"
        connection.executescript(migration.read_text(encoding="utf-8"))
        store = DatasetStore(connection)
        first = store.import_snapshot(working, chunk_size=2)
        self.assertEqual(store.import_snapshot(working), first)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["dataset_lifecycle"], "working")
        self.assertFalse(first["frozen"])
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


class CachedPaginationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = Mock(api_base="https://api.github.com", api_version="2026-03-10")
        self.date = datetime(2026, 10, 15, tzinfo=timezone.utc)

    def test_pages_are_cached_and_can_resume_after_second_page_timeout(self):
        next_url = "https://api.github.com/repos/synthetic/project/commits?page=2"
        first = GitHubResponse("https://api.github.com/repos/synthetic/project/commits", 200,
                               {"link": f'<{next_url}>; rel="next"'}, [{"sha": "1"}], self.date)
        last = GitHubResponse(next_url, 200, {}, [{"sha": "2"}], self.date)
        self.client.get.side_effect = [first, TimeoutError("synthetic timeout")]
        source = CachedGitHubSource(self.client, self.root, "synthetic/project", allow_network=True)
        with self.assertRaises(TimeoutError):
            list(source.pages("/repos/synthetic/project/commits", {}))
        self.client.get.reset_mock()
        self.client.get.side_effect = [last]
        resumed = CachedGitHubSource(self.client, self.root, "synthetic/project", allow_network=True)
        self.assertEqual(len(list(resumed.pages("/repos/synthetic/project/commits", {}))), 2)
        self.assertEqual(self.client.get.call_count, 1)

    def test_next_page_cannot_leave_github_origin(self):
        self.client.get.return_value = GitHubResponse("https://api.github.com/repos/synthetic/project/commits", 200,
            {"link": '<https://example.invalid/steal>; rel="next"'}, [], self.date)
        source = CachedGitHubSource(self.client, self.root, "synthetic/project", allow_network=True)
        with self.assertRaisesRegex(ValueError, "Cross-origin"):
            list(source.pages("/repos/synthetic/project/commits", {}))
        self.assertEqual(self.client.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
