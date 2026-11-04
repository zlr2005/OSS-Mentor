import copy
import gzip
import json
import unittest
import tempfile
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from oss_mentor.collector.github_client import GitHubResponse
from oss_mentor.datasets.github_history import CachedGitHubSource, digest, write_snapshot
from oss_mentor.datasets.issue_links import (
    assemble_issue_records, closing_references, issue_page_record, resolution_proof, enrich_issue_links, get_issue_page,
)
from oss_mentor.datasets.normalization import canonical_json
from oss_mentor.datasets.snapshot import _utc, validate_snapshot
from oss_mentor.storage.datasets import DatasetStore
from oss_mentor.datasets.connector_cache import cache_connector_reads
from oss_mentor.datasets.issue_links import read_evidence
from scripts.midterm_a.inspect_working_dataset import summarize
import tests.midterm_a.test_git_history as git_fixture


class IssueLinkTests(unittest.TestCase):
    """Synthetic evidence; no external API or HTML requests."""
    def setUp(self):
        self.repository = "synthetic/project"
        self.sha = "1" * 40
        self.issue_url = "https://github.com/synthetic/project/issues/44"
        self.pr_url = "https://github.com/synthetic/project/pull/10"
        self.date = "2022-01-05T00:00:00Z"
        self.observed = "2026-10-15T00:00:00Z"
        self.cutoff = "2022-02-01T00:00:00Z"
        self.pr = {"number": 10, "merged": True, "merged_at": self.date, "html_url": self.pr_url,
                   "base": {"repo": {"full_name": self.repository}}}
        self.issue = {"id": 444, "number": 44, "title": "Synthetic bug", "body": "Look at src/a.py",
                      "html_url": self.issue_url, "labels": [{"name": "bug"}], "state": "closed",
                      "state_reason": "completed", "created_at": "2022-01-01T00:00:00Z", "closed_at": self.date}
        self.events = [{"id": 1, "event": "closed", "commit_id": None, "created_at": self.date}]
        self.page = {"number": 44, "url": self.issue_url, "state": "CLOSED",
                     "closedByPullRequestsReferences": {"nodes": [{"number": 10, "url": self.pr_url, "state": "MERGED"}]}}
        self.records = {"repositories": [{"repository_key": "repo", "source_repository_id": "100"}],
                        "code_documents": [
                            {"commit_sha": self.sha, "path": "src/a.py", "file_key": "file-a", "snapshot_at": "2022-01-04T00:00:00Z"},
                            {"commit_sha": self.sha, "path": "src/b.py", "file_key": "file-b", "snapshot_at": "2022-01-04T00:00:00Z"}]}
        self.contents = {}

    def proof(self, *, pr=None, issue=None, events=None, page=None):
        return resolution_proof(pr or self.pr, issue or self.issue, events if events is not None else self.events,
                                page if page is not None else self.page, {self.sha}, self.cutoff)

    def evidence(self, location, path, payload, *, html=False):
        envelope = {"metadata": {"source_system": "github_issue_html" if html else "github_rest",
                                  "source_url": ("https://github.com" if html else "https://api.github.com") + path,
                                  "status_code": 200, "fetched_at": self.observed,
                                  "response_sha256": digest(canonical_json(payload).encode())}, "payload": payload}
        self.contents[location] = gzip.compress(canonical_json(envelope).encode(), mtime=0)
        return location

    def assembly(self):
        prefix = "/repos/" + self.repository
        html = '<script type="application/json">' + json.dumps({"payload": {"issueViewerRoute": {
            "data": {"repository": {"issue": self.page}}}}}) + '</script>'
        cases = [{"commit_sha": self.sha,
                  "associated_pr_pages": [self.evidence("associated.gz", prefix + f"/commits/{self.sha}/pulls", [{"number": 10}])],
                  "pull_requests": [{"number": 10,
                     "pr_source": self.evidence("pr.gz", prefix + "/pulls/10", self.pr),
                     "file_pages": [self.evidence("files.gz", prefix + "/pulls/10/files", [{"filename": "src/a.py"}])],
                     "commit_pages": [self.evidence("commits.gz", prefix + "/pulls/10/commits", [{"sha": self.sha}])],
                     "issues": [{"number": 44, "issue_source": self.evidence("issue.gz", prefix + "/issues/44", self.issue),
                                 "timeline_pages": [self.evidence("timeline.gz", prefix + "/issues/44/timeline", self.events)],
                                 "page_source": self.evidence("page.gz", "/" + self.repository + "/issues/44", {"html": html}, html=True)}]}]}]
        return assemble_issue_records(self.records, cases, self.contents, repository=self.repository, cutoff=self.cutoff)

    def test_explicit_closed_by_pr_is_required_when_closed_event_has_no_commit(self):
        self.assertEqual(self.proof()["proof_kind"], "explicit_github_closed_by_pr")
        page = dict(self.page, closedByPullRequestsReferences={"nodes": []},
                    linkedPullRequests={"nodes": [{"number": 10, "url": self.pr_url, "state": "MERGED"}]})
        self.assertIsNone(self.proof(page=page))

    def test_closed_event_commit_can_supply_independent_proof(self):
        events = [dict(self.events[0], commit_id=self.sha)]
        page = dict(self.page, closedByPullRequestsReferences={"nodes": []})
        self.assertEqual(self.proof(events=events, page=page)["proof_kind"], "closed_event_commit")

    def test_unmerged_pr_is_not_a_resolution(self):
        self.assertIsNone(self.proof(pr=dict(self.pr, merged=False, merged_at=None)))

    def test_not_planned_and_reopened_issues_are_not_success_labels(self):
        self.assertIsNone(self.proof(issue=dict(self.issue, state_reason="not_planned")))
        self.assertIsNone(self.proof(events=self.events + [{"event": "reopened", "created_at": "2022-01-06T00:00:00Z"}]))

    def test_closure_after_history_cutoff_is_not_a_historical_result(self):
        later = "2023-01-05T00:00:00Z"
        self.assertIsNone(self.proof(issue=dict(self.issue, closed_at=later), events=[dict(self.events[0], created_at=later)]))

    def test_same_pr_number_in_another_repository_is_not_explicit_proof(self):
        page = dict(self.page, closedByPullRequestsReferences={"nodes": [
            {"number": 10, "url": "https://github.com/another/project/pull/10", "state": "MERGED"}]})
        self.assertIsNone(self.proof(page=page))

    def test_only_pr_changed_files_are_linked_and_text_is_observed_now(self):
        issues, links, stats = self.assembly()
        outcomes = [row for row in links if row["feature_role"] == "outcome_only"]
        hints = [row for row in links if row["feature_role"] == "observed_text_hint"]
        self.assertEqual([row["file_key"] for row in outcomes], ["file-a"])
        self.assertGreaterEqual(_utc(outcomes[0]["available_at"]), _utc(self.date))
        self.assertFalse(outcomes[0]["causal_file_attribution_verified"])
        self.assertIsNone(outcomes[0]["confidence"])
        self.assertEqual(hints[0]["available_at"], self.observed)
        self.assertFalse(issues[0]["historical_text_verified"])
        self.assertEqual(issues[0]["current_availability"], "excluded")
        self.assertIsNone(issues[0]["outcome"]["resolver_developer_key"])
        self.assertEqual(stats["resolved_unique_issues"], 1)

    def test_plain_reference_remains_candidate_without_outcome_links(self):
        self.page["closedByPullRequestsReferences"]["nodes"] = []
        issues, links, stats = self.assembly()
        self.assertIsNone(issues[0]["outcome"])
        self.assertFalse(any(row["feature_role"] == "outcome_only" for row in links))
        self.assertEqual(stats["candidate_unique_issues"], 1)
        self.assertEqual(stats["resolved_unique_issues"], 0)

    def test_page_parser_uses_issue_route_and_exact_url(self):
        html = '<script type="application/json">' + json.dumps({"payload": {"issueViewerRoute": {
            "data": {"repository": {"issue": self.page}}}}}) + '</script>'
        self.assertEqual(issue_page_record(html, self.issue_url, 44), self.page)
        self.assertIsNone(issue_page_record(html, self.issue_url, 45))
        self.assertIsNone(issue_page_record('<a href="' + self.pr_url + '">closed</a>', self.issue_url, 44))

    def test_keyword_parser_ignores_mentions_quotes_code_and_other_repositories(self):
        text = 'References #1\nFixes: #2\nCLOSES synthetic/project#3\nResolves https://github.com/synthetic/project/issues/4\nFixes other/project#5\n> Fixes #6\n```\nFixes #7\n```\n`Fixes #8`'
        self.assertEqual(closing_references(text, self.repository), {2, 3, 4})

    def test_html_source_is_cached_and_verified_without_repeat_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = Mock(api_base="https://api.github.com", api_version="2026-03-10")
            source = CachedGitHubSource(client, Path(temporary), self.repository, allow_network=True)
            response = Mock(status=200)
            response.geturl.return_value = self.issue_url
            response.read.return_value = b'<html>synthetic page</html>'
            with patch('urllib.request.urlopen') as network:
                network.return_value.__enter__.return_value = response
                location = get_issue_page(source, 44)
                self.assertEqual(network.call_count, 1)
                source.allow_network = False
                self.assertEqual(get_issue_page(source, 44), location)
                self.assertEqual(network.call_count, 1)

    def test_enriched_working_data_roundtrip_and_repeat_import(self):
        base = git_fixture.GitHistoryTests('test_working_data_excludes_merge_and_preserves_rename_and_unknown_identity')
        base.setUp()
        self.addCleanup(base.doCleanups)
        manifest, records, assets = base.collect()
        directory = base.root / 'base'
        write_snapshot(directory, manifest, records, assets)
        sha = records['contributions'][0]['commit_sha']
        self.issue['body'] = 'Look at new.py'
        self.pr['body'] = 'Fixes #44'
        prefix = '/repos/' + self.repository
        payloads = {prefix + f'/commits/{sha}/pulls': [{'number': 10}], prefix + '/pulls/10': self.pr,
                    prefix + '/pulls/10/commits': [{'sha': sha}], prefix + '/pulls/10/files': [{'filename': 'new.py'}],
                    prefix + '/issues/44': self.issue, prefix + '/issues/44/timeline': self.events}
        client = Mock(api_base='https://api.github.com', api_version='2026-03-10')
        client.get.side_effect = lambda path, params=None: GitHubResponse(
            'https://api.github.com' + path, 200, {}, payloads[path], datetime(2026, 10, 15, tzinfo=timezone.utc))
        source = CachedGitHubSource(client, base.root / 'enrichment-cache', self.repository, allow_network=True)
        html = '<script type="application/json">' + json.dumps({'payload': {'issueViewerRoute': {
            'data': {'repository': {'issue': self.page}}}}}) + '</script>'
        location = self.evidence('page.gz', '/' + self.repository + '/issues/44', {'html': html}, html=True)
        def page(source, number):
            source.used[location] = self.contents[location]
            return location
        with patch('oss_mentor.datasets.issue_links.get_issue_page', side_effect=page):
            enriched, rows, data = enrich_issue_links(directory, source, commit_limit=2)
        working = base.root / 'enriched'
        write_snapshot(working, enriched, rows, data)
        self.assertEqual(validate_snapshot(working)['status'], 'source_verified')
        self.assertFalse(enriched['frozen'])
        self.assertEqual(enriched['source_provenance']['issue_enrichment']['summary']['resolved_unique_issues'], 1)
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.execute('PRAGMA foreign_keys = ON')
        connection.executescript((Path(__file__).resolve().parents[2] / 'db/sqlite/011_midterm_datasets.sql').read_text(encoding='utf-8'))
        store = DatasetStore(connection)
        first = store.import_snapshot(working, chunk_size=3)
        self.assertEqual(store.import_snapshot(working), first)
        self.assertFalse(first['frozen'])
        self.assertEqual(first['entity_counts']['issues'], 1)
        report = summarize(working)
        self.assertEqual(report['api_metadata_pages'], 1)
        self.assertEqual(report['issue_enrichment']['resolved_unique_issues'], 1)
        extra_location = self.evidence('provenance/numeric-page.json.gz', '/repositories/100/commits',
                                       [{'sha': enriched['source_provenance']['selection']['commit_shas'][0]}])
        with patch('scripts.midterm_a.inspect_working_dataset.load_snapshot',
                   return_value=(enriched, rows, {**data, extra_location: self.contents[extra_location]})):
            numeric_report = summarize(working)
        self.assertEqual(numeric_report['api_metadata_pages'], 2)
        altered = copy.deepcopy(rows)
        altered['issues'][0]['outcome']['final_pr_number'] = 999
        with self.assertRaisesRegex(ValueError, 'differ from source evidence'):
            write_snapshot(base.root / 'tampered', enriched, altered, data)
        altered = copy.deepcopy(rows)
        hint = next(row for row in altered['issue_file_links'] if row['feature_role'] == 'observed_text_hint')
        hint['available_at'] = altered['issues'][0]['created_at']
        with self.assertRaisesRegex(ValueError, 'cannot be backdated'):
            write_snapshot(base.root / 'backdated', enriched, altered, data)

    def test_successful_connector_read_retains_unknown_http_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = Mock(api_base='https://api.github.com', api_version='2026-03-10')
            source = CachedGitHubSource(client, Path(temporary), self.repository, allow_network=False)
            entry = {'source_system': 'github_connector', 'tool_success': True, 'fetched_at': self.observed,
                     'url': 'https://api.github.com/repos/synthetic/project/issues/44', 'payload': self.issue}
            self.assertEqual(cache_connector_reads(source, [entry]), 1)
            response, location = source.get('/repos/synthetic/project/issues/44')
            self.assertEqual(response.payload, self.issue)
            envelope = read_evidence(source.used, location, '/repos/synthetic/project/issues/44')
            self.assertIsNone(envelope['metadata']['status_code'])
            self.assertFalse(envelope['metadata']['headers_available'])
            self.assertIsNone(envelope['metadata']['api_version'])
            client.get.assert_not_called()

    def test_connector_import_rejects_other_repository_and_unconfirmed_pagination(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = Mock(api_base='https://api.github.com', api_version='2026-03-10')
            source = CachedGitHubSource(client, Path(temporary), self.repository, allow_network=False)
            entry = {'source_system': 'github_connector', 'tool_success': True, 'fetched_at': self.observed,
                     'url': 'https://api.github.com/repos/other/project/issues/44', 'payload': self.issue}
            with self.assertRaisesRegex(ValueError, 'origin/repository'):
                cache_connector_reads(source, [entry])
            entry.update(url='https://api.github.com/repos/synthetic/project/pulls/10/commits?per_page=1', payload=[{'sha': self.sha}])
            with self.assertRaisesRegex(ValueError, 'paginated'):
                cache_connector_reads(source, [entry])


if __name__ == "__main__":
    unittest.main()
