import copy
import unittest
import numpy as np
import torch
from pfirec_inspired_ranker import history_features,build,evaluate


class PairFeatureTest(unittest.TestCase):
    def test_history_repository_is_observed_not_resolver_feature(self):
        history=[['o/r','a','src/a.py'],['o/r','a','test/a.py'],['q/s','b','x.java']]
        values=history_features(history,'o/r#1','Please fix src/a.py')
        self.assertEqual(values[0],1)
        self.assertEqual(values[1],.5)
        self.assertEqual(values,history_features(list(reversed(history)),'o/r#1','Please fix src/a.py'))

    def test_role_outcome_fields_cannot_change_pair_features(self):
        keys=['o/r#'+str(i) for i in range(1,22)]
        samples=[dict(profile_version='p',positive_issue_key=keys[0],comparison_issue_keys=keys[1:])]
        rng=np.random.default_rng(1)
        a=dict(profile_versions=np.asarray(['p']),issue_keys=np.asarray(keys),user=rng.normal(size=(1,384)).astype('float32'),
               item=rng.normal(size=(21,384)).astype('float32'),values=np.zeros((1,23),dtype='float32'),masks=np.zeros((1,23),dtype='float32'))
        lin={'p':dict(retained_X=[['o/r','a','src/a.py']])}
        c={k:dict(title='Bug in a.py',body_text='fix',snapshot_role='positive',resolver='someone') for k in keys}
        before=build(samples,a,lin,c)[0]
        for item in c.values():item.update(snapshot_role='control',resolver='different',resolved_at='2099-01-01')
        after=build(samples,a,lin,c)[0]
        self.assertTrue(np.array_equal(before,after))

    def test_same_repository_diagnostic_is_distinct_from_full_rank(self):
        samples=[dict(github_numeric_id=1,positive_issue_key='o/r#1',comparison_issue_keys=['o/r#2']+['q/s#'+str(i) for i in range(3,22)])]
        scores=np.zeros(21);scores[0]=.5;scores[1]=.6;scores[2]=.9
        ranks,m=evaluate(scores,samples)
        self.assertEqual(ranks,[3])
        self.assertEqual(m['same_repo_diagnostic']['mrr'],.5)


if __name__=='__main__':unittest.main()
