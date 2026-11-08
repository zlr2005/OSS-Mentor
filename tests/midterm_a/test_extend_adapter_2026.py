import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/midterm_a'))
import extend_adapter_2026 as extension


class ExtendedScopeTests(unittest.TestCase):
    def test_observation_end_clamps_quarters_and_omits_future_ranges(self):
        settings = {'repositories': [{'name': 'o/r'}]}
        self.assertEqual(extension.jobs(settings, '2026-05-10'), [
            ('o/r', '2026-01-01', '2026-03-31'), ('o/r', '2026-04-01', '2026-05-10')])
        self.assertEqual(extension.jobs(settings, '2026-11-02')[-1],
                         ('o/r', '2026-10-01', '2026-11-02'))

    def test_reject_other_year_observation(self):
        with self.assertRaises(ValueError):
            extension.jobs({'repositories': []}, '2027-01-01')
