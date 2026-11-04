from __future__ import annotations

from contextlib import closing, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from oss_mentor.api import RecommendationApi
from oss_mentor.cli import main
from oss_mentor.contracts_midterm import EnhancedProfileFeatures
from oss_mentor.model_scoring import ModelScore, ModelScoreBatch
from oss_mentor.services.auth_service import (
    AuthService,
    AuthSettings,
    SESSION_COOKIE_NAME,
)
from oss_mentor.services.profile_service import ProfileService
from oss_mentor.services.recommendation_service import RecommendationService
from oss_mentor.storage.candidates import SQLiteCandidateStorage
from oss_mentor.storage.identity import IdentityStore
from oss_mentor.storage.profiles import SQLiteProfileStorage

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "sqlite" / "001_mvp.sql"


@dataclass(frozen=True)
class PairedRow:
    task_candidate_id: int
    coverage_state: str
    raw_score_original: float
    raw_score_profile: float
    rank_original: int
    rank_profile: int


@dataclass(frozen=True)
class PairedBatch:
    scores: tuple[PairedRow, ...]
    model_version: str
    profile_adapter_version: str
    profile_key: str
    profile_version: str


class FakeCoder:
    def __init__(self) -> None:
        self.manifest = {
            "model_version": "coder-test-v1",
            "dataset_version": "dataset-test",
            "graph_version": "graph-test",
            "encoder_version": "encoder-test",
        }
        self.profile_adapter = object()
        self.profile_adapter_manifest = {
            "adapter_version": "advanced-adapter-test-v1",
            "base_user_mode": "population_mean",
        }
        self.score_profiles: list[object] = []

    def _base_user(self, developer_key):
        del developer_key
        return None, "population_mean", []

    def score(self, *, profile, developer_key, candidates):
        del developer_key
        self.score_profiles.append(profile)
        profile_mode = (
            "population_mean_profile"
            if self.profile_adapter is not None
            else "population_mean"
        )
        return ModelScoreBatch(
            scores=tuple(
                ModelScore(
                    task_candidate_id=int(candidate["task_candidate_id"]),
                    score=0.5,
                    coverage_state="fixed_train_graph",
                )
                for candidate in candidates
            ),
            model_version="coder-test-v1",
            dataset_version="dataset-test",
            graph_version="graph-test",
            encoder_version="encoder-test",
            profile_mode=profile_mode,
        )

    def score_paired(self, *, profile, developer_key, candidates):
        del profile, developer_key
        count = len(candidates)
        return PairedBatch(
            scores=tuple(
                PairedRow(
                    task_candidate_id=int(candidate["task_candidate_id"]),
                    coverage_state="fixed_train_graph",
                    raw_score_original=float(index),
                    raw_score_profile=float(count - index),
                    rank_original=index + 1,
                    rank_profile=count - index,
                )
                for index, candidate in enumerate(candidates)
            ),
            model_version="coder-test-v1",
            profile_adapter_version="advanced-adapter-test-v1",
            profile_key="fixture-user",
            profile_version="profile:v1",
        )


class FakeAuth:
    def __init__(self, user: dict[str, object]) -> None:
        self.user = user

    def current_user(self, session_id):
        return self.user if session_id == "advanced-session" else None


class FakeMidtermProfiles:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[int, str | None]] = []

    def enhanced_features(self, *, user_id, developer_key):
        self.calls.append((user_id, developer_key))

        class Snapshot:
            def to_dict(self_inner):
                return dict(self.snapshot)

        return Snapshot()


class AdvancedAdapterApiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "advanced-adapter.sqlite3"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                0,
                main(["init-demo", "--database", str(self.database)]),
            )
        self.store = SQLiteCandidateStorage(self.database, MIGRATION)
        self.store.initialize()
        with self.store.connect() as connection:
            connection.execute("DELETE FROM task_skill_requirement")
            connection.execute(
                """
                UPDATE task_candidate
                SET candidate_availability = 'available',
                    task_types_json = '["testing"]',
                    labels_json = '[]'
                """
            )
            connection.execute(
                "UPDATE repository SET primary_language = 'Python'"
            )
        profile_storage = SQLiteProfileStorage(self.database, MIGRATION)
        profile_storage.initialize()
        identities = IdentityStore(profile_storage)
        self.user_id = identities.create_user(
            github_user_id=801,
            github_login="api-user",
        )
        identities.create_session(
            user_id=self.user_id,
            session_id="advanced-session",
            expires_at="2099-01-01T00:00:00Z",
        )
        self.profile_service = ProfileService(profile_storage)
        profile = json.loads(
            (ROOT / "fixtures" / "contracts" / "v0.5" / "profiles.json").read_text(
                encoding="utf-8"
            )
        )["profiles"][0]
        self.profile_service.save_manual_profile(profile, user_id=self.user_id)
        saved_profile = self.profile_service.profile_for_user(self.user_id)
        assert saved_profile is not None
        self.profile_key = str(saved_profile["profile_key"])
        questionnaire = json.loads(
            (
                ROOT / "config" / "midterm_profile" / "questionnaire_v0.1.json"
            ).read_text(encoding="utf-8")
        )
        feature_names = tuple(sorted(questionnaire["feature_names"]))
        observed_name = "current_interest.task.quality"
        observed_index = feature_names.index(observed_name)
        values = [0.0] * len(feature_names)
        observed = [False] * len(feature_names)
        values[observed_index] = 1.0
        observed[observed_index] = True
        enhanced = EnhancedProfileFeatures(
            profile_key=self.profile_key,
            profile_version="profile:v1",
            feature_schema_version="enhanced-profile-features-v0.1",
            as_of=datetime.now(timezone.utc).isoformat(),
            feature_names=feature_names,
            values=tuple(values),
            observed_mask=tuple(observed),
            locked_mask=tuple(False for _ in feature_names),
            current_interests=(observed_name,),
            evidence_refs=tuple(() for _ in feature_names),
            developer_key="api-user",
        )
        self.snapshot = enhanced.to_dict()
        self.midterm_profiles = FakeMidtermProfiles(self.snapshot)
        self.auth = FakeAuth(
            {
                "user_id": self.user_id,
                "github_login": "api-user",
                "display_name": "API User",
            }
        )

    def api(self, mode: str, scorer: FakeCoder) -> RecommendationApi:
        return RecommendationApi(
            self.store,
            auth_service=self.auth,
            profile_service=self.profile_service,
            recommendation_service=RecommendationService(
                self.store,
                model_scorer=scorer,
            ),
            midterm_profile_service=self.midterm_profiles,
            advanced_adapter_mode=mode,
        )

    def recommendations(self, mode: str):
        scorer = FakeCoder()
        api = self.api(mode, scorer)
        response = api.handle(
            "GET",
            "/api/v1/me/recommendations",
            query={"limit": ["10"]},
            cookies={SESSION_COOKIE_NAME: "advanced-session"},
        )
        return api, scorer, response

    def test_off_shadow_and_serve_have_distinct_ranking_semantics(self) -> None:
        off_api, off_scorer, off = self.recommendations("off")
        self.assertEqual(200, off.status, off.body)
        self.assertFalse(off.body["advanced_adapter"]["active"])
        self.assertEqual("off", off.body["advanced_adapter"]["mode"])
        self.assertNotEqual(self.snapshot, off_scorer.score_profiles[0])

        shadow_api, _, shadow = self.recommendations("shadow")
        self.assertEqual(200, shadow.status, shadow.body)
        self.assertFalse(shadow.body["advanced_adapter"]["active"])
        self.assertTrue(shadow.body["advanced_adapter"]["shadow_recorded"])
        self.assertEqual("population_mean", shadow.body["rankings"][0]["profile_mode"])
        run = shadow_api.handle(
            "GET",
            f"/api/v1/recommendation-runs/{shadow.body['run_id']}",
            cookies={SESSION_COOKIE_NAME: "advanced-session"},
        )
        self.assertEqual(200, run.status, run.body)
        self.assertEqual(
            "advanced-adapter-test-v1",
            run.body["advanced_adapter_shadow"]["profile_adapter_version"],
        )
        self.assertIn("raw_score_profile", run.body["advanced_adapter_shadow"]["paired"]["scores"][0])

        serve_api, serve_scorer, serve = self.recommendations("serve")
        self.assertEqual(200, serve.status, serve.body)
        self.assertTrue(serve.body["advanced_adapter"]["active"])
        self.assertTrue(serve.body["advanced_adapter"]["profile_applied"])
        self.assertEqual(self.snapshot, serve_scorer.score_profiles[0])
        self.assertEqual(
            "population_mean_profile",
            serve.body["rankings"][0]["profile_mode"],
        )
        run = serve_api.handle(
            "GET",
            f"/api/v1/recommendation-runs/{serve.body['run_id']}",
            cookies={SESSION_COOKIE_NAME: "advanced-session"},
        )
        self.assertEqual("serve", run.body["advanced_adapter"]["mode"])

    def test_shadow_pair_is_persisted_with_candidate_and_profile_versions(self) -> None:
        _, _, response = self.recommendations("shadow")
        self.assertEqual(200, response.status, response.body)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT *
                FROM recommendation_advanced_adapter_shadow
                WHERE run_id = ?
                """,
                (response.body["run_id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(self.user_id, int(row["user_id"]))
        self.assertEqual("population_mean", row["profile_mode"])
        self.assertEqual(response.body["candidate_hash"], row["candidate_snapshot_hash"])
        paired = json.loads(row["paired_json"])
        self.assertEqual("shadow", paired["advanced_adapter_mode"])
        self.assertTrue(paired["scores"])

    def test_shadow_and_serve_fail_closed_without_adapter_bundle(self) -> None:
        for mode in ("shadow", "serve"):
            with self.subTest(mode=mode):
                scorer = FakeCoder()
                scorer.profile_adapter = None
                scorer.profile_adapter_manifest = None
                response = self.api(mode, scorer).handle(
                    "GET",
                    "/api/v1/me/recommendations",
                    cookies={SESSION_COOKIE_NAME: "advanced-session"},
                )
                self.assertEqual(503, response.status)
                self.assertEqual("model_unavailable", response.body["error"]["code"])


if __name__ == "__main__":
    unittest.main()
