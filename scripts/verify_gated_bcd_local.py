"""Run a local B/C/D contract acceptance with real frozen assets and mock boundaries."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from oss_mentor.contracts import DeveloperProfileV2
from oss_mentor.gated_scoring import CachedGatedScorer, context_key
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


class MockDStore:
    def __init__(self, tasks):
        self.tasks = tasks

    def matchable_candidates(self):
        return self.tasks

    def feedback_states(self, _context, _ids):
        return {}


def mock_b_profile(login: str, version: str = "mock-b-confirmed-v1"):
    observed = [name == "preference.language.python" for name in PROFILE_FEATURE_NAMES]
    return {
        "schema_version": "enhanced-profile-export-v0.1",
        "profile_state": "confirmed",
        "profile_key": "profile:mock-b",
        "profile_version": version,
        "developer_key": login.strip().casefold(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "feature_schema_version": PROFILE_FEATURE_SCHEMA_VERSION,
        "feature_names": list(PROFILE_FEATURE_NAMES),
        "values": [0.8 if value else 0.0 for value in observed],
        "observed_mask": observed,
        "locked_mask": [False] * len(observed),
        "current_interests": [],
        "evidence_refs": [["mock-b:python"] if value else [] for value in observed],
    }


def run(assets: Path, output: Path) -> dict[str, object]:
    assets = assets.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Output must not already exist")
    output.mkdir(parents=True)
    bundle = assets / "bundle"
    runtime_assets = assets / "assets"
    mapping = pq.read_table(
        runtime_assets / "runs/public-supplement-20261106-v2/mappings/repository_mapping.parquet",
        columns=["repository_key"],
    )
    repository = str(mapping["repository_key"][0].as_py()).casefold()
    now = datetime.now(timezone.utc).isoformat()
    tasks = [
        {
            "task_candidate_id": 1,
            "repository": repository,
            "issue_number": 1,
            "title": "Mock D production-shaped task",
            "body_text": "Add a deterministic Python test for the local acceptance path.",
            "html_url": f"https://github.com/{repository}/issues/1",
            "newcomer_label_signal": 1,
            "estimated_code_difficulty": 1,
            "estimated_setup_difficulty": 1,
            "newcomer_score": 80,
            "growth_value_score": 80,
            "text_clarity_score": 80,
            "primary_language": "Python",
            "task_types": ["testing"],
            "task_feature_version": "mock-d-v1",
            "feature_evidence": {"difficulty_assessment": {"information_quality": {"actionability": "actionable"}}},
            "requirements": [{"skill_name": "Python", "minimum_level": 1, "importance": 1.0}],
            "candidate_availability": "available",
            "github_verified_at": now,
            "warnings_json": "[]",
        }
    ]
    profile = DeveloperProfileV2(
        profile_key="mock-d-profile",
        display_name="Mock D User",
        service_track="newcomer",
        preferred_languages=("Python",),
        operating_systems=("linux",),
        preferred_task_types=("testing",),
        max_code_difficulty=1,
        max_setup_difficulty=1,
        desired_skill_stretch=0,
        skills={"python": 2},
    )
    login = "mock-user"
    enhanced = mock_b_profile(login)
    evidence = output / "evidence"
    evidence.mkdir()
    atomic_json(
        evidence / "evidence_manifest.json",
        {
            "login": login,
            "github_numeric_id": 4242,
            "as_of": now,
            "responses": [],
            "documents": [],
            "retained_X": [],
            "history_complete": False,
            "collection_status": "mock-d-local",
        },
    )
    config = {
        "bundle": str(bundle),
        "cache": str(output / "cache"),
        "requests": str(output / "requests"),
        "assets": str(runtime_assets),
        "auto_collect_public": False,
        "evidence_by_login": {login: str(evidence)},
    }
    scorer = CachedGatedScorer(
        bundle,
        Path(config["cache"]),
        request_dir=Path(config["requests"]),
        automatic_encoding=True,
    ).bind_enhanced_profile(enhanced)
    service = RecommendationService(
        MockDStore(tasks), model_scorer=scorer, allow_preparing_recommendations=True
    )
    initial = service.recommend_batch(profile=profile, developer_key=login)
    if initial.rankings[0]["scoring_engine"] != "profile_bootstrap_rules":
        raise AssertionError("Missing cache did not enter explicit bootstrap state")
    request_paths = list(Path(config["requests"]).glob("*.json"))
    if len(request_paths) != 1:
        raise AssertionError("Expected exactly one deduplicated worker request")
    status = process(request_paths[0], config)
    if status["state"] != "ready":
        raise RuntimeError(status)
    ready = service.recommend_batch(profile=profile, developer_key=login)
    if ready.rankings[0]["scoring_engine"] != "coder_gated":
        raise AssertionError("Prepared request did not switch to gated scoring")
    changed = scorer.bind_enhanced_profile(mock_b_profile(login, "mock-b-confirmed-v2"))
    changed_service = RecommendationService(
        MockDStore(tasks), model_scorer=changed, allow_preparing_recommendations=True
    )
    invalidated = changed_service.recommend_batch(profile=profile, developer_key=login)
    if invalidated.rankings[0]["scoring_engine"] != "profile_bootstrap_rules":
        raise AssertionError("Changed B profile_version did not invalidate the cache")
    result = {
        "status": "passed",
        "mock_b_schema": enhanced["schema_version"],
        "mock_b_profile_version": enhanced["profile_version"],
        "mock_d_authenticated_login": login,
        "mock_d_numeric_identity": 4242,
        "candidate_repository": repository,
        "initial_scoring_engine": initial.rankings[0]["scoring_engine"],
        "ready_scoring_engine": ready.rankings[0]["scoring_engine"],
        "changed_profile_scoring_engine": invalidated.rankings[0]["scoring_engine"],
        "model_version": ready.rankings[0]["model_version"],
        "context_key": context_key(profile, login, enhanced),
        "real_frozen_assets": True,
        "real_oauth_exercised": False,
        "real_github_inventory_used": False,
    }
    atomic_json(output / "verification.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.assets, args.output), ensure_ascii=False, indent=2))
