from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/midterm_a'))
from prepare_inventory_gaps import gaps


class InventoryGapTests(unittest.TestCase):
    def test_overlapping_successes_are_not_queried_twice(self):
        self.assertEqual(gaps(0,100,[(10,30),(20,40),(40,60)]),[(0,10),(60,100)])

    def test_outside_ranges_do_not_expand_scope(self):
        self.assertEqual(gaps(10,50,[(-5,15),(45,70),(80,90)]),[(15,45)])

    def test_full_coverage_has_no_pending_query(self):
        self.assertEqual(gaps(10,50,[(0,25),(25,70)]),[])

    def test_empty_coverage_keeps_the_entire_gap(self):
        self.assertEqual(gaps(10,50,[]),[(10,50)])


if __name__=='__main__':
    unittest.main()
