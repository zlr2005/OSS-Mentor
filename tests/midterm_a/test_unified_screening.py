"""Unified screening must fail closed for missing, stale or conflicting evidence."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
import hashlib
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/midterm_a'))
from screen_unified_teacher import assess,absent_from_latest_inventory,context_fingerprint


class UnifiedScreeningTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.at=self.now.isoformat()
        self.source=dict(repo_name='example/demo',repo_id=42,issue_number=1,issue_id=101)
        self.detail=dict(id=101,number=1,repository_url='https://api.github.com/repos/example/demo',
                         html_url='https://github.com/example/demo/issues/1',title='Fix parser escaping',
                         state='open',locked=False,assignees=[],labels=[],comments=0,
                         body='The parser fails on escaped characters. Reproduction: parse the attached input.',
                         created_at=self.at,updated_at=self.at)
        self.repo=dict(id=42,full_name='example/demo',html_url='https://github.com/example/demo',
                       archived=False,disabled=False,has_issues=True,pushed_at=self.at,language='Python')
        self.timeline=dict(complete=True,observed_at=self.at,pages=[[]])

    def run_review(self,**changes):
        args=dict(source=self.source,detail=self.detail,observed=self.at,repository=self.repo,
                  repository_at=self.at,timeline=self.timeline,prs={},now=self.now)
        args.update(changes)
        return assess(**args)

    def test_fresh_complete_unassigned_passes_but_incomplete_timeline_does_not(self):
        self.assertEqual(self.run_review()['decision'],'availability_passed')
        self.assertEqual(self.run_review(timeline=None)['decision'],'unverified')

    def test_identity_conflict_and_stale_status_do_not_pass(self):
        self.assertEqual(self.run_review(detail={**self.detail,'id':102})['reason'],'identity_mismatch')
        old=(self.now-timedelta(hours=25)).isoformat()
        self.assertEqual(self.run_review(observed=old)['decision'],'unverified')

    def test_inventory_presence_preserves_old_detail_without_extending_its_time(self):
        earlier=(self.now-timedelta(hours=1)).isoformat()
        envelope=dict(observed_at=earlier,method='github_source_bound_read')
        inventory=dict(complete=True,observed_at=self.at,source_keys_present=['example/demo#1'])
        self.assertFalse(absent_from_latest_inventory('example/demo#1',self.detail,envelope,inventory))
        self.assertEqual(envelope['observed_at'],earlier)
        inventory['source_keys_present']=[]
        self.assertTrue(absent_from_latest_inventory('example/demo#1',self.detail,envelope,inventory))
        inventory['complete']=False
        self.assertFalse(absent_from_latest_inventory('example/demo#1',self.detail,envelope,inventory))

    def test_referenced_pr_requires_current_status(self):
        url='https://api.github.com/repos/example/demo/pulls/2'
        timeline={**self.timeline,'pages':[[dict(event='cross-referenced',source=dict(issue=dict(pull_request=dict(url=url))))]]}
        self.assertEqual(self.run_review(timeline=timeline)['decision'],'unverified')
        self.assertEqual(self.run_review(timeline=timeline,prs={url:dict(state='open',observed_at=self.at)})['reason'],'linked_open_pr')
        self.assertEqual(self.run_review(timeline=timeline,prs={url:dict(state='closed',observed_at=self.at)})['decision'],'availability_passed')

    def test_claim_comment_goes_to_manual_review(self):
        timeline={**self.timeline,'pages':[[dict(event='commented',body="I'd like to work on this issue.",html_url='https://github.com/example/demo/issues/1#issuecomment-2')]]}
        self.assertEqual(self.run_review(timeline=timeline)['reason'],'claim_or_work_in_progress_text')

    def clearance(self,detail=None,timeline=None):
        return dict(issue_id='101',reviewed_at=self.at,decision='clear_for_availability_checks',
                    rationale='Read exact text',evidence_sha256=context_fingerprint(detail or self.detail,timeline or self.timeline))

    def test_context_clearance_is_bound_to_exact_conversation_and_identity(self):
        resolution=self.clearance()
        self.assertEqual(self.run_review(resolution=resolution)['decision'],'availability_passed')
        changed={**self.detail,'body':self.detail['body']+' New content.'}
        self.assertEqual(self.run_review(detail=changed,resolution=resolution)['reason'],'context_review_stale_or_changed')
        self.assertEqual(self.run_review(resolution={**resolution,'issue_id':'102'})['decision'],'unverified')

    def test_context_clearance_cannot_bypass_status_timeline_or_pr_checks(self):
        detail={**self.detail,'assignees':[{'login':'worker'}]}
        self.assertEqual(self.run_review(detail=detail,resolution=self.clearance(detail=detail))['reason'],'assigned')
        timeline={**self.timeline,'complete':False}
        self.assertEqual(self.run_review(timeline=timeline,resolution=self.clearance(timeline=timeline))['reason'],'timeline_incomplete_or_stale')
        url='https://api.github.com/repos/example/demo/pulls/2'
        timeline={**self.timeline,'pages':[[dict(event='cross-referenced',source=dict(issue=dict(pull_request=dict(url=url))))]]}
        resolution=self.clearance(timeline=timeline)
        self.assertEqual(self.run_review(timeline=timeline,resolution=resolution)['decision'],'unverified')
        self.assertEqual(self.run_review(timeline=timeline,resolution=resolution,prs={url:dict(state='open',observed_at=self.at)})['reason'],'linked_open_pr')

    def test_only_exact_reviewed_nonclaim_text_can_be_ignored(self):
        text='- [ ] I want to work on this issue'
        detail={**self.detail,'body':text}
        resolution={**self.clearance(detail=detail),'ignored_texts':{detail['html_url']:hashlib.sha256(text.encode()).hexdigest()}}
        self.assertEqual(self.run_review(detail=detail,resolution=resolution)['decision'],'availability_passed')
        self.assertEqual(self.run_review(detail=detail,resolution={**resolution,'ignored_texts':{}})['decision'],'review_pending')

    def test_explicit_pr_in_reviewed_text_still_requires_a_current_read(self):
        html='https://github.com/example/demo/pull/2'
        detail={**self.detail,'body':'Related patch '+html}
        resolution={**self.clearance(detail=detail),'additional_pr_references':[dict(source_url=detail['html_url'],text_sha256=hashlib.sha256(detail['body'].encode()).hexdigest(),html_url=html)]}
        self.assertEqual(self.run_review(detail=detail,resolution=resolution)['decision'],'unverified')
        prs={'https://api.github.com/repos/example/demo/pulls/2':dict(state='closed',observed_at=self.at)}
        self.assertEqual(self.run_review(detail=detail,resolution=resolution,prs=prs)['decision'],'availability_passed')

    def test_projected_rejection_cannot_approve_or_invent_claim(self):
        projected={**self.detail,'body_omitted':True};projected.pop('body')
        self.assertEqual(self.run_review(detail=projected)['decision'],'unverified')
        with self.assertRaises(ValueError):
            self.run_review(detail=projected,projected_rejection=dict(reason='availability_passed'))
        with self.assertRaises(ValueError):
            self.run_review(detail=projected,projected_rejection=dict(reason='claim_or_work_in_progress_text',claim_signals=[]))
        valid=dict(reason='claim_or_work_in_progress_text',claim_signals=[dict(text="I'd like to work on this issue",url=self.detail['html_url'])])
        self.assertEqual(self.run_review(detail=projected,projected_rejection=valid)['decision'],'review_pending')

    def test_reviewed_environment_text_does_not_ignore_an_edited_claim(self):
        url='https://github.com/example/demo/issues/1#issuecomment-2'
        text="Windows is the system I'm working on."
        reviews={url:dict(text_sha256=hashlib.sha256(text.encode()).hexdigest())}
        event=dict(event='commented',body=text,html_url=url)
        timeline={**self.timeline,'pages':[[event]]}
        with patch('screen_unified_teacher.NON_CLAIM_REVIEW', reviews):
            self.assertEqual(self.run_review(timeline=timeline)['decision'],'availability_passed')
            changed={**event,'body':text+" I will fix this issue."}
            self.assertEqual(self.run_review(timeline={**self.timeline,'pages':[[changed]]})['decision'],'review_pending')


if __name__ == '__main__':
    unittest.main()
