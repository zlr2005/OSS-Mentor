import unittest
from skill_ranker_experiment import proxy, corpus_stats, hard_controls, NAMES


class SkillExperimentTests(unittest.TestCase):
    def test_proxy_ignores_outcome_metadata(self):
        history=[['o/r','abc','tests/test_client.py']]
        c={'o/r#1':dict(title='Python client test fails',body_text='client.py',resolver='x')}
        stats=corpus_stats(list(c),c)
        before=proxy(history,c['o/r#1']['title'],c['o/r#1']['body_text'],stats)
        c['o/r#1']['resolver']='future person'
        self.assertEqual(before,proxy(history,c['o/r#1']['title'],c['o/r#1']['body_text'],stats))
        self.assertEqual(len(before),len(NAMES))
        self.assertGreater(before[NAMES.index('language_overlap')],0)
        self.assertGreater(before[NAMES.index('test_match')],0)
        self.assertEqual(before,proxy(list(reversed(history)),c['o/r#1']['title'],c['o/r#1']['body_text'],stats))

    def test_no_evidence_stays_zero(self):
        v=proxy([], 'unrelated issue', '', ({},1,2))
        self.assertEqual(v,[0]*len(NAMES))

    def test_controls_are_unique_cutoff_eligible_and_exclude_known_positives(self):
        keys=['o/r#'+str(i) for i in range(40)]
        sample=dict(positive_issue_key=keys[0], comparison_issue_keys=keys[1:21])
        sims={k:float(i) for i,k in enumerate(keys)}
        result=hard_controls(sample,keys[:35],{},sims,{keys[0],keys[1],keys[34]})['comparison_issue_keys']
        self.assertEqual(len(result),20)
        self.assertEqual(len(set(result)),20)
        self.assertTrue(set(result)<=set(keys[:35]))
        self.assertFalse(set(result)&{keys[0],keys[1],keys[34]})
        self.assertEqual(sample['comparison_issue_keys'],keys[1:21])

    def test_corpus_uses_only_requested_documents(self):
        candidates={'a':dict(title='python',body_text=''), 'future':dict(title='java',body_text='')}
        df,n,avg=corpus_stats(['a'],candidates)
        self.assertNotIn('java',df)
        self.assertEqual(n,1)


if __name__=='__main__': unittest.main()
