"""Prevent stale/negative observations from reviving recommendable candidates."""
import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from oss_mentor.collector.source_comparison import normalize_github_issue
from oss_mentor.datasets.merged_candidates import admission, build_merged, choose_latest
from oss_mentor.storage.candidates import SQLiteCandidateStorage

ROOT = Path(__file__).resolve().parents[2]


class MergedCandidateTests(unittest.TestCase):
    def test_fresh_repository_and_issue_in_an_older_batch_keep_their_actual_times(self):
        now=datetime.now(timezone.utc);at=now.isoformat()
        issue=dict(id=101,number=1,html_url='https://github.com/example/demo/issues/1',
                   repository_url='https://api.github.com/repos/example/demo',state='open',
                   title='Fix parser escaping',body='Reproduction: parser fails on escaped input.',
                   locked=False,assignees=[],labels=[],comments=0,created_at=at,updated_at=at)
        repo=dict(id=42,full_name='example/demo',html_url='https://github.com/example/demo',
                  archived=False,disabled=False,has_issues=True,pushed_at=at,language='Python')
        pack=dict(observed_start=(now-timedelta(days=2)).isoformat(),
                  repository_observed_at={'example/demo':at},detail_observed_at={'example/demo#1':at},
                  source_issues=[dict(repo_name='example/demo',repo_id='42',issue_id='101',issue_number=1)],
                  decisions=[dict(key='example/demo#1',repository='example/demo',number=1,github_issue_id='101',
                                  teacher_issue_id='101',decision='availability_passed',open_pr_references=[])],
                  issue_details={'example/demo#1':issue},repository_details={'example/demo':repo})
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);old=base/'old.json';old.write_text('[]','utf-8')
            teacher=base/'teacher';teacher.mkdir()
            (teacher/'review_pack.json.gz').write_bytes(gzip.compress(json.dumps(pack).encode()))
            report=build_merged(old_candidates=old,old_source=base/'missing',teacher_review=teacher,
                                database=base/'merged.sqlite3',output=base/'output',
                                migration=ROOT/'db/sqlite/001_mvp.sql',now=now)
            self.assertEqual(report['available_candidates'],1)
            candidate=json.loads((base/'output/candidates.json').read_text('utf-8'))[0]
            self.assertEqual(candidate['github_verified_at'],at)

    def test_newer_negative_replaces_old_positive(self):
        old = {'github_verified_at': '2026-10-15T12:00:00Z', 'github_issue_id': 101}
        new = {'decision': 'excluded', 'github_issue_id': '101'}
        self.assertEqual(choose_latest(old, new, '2026-10-16T08:00:00Z'), 'teacher')
        old['github_verified_at'] = '2026-10-16T09:00:00Z'
        self.assertEqual(choose_latest(old, new, '2026-10-16T08:00:00Z'), 'old')

    def test_identity_conflict_fails_closed(self):
        old = {'github_verified_at': '2026-10-15T12:00:00Z', 'github_issue_id': 101}
        self.assertEqual(choose_latest(old, {'github_issue_id': 102}, '2026-10-16T08:00:00Z'), 'identity_conflict')

    def test_stale_future_and_unverified_repository_not_admitted(self):
        now = datetime.now(timezone.utc)
        record = dict(state='open', assignment_state='unassigned', labels=[], is_locked=False,
                      body_text='Fix documented behavior', has_linked_open_pr=False)
        for observed, usable in [(now-timedelta(hours=25), True), (now+timedelta(minutes=10), True), (now, False)]:
            self.assertEqual(admission(record, observed=observed.isoformat(), approved=True,
                                       repo_usable=usable, now=now).eligibility, 'unknown')

    def test_standard_reader_cannot_return_newly_closed_overlap(self):
        now = datetime.now(timezone.utc)
        old_time = (now-timedelta(hours=1)).isoformat()
        new_time = now.isoformat()
        issue = dict(id=101, number=1, html_url='https://github.com/example/demo/issues/1',
                     repository_url='https://api.github.com/repos/example/demo', state='open',
                     title='Fix the parser', body='Reproduction and expected behavior', locked=False,
                     assignees=[], labels=[], comments=2, created_at=old_time, updated_at=old_time)
        old = normalize_github_issue(issue)
        old.update(repository='example/demo', source_system='github_rest', source_fetched_at=old_time,
                   github_verified_at=old_time, has_linked_open_pr=False, is_pull_request=False)
        repo = dict(id=42, full_name='example/demo', html_url='https://github.com/example/demo',
                    archived=False, disabled=False, has_issues=True, pushed_at=new_time, language='Python')
        # Batch start can precede the old record; the actual detail observation is newer.
        pack = dict(observed_start=(now-timedelta(hours=2)).isoformat(),
                    detail_observed_at={'example/demo#1':new_time},
                    source_issues=[dict(repo_name='example/demo',repo_id='42',issue_id='101',issue_number=1)],
                    decisions=[dict(key='example/demo#1',repository='example/demo',number=1,github_issue_id='101',
                                    teacher_issue_id='101',decision='excluded',reason='closed',open_pr_references=[])],
                    issue_details={'example/demo#1': {**issue, 'state': 'closed'}}, repository_details={'example/demo':repo})
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            old_file = base/'old.json'
            old_file.write_text(json.dumps([old]), encoding='utf-8')
            source = base/'source/example__demo'
            source.mkdir(parents=True)
            (source/'repository.json').write_text(json.dumps(dict(tool_success=True,url='https://api.github.com/repos/example/demo',
                                                                observed_at=old_time,payload=repo)),encoding='utf-8')
            teacher = base/'teacher'
            teacher.mkdir()
            (teacher/'review_pack.json.gz').write_bytes(gzip.compress(json.dumps(pack).encode()))
            original = old_file.read_bytes()
            db = base/'merged.sqlite3'
            report = build_merged(old_candidates=old_file, old_source=base/'source', teacher_review=teacher,
                                  database=db, output=base/'output', migration=ROOT/'db/sqlite/001_mvp.sql',now=now)
            self.assertEqual(report['available_candidates'], 0)
            self.assertEqual(report['newer_teacher_removed_old'], 1)
            store = SQLiteCandidateStorage(db, ROOT/'db/sqlite/001_mvp.sql')
            self.assertEqual(store.available_candidates(), [])
            self.assertEqual(old_file.read_bytes(), original)
            # Later work versions can include repositories absent from the paper cache.
            rebuilt = build_merged(old_candidates=old_file, old_source=base/'missing_source', teacher_review=teacher,
                                   prior_reviews=[teacher], database=base/'next.sqlite3', output=base/'next_output',
                                   migration=ROOT/'db/sqlite/001_mvp.sql', now=now)
            self.assertEqual(rebuilt['available_candidates'], 0)
            self.assertEqual(rebuilt['sqlite_integrity'], 'ok')
            # A matching closed-state proof does not invent omitted comment
            # counts or revive an older open candidate in the standard reader.
            compact = dict(pack)
            compact['issue_details']={'example/demo#1':{k:v for k,v in issue.items() if k not in ('comments','created_at','body')}}
            compact['issue_details']['example/demo#1']['state']='closed'
            (teacher/'review_pack.json.gz').write_bytes(gzip.compress(json.dumps(compact).encode()))
            minimal = build_merged(old_candidates=old_file, old_source=base/'source', teacher_review=teacher,
                                   database=base/'minimal.sqlite3', output=base/'minimal_output',
                                   migration=ROOT/'db/sqlite/001_mvp.sql', now=now)
            self.assertEqual(minimal['available_candidates'],0)
            self.assertEqual(minimal['stored_records'],0)
            minimal_audit=json.loads((base/'minimal_output/audit.json').read_text('utf-8'))
            self.assertEqual(minimal_audit[0]['eligibility'],'ineligible')
            self.assertEqual(minimal_audit[0]['reasons'],['merge:closed'])
            self.assertIsNone(minimal_audit[0]['record'])
            with self.assertRaises(ValueError):
                build_merged(old_candidates=old_file, old_source=base/'source', teacher_review=teacher,
                             database=db, output=base/'output', migration=ROOT/'db/sqlite/001_mvp.sql',now=now)


if __name__ == '__main__':
    unittest.main()
