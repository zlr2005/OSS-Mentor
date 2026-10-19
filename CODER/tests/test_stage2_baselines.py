from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np

from coder_issue.baselines.candidates import iter_candidate_batches, materialize_candidates
from coder_issue.baselines.config import load_baseline_settings, validate_frozen_inputs
from coder_issue.baselines.dataset import Interactions, IssueCatalog, training_history
from coder_issue.baselines.metrics import evaluate
from coder_issue.baselines.models import BPRModel, PopularityModel, TextSimilarityModel


CODER_ROOT = Path(__file__).resolve().parents[1]


def _catalog() -> IssueCatalog:
    return IssueCatalog(
        issue_ids=np.arange(5, dtype=np.int64),
        repository_ids=np.asarray([0, 0, 1, 1, 0]),
        created_times=np.asarray(
            ["2019-01-01", "2019-01-02", "2019-01-01", "2019-02-01", "2019-03-01"],
            dtype="datetime64[us]",
        ),
        resolved_times=np.asarray(
            ["2019-02-01", "2019-03-01", "2019-04-01", "2019-04-02", "2019-05-01"],
            dtype="datetime64[us]",
        ),
        texts=["python bug", "python docs", "java bug", "java docs", "python feature"],
    )


def _interactions(developers, issues, repos, times) -> Interactions:
    return Interactions(
        np.asarray(developers, dtype=np.int64),
        np.asarray(issues, dtype=np.int64),
        np.asarray(repos, dtype=np.int64),
        np.asarray(times, dtype="datetime64[us]"),
    )


class StageTwoTests(unittest.TestCase):
    def test_frozen_real_inputs_match_configured_hashes(self) -> None:
        settings = load_baseline_settings(CODER_ROOT / "configs/baselines.toml")
        hashes = validate_frozen_inputs(settings)
        self.assertEqual("gfibot-m0-v1.1", settings.dataset_version)
        self.assertEqual(10, len(hashes))

    def test_candidate_file_is_shared_deterministic_and_contains_positive(self) -> None:
        train = _interactions([0], [0], [0], ["2019-02-01"])
        queries = _interactions([0, 1], [1, 2], [0, 1], ["2019-03-01", "2019-04-01"])
        solved, known = training_history(train)
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            first = Path(directory) / "first.parquet"
            second = Path(directory) / "second.parquet"
            kwargs = dict(
                catalog=_catalog(), queries=queries, training_solved=solved,
                known_developers=known, split="valid", mode="cross_repo",
                seed=7, max_candidates=3,
            )
            materialize_candidates(**kwargs, output_path=first)
            materialize_candidates(**kwargs, output_path=second)
            rows1 = list(iter_candidate_batches(first))[0]
            rows2 = list(iter_candidate_batches(second))[0]
            self.assertEqual(rows1, rows2)
            for target, candidates in zip(rows1["target_issue_id"], rows1["candidate_issue_ids"], strict=True):
                self.assertIn(target, candidates)
                self.assertNotIn(0, candidates)
            self.assertEqual([False, True], rows1["is_cold_start"])

    def test_models_save_reload_and_score(self) -> None:
        catalog = _catalog()
        train = _interactions([0, 0, 1], [0, 1, 2], [0, 0, 1], ["2019-02-01"] * 3)
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            root = Path(directory)
            popularity = PopularityModel.fit(train, catalog, 2)
            popularity.save(root / "pop")
            np.testing.assert_allclose(
                popularity.score(0, np.asarray([1, 2])),
                PopularityModel.load(root / "pop").score(0, np.asarray([1, 2])),
            )
            text = TextSimilarityModel.fit(
                train, catalog, developer_count=3, max_features=20, min_df=1, ngram_max=1
            )
            text.save(root / "text")
            np.testing.assert_allclose(
                text.score(0, np.asarray([1, 2])),
                TextSimilarityModel.load(root / "text").score(0, np.asarray([1, 2])),
            )
            negatives = np.asarray([[1, 2], [2, 3], [3, 4]], dtype=np.int64)
            bpr = BPRModel.fit(
                train, negatives, developer_count=3, issue_count=5, dimensions=4,
                learning_rate=0.01, regularization=0.001, epochs=2, batch_size=2, seed=11,
            )
            bpr.save(root / "bpr")
            np.testing.assert_allclose(
                bpr.score(0, np.asarray([1, 2])),
                BPRModel.load(root / "bpr").score(0, np.asarray([1, 2])),
            )
            self.assertFalse(bpr.is_predictable(2))

    def test_metric_tie_break_is_issue_id_and_groups_cold_start(self) -> None:
        class ConstantModel:
            name = "constant"

            def score(self, developer_id, candidates):
                return np.zeros(len(candidates))

            def is_predictable(self, developer_id):
                return developer_id == 0

        train = _interactions([0], [0], [0], ["2019-02-01"])
        queries = _interactions([0, 1], [1, 2], [0, 1], ["2019-03-01", "2019-04-01"])
        solved, known = training_history(train)
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            path = Path(directory) / "candidates.parquet"
            materialize_candidates(
                catalog=_catalog(), queries=queries, training_solved=solved,
                known_developers=known, split="valid", mode="cross_repo", seed=7,
                max_candidates=3, output_path=path,
            )
            report = evaluate(ConstantModel(), path, (1, 2))
            self.assertEqual(2, report["all"]["query_count"])
            self.assertEqual(1, report["cold_start"]["query_count"])
            self.assertEqual(1, report["predictable"]["query_count"])


if __name__ == "__main__":
    unittest.main()
