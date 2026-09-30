from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from oss_mentor.contracts import (
    REASON_CODE_NEGATIVE_FEEDBACK,
    DeveloperProfileV2,
    RecommendationItemV3,
)
from oss_mentor.services.auth_service import AuthService, AuthSettings
from oss_mentor.services.profile_service import ProfileService
from oss_mentor.services.recommendation_service import (
    AuthenticationRequired,
    RecommendationService,
)
from oss_mentor.storage.identity import IdentityStore
from oss_mentor.storage.profiles import SQLiteProfileStorage
from oss_mentor.storage.recommendations import SQLiteRecommendationStorage

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "sqlite" / "001_mvp.sql"


def candidate(candidate_id: int, score: float, *, repository: str = "example/demo"):
    return {
        "task_candidate_id": candidate_id,
        "repository": repository,
        "issue_number": candidate_id,
        "title": f"Task {candidate_id}",
        "html_url": f"https://github.com/{repository}/issues/{candidate_id}",
        "newcomer_label_signal": 1,
        "estimated_code_difficulty": 1,
        "estimated_setup_difficulty": 1,
        "newcomer_score": score,
        "growth_value_score": score,
        "text_clarity_score": score,
        "primary_language": "Python",
        "task_types": ["testing"],
        "requirements": [
            {"skill_name": "Python", "minimum_level": 1, "importance": 1.0}
        ],
        "candidate_availability": "available",
        "github_verified_at": "2026-09-29T00:00:00Z",
        "warnings_json": "[]",
    }


class CandidateStoreFixture:
    def __init__(self, tasks, feedback=None):
        self.tasks = tasks
        self.feedback = feedback or {}
        self.requested_contexts = []

    def matchable_candidates(self):
        return self.tasks

    def feedback_states(self, context, task_candidate_ids):
        self.requested_contexts.append((context, tuple(task_candidate_ids)))
        return {
            candidate_id: self.feedback[candidate_id]
            for candidate_id in task_candidate_ids if candidate_id in self.feedback
        }


def profile(**overrides):
    values = {
        "profile_key": "profile-1",
        "display_name": "Developer",
        "service_track": "newcomer",
        "preferred_languages": ("Python",),
        "operating_systems": ("linux",),
        "preferred_task_types": ("testing",),
        "max_code_difficulty": 1,
        "max_setup_difficulty": 1,
        "desired_skill_stretch": 0,
        "skills": {"Python": 2},
    }
    values.update(overrides)
    return DeveloperProfileV2(**values)


class RecommendationContractTests(unittest.TestCase):
    def test_service_accepts_profile_contract_and_maps_match_result(self):
        store = CandidateStoreFixture([candidate(1, 80)])
        item = RecommendationService(store).recommend(profile=profile()).items[0]

        self.assertIsInstance(item, RecommendationItemV3)
        self.assertEqual("python", next(iter(profile().skills)))
        self.assertGreaterEqual(item.score, 0.0)
        self.assertLessEqual(item.score, 1.0)
        self.assertEqual("example/demo", item.repository_full_name)
        self.assertEqual(("Python",), item.matched_skills)
        self.assertTrue(item.reasons)
        self.assertTrue(all(reason.code and reason.evidence for reason in item.reasons))
        self.assertEqual("2026-09-29T00:00:00Z", item.verified_at)

    def test_rejects_legacy_dictionary_profile(self):
        with self.assertRaises(TypeError):
            RecommendationService(CandidateStoreFixture([])).recommend(profile={})

    def test_detail_and_exclusion_use_the_same_contract(self):
        service = RecommendationService(CandidateStoreFixture([
            candidate(1, 80), candidate(2, 70)
        ]))
        detail = service.recommendation_detail(profile=profile(), task_candidate_id=1)
        items = service.recommend(
            profile=profile(), excluded_candidate_ids=(1,)
        ).items
        self.assertEqual(1, detail.task_candidate_id)
        self.assertEqual([2], [item.task_candidate_id for item in items])

    def test_current_context_negative_feedback_is_applied_before_ranking(self):
        store = CandidateStoreFixture(
            [candidate(1, 100), candidate(2, 70)],
            feedback={1: "not_suitable"},
        )
        items = RecommendationService(store).recommend(
            profile=profile(), feedback_context="user:7:profile:profile-1"
        ).items

        self.assertEqual([2, 1], [item.task_candidate_id for item in items])
        penalized = items[1]
        self.assertEqual("not_suitable", penalized.feedback_state)
        self.assertIn(REASON_CODE_NEGATIVE_FEEDBACK, [reason.code for reason in penalized.reasons])
        penalty = next(reason for reason in penalized.reasons
                       if reason.code == REASON_CODE_NEGATIVE_FEEDBACK)
        self.assertEqual(-0.25, penalty.score_delta)

        batch = RecommendationService(store).recommend_batch(
            profile=profile(), feedback_context="user:7:profile:profile-1"
        )
        penalized_ranking = next(
            row for row in batch.rankings if row["task_candidate_id"] == 1
        )
        self.assertEqual(25.0, round(
            penalized_ranking["raw_score"] - penalized_ranking["final_score"], 2
        ))

    def test_diversity_tracks_raw_and_final_positions(self):
        tasks = [
            {**candidate(1, 100, repository="same/repo"), "task_types": ["testing"]},
            {**candidate(2, 99, repository="same/repo"), "task_types": ["testing"]},
            {**candidate(3, 98, repository="same/repo"), "task_types": ["testing"]},
            {**candidate(4, 97, repository="same/repo"), "task_types": ["testing"]},
            {**candidate(5, 60, repository="docs/repo"), "task_types": ["documentation"]},
        ]
        batch = RecommendationService(CandidateStoreFixture(tasks)).recommend_batch(
            profile=profile(preferred_task_types=("testing", "documentation")), limit=4
        )
        repositories = [item.repository_full_name for item in batch.items]
        self.assertLessEqual(repositories.count("same/repo"), 3)
        self.assertIn(5, [item.task_candidate_id for item in batch.items])
        self.assertTrue(any(row["diversity_reranked"] for row in batch.rankings))

    def test_snapshot_persists_hashes_ranks_reasons_and_warnings(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = SQLiteRecommendationStorage(Path(temporary) / "snapshot.sqlite3", MIGRATIONS)
            service = RecommendationService(
                CandidateStoreFixture([candidate(1, 80)]),
                recommendation_store=storage,
            )
            batch = service.recommend_batch(profile=profile(), feedback_context="fixture")
            saved = storage.find_recommendation_batch(batch.run_id)

        self.assertEqual(batch.profile_hash, saved["profile_snapshot_hash"])
        self.assertEqual(batch.candidate_hash, saved["candidate_snapshot_hash"])
        self.assertEqual(1, saved["items"][0]["final_rank"])
        self.assertTrue(saved["items"][0]["reasons"])
        self.assertNotIn("skills", saved)


class SessionProfileConsumptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.storage = SQLiteProfileStorage(Path(temporary.name) / "session.sqlite3", MIGRATIONS)
        self.storage.initialize()
        self.identities = IdentityStore(self.storage)
        self.user_id = self.identities.create_user(
            github_user_id=501, github_login="session-owner"
        )
        self.identities.create_session(
            user_id=self.user_id,
            session_id="real-session",
            expires_at="2099-01-01T00:00:00Z",
        )
        self.auth = AuthService(self.identities, AuthSettings(session_secret="test-secret"))
        self.profile_service = ProfileService(self.storage)
        saved = json.loads(
            (ROOT / "fixtures" / "contracts" / "v0.5" / "profiles.json").read_text()
        )["profiles"][0]
        saved["skills"] = {"Python": 2, "TESTING": 1}
        self.profile_service.save_manual_profile(saved, user_id=self.user_id)

    def test_real_session_consumes_its_current_owned_profile(self):
        store = CandidateStoreFixture([candidate(1, 80)])
        service = RecommendationService(
            store, profile_service=self.profile_service, auth_service=self.auth
        )
        result = service.recommend_for_session(session_id="real-session")

        self.assertEqual(self.user_id, self.auth.current_user("real-session")["user_id"])
        self.assertEqual({"python": 2, "testing": 1}, result.profile.skills)
        self.assertEqual(
            f"user:{self.user_id}:profile:{result.profile.profile_key}",
            result.feedback_context,
        )
        self.assertEqual(result.feedback_context, store.requested_contexts[0][0])
        self.assertEqual(1, result.items[0].task_candidate_id)

    def test_invalid_session_cannot_select_a_profile(self):
        service = RecommendationService(
            CandidateStoreFixture([]),
            profile_service=self.profile_service,
            auth_service=self.auth,
        )
        with self.assertRaises(AuthenticationRequired):
            service.recommend_for_session(session_id="missing-session")


if __name__ == "__main__":
    unittest.main()
