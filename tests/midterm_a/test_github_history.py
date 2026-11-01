import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from oss_mentor.collector.github_client import GitHubResponse
from oss_mentor.datasets.github_history import CachedGitHubSource, collect_history, decode_blob, write_snapshot
from oss_mentor.datasets.snapshot import validate_snapshot
from oss_mentor.storage.datasets import DatasetStore


def git_blob(content: bytes) -> dict:
    import base64
    return {"sha": hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest(),
            "encoding": "base64", "size": len(content), "content": base64.b64encode(content).decode()}


class GitHubHistoryTests(unittest.TestCase):
    """Synthetic API responses only; these tests make no network requests."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sha1, self.sha2 = "1" * 40, "2" * 40
        self.tree1, self.tree2 = "a" * 40, "b" * 40
        self.code = git_blob(b"def example():\n    return 1\n")
        self.license = git_blob(b"Synthetic MIT test: Permission is hereby granted, free of charge\n")
        self.prefix = "/repos/synthetic/project"
        self.payloads = {
            self.prefix: {"id": 100, "private": False, "license": {"spdx_id": "MIT"},
                          "full_name": "synthetic/project", "language": "Python", "archived": False,
                          "html_url": "https://github.com/synthetic/project"},
            self.prefix + "/commits": [{"sha": self.sha1}, {"sha": self.sha2}],
            self.prefix + "/git/blobs/" + self.code["sha"]: self.code,
            self.prefix + "/git/blobs/" + self.license["sha"]: self.license,
        }
        for sha, tree, path, date, author, status in (
            (self.sha1, self.tree1, "src/new.py", "2022-01-02T00:00:00Z", {"id": 10}, "renamed"),
            (self.sha2, self.tree2, "src/old.py", "2022-01-01T00:00:00Z", None, "modified"),
        ):
            self.payloads[self.prefix + "/commits/" + sha] = {
                "sha": sha, "author": author, "committer": {"id": 99},
                "html_url": "https://github.com/synthetic/project/commit/" + sha,
                "commit": {"tree": {"sha": tree}, "author": {"date": date}, "committer": {"date": date}},
                "files": [{"filename": path, "previous_filename": "src/old.py", "status": status}],
            }
            self.payloads[self.prefix + "/git/trees/" + tree] = {
                "sha": tree, "truncated": False,
                "tree": [{"path": path, "type": "blob", "mode": "100644", "sha": self.code["sha"], "size": self.code["size"]},
                         {"path": "LICENSE", "type": "blob", "mode": "100644", "sha": self.license["sha"], "size": self.license["size"]}],
            }
        self.client = Mock(api_base="https://api.github.com", api_version="2026-03-10")

        def get(path, params=None):
            from urllib.parse import urlencode
            return GitHubResponse(url="https://api.github.com" + path + ("?" + urlencode(params) if params else ""),
                                  status_code=200, headers={}, payload=self.payloads[path],
                                  fetched_at=datetime(2026, 10, 15, tzinfo=timezone.utc))
        self.client.get.side_effect = get
        self.source = CachedGitHubSource(self.client, self.root / "source", "synthetic/project", allow_network=True)

    def collect(self, source=None):
        return collect_history(source or self.source, until="2022-02-01T00:00:00Z", commit_limit=2, files_per_commit=1)

    def test_pinned_code_author_and_explicit_rename_roundtrip(self):
        manifest, records, assets = self.collect()
        self.assertFalse(manifest["fixture_only"])
        self.assertEqual(len(records["files"]), 1)
        self.assertEqual(len(records["code_documents"]), 2)
        self.assertEqual(len(records["file_aliases"]), 1)
        self.assertEqual(records["developers"][0]["github_numeric_id"], 10)
        self.assertEqual(records["developers"][1]["identity_status"], "unconfirmed")
        self.assertEqual(records["contributions"][0]["committer_source_id"], "99")
        self.assertEqual(records["issues"], [])
        self.assertEqual(records["issue_file_links"], [])
        output = self.root / "output"
        report = write_snapshot(output, manifest, records, assets)
        self.assertEqual(report["status"], "source_verified")
        self.assertEqual(validate_snapshot(output), report)

    def test_multilingual_declared_license_and_pinned_evidence(self):
        self.payloads[self.prefix]['license'] = {'spdx_id': 'Apache-2.0'}
        for sha, tree in [(self.sha1,self.tree1),(self.sha2,self.tree2)]:
            change = self.payloads[self.prefix+'/commits/'+sha]['files'][0]
            change['filename'] = change['filename'].replace('.py','.java')
            change['previous_filename'] = 'src/old.java'
            self.payloads[self.prefix+'/git/trees/'+tree]['tree'][0]['path'] = change['filename']
        manifest, records, assets = collect_history(self.source, until='2022-02-01T00:00:00Z',
                                                   commit_limit=2, files_per_commit=1, multilingual=True)
        self.assertEqual(manifest['source_provenance']['adapter_version'],'github-history-v2')
        self.assertIn('without asserting',manifest['source_provenance']['license_basis'])
        self.assertEqual({c['language'] for c in records['code_documents']},{'Java'})
        self.assertEqual(write_snapshot(self.root/'java',manifest,records,assets)['status'],'source_verified')
        records['code_documents'][0]['language']='Python'
        with self.assertRaisesRegex(ValueError,'language'):
            write_snapshot(self.root/'wrong-language',manifest,records,assets)

    def test_multilingual_unknown_license_stays_unresolved(self):
        self.payloads[self.prefix]['license']={'spdx_id':'NOASSERTION'}
        with self.assertRaisesRegex(ValueError,'license declaration unavailable'):
            collect_history(self.source,until='2022-02-01T00:00:00Z',multilingual=True)

    def test_repair_unknown_spdx_nested_paths_lowercase_license_and_tamper(self):
        self.payloads[self.prefix]['license']=None
        for tree, child, filename in [(self.tree1,'c'*40,'new.py'),(self.tree2,'d'*40,'old.py')]:
            self.payloads[self.prefix+'/git/trees/'+tree]['tree']=[
                {'path':'src','type':'tree','mode':'040000','sha':child},
                {'path':'license.txt','type':'blob','mode':'100644','sha':self.license['sha'],'size':self.license['size']}]
            self.payloads[self.prefix+'/git/trees/'+child]={'sha':child,'truncated':False,'tree':[
                {'path':filename,'type':'blob','mode':'100644','sha':self.code['sha'],'size':self.code['size']}]}
        manifest,records,assets=collect_history(self.source,until='2022-02-01T00:00:00Z',
                commit_limit=2,files_per_commit=1,multilingual=True,repair=True)
        self.assertEqual(manifest['source_provenance']['adapter_version'],'github-history-v3')
        self.assertIsNone(manifest['source_provenance']['license_evidence'][0]['spdx_id'])
        self.assertEqual(write_snapshot(self.root/'repair',manifest,records,assets)['status'],'source_verified')
        location=manifest['source_provenance']['tree_snapshots'][0]['content_location']
        proof=json.loads(assets[location]);proof['entries'][0]['sha']='f'*40
        assets[location]=json.dumps(proof).encode()
        with self.assertRaisesRegex(ValueError,'differs from pinned entry'):
            write_snapshot(self.root/'bad-proof',manifest,records,assets)

    def test_repair_skips_recent_noncode_commits(self):
        self.payloads[self.prefix+'/commits/'+self.sha1]['files']=[]
        for tree,filename in [(self.tree2,'old.py')]:
            self.payloads[self.prefix+'/git/trees/'+tree]['tree'][0]['path']=filename
            self.payloads[self.prefix+'/commits/'+self.sha2]['files'][0]['filename']=filename
        manifest,records,assets=collect_history(self.source,until='2022-02-01T00:00:00Z',
                commit_limit=1,files_per_commit=1,multilingual=True,repair=True)
        self.assertEqual(manifest['source_provenance']['selection']['commit_shas'],[self.sha2])
        self.assertEqual(write_snapshot(self.root/'older-code',manifest,records,assets)['status'],'source_verified')

    def test_repair_finds_package_license_without_root_license(self):
        for tree, child, filename in [(self.tree1,'c'*40,'new.py'),(self.tree2,'d'*40,'old.py')]:
            self.payloads[self.prefix+'/git/trees/'+tree]['tree']=[{'path':'src','type':'tree','mode':'040000','sha':child}]
            self.payloads[self.prefix+'/git/trees/'+child]={'sha':child,'truncated':False,'tree':[
                {'path':filename,'type':'blob','mode':'100644','sha':self.code['sha'],'size':self.code['size']},
                {'path':'LICENSE-MIT','type':'blob','mode':'100644','sha':self.license['sha'],'size':self.license['size']}]}
        manifest,records,assets=collect_history(self.source,until='2022-02-01T00:00:00Z',
                commit_limit=2,files_per_commit=1,multilingual=True,repair=True)
        self.assertEqual(write_snapshot(self.root/'package-license',manifest,records,assets)['status'],'source_verified')

    def test_repair_excludes_unlicensed_tree_without_losing_other_commits(self):
        for sha,tree,name in [(self.sha1,self.tree1,'new.py'),(self.sha2,self.tree2,'old.py')]:
            self.payloads[self.prefix+'/git/trees/'+tree]['tree'][0]['path']=name
            self.payloads[self.prefix+'/commits/'+sha]['files'][0]['filename']=name
            self.payloads[self.prefix+'/commits/'+sha]['files'][0]['status']='modified'
        self.payloads[self.prefix+'/git/trees/'+self.tree1]['tree'].pop()
        manifest,records,assets=collect_history(self.source,until='2022-02-01T00:00:00Z',
                commit_limit=1,files_per_commit=1,multilingual=True,repair=True)
        self.assertEqual(manifest['source_provenance']['selection']['commit_shas'],[self.sha2])
        self.assertEqual(manifest['source_provenance']['selection']['excluded_commits'][0]['commit_sha'],self.sha1)
        self.assertEqual(write_snapshot(self.root/'excluded-commit',manifest,records,assets)['status'],'source_verified')
        manifest['source_provenance']['selection']['excluded_commits'][0]['commit_sha']=self.sha2
        with self.assertRaisesRegex(ValueError,'Excluded commit identity'):
            write_snapshot(self.root/'bad-exclusion',manifest,records,assets)

    def test_cached_evidence_rebuilds_same_snapshot_without_network(self):
        expected = self.collect()
        self.client.get.reset_mock()
        offline = CachedGitHubSource(self.client, self.root / "source", "synthetic/project", allow_network=False)
        actual = self.collect(offline)
        self.client.get.assert_not_called()
        self.assertEqual(actual, expected)

    def test_missing_cache_requires_explicit_network_opt_in(self):
        offline = CachedGitHubSource(self.client, self.root / "empty", "synthetic/project", allow_network=False)
        with self.assertRaisesRegex(ValueError, "allow-network"):
            self.collect(offline)
        self.client.get.assert_not_called()

    def test_truncated_tree_is_rejected(self):
        self.payloads[self.prefix + "/git/trees/" + self.tree1]["truncated"] = True
        with self.assertRaisesRegex(ValueError, "complete"):
            self.collect()

    def test_current_code_cannot_replace_pinned_blob(self):
        manifest, records, assets = self.collect()
        code = records["code_documents"][0]
        assets[code["content_location"]] = b"newer code"
        code["content_hash"] = hashlib.sha256(b"newer code").hexdigest()
        with self.assertRaisesRegex(ValueError, "differs from source blob"):
            write_snapshot(self.root / "bad", manifest, records, assets)
        self.assertFalse((self.root / "bad").exists())

    def test_author_cannot_be_replaced_by_committer(self):
        manifest, records, assets = self.collect()
        records["developers"][0]["github_numeric_id"] = 99
        with self.assertRaisesRegex(ValueError, "author differs"):
            write_snapshot(self.root / "bad", manifest, records, assets)

    def test_renamed_path_requires_commit_evidence(self):
        manifest, records, assets = self.collect()
        records["file_aliases"][0]["old_path"] = "invented.py"
        with self.assertRaisesRegex(ValueError, "rename lacks"):
            write_snapshot(self.root / "bad", manifest, records, assets)

    def test_blob_hash_is_verified(self):
        broken = dict(self.code, sha="f" * 40)
        with self.assertRaisesRegex(ValueError, "hash/size mismatch"):
            decode_blob(broken)

    def test_unknown_license_does_not_collect_code(self):
        self.payloads[self.prefix]["license"] = None
        with self.assertRaisesRegex(ValueError, "MIT license"):
            self.collect()
        self.assertEqual(self.client.get.call_count, 1)

    def test_failed_collection_can_resume_successful_requests(self):
        blob_path = self.prefix + "/git/blobs/" + self.code["sha"]
        normal = self.client.get.side_effect

        def fail_once(path, params=None):
            if path == blob_path:
                raise TimeoutError("synthetic timeout")
            return normal(path, params=params)
        self.client.get.side_effect = fail_once
        with self.assertRaises(TimeoutError):
            self.collect()
        self.client.get.reset_mock()
        self.client.get.side_effect = normal
        resumed = CachedGitHubSource(self.client, self.root / "source", "synthetic/project", allow_network=True)
        self.collect(resumed)
        self.assertEqual(self.client.get.call_count, 1)

    def test_source_snapshot_import_and_export_preserve_provenance(self):
        manifest, records, assets = self.collect()
        source_path = self.root / "snapshot"
        write_snapshot(source_path, manifest, records, assets)
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("PRAGMA foreign_keys = ON")
        migration = Path(__file__).resolve().parents[2] / "db/sqlite/011_midterm_datasets.sql"
        connection.executescript(migration.read_text(encoding="utf-8"))
        store = DatasetStore(connection)
        first = store.import_snapshot(source_path, chunk_size=3)
        self.assertEqual(store.import_snapshot(source_path), first)
        report = store.export_snapshot(manifest["dataset_version"], self.root / "export")
        self.assertFalse(report["fixture_only"])
        self.assertEqual(report["status"], "source_verified")
        exported = json.loads((self.root / "export/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(exported["normalization_version"], "midterm-a-normalize-v2")
        self.assertTrue(all(f["format"] == "gzip-json" for f in exported["files"] if f["path"].endswith(".gz")))
        self.assertEqual(exported["source_provenance"], manifest["source_provenance"])

    def test_corrupted_source_cache_is_rejected_without_network(self):
        self.collect()
        entry = next(iter(self.source.index.values()))
        (self.source.root / entry["path"]).write_bytes(b"corrupt")
        self.client.get.reset_mock()
        offline = CachedGitHubSource(self.client, self.root / "source", "synthetic/project", allow_network=False)
        with self.assertRaisesRegex(ValueError, "cache checksum"):
            self.collect(offline)
        self.client.get.assert_not_called()

    def test_real_data_cannot_use_fixture_schema(self):
        manifest, records, assets = self.collect()
        manifest["schema_version"] = "midterm-a-draft-v1"
        with self.assertRaisesRegex(ValueError, "flag disagree"):
            write_snapshot(self.root / "bad", manifest, records, assets)


if __name__ == "__main__":
    unittest.main()
