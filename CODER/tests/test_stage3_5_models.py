from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import torch

from coder_issue.baselines.candidates import CANDIDATE_SCHEMA
from coder_issue.evaluation.groups import cross_repository_users, project_module_coverage
from coder_issue.evaluation.multigranular import evaluate_issue_groups, evaluate_repository_groups
from coder_issue.models.coder import CoderConfig, CoderIssueModel
from coder_issue.models.gat import (
    StructuralGATConfig, StructuralGATEncoder, bidirectional_structural_edges,
)
from coder_issue.models.lightgcn import (
    LightGCN, LightGCNConfig, normalized_bipartite_adjacency,
)
from coder_issue.models.text_encoder import EmbeddingCache, IssueText, segment_issue_text
from coder_issue.training.config import load_neural_settings, validate_neural_inputs
from coder_issue.training.losses import BPRLoss, MultiGranularLoss
from coder_issue.training.monitoring import RunMonitor, format_duration
from coder_issue.training.negative_sampling import (
    CanonicalCandidateSampler, RepositoryCandidateSampler,
)
from coder_issue.training.trainer import (
    BatchProgress, TorchIssueRanker, seed_training, train_coder_epoch,
)


CODER_ROOT = Path(__file__).resolve().parents[1]


def _issue_graph() -> torch.Tensor:
    return normalized_bipartite_adjacency(
        torch.tensor([0, 0, 1]), torch.tensor([0, 1, 2]), num_users=2, num_items=4
    )


def _repo_graph() -> torch.Tensor:
    return normalized_bipartite_adjacency(
        torch.tensor([0, 1]), torch.tensor([0, 1]), num_users=2, num_items=2
    )


def _structural() -> StructuralGATEncoder:
    edges = bidirectional_structural_edges(
        torch.tensor([[0, 0], [1, 0], [2, 1], [3, 1]]),
        torch.tensor([[0, 0], [1, 1], [2, 0]]),
        num_repositories=2,
        num_issues=4,
        num_labels=2,
    )
    return StructuralGATEncoder(StructuralGATConfig(2, 4, 2, 4, 2, 2, 0.0), edges)


def _candidate_file(path: Path) -> None:
    when = datetime(2020, 1, 1, tzinfo=timezone.utc)
    rows = [
        {"query_id": 0, "developer_id": 0, "target_issue_id": 0, "repository_id": 0,
         "interaction_at": when, "is_cold_start": False, "candidate_issue_ids": [0, 2, 3]},
        {"query_id": 1, "developer_id": 1, "target_issue_id": 2, "repository_id": 1,
         "interaction_at": when, "is_cold_start": False, "candidate_issue_ids": [0, 1, 2]},
    ]
    pq.write_table(pa.Table.from_pylist(rows, schema=CANDIDATE_SCHEMA), path)


class FakeEncoder:
    model_name = "fake"
    revision = "abc123"
    dimension = 3

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, segments):
        self.calls += len(segments)
        return np.asarray(
            [[len(value), sum(map(ord, value)) % 17, 1] for value in segments], dtype=np.float32
        )


class StageThreeToFiveTests(unittest.TestCase):
    def setUp(self) -> None:
        torch.manual_seed(7)

    def test_lightgcn_propagates_averages_scores_and_roundtrips(self) -> None:
        model = LightGCN(LightGCNConfig(2, 4, 4, 2), _issue_graph())
        users, issues = model.propagated_embeddings()
        self.assertEqual((2, 4), tuple(users.shape))
        self.assertEqual((4, 4), tuple(issues.shape))
        scores = model.score_known_users(torch.tensor([0, 1]), torch.tensor([[0, 1], [2, 3]]))
        self.assertEqual((2, 2), tuple(scores.shape))
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            root = Path(directory)
            model.save_bundle(root)
            loaded = LightGCN.load_bundle(root)
            torch.testing.assert_close(scores, loaded.score_known_users(
                torch.tensor([0, 1]), torch.tensor([[0, 1], [2, 3]])
            ))
            loaded.export_embeddings(root / "vectors.npz")
            self.assertTrue((root / "vectors.npz").is_file())

    def test_losses_have_gradients_and_repository_weight(self) -> None:
        positive = torch.tensor([2.0, 1.0], requires_grad=True)
        unobserved = torch.tensor([0.0, 0.5], requires_grad=True)
        loss = BPRLoss(0.1)(positive, unobserved, positive, unobserved)
        loss.backward()
        self.assertIsNotNone(positive.grad)
        self.assertAlmostEqual(2.0, float(MultiGranularLoss(0.5)(torch.tensor(1.0), torch.tensor(2.0))))

    def test_canonical_and_repository_sampling_are_reproducible(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            path = Path(directory) / "candidates.parquet"
            _candidate_file(path)
            first = CanonicalCandidateSampler.from_parquet(path, seed=9)
            second = CanonicalCandidateSampler.from_parquet(path, seed=9)
            np.testing.assert_array_equal(first.sample(3).unobserved_ids, second.sample(3).unobserved_ids)
            sampled = first.sample(3)
            self.assertTrue(np.all(sampled.positive_ids != sampled.unobserved_ids))
            repositories = RepositoryCandidateSampler.from_issue_candidates(
                path, issue_to_repository=np.asarray([0, 0, 1, 1]), seed=9
            )
            repo_sample = repositories.sample(0)
            self.assertTrue(np.all(repo_sample.positive_ids != repo_sample.unobserved_ids))

    def test_segmentation_and_cache_reuse_changed_only(self) -> None:
        first = segment_issue_text(
            IssueText(0, "A title", "Paragraph one\n\n```py\nprint(1)\n```\n\nlast"), max_segments=3
        )
        second = segment_issue_text(IssueText(1, "Second", "Body"), max_segments=3)
        self.assertTrue(first.segments[0].startswith("[TITLE]"))
        self.assertTrue(any(value.startswith("[CODE]") for value in first.segments))
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            cache = EmbeddingCache(Path(directory))
            encoder = FakeEncoder()
            vectors, stats = cache.encode_or_reuse(
                [first, second], encoder, dataset_version="v1", mapping_sha256="hash", max_segments=3
            )
            self.assertEqual(2, stats["encoded"])
            _, repeated = cache.encode_or_reuse(
                [first, second], encoder, dataset_version="v1", mapping_sha256="hash", max_segments=3
            )
            self.assertEqual({"reused": 2, "encoded": 0}, repeated)
            self.assertEqual((2, 3), vectors.shape)
            np.testing.assert_array_equal(np.load(cache.row_mapping_path), np.asarray([0, 1]))

    def test_gat_and_full_coder_variant_roundtrip(self) -> None:
        structural = _structural()
        repositories, issues, labels = structural()
        self.assertEqual(((2, 4), (4, 4), (2, 4)), (
            tuple(repositories.shape), tuple(issues.shape), tuple(labels.shape)
        ))
        config = CoderConfig(
            2, 4, 2, embedding_dim=4, issue_layers=2, repository_layers=1,
            structure_layers=2, structure_heads=2, structure_dropout=0.0,
            semantic_dim=3, use_text=True, use_structure=True, use_repository=True,
        )
        model = CoderIssueModel(
            config,
            issue_adjacency=_issue_graph(),
            issue_to_repository=torch.tensor([0, 0, 1, 1]),
            repository_adjacency=_repo_graph(),
            semantic_embeddings=torch.arange(12, dtype=torch.float32).reshape(4, 3),
            structural_encoder=structural,
        )
        joint_result = train_coder_epoch(
            model,
            optimizer=torch.optim.Adam(model.parameters(), lr=0.001),
            criterion=BPRLoss(),
            developer_ids=np.asarray([0, 1]),
            issue_sampler=CanonicalCandidateSampler(
                np.asarray([0, 2]), [np.asarray([2]), np.asarray([0])], seed=5
            ),
            repository_sampler=RepositoryCandidateSampler(
                np.asarray([0, 1]), [np.asarray([1]), np.asarray([0])], seed=5
            ),
            repository_weight=0.2,
            epoch=0,
            batch_size=2,
            seed=5,
            device=torch.device("cpu"),
        )
        self.assertEqual(2, joint_result.repository_comparison_count)
        issue_scores = model.score_known_users(torch.tensor([0]), torch.tensor([0, 1, 2]))
        repo_scores = model.score_known_users_repositories(torch.tensor([0]), torch.tensor([0, 1]))
        self.assertEqual((1, 3), tuple(issue_scores.shape))
        self.assertEqual((1, 2), tuple(repo_scores.shape))
        metadata = {
            "model_version": "m", "dataset_version": "d", "graph_version": "g",
            "encoder_version": "e", "profile_mode": "known", "coverage_state": "fixed",
        }
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            root = Path(directory)
            model.save_bundle(root, metadata=metadata)
            loaded = CoderIssueModel.load_bundle(root)
            torch.testing.assert_close(
                issue_scores,
                loaded.score_known_users(torch.tensor([0]), torch.tensor([0, 1, 2])),
            )

    def test_text_and_structure_can_be_enabled_independently(self) -> None:
        variants = [
            (True, False, "coder_issue_text"),
            (False, True, "coder_issue_structure"),
            (True, True, "coder_issue_base"),
        ]
        for use_text, use_structure, expected in variants:
            config = CoderConfig(
                2, 4, 2, embedding_dim=4, issue_layers=1,
                structure_layers=2, structure_heads=2, structure_dropout=0.0,
                semantic_dim=3 if use_text else 0,
                use_text=use_text, use_structure=use_structure,
            )
            model = CoderIssueModel(
                config,
                issue_adjacency=_issue_graph(),
                issue_to_repository=torch.tensor([0, 0, 1, 1]),
                semantic_embeddings=torch.ones(4, 3) if use_text else None,
                structural_encoder=_structural() if use_structure else None,
            )
            users, issues = model.fused_embeddings()
            self.assertEqual(expected, config.variant)
            self.assertEqual(((2, 4), (4, 4)), (tuple(users.shape), tuple(issues.shape)))

    def test_issue_and_repository_metrics_are_separate_and_grouped(self) -> None:
        config = CoderConfig(
            2, 4, 2, embedding_dim=4, issue_layers=1, repository_layers=1,
            use_repository=True,
        )
        model = CoderIssueModel(
            config,
            issue_adjacency=_issue_graph(),
            issue_to_repository=torch.tensor([0, 0, 1, 1]),
            repository_adjacency=_repo_graph(),
        )
        known = np.asarray([True, True])
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            path = Path(directory) / "candidates.parquet"
            _candidate_file(path)
            issue_report = evaluate_issue_groups(
                TorchIssueRanker(model, known, device=torch.device("cpu")),
                path,
                cross_repository_users={0},
                cutoffs=(1, 2),
            )
            repository_report = evaluate_repository_groups(
                model,
                path,
                issue_to_repository=np.asarray([0, 0, 1, 1]),
                known_developers=known,
                cross_repository_users={0},
                cutoffs=(1, 2),
                device=torch.device("cpu"),
            )
            self.assertEqual(2, issue_report["all"]["query_count"])
            self.assertEqual(2, repository_report["all"]["query_count"])
            self.assertEqual(1, issue_report["cross_repository_user"]["query_count"])
            self.assertEqual(1, repository_report["cross_repository_user"]["query_count"])
            self.assertEqual(1, issue_report["cross_repository_user"]["developer_count"])

    def test_groups_and_real_frozen_input_dry_run(self) -> None:
        self.assertEqual({0}, cross_repository_users(
            np.asarray([0, 0, 1]), np.asarray([0, 1, 1])
        ))
        coverage = project_module_coverage(
            np.asarray([0, 2]), np.asarray([0, 1]), np.asarray([0, 1]), np.asarray([1, 1])
        )
        self.assertEqual(0.5, coverage["coverage"])
        self.assertEqual(0.5, coverage["developer_coverage"])
        settings = load_neural_settings(CODER_ROOT / "configs/coder_multigranular.toml")
        hashes = validate_neural_inputs(settings)
        self.assertEqual(15, len(hashes))
        self.assertEqual(15, len(validate_neural_inputs(settings, require_text=True)))
        self.assertTrue(all(len(option.revision) == 40 for option in settings.encoders))

    def test_one_epoch_updates_lightgcn_variant_reproducibly(self) -> None:
        sampler = CanonicalCandidateSampler(
            np.asarray([0, 2]),
            [np.asarray([2, 3]), np.asarray([0, 1])],
            seed=17,
        )

        def train_once(progress_callback=None):
            seed_training(23)
            model = CoderIssueModel(
                CoderConfig(2, 4, 2, embedding_dim=4, issue_layers=1),
                issue_adjacency=_issue_graph(),
                issue_to_repository=torch.tensor([0, 0, 1, 1]),
            )
            before = model.issue_graph.embedding.weight.detach().clone()
            optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
            result = train_coder_epoch(
                model,
                optimizer=optimizer,
                criterion=BPRLoss(),
                developer_ids=np.asarray([0, 1]),
                issue_sampler=sampler,
                repository_sampler=None,
                repository_weight=0.0,
                epoch=0,
                batch_size=2,
                seed=23,
                device=torch.device("cpu"),
                progress_callback=progress_callback,
            )
            scores = model.score_known_users(
                torch.tensor([0, 1]), torch.tensor([[0, 2], [1, 2]])
            ).detach().clone()
            return before, model.issue_graph.embedding.weight.detach().clone(), scores, result

        progress_events: list[BatchProgress] = []
        before, after, scores, result = train_once(progress_events.append)
        _, repeated, repeated_scores, repeated_result = train_once()
        self.assertFalse(torch.equal(before, after))
        torch.testing.assert_close(after, repeated)
        torch.testing.assert_close(scores, repeated_scores)
        self.assertAlmostEqual(result.issue_loss, repeated_result.issue_loss)
        self.assertEqual(2, result.issue_comparison_count)
        self.assertEqual(1, len(progress_events))
        self.assertEqual((1, 1, 2, 2), (
            progress_events[0].batch,
            progress_events[0].total_batches,
            progress_events[0].processed,
            progress_events[0].total,
        ))

    def test_run_monitor_persists_progress_and_clean_cancel_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            run_dir = Path(directory)
            monitor = RunMonitor(
                run_dir,
                run_id="observable-test",
                variant="lightgcn",
                interval_seconds=0.001,
            )
            try:
                monitor.update(
                    logging.getLogger(__name__),
                    "training",
                    "epoch 1/2",
                    epoch=1,
                    total_epochs=2,
                )
                state = json.loads((run_dir / "_RUNNING").read_text(encoding="utf-8"))
                self.assertEqual("training", state["stage"])
                self.assertEqual(1, state["epoch"])
                monitor.finish(
                    "cancelled", error_type="KeyboardInterrupt", error_message="Ctrl+C"
                )
                self.assertFalse((run_dir / "_RUNNING").exists())
                cancelled = json.loads(
                    (run_dir / "_CANCELLED").read_text(encoding="utf-8")
                )
                self.assertEqual("cancelled", cancelled["status"])
                self.assertTrue((run_dir / "training.log").is_file())
            finally:
                monitor.close()
        self.assertEqual("1h01m01s", format_duration(3661))


if __name__ == "__main__":
    unittest.main()
