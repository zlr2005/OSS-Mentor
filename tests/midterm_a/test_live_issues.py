"""Current candidates must fail closed on stale or incomplete GitHub evidence."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oss_mentor.datasets.live_issues import (
    DISCOVERY_QUERY, build_pool, fresh, normalize_live, strict_eligibility, timeline_evidence,
)

ROOT = Path(__file__).resolve().parents[2]


def issue(number=1, **changes):
    return {'id': number + 100, 'number': number, 'repository_url': 'https://api.github.com/repos/example/demo',
            'html_url': f'https://github.com/example/demo/issues/{number}', 'title': f'Task {number}',
            'body': 'Implement a documented change and add a regression test.', 'state': 'open',
            'assignees': [], 'locked': False, 'comments': 0, 'labels': [{'name': 'good first issue'}],
            'created_at': '2026-10-01T00:00:00Z', 'updated_at': '2026-10-15T00:00:00Z', **changes}


def envelope(url, payload, observed_at=None):
    return {'url': url, 'payload': payload, 'observed_at': observed_at or datetime.now(timezone.utc).isoformat(),
            'retrieval_method': 'github_connector', 'tool_success': True, 'http_status': None, 'headers_available': False}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class LiveCandidateTests(unittest.TestCase):
    def test_unknown_pr_never_eligible(self):
        record = normalize_live(issue(), envelope('', {}), 'example/demo')
        self.assertEqual(strict_eligibility(record, timeline_complete=False).eligibility, 'unknown')

    def test_empty_body_and_claimed_labels_blocked(self):
        for changes in ({'body': ' '}, {'labels': [{'name': 'In Progress'}]}, {'labels': [{'name': 'Question'}]}):
            record = normalize_live(issue(**changes), envelope('', {}), 'example/demo')
            record['has_linked_open_pr'] = False
            self.assertEqual(strict_eligibility(record, timeline_complete=True).eligibility, 'temporarily_ineligible')

    def test_transferred_issue_rejected(self):
        with self.assertRaises(ValueError):
            normalize_live(issue(repository_url='https://api.github.com/repos/other/demo'), envelope('', {}), 'example/demo')

    def test_missing_assignment_status_rejected(self):
        row = issue()
        row.pop('assignees')
        with self.assertRaises(ValueError):
            normalize_live(row, envelope('', {}), 'example/demo')

    def test_old_and_future_evidence_not_fresh(self):
        now = datetime.now(timezone.utc)
        for stamp in (now - timedelta(hours=25), now + timedelta(minutes=10)):
            self.assertFalse(fresh(envelope('', {}, stamp.isoformat()), now))

    def test_pagination_requires_final_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = 'https://api.github.com/repos/example/demo/issues/1/timeline?per_page=100&page='
            write(root / 'example__demo/timeline_1_1.json', envelope(base + '1', [{'event': 'labeled'}] * 100))
            value, _ = timeline_evidence(root, 'example/demo', 'example/demo', 1, datetime.now(timezone.utc))
            self.assertIsNone(value)
            write(root / 'example__demo/timeline_1_2.json', envelope(base + '2', []))
            value, _ = timeline_evidence(root, 'example/demo', 'example/demo', 1, datetime.now(timezone.utc))
            self.assertFalse(value)

    def test_open_pr_blocks_but_closed_pr_does_not(self):
        for state, expected in [('open', True), ('closed', False)]:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                row = {'event': 'cross-referenced', 'source': {'issue': {'pull_request': {}, 'state': state}}}
                url = 'https://api.github.com/repos/example/demo/issues/1/timeline?per_page=100&page=1'
                write(root / 'example__demo/timeline_1_1.json', envelope(url, [row]))
                actual, _ = timeline_evidence(root, 'example/demo', 'example/demo', 1, datetime.now(timezone.utc))
                self.assertEqual(actual, expected)

    def test_real_candidate_schema_and_unknown_exclusion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'sources'
            cache = source / 'example__demo'
            now = datetime.now(timezone.utc).isoformat()
            repo = {'id': 12, 'full_name': 'example/demo', 'html_url': 'https://github.com/example/demo',
                    'pushed_at': now, 'language': 'Python', 'archived': False, 'disabled': False, 'has_issues': True}
            write(cache / 'repository.json', envelope('https://api.github.com/repos/example/demo', repo))
            write(cache / 'recent.json', envelope('https://api.github.com/repos/example/demo/issues' + DISCOVERY_QUERY, [issue(1), issue(2), issue(3)]))
            write(cache / 'newcomer.json', envelope('https://api.github.com/repos/example/demo/issues' + DISCOVERY_QUERY + '&labels=good%20first%20issue', []))
            for number in (1, 2, 3):
                write(cache / f'issue_{number}.json', envelope(f'https://api.github.com/repos/example/demo/issues/{number}', issue(number, state='closed' if number == 3 else 'open')))
            write(cache / 'timeline_1_1.json', envelope('https://api.github.com/repos/example/demo/issues/1/timeline?per_page=100&page=1', []))
            report = build_pool(source, ['example/demo'], root / 'pool.sqlite3', root / 'output', ROOT / 'db/sqlite/001_mvp.sql')
            self.assertEqual(report['available_candidates'], 1)
            self.assertEqual(report['eligibility_counts'], {'eligible': 1, 'unknown': 1, 'excluded': 1})
            self.assertFalse(report['frozen'])
            rows = json.loads((root / 'output/candidates.json').read_text())
            self.assertEqual([r['issue_number'] for r in rows], [1])

    def test_review_can_exclude_entire_successor_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            review = {'excluded_repositories': {'example/demo': {'reason': 'project_moved'}}}
            report = build_pool(root / 'sources', ['example/demo'], root / 'pool.sqlite3', root / 'output',
                                ROOT / 'db/sqlite/001_mvp.sql', review)
            self.assertEqual(report['available_candidates'], 0)
            self.assertEqual(report['repositories'][0]['status'], 'excluded_by_review')


if __name__ == '__main__':
    unittest.main()
