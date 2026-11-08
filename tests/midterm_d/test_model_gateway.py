from __future__ import annotations

from dataclasses import dataclass
import unittest

from oss_mentor.services.model_gateway import ModelUnavailable, scorer_with_profile


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


@dataclass(frozen=True)
class FakePairedRow:
    task_candidate_id: int
    coverage_state: str
    raw_score_original: float
    raw_score_profile: float
    rank_original: int
    rank_profile: int


@dataclass(frozen=True)
class FakePairedBatch:
    scores: tuple[FakePairedRow, ...]
    model_version: str
    profile_adapter_version: str
    profile_key: str
    profile_version: str


class FakeShadowScorer(FakeScorer):
    def __init__(self) -> None:
        super().__init__()
        self.manifest = {
            "model_version": "coder-test",
            "dataset_version": "dataset-test",
            "graph_version": "graph-test",
            "encoder_version": "encoder-test",
        }
        self.profile_adapter_manifest = {
            "adapter_version": "adapter-test",
            "base_user_mode": "population_mean",
        }

    def _base_user(self, developer_key):
        return None, "population_mean", []

    def score_paired(self, *, profile, developer_key, candidates):
        self.calls.append(
            {
                "profile": profile,
                "developer_key": developer_key,
                "candidates": candidates,
            }
        )
        return FakePairedBatch(
            scores=(
                FakePairedRow(1, "fixed_train_graph", 0.2, 0.8, 2, 1),
                FakePairedRow(2, "platform_candidate_cache", 0.7, 0.1, 1, 2),
            ),
            model_version="coder-test",
            profile_adapter_version="adapter-test",
            profile_key="profile:test",
            profile_version="profile:v1",
        )


class FakeBoundScorer:
    scoring_engine = "coder_gated"

    def __init__(self, enhanced_profile=None) -> None:
        self.enhanced_profile = enhanced_profile
        self.profile_adapter = None

    def bind_enhanced_profile(self, profile):
        return FakeBoundScorer(profile)


class ModelGatewayTests(unittest.TestCase):
    def test_gated_scorer_binds_confirmed_profile_before_scoring(self) -> None:
        base = FakeBoundScorer()
        snapshot = {"schema_version": "enhanced-profile-export-v0.1"}
        scorer = scorer_with_profile(base, snapshot, mode="off")
        self.assertIsNot(base, scorer)
        self.assertEqual(snapshot, scorer.enhanced_profile)
        self.assertIsNone(base.enhanced_profile)

    def test_confirmed_profile_is_injected_into_scorer_port(self) -> None:
        base = FakeScorer()
        snapshot = {
            "schema_version": "enhanced-profile-export-v0.1",
            "profile_state": "confirmed",
        }
        scorer = scorer_with_profile(base, snapshot, mode="serve")
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

    def test_shadow_mode_returns_original_scores_and_captures_pairing(self) -> None:
        base = FakeShadowScorer()
        snapshot = {"profile_state": "confirmed", "profile_key": "profile:test"}
        scorer = scorer_with_profile(
            base,
            snapshot,
            mode="shadow",
            service_track="growth",
        )
        batch = scorer.score(
            profile=object(),
            developer_key="alice",
            candidates=[
                {"task_candidate_id": 1},
                {"task_candidate_id": 2},
            ],
        )
        self.assertEqual("population_mean", batch.profile_mode)
        self.assertEqual([1, 2], [row.task_candidate_id for row in batch.scores])
        self.assertGreater(batch.scores[1].score, batch.scores[0].score)
        paired = scorer.paired_payload()
        self.assertEqual("adapter-test", paired["profile_adapter_version"])
        self.assertEqual("profile:v1", paired["profile_version"])
        self.assertEqual("shadow", paired["advanced_adapter_mode"])
        self.assertEqual("population_mean", paired["profile_mode"])
        self.assertIs(snapshot, base.calls[0]["profile"])

    def test_newcomer_and_off_modes_never_apply_profile_adapter(self) -> None:
        snapshot = {"profile_state": "confirmed"}
        for mode, track in (("serve", "newcomer"), ("off", "growth")):
            with self.subTest(mode=mode, track=track):
                base = FakeScorer()
                scorer = scorer_with_profile(
                    base,
                    snapshot,
                    mode=mode,
                    service_track=track,
                )
                self.assertIsNot(base, scorer)
                self.assertIsNone(scorer.profile_adapter)

    def test_invalid_runtime_mode_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "off, shadow or serve"):
            scorer_with_profile(FakeScorer(), {}, mode="unsafe")

    def test_shadow_and_serve_require_loaded_adapter_for_confirmed_profile(self) -> None:
        base = FakeScorer()
        base.profile_adapter = None
        base.profile_adapter_manifest = None
        for mode in ("shadow", "serve"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ModelUnavailable, "loaded advanced adapter"):
                    scorer_with_profile(
                        base,
                        {"profile_state": "confirmed"},
                        mode=mode,
                        service_track="growth",
                    )


if __name__ == "__main__":
    unittest.main()
