from __future__ import annotations

import unittest

from oss_mentor.services.model_gateway import scorer_with_profile


class FakeScorer:
    def __init__(self) -> None:
        self.profile_adapter = object()
        self.profile_adapter_manifest = {"adapter_version": "fixture"}
        self.calls = []

    def score(self, *, profile, developer_key, candidates):
        self.calls.append(
            {
                "profile": profile,
                "developer_key": developer_key,
                "candidates": candidates,
            }
        )
        return "scored"


class ModelGatewayTests(unittest.TestCase):
    def test_confirmed_profile_is_injected_into_scorer_port(self) -> None:
        base = FakeScorer()
        snapshot = {
            "schema_version": "enhanced-profile-export-v0.1",
            "profile_state": "confirmed",
        }
        scorer = scorer_with_profile(base, snapshot)
        result = scorer.score(
            profile=object(),
            developer_key="alice",
            candidates=[{"task_candidate_id": 1}],
        )
        self.assertEqual("scored", result)
        self.assertIs(snapshot, base.calls[0]["profile"])
        self.assertEqual("alice", base.calls[0]["developer_key"])

    def test_missing_profile_uses_base_only_copy_when_adapter_is_loaded(self) -> None:
        base = FakeScorer()
        scorer = scorer_with_profile(base, None)
        self.assertIsNot(base, scorer)
        self.assertIsNone(scorer.profile_adapter)
        self.assertIsNone(scorer.profile_adapter_manifest)
        self.assertIsNotNone(base.profile_adapter)

    def test_legacy_scorer_is_unchanged_without_adapter_or_snapshot(self) -> None:
        base = FakeScorer()
        base.profile_adapter = None
        base.profile_adapter_manifest = None
        self.assertIs(base, scorer_with_profile(base, None))


if __name__ == "__main__":
    unittest.main()
