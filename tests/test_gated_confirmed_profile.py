from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests")]

import test_gated_scoring as fixtures
from oss_mentor.gated_scoring import (
    CachedGatedScorer,
    FEATURE_SCHEMA,
    context_key,
    context_key_from_fingerprint,
    content_hash,
    enhanced_profile_fingerprint,
)
from oss_mentor.api import RecommendationApi
from oss_mentor.services.auth_service import SESSION_COOKIE_NAME
from oss_mentor.services.recommendation_service import RecommendationService
from run_gated_feature_worker import atomic_json, process


PROFILE_FEATURE_SCHEMA_VERSION = "enhanced-profile-features-v0.1"
PROFILE_FEATURE_NAMES = (
    "capability.change_scope", "capability.code_change", "capability.debugging",
    "capability.documentation", "capability.documentation_structure",
    "capability.documentation_validation", "capability.open_source_experience",
    "capability.reproduction", "capability.test_design", "capability.testing",
    "constraint.os.linux", "constraint.os.macos", "constraint.os.windows",
    "constraint.weekly_time", "current_interest.task.documentation",
    "current_interest.task.implementation", "current_interest.task.quality",
    "preference.language.go", "preference.language.java",
    "preference.language.javascript", "preference.language.python",
    "preference.language.rust", "preference.language.typescript",
)


def confirmed_profile(developer_key: str = "sample", version: str = "confirmed-v1"):
    observed = [name == "preference.language.python" for name in PROFILE_FEATURE_NAMES]
    values = [0.8 if value else 0.0 for value in observed]
    return {
        "schema_version": "enhanced-profile-export-v0.1",
        "profile_state": "confirmed",
        "profile_key": "profile:confirmed-test",
        "profile_version": version,
        "developer_key": developer_key,
        "as_of": "2026-11-08T00:00:00+08:00",
        "feature_schema_version": PROFILE_FEATURE_SCHEMA_VERSION,
        "feature_names": list(PROFILE_FEATURE_NAMES),
        "values": values,
        "observed_mask": observed,
        "locked_mask": [False] * len(observed),
        "current_interests": [],
        "evidence_refs": [["mock-b:python"] if value else [] for value in observed],
    }


class ConfirmedProfileGatedTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GatedScoringTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.enhanced = confirmed_profile()
        self.requests = self.root / "confirmed-requests"
        self.cache = self.root / "confirmed-cache"
        self.config = {
            "bundle": str(self.fixture.bundle),
            "cache": str(self.cache),
            "requests": str(self.requests),
            "assets": str(self.root),
            "evidence_by_login": {"sample": str(self.root)},
        }
        self.request = {
            "context_key": context_key(self.fixture.profile, "sample", self.enhanced),
            "profile": asdict(self.fixture.profile),
            "enhanced_profile": self.enhanced,
            "developer_key": "sample",
            "candidates": self.fixture.tasks,
            "model_version": self.fixture.model.manifest["model_version"],
            "backbone_sha256": self.fixture.model.manifest["backbone_sha256"],
            "feature_schema": FEATURE_SCHEMA,
        }
        self.path = self.requests / "confirmed.json"
        atomic_json(self.path, self.request)

    def encoder(self, request, *_):
        self.assertEqual(request["enhanced_profile"]["profile_version"], "confirmed-v1")
        with np.load(self.root / "prepared.npz", allow_pickle=False) as source:
            arrays = {key: source[key].copy() for key in source.files}
        arrays["content_hashes"] = np.asarray(
            [content_hash(task) for task in request["candidates"]]
        )
        now = datetime.now(timezone.utc)
        return arrays, {
            "evidence_manifest_sha256": "b" * 64,
            "as_of": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        }

    def test_worker_publishes_cache_bound_to_confirmed_profile(self):
        result = process(self.path, self.config, self.encoder)
        self.assertEqual(result["state"], "ready", result)
        scorer = CachedGatedScorer(self.fixture.bundle, self.cache).bind_enhanced_profile(
            self.enhanced
        )
        batch = scorer.score(
            profile=self.fixture.profile,
            developer_key="sample",
            candidates=self.fixture.tasks,
        )
        self.assertEqual(len(batch.scores), 2)
        pointer = json.loads(
            (self.cache / f"{self.request['context_key']}.current.json").read_text()
        )
        metadata = json.loads(
            (
                self.cache
                / "generations"
                / pointer["generation"]
                / f"{self.request['context_key']}.json"
            ).read_text()
        )
        self.assertEqual(
            metadata["enhanced_profile_sha256"],
            enhanced_profile_fingerprint(self.enhanced),
        )
        self.assertEqual(metadata["enhanced_profile_version"], "confirmed-v1")

    def test_authenticated_api_binds_confirmed_profile_to_gated_scorer(self):
        self.assertEqual(process(self.path, self.config, self.encoder)["state"], "ready")
        profile_payload = asdict(self.fixture.profile)
        auth = SimpleNamespace(
            current_user=lambda session_id: (
                {"user_id": 1, "github_login": "sample"}
                if session_id == "confirmed-session"
                else None
            )
        )
        profiles = SimpleNamespace(profile_for_user=lambda user_id: profile_payload)
        midterm = SimpleNamespace(
            enhanced_features=lambda **_kwargs: SimpleNamespace(
                to_dict=lambda: self.enhanced
            )
        )
        service = RecommendationService(
            fixtures.Store(self.fixture.profile, self.fixture.tasks),
            model_scorer=CachedGatedScorer(self.fixture.bundle, self.cache),
            auth_service=auth,
            profile_service=profiles,
        )
        api = RecommendationApi(
            fixtures.Store(self.fixture.profile, self.fixture.tasks),
            auth_service=auth,
            profile_service=profiles,
            recommendation_service=service,
            midterm_profile_service=midterm,
        )
        response = api.handle(
            "GET",
            "/api/v1/me/recommendations",
            cookies={SESSION_COOKIE_NAME: "confirmed-session"},
        )
        self.assertEqual(200, response.status, response.body)
        self.assertEqual("coder_gated", response.body["rankings"][0]["scoring_engine"])
        self.assertEqual(
            self.fixture.model.manifest["model_version"],
            response.body["rankings"][0]["model_version"],
        )

    def test_profile_version_and_identity_change_require_new_context(self):
        self.assertEqual(process(self.path, self.config, self.encoder)["state"], "ready")
        changed = confirmed_profile(version="confirmed-v2")
        scorer = CachedGatedScorer(self.fixture.bundle, self.cache).bind_enhanced_profile(changed)
        with self.assertRaises(RuntimeError):
            scorer.score(
                profile=self.fixture.profile,
                developer_key="sample",
                candidates=self.fixture.tasks,
            )
        wrong_identity = confirmed_profile(developer_key="other")
        wrong_request = dict(self.request, enhanced_profile=wrong_identity)
        wrong_request["context_key"] = context_key(
            self.fixture.profile, "sample", wrong_identity
        )
        atomic_json(self.path, wrong_request)

        def reject_identity(request, *_):
            if request["enhanced_profile"]["developer_key"] != request["developer_key"]:
                raise ValueError("Confirmed profile identity does not match authenticated user")
            return self.encoder(request)

        failed = process(self.path, self.config, reject_identity)
        self.assertEqual(failed["state"], "failed")

    def test_authenticated_login_change_invalidates_same_profile_version(self):
        self.assertEqual(process(self.path, self.config, self.encoder)["state"], "ready")
        renamed = confirmed_profile(developer_key="renamed-login", version="confirmed-v1")
        renamed_key = context_key(self.fixture.profile, "renamed-login", renamed)
        self.assertNotEqual(self.request["context_key"], renamed_key)
        scorer = CachedGatedScorer(self.fixture.bundle, self.cache).bind_enhanced_profile(
            renamed
        )
        with self.assertRaises(RuntimeError):
            scorer.score(
                profile=self.fixture.profile,
                developer_key="renamed-login",
                candidates=self.fixture.tasks,
            )

    def test_preparation_request_contains_exact_b_snapshot(self):
        scorer = CachedGatedScorer(
            self.fixture.bundle,
            self.root / "empty-cache",
            request_dir=self.requests,
        ).bind_enhanced_profile(self.enhanced)
        with self.assertRaises(RuntimeError):
            scorer.score(
                profile=self.fixture.profile,
                developer_key="sample",
                candidates=self.fixture.tasks,
            )
        requests = [path for path in self.requests.glob("*.json") if path != self.path]
        self.assertEqual(len(requests), 1)
        payload = json.loads(requests[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["enhanced_profile"], self.enhanced)
        self.assertEqual(payload["context_key"], self.request["context_key"])

    def test_external_profile_fingerprint_must_be_sha256(self):
        with self.assertRaises(ValueError):
            context_key_from_fingerprint(self.fixture.profile, "sample", "not-a-digest")


if __name__ == "__main__":
    unittest.main()
