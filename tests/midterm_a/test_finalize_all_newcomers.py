import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS=Path(__file__).resolve().parents[2]/'scripts/midterm_a'
sys.path.insert(0,str(SCRIPTS))
from finalize_all_newcomers import supplementary_review


class SupplementalNewcomerReviewTests(unittest.TestCase):
    def test_unreadable_pr_in_text_cannot_be_silently_treated_as_closed(self):
        r=supplementary_review(self.detail(body='Background: https://github.com/example/repo/pull/9'),self.timeline(),{})
        self.assertEqual(r['decision'],'review_pending')
        self.assertEqual(r['reason'],'pr_in_text_unverified_requires_context')

    def detail(self,labels=None,body='A bounded documentation fix.'):
        return dict(html_url='https://github.com/example/repo/issues/1',body=body,
                    labels=[dict(name=label) for label in labels or []])

    def timeline(self,text='',events=None):
        return dict(pages=[events or ([dict(event='commented',html_url='https://github.com/example/repo/issues/1#issuecomment-1',body=text)] if text else [])])

    def test_pr_in_works_label_prevents_recommendation(self):
        r=supplementary_review(self.detail(['status: PR in works']),self.timeline(),{})
        self.assertEqual(r['decision'],'review_pending')
        self.assertEqual(r['reason'],'work_in_progress_label')

    def test_missing_subject_claim_is_deferred_not_declared_occupied(self):
        r=supplementary_review(self.detail(),self.timeline('Thank you, will work on it.'),{})
        self.assertEqual(r['decision'],'review_pending')
        self.assertIn('will work on it',r['evidence'][0]['text'])

    def test_plain_welcome_to_contribute_does_not_trigger_claim(self):
        self.assertIsNone(supplementary_review(self.detail(),self.timeline('A PR fixing this would be welcome.'),{}))

    def test_referenced_open_pr_in_body_needs_context(self):
        r=supplementary_review(self.detail(body='See https://github.com/example/repo/pull/2'),self.timeline(),
                               {'https://api.github.com/repos/example/repo/pulls/2':dict(state='open')})
        self.assertEqual(r['reason'],'open_pr_in_text_requires_context')
        self.assertEqual(r['decision'],'review_pending')

    def test_closed_pr_reference_does_not_prove_current_occupation(self):
        self.assertIsNone(supplementary_review(self.detail(body='See https://github.com/example/repo/pull/2'),self.timeline(),
            {'https://api.github.com/repos/example/repo/pulls/2':dict(state='closed')}))

    def test_commit_reference_does_not_automatically_become_a_candidate(self):
        r=supplementary_review(self.detail(),self.timeline(events=[dict(event='referenced',commit_url='https://api.github.com/repos/example/repo/commits/abcd')]),{})
        self.assertEqual(r['decision'],'review_pending')
        self.assertEqual(r['reason'],'commit_reference_requires_context')


if __name__=='__main__':unittest.main()
