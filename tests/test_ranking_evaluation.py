from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from oss_mentor.candidate_rules import evaluate_candidate
from oss_mentor.cli import main
from oss_mentor.developer_profiles import DeveloperProfile
from oss_mentor.matching import (
    MATCH_VERSION_V1,
    MATCH_VERSION_V2,
    match_candidate,
    rank_for_profile,
)
from oss_mentor.ranking_evaluation import (
    build_ranking_evaluation_report,
    load_task_fit_annotations,
)
from oss_mentor.sqlite_store import SQLiteCandidateStore


def profile(**overrides):
    value = {
        "profile_key": "growth_demo",
        "service_track": "growth",
        "preferred_languages": ["Python"],
        "operating_systems": ["macos"],
        "preferred_task_types": ["bug_fix", "testing", "documentation"],
        "max_code_difficulty": 3,
        "max_setup_difficulty": 3,
        "desired_skill_stretch": 1,
        "skills": {"python": 1, "testing": 1, "documentation": 1},
    }
    value.update(overrides)
    return value


def task(**overrides):
    value = {
        "task_candidate_id": 1,
        "repository": "example/demo",
        "issue_number": 1,
        "title": "Fix parser bug",
        "html_url": "https://github.com/example/demo/issues/1",
        "newcomer_label_signal": 1,
        "estimated_code_difficulty": 1,
        "estimated_setup_difficulty": 1,
        "text_clarity_score": 80.0,
        "newcomer_score": 80.0,
        "growth_value_score": 60.0,
        "primary_language": "Python",
        "task_types": ["bug_fix"],
        "requirements": [
            {"skill_name": "Python", "minimum_level": 1, "importance": 1.0}
        ],
    }
    value.update(overrides)
    return value


class RankingEvaluationTests(unittest.TestCase):
    def _write_annotations(self, root: Path, text: str) -> Path:
        path = root / "annotations.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_annotation_csv_validation_accepts_double_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write_annotations(
                Path(temporary),
                (
                    "repository,issue_number,newcomer_fit,growth_fit,code_difficulty,"
                    "setup_difficulty,clarity,required_skills,critical_blocker,"
                    "annotation_reason,annotator\n"
                    "example/demo,1,3,2,1,1,3,\"Python, testing\",0,clear,a\n"
                    "example/demo,1,2,2,1,1,2,Python,false,also ok,b\n"
                ),
            )
            annotations = load_task_fit_annotations(path)
        self.assertEqual(2, len(annotations))
        self.assertEqual(("Python", "testing"), annotations[0].required_skills)

    def test_annotation_csv_rejects_bad_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = self._write_annotations(
                root,
                "repository,issue_number,newcomer_fit\nexample/demo,1,2\n",
            )
            with self.assertRaisesRegex(ValueError, "missing fields"):
                load_task_fit_annotations(missing)

            invalid = self._write_annotations(
                root,
                (
                    "repository,issue_number,newcomer_fit,growth_fit,code_difficulty,"
                    "setup_difficulty,clarity,required_skills,critical_blocker,"
                    "annotation_reason,annotator\n"
                    "example/demo,1,4,2,1,1,3,Python,0,clear,a\n"
                ),
            )
            with self.assertRaisesRegex(ValueError, "newcomer_fit"):
                load_task_fit_annotations(invalid)

            duplicate = self._write_annotations(
                root,
                (
                    "repository,issue_number,newcomer_fit,growth_fit,code_difficulty,"
                    "setup_difficulty,clarity,required_skills,critical_blocker,"
                    "annotation_reason,annotator\n"
                    "example/demo,1,3,2,1,1,3,Python,0,clear,a\n"
                    "example/demo,1,2,2,1,1,3,Python,0,copy,a\n"
                ),
            )
            with self.assertRaisesRegex(ValueError, "duplicate annotation"):
                load_task_fit_annotations(duplicate)

    def test_metrics_cover_precision_empty_mismatch_and_diversity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            annotations = load_task_fit_annotations(
                self._write_annotations(
                    Path(temporary),
                    (
                        "repository,issue_number,newcomer_fit,growth_fit,code_difficulty,"
                        "setup_difficulty,clarity,required_skills,critical_blocker,"
                        "annotation_reason,annotator\n"
                        "example/demo,1,3,3,1,1,3,Python,0,good,a\n"
                        "example/demo,2,0,0,3,3,0,Go,1,blocked,a\n"
                        "example/other,3,2,2,1,1,2,Python,0,ok,a\n"
                    ),
                )
            )
        candidates = [
            task(task_candidate_id=1, issue_number=1),
            task(
                task_candidate_id=2,
                issue_number=2,
                title="Needs Go",
                requirements=[
                    {"skill_name": "Python", "minimum_level": 1, "importance": 1.0},
                    {"skill_name": "Go", "minimum_level": 2, "importance": 1.0},
                ],
                growth_value_score=95,
            ),
            task(
                task_candidate_id=3,
                repository="example/other",
                issue_number=3,
                task_types=["documentation"],
                growth_value_score=50,
            ),
        ]
        report = build_ranking_evaluation_report(
            track="growth",
            profile=profile(skills={"python": 1, "testing": 1, "documentation": 1, "go": 1}),
            candidates=candidates,
            annotations=annotations,
            limit=10,
        )
        metrics = report["metrics_by_version"][MATCH_VERSION_V2]
        self.assertGreaterEqual(metrics["precision_at_5"], 0.5)
        self.assertGreaterEqual(metrics["repository_diversity"], 2)
        self.assertGreaterEqual(metrics["task_type_diversity"], 2)
        self.assertGreater(metrics["critical_skill_mismatch_rate"], 0)
        self.assertFalse(report["annotation_acceptance"]["passed"])
        self.assertFalse(
            report["annotation_acceptance"]["checks"][
                "minimum_double_annotated_task_count"
            ]
        )

        empty = build_ranking_evaluation_report(
            track="newcomer",
            profile=profile(
                service_track="newcomer",
                preferred_languages=["Rust"],
                skills={"rust": 1},
            ),
            candidates=candidates,
            annotations=annotations,
        )
        self.assertEqual(
            1.0,
            empty["metrics_by_version"][MATCH_VERSION_V2]["empty_recommendation_rate"],
        )

    def test_v1_default_is_preserved_and_v2_reports_version(self) -> None:
        candidate = task(
            requirements=[
                {"skill_name": "Python", "minimum_level": 2, "importance": 1.0}
            ],
            estimated_code_difficulty=2,
        )
        old = match_candidate(profile(skills={"python": 1}), candidate)
        new = match_candidate(
            profile(skills={"python": 1}),
            candidate,
            match_version=MATCH_VERSION_V2,
        )
        self.assertIsNotNone(old)
        self.assertEqual(MATCH_VERSION_V1, old.match_version)
        self.assertIsNone(new)

        ranked = rank_for_profile(
            profile(),
            [
                task(task_candidate_id=1, repository="same/repo", issue_number=1, growth_value_score=99),
                task(task_candidate_id=2, repository="same/repo", issue_number=2, growth_value_score=98),
                task(task_candidate_id=3, repository="other/repo", issue_number=3, growth_value_score=80),
            ],
            limit=2,
            match_version=MATCH_VERSION_V2,
        )
        self.assertEqual(["same/repo", "other/repo"], [item.repository for item in ranked])
        self.assertEqual(MATCH_VERSION_V2, ranked[0].match_version)


class RankingEvaluationCliTests(unittest.TestCase):
    def _make_store(self, database_path: Path) -> SQLiteCandidateStore:
        root = Path(__file__).resolve().parents[1]
        store = SQLiteCandidateStore(
            database_path,
            root / "db" / "sqlite" / "001_mvp.sql",
        )
        store.initialize()
        store.upsert_profile(
            DeveloperProfile(
                profile_key="growth_demo",
                display_name="Growth demo",
                service_track="growth",
                preferred_languages=("Python",),
                operating_systems=("macos",),
                preferred_task_types=("bug_fix",),
                max_code_difficulty=3,
                max_setup_difficulty=3,
                desired_skill_stretch=1,
                profile_source="demo",
                skills={"python": 1},
            )
        )
        record = {
            "issue_number": 1,
            "github_issue_id": 100,
            "html_url": "https://github.com/example/demo/issues/1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "author_association": "NONE",
            "title": "Fix parser bug",
            "body_text": "Steps to reproduce.",
            "labels": ["bug"],
            "state": "open",
            "assignment_state": "unassigned",
            "is_locked": False,
            "has_linked_open_pr": False,
            "comment_count": 1,
            "last_activity_at": "2026-01-02T00:00:00+00:00",
            "source_system": "github_rest",
            "source_fetched_at": "2026-01-03T00:00:00+00:00",
            "github_verified_at": "2026-01-03T00:00:00+00:00",
            "is_pull_request": False,
        }
        with store.connect() as connection:
            repository_id = store.upsert_repository(
                connection,
                full_name="example/demo",
                github_repository_id=1,
                html_url="https://github.com/example/demo",
                ecosystems_last_synced_at=None,
                ecosystem="pypi",
                primary_language="Python",
                github_verified_at="2026-01-03T00:00:00+00:00",
            )
            store.upsert_candidate(
                connection,
                repository_id=repository_id,
                record=record,
                eligibility=evaluate_candidate(record),
            )
            candidate_id = int(
                connection.execute(
                    "SELECT task_candidate_id FROM task_candidate"
                ).fetchone()[0]
            )
            connection.execute(
                """
                UPDATE task_candidate SET
                    task_types_json = '["bug_fix"]',
                    text_clarity_score = 80,
                    estimated_code_difficulty = 1,
                    estimated_setup_difficulty = 1,
                    estimated_project_context_difficulty = 1,
                    estimated_collaboration_difficulty = 0,
                    estimated_effort_bucket = 'half_day',
                    novice_fit_probability = 0.8,
                    newcomer_score = 80,
                    growth_value_score = 60,
                    feature_evidence_json = '{}',
                    feature_extracted_at = '2026-01-04T00:00:00+00:00',
                    task_feature_version = 'task-features-v0.1'
                """
            )
            connection.execute(
                """
                INSERT INTO task_skill_requirement (
                    task_candidate_id, skill_name, minimum_level, importance,
                    requirement_source, feature_version
                ) VALUES (?, 'Python', 1, 1.0, 'repository_primary_language', 'task-features-v0.1')
                """,
                (candidate_id,),
            )
        return store

    def test_evaluate_ranking_cli_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = root / "demo.sqlite3"
            self._make_store(database_path)
            annotations = root / "task_fit.csv"
            annotations.write_text(
                (
                    "repository,issue_number,newcomer_fit,growth_fit,code_difficulty,"
                    "setup_difficulty,clarity,required_skills,critical_blocker,"
                    "annotation_reason,annotator\n"
                    "example/demo,1,3,3,1,1,3,Python,0,good,a\n"
                ),
                encoding="utf-8",
            )
            json_path = root / "report.json"
            markdown_path = root / "report.md"
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(
                    [
                        "evaluate-ranking",
                        "--database",
                        str(database_path),
                        "--track",
                        "growth",
                        "--annotations",
                        str(annotations),
                        "--profile",
                        "growth_demo",
                        "--output",
                        str(json_path),
                        "--markdown-output",
                        str(markdown_path),
                    ]
                )
            self.assertEqual(0, result)
            summary = json.loads(output.getvalue())
            self.assertEqual("ranking_evaluation_generated", summary["event"])
            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual("ranking_evaluation_v0.2", report["schema_version"])
            self.assertFalse(report["annotation_acceptance"]["passed"])
            self.assertIn("推荐算法离线评估", markdown_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
