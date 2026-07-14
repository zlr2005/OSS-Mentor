from __future__ import annotations

import unittest

from oss_mentor.candidate_sync import CandidateSynchronizer


class CandidateSyncTests(unittest.TestCase):
    def test_detects_only_open_cross_referenced_pull_request(self) -> None:
        events = [
            {
                "event": "cross-referenced",
                "source": {"issue": {"state": "closed", "pull_request": {}}},
            },
            {"event": "assigned", "assignee": {"login": "example"}},
        ]
        self.assertFalse(CandidateSynchronizer._has_linked_open_pr(events))

        events.append(
            {
                "event": "cross-referenced",
                "source": {"issue": {"state": "open", "pull_request": {}}},
            }
        )
        self.assertTrue(CandidateSynchronizer._has_linked_open_pr(events))


if __name__ == "__main__":
    unittest.main()
