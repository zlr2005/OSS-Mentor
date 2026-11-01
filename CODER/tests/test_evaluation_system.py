from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from coder_issue.evaluation.comparison import (
    build_run_comparison,
    write_run_comparison,
)
from coder_issue.evaluation.multigranular import summarize_ranked_relevance
from coder_issue.training.config import expand_search_trials, load_neural_settings


CODER_ROOT = Path(__file__).resolve().parents[1]


class EvaluationSystemTests(unittest.TestCase):
    def test_multi_positive_metrics_and_candidate_boundaries(self) -> None:
        report = summarize_ranked_relevance(
            [[1, 3], [2], []],
            (1, 2, 5),
            developer_count=2,
            candidate_counts=[3, 2, 0],
        )
        self.assertEqual(3, report["query_count"])
        self.assertEqual(2, report["evaluable_query_count"])
        self.assertEqual(1, report["skipped_no_positive_query_count"])
        self.assertEqual(3, report["positive_count"])
        self.assertEqual(0, report["candidate_count_min"])
        self.assertEqual(3, report["candidate_count_max"])
        self.assertAlmostEqual(0.25, report["recall@1"])
        self.assertAlmostEqual(0.5, report["hit@1"])
        self.assertAlmostEqual(0.75, report["recall@2"])
        self.assertAlmostEqual(1.0, report["hit@5"])
        self.assertAlmostEqual(1.0, report["recall@5"])
        self.assertLessEqual(report["ndcg@5"], 1.0)

    def test_single_positive_protocol_keeps_recall_equal_to_hit_rate(self) -> None:
        report = summarize_ranked_relevance(
            [[1], [3]],
            (1, 10),
            developer_count=2,
            candidate_counts=[1, 3],
        )
        self.assertEqual(report["recall@1"], report["hit@1"])
        self.assertEqual(report["recall@10"], report["hit@10"])
        self.assertEqual(1.0, report["recall@10"])
        self.assertAlmostEqual(0.75, report["ndcg@10"])
        self.assertAlmostEqual((1.0 + 1.0 / 3.0) / 2.0, report["mrr@10"])

    def test_explicit_full_search_has_exactly_eight_unique_trials(self) -> None:
        settings = load_neural_settings(CODER_ROOT / "configs/coder_full_search8.toml")
        encoder_versions = tuple(
            f"{item.name}@{item.revision}" for item in settings.encoders
        )
        trials = expand_search_trials(
            settings,
            use_text=True,
            use_structure=True,
            use_repository=True,
            encoder_versions=encoder_versions,
        )
        self.assertEqual(8, len(trials))
        self.assertEqual(8, len({tuple(sorted(row.items())) for row in trials}))
        self.assertIn(
            {
                "embedding_dim": 32,
                "issue_layers": 4,
                "learning_rate": 0.001,
                "structure_layers": 2,
                "repository_loss_weight": 0.1,
                "encoder_version": encoder_versions[0],
            },
            trials,
        )

    def test_comparison_preserves_missing_values_and_protocol_boundaries(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            root = Path(directory)
            runs = []
            for index, graph_version in enumerate(("g1", "g1", "g2"), start=1):
                run = root / f"run-{index}"
                run.mkdir()
                (run / "_SUCCESS").write_text(
                    json.dumps({"status": "success"}), encoding="utf-8"
                )
                (run / "run_manifest.json").write_text(
                    json.dumps(
                        {
                            "run_id": run.name,
                            "variant": "lightgcn",
                            "dataset_version": "d1",
                            "graph_version": graph_version,
                            "input_sha256": {"candidate_test_cross_repo": "same"},
                        }
                    ),
                    encoding="utf-8",
                )
                (run / "metrics.json").write_text(
                    json.dumps(
                        {
                            "issue": {
                                "test": {
                                    "cross_repo": {
                                        "predictable": {
                                            "query_count": 2,
                                            "recall@10": 0.1 * index,
                                            "ndcg@10": 0.05 * index,
                                            "hit@10": 0.1 * index,
                                        }
                                    }
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                runs.append(run)
            incomplete = root / "full-incomplete"
            incomplete.mkdir()
            (incomplete / "_RUNNING").write_text(
                json.dumps({"run_id": incomplete.name, "variant": "coder_issue_repo"}),
                encoding="utf-8",
            )
            runs.append(incomplete)

            report = build_run_comparison(runs)
            self.assertTrue(report["rows"][0]["comparable_in_main_table"])
            self.assertTrue(report["rows"][1]["comparable_in_main_table"])
            self.assertFalse(report["rows"][2]["comparable_in_main_table"])
            self.assertIsNone(report["rows"][3]["ndcg@10"])
            self.assertEqual("run_status=incomplete", report["rows"][3]["missing_reason"])

            json_path, csv_path = root / "comparison.json", root / "comparison.csv"
            write_run_comparison(report, json_path=json_path, csv_path=csv_path)
            self.assertEqual(4, len(json.loads(json_path.read_text(encoding="utf-8"))["rows"]))
            self.assertIn("ndcg@10", csv_path.read_text(encoding="utf-8-sig"))
            with self.assertRaises(FileExistsError):
                write_run_comparison(report, json_path=json_path, csv_path=csv_path)


if __name__ == "__main__":
    unittest.main()
