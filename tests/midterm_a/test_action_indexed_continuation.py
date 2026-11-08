from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/midterm_a'))
from prepare_action_indexed_continuation import merge_intervals


class ActionContinuationTests(unittest.TestCase):
    def test_merges_duplicates_overlap_and_adjacency_but_preserves_completed_gaps(self):
        self.assertEqual(merge_intervals([(10,20),(0,10),(5,15),(0,10),(30,40)]),[[0,20],[30,40]])
        self.assertEqual(merge_intervals([]),[])


if __name__=='__main__':unittest.main()
