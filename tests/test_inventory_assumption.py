from concurrent.futures import ThreadPoolExecutor
import unittest
from oss_mentor.candidate_freshness import inventory_assumption, recommendation_is_current, verification_is_current
from oss_mentor.services.recommendation_service import _warnings


class InventoryAssumptionTests(unittest.TestCase):
    def test_trial_accepts_stale_available_without_changing_evidence(self):
        task = dict(candidate_availability='available',github_verified_at='2020-01-01T00:00:00Z')
        original = dict(task)
        with inventory_assumption(False):
            self.assertFalse(recommendation_is_current(task))
            with inventory_assumption(True):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    self.assertTrue(pool.submit(recommendation_is_current,task).result())
                self.assertFalse(verification_is_current(task))
                self.assertIn('inventory_availability_assumed',_warnings(task))
                self.assertFalse(recommendation_is_current(dict(task,candidate_availability='closed')))
            self.assertFalse(recommendation_is_current(task))
        self.assertEqual(task,original)

    def test_setting_restored_after_failure(self):
        task = dict(candidate_availability='available')
        with inventory_assumption(False):
            with self.assertRaises(RuntimeError):
                with inventory_assumption(True):
                    raise RuntimeError('test')
            self.assertFalse(recommendation_is_current(task))
