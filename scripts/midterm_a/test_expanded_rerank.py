import unittest
import json
import numpy as np
from evaluate_expanded_pool import eligible_pool,rank_of
from zeroshot_rerank_pilot import reordered_pool


class ExpandedTest(unittest.TestCase):
    def test_exact_snapshot_boundary_and_known_positive_filter(self):
        c=[dict(issue_key='o/r#1',available_from='2026-01-01T00:00:00Z',available_until='2026-01-01T00:00:01Z')]
        self.assertEqual(eligible_pool(c,'2026-01-01T00:00:00+00:00',set()),['o/r#1'])
        self.assertEqual(eligible_pool(c,'2026-01-01T00:00:01Z',set()),[])
        self.assertEqual(eligible_pool(c,'2026-01-01T00:00:00Z',{'o/r#1'}),[])

    def test_missing_target_not_injected_and_numpy_rank_serializes(self):
        self.assertIsNone(rank_of(['a','b'],np.array([.1,.2]),'c'))
        rank=rank_of(['a','b','c'],np.array([.1,.2,.2]),'b')
        self.assertEqual(rank,2)
        self.assertEqual(json.dumps(rank),'2')

    def test_rerank_only_changes_retrieved_prefix(self):
        keys=['a','b','c','d'];retriever=np.array([4,3,2,1])
        order=reordered_pool(keys,retriever,[0,1],k=2)
        self.assertEqual(order,[1,0,2,3])
        self.assertEqual(set(order),set(range(4)))
        self.assertEqual(order.index(2),2)

    def test_reranker_cannot_accept_extra_positive(self):
        with self.assertRaises(ValueError):reordered_pool(['a','b','c'],[3,2,1],[1,2,3],k=2)

    def test_rrf_is_stable_and_keeps_tail(self):
        a=reordered_pool(['a','b','c'],[3,2,1],[0,1],k=2,rrf=True)
        b=reordered_pool(['a','b','c'],[3,2,1],[0,1],k=2,rrf=True)
        self.assertEqual(a,b);self.assertEqual(a[-1],2)


if __name__=='__main__':unittest.main()
