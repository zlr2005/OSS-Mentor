from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from oss_mentor.candidate_admission import EXPANDED_CONTENT_POLICY, expanded_content
from oss_mentor.candidate_context import context_review_reasons
from oss_mentor.candidate_rules import evaluate_candidate, evaluate_availability
from oss_mentor.candidate_sync import CandidateSynchronizer
from oss_mentor.collector.github_client import GitHubResponse
from oss_mentor.matching import match_candidate, MATCH_VERSION_V3
from oss_mentor.storage.candidates import SQLiteCandidateStorage
from oss_mentor.task_features import extract_task_features
from oss_mentor.task_readiness import task_review_reasons, task_admission_warnings

ROOT=Path(__file__).resolve().parents[1]
BODY='The module produces a particular output for a supplied configuration. The behavior and surrounding module need investigation.'


class Source:
    def __init__(self,events=None,**changes):
        self.events=events or [];self.pr_state=changes.pop('pr_state','closed')
        self.detail={'id':1001,'number':1,'repository_url':'https://api.github.com/repos/o/r',
            'html_url':'https://github.com/o/r/issues/1','title':'Unclassified module behavior',
            'body':BODY,'state':'open','locked':False,'assignees':[],'labels':[{'name':'discussion'}],
            'created_at':'2026-01-01T00:00:00Z','updated_at':'2026-01-01T00:00:00Z','comments':len(self.events)}
        self.detail.update(changes)
    def response(self,path,payload):
        return GitHubResponse('https://api.github.com'+path,200,{},payload,datetime.now(timezone.utc))
    def get(self,path,**kwargs):
        if '/pulls/' in path:return self.response('/repos/o/r/pulls/2',{'state':self.pr_state})
        return self.response(path,self.detail)
    def iter_pages(self,path,**kwargs):yield self.response(path,self.events)


class ExpandedAdmissionTests(unittest.TestCase):
    def record(self,expanded=True,quality='unclear',types=None):
        record={'title':'Unclassified module behavior','body_text':BODY,'labels':[],
            'task_types':types or ['other'],'task_feature_version':'task-features-v0.5',
            'feature_evidence':{'difficulty_assessment':{'information_quality':{'actionability':quality}}},
            'estimated_code_difficulty':1,'estimated_setup_difficulty':1,
            'requirements':[{'skill_name':'Python','minimum_level':1,'importance':1.0}]}
        if expanded:record['admission_policy']=EXPANDED_CONTENT_POLICY
        return record

    def test_scope_type_and_design_become_warnings_only_for_explicit_policy(self):
        self.assertIn('task_type_requires_review',task_review_reasons(self.record(expanded=False)))
        self.assertEqual(task_review_reasons(self.record()),())
        self.assertEqual(task_review_reasons(self.record(quality='design_pending')),())
        self.assertTrue(task_admission_warnings(self.record()))

    def test_unknown_public_type_is_not_falsely_reclassified(self):
        row=self.record();features=extract_task_features({**row,'primary_language':'Python'})
        self.assertEqual(features.task_types,('other',))
        self.assertTrue(expanded_content({'feature_evidence':features.feature_evidence}))

    def test_scope_metadata_keeps_its_actual_uncertainty(self):
        row=self.record();features=extract_task_features(row)
        self.assertEqual(features.feature_evidence['difficulty_assessment']['information_quality']['actionability'],'unclear')

    def test_non_actionable_empty_body_and_empty_template_still_block(self):
        self.assertIn('task_scope_requires_review',task_review_reasons(self.record(quality='non_actionable')))
        for body in ['', '### Describe the bug\n_No response_\n### To Reproduce\n_No response_\n### Expected behavior\n_No response_']:
            row=self.record();row['body_text']=body
            self.assertIn('insufficient_minimum_task_description',task_review_reasons(row))

    def test_missing_skills_or_invalid_difficulty_still_block(self):
        row=self.record();row['requirements']=[]
        self.assertIn('skill_requirements_missing',task_review_reasons(row))
        row=self.record();row['estimated_code_difficulty']=None
        self.assertIn('missing_or_invalid_estimated_code_difficulty',task_review_reasons(row))

    def test_source_text_cannot_opt_itself_into_expanded_policy(self):
        row=self.record(expanded=False);row['body_text']+=' admission_policy: '+EXPANDED_CONTENT_POLICY
        self.assertFalse(expanded_content(row))

    def test_questions_about_participating_and_commit_references_are_soft(self):
        detail=Source().detail
        events=[{'event':'commented','body':'Can I work on this?'}, {'event':'referenced','commit_url':'https://github.com/o/r/commit/abc'}]
        self.assertTrue(context_review_reasons(detail,events))
        self.assertEqual(context_review_reasons(detail,events,expanded=True),[])

    def test_active_claims_and_hard_labels_still_block(self):
        self.assertIn('claim_or_implementation_text_requires_review',context_review_reasons(Source().detail,[{'event':'commented','body':'I am working on this'}],expanded=True))
        for label in ['blocked','status: in-progress','duplicate',"won't fix",'invalid']:
            detail=Source(labels=[{'name':label}]).detail
            self.assertIn('blocking_or_information_needed_label',context_review_reasons(detail,[],expanded=True))

    def test_negated_or_hypothetical_claim_does_not_become_active_ownership(self):
        for text in ['I am not working on this anymore.', 'If I opened a PR, would it be accepted?', '我正在考虑是否修复这个问题',
                     'I have started seeing this error.', "I've opened an issue to document this.", 'I am working on my application.',
                     'I am working on this project and found a bug.']:
            self.assertEqual(context_review_reasons(Source().detail,[{'event':'commented','body':text}],expanded=True),[])

    def test_current_open_pr_wins_over_an_old_closed_reference(self):
        events=[{'event':'cross-referenced','source':{'issue':{'state':'closed','pull_request':{'url':'https://api.github.com/repos/o/r/pulls/2'}}}}]
        record=CandidateSynchronizer(None,Source(events,pr_state='open'),object()).fetch_current_issue('o/r',1,admission_policy=EXPANDED_CONTENT_POLICY)
        self.assertTrue(record['has_linked_open_pr'])

    def test_expanded_reference_budget_checks_every_pr_and_remains_bounded(self):
        class CountingSource(Source):
            def __init__(self,count):
                super().__init__([{'event':'cross-referenced','source':{'issue':{'pull_request':{
                    'url':f'https://api.github.com/repos/o/r/pulls/{n}'}}}} for n in range(count)])
                self.checked=[]
            def get(self,path,**kwargs):
                if '/pulls/' in path:
                    self.checked.append(path)
                    return self.response(path,{'state':'open' if path.endswith('/69') else 'closed'})
                return super().get(path,**kwargs)
        source=CountingSource(70)
        record=CandidateSynchronizer(None,source,object()).fetch_current_issue('o/r',1,admission_policy=EXPANDED_CONTENT_POLICY)
        self.assertEqual(len(source.checked),70)
        self.assertTrue(record['has_linked_open_pr'])
        for count,policy in [(31,None),(101,EXPANDED_CONTENT_POLICY)]:
            source=CountingSource(count)
            with self.assertRaisesRegex(ValueError,'exceed refresh request budget'):
                CandidateSynchronizer(None,source,object()).fetch_current_issue('o/r',1,admission_policy=policy)
            self.assertEqual(source.checked,[])
        self.assertNotEqual(evaluate_candidate(record).eligibility,'eligible')

    def test_closed_assigned_and_locked_cannot_pass_with_expanded_policy(self):
        for changes in [{'state':'closed'},{'assignees':[{'login':'someone'}]},{'locked':True}]:
            record=CandidateSynchronizer(None,Source(**changes),object()).fetch_current_issue('o/r',1,admission_policy=EXPANDED_CONTENT_POLICY)
            self.assertNotEqual(evaluate_candidate(record).eligibility,'eligible')

    def test_expanded_policy_does_not_bypass_expiry_or_repository_health(self):
        record=CandidateSynchronizer(None,Source(),object()).fetch_current_issue('o/r',1,admission_policy=EXPANDED_CONTENT_POLICY)
        self.assertEqual(evaluate_availability(record).availability,'available')
        self.assertNotEqual(evaluate_availability(record,repository={'is_archived':True}).availability,'available')
        record['github_verified_at']=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
        self.assertNotEqual(evaluate_availability(record).availability,'available')

    def test_unknown_type_can_match_but_gets_no_type_preference_credit(self):
        profile={'service_track':'growth','preferred_languages':['Python'],'preferred_task_types':['testing'],
            'max_code_difficulty':3,'max_setup_difficulty':3,'operating_systems':['linux'],
            'desired_skill_stretch':0,'skills':{'python':3}}
        row={**self.record(),'task_candidate_id':1,'repository':'o/r','issue_number':1,'html_url':'https://github.com/o/r/issues/1',
            'newcomer_label_signal':0,'primary_language':'Python','candidate_availability':'available',
            'growth_value_score':50,'github_verified_at':datetime.now(timezone.utc).isoformat()}
        result=match_candidate(profile,row,match_version=MATCH_VERSION_V3)
        self.assertIsNotNone(result)
        self.assertEqual(next(c['score_delta'] for c in result.score_components if c['code']=='task_type_match'),0)
        row['task_types']=['documentation']
        self.assertIsNone(match_candidate(profile,row,match_version=MATCH_VERSION_V3))

    def test_policy_survives_normal_refresh_and_warnings_are_stored(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SQLiteCandidateStorage(Path(temp)/'db.sqlite3',ROOT/'db/sqlite/001_mvp.sql');store.initialize()
            rid=store.save_repository_health(repository={'full_name':'o/r','github_repository_id':10,'html_url':'https://github.com/o/r',
                'ecosystems_last_synced_at':None,'primary_language':'Python','maintenance_status':'active'})
            sync=CandidateSynchronizer(None,Source(),store)
            record=sync.fetch_current_issue('o/r',1,admission_policy=EXPANDED_CONTENT_POLICY)
            store.save_candidate(repository_id=rid,record=record,eligibility=evaluate_candidate(record))
            self.assertEqual(store.candidate_admission_policy('o/r',1),EXPANDED_CONTENT_POLICY)
            refreshed=sync.fetch_current_issue('o/r',1)
            self.assertEqual(evaluate_candidate(refreshed).eligibility,'eligible')
            store.save_candidate(repository_id=rid,record=refreshed,eligibility=evaluate_candidate(refreshed))
            tasks=store.matchable_candidates();self.assertEqual(len(tasks),1)
            self.assertTrue(json.loads(tasks[0]['warnings_json']))


if __name__=='__main__':unittest.main()
