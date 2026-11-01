from __future__ import annotations

import hashlib
from pathlib import Path
import json
import random
import tempfile
import unittest
from unittest import mock

import numpy as np
import torch

from coder_issue.models.coder import CoderConfig, CoderIssueModel
from coder_issue.models.gat import StructuralGATConfig, StructuralGATEncoder, bidirectional_structural_edges
from coder_issue.models.lightgcn import normalized_bipartite_adjacency
from coder_issue.models.text_encoder import (
    EmbeddingCache,
    IssueText,
    _text_hash,
    segment_issue_text,
)
from coder_issue.training.checkpoint import CheckpointManager
from coder_issue.training.losses import BPRLoss
from coder_issue.training.negative_sampling import CanonicalCandidateSampler, RepositoryCandidateSampler
from coder_issue.training.monitoring import RunMonitor, process_identity
from coder_issue.training.pipeline import (
    _finish_probe_run,
    _prepare_run_directories,
    _resume_start_trial,
)
from coder_issue.training.trainer import BatchResumeState, seed_training, train_coder_epoch


CODER_ROOT = Path(__file__).resolve().parents[1]


def full_model(dropout: float = 0.0) -> CoderIssueModel:
    issue_graph = normalized_bipartite_adjacency(
        torch.tensor([0, 0, 1, 1]), torch.tensor([0, 1, 2, 3]),
        num_users=2, num_items=4,
    )
    repo_graph = normalized_bipartite_adjacency(
        torch.tensor([0, 1]), torch.tensor([0, 1]), num_users=2, num_items=2,
    )
    edges = bidirectional_structural_edges(
        torch.tensor([[0, 0], [1, 0], [2, 1], [3, 1]]),
        torch.tensor([[0, 0], [1, 1], [2, 0], [3, 1]]),
        num_repositories=2, num_issues=4, num_labels=2,
    )
    structural = StructuralGATEncoder(StructuralGATConfig(2, 4, 2, 4, 2, 2, dropout), edges)
    return CoderIssueModel(
        CoderConfig(
            2, 4, 2, embedding_dim=4, issue_layers=2, repository_layers=1,
            structure_layers=2, structure_heads=2, structure_dropout=dropout,
            semantic_dim=3, use_text=True, use_structure=True, use_repository=True,
        ),
        issue_adjacency=issue_graph,
        issue_to_repository=torch.tensor([0, 0, 1, 1]),
        repository_adjacency=repo_graph,
        semantic_embeddings=torch.arange(12, dtype=torch.float32).reshape(4, 3),
        structural_encoder=structural,
    )


def samplers():
    issue = CanonicalCandidateSampler(
        np.asarray([0, 1, 2, 3]),
        [np.asarray([2]), np.asarray([3]), np.asarray([0]), np.asarray([1])], seed=19,
    )
    repository = RepositoryCandidateSampler(
        np.asarray([0, 0, 1, 1]),
        [np.asarray([1]), np.asarray([1]), np.asarray([0]), np.asarray([0])], seed=19,
    )
    return issue, repository


class InterruptingEncoder:
    model_name = "fake"
    revision = "fixed"
    dimension = 3

    def __init__(self, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def encode(self, segments):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise KeyboardInterrupt
        return np.asarray([[len(x), sum(map(ord, x)) % 13, 1] for x in segments], dtype=np.float32)


class ResumableTrainingTests(unittest.TestCase):
    def test_probe_completion_writes_result_and_success_terminal(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            run_dir = Path(directory)
            monitor = RunMonitor(
                run_dir,
                run_id="probe-test",
                variant="coder_issue_repo",
                interval_seconds=0.01,
            )
            try:
                _finish_probe_run(
                    monitor,
                    run_dir,
                    run_id="probe-test",
                    variant="coder_issue_repo",
                    trial=1,
                    parameters={"embedding_dim": 32},
                    issue_loss=0.6,
                    repository_loss=0.7,
                    validation_score=0.1,
                    best_epoch=1,
                    best_checkpoint="checkpoints/trial-0001.best.pt",
                )
            finally:
                monitor.close()

            result = json.loads(
                (run_dir / "probe_result.json").read_text(encoding="utf-8")
            )
            terminal = json.loads((run_dir / "_SUCCESS").read_text(encoding="utf-8"))
            self.assertTrue(result["formal_final_evaluation_skipped"])
            self.assertEqual(0.1, result["validation_score"])
            self.assertEqual("success", terminal["status"])
            self.assertFalse((run_dir / "_RUNNING").exists())

    def test_run_directory_resume_guards_and_archives_stale_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            root = Path(directory)
            run_dir, model_dir = root / "run", root / "model"
            _prepare_run_directories(run_dir, model_dir, resume=False)
            with self.assertRaises(FileExistsError):
                _prepare_run_directories(run_dir, model_dir, resume=False)

            (run_dir / "_SUCCESS").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "_SUCCESS"):
                _prepare_run_directories(run_dir, model_dir, resume=True)
            (run_dir / "_SUCCESS").unlink()

            (run_dir / "_RUNNING").write_text(
                json.dumps(process_identity()), encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "active"):
                _prepare_run_directories(run_dir, model_dir, resume=True)

            stale = process_identity()
            stale["process_create_time"] = float(stale["process_create_time"]) - 100
            (run_dir / "_RUNNING").write_text(json.dumps(stale), encoding="utf-8")
            (run_dir / "_CANCELLED").write_text('{"status":"cancelled"}', encoding="utf-8")
            _prepare_run_directories(run_dir, model_dir, resume=True)
            self.assertFalse((run_dir / "_RUNNING").exists())
            self.assertFalse((run_dir / "_CANCELLED").exists())
            self.assertEqual(1, len(list(run_dir.glob("_RUNNING.stale.*.json"))))
            self.assertEqual(1, len(list(run_dir.glob("_CANCELLED.history.*.json"))))
            self.assertEqual(2, _resume_start_trial({"phase": "between_trials", "next_trial": 2}))
            self.assertEqual(3, _resume_start_trial({"phase": "training", "trial": 3}))

    def test_checkpoint_restores_model_adam_and_rng_and_falls_back(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            seed_training(7)
            model = full_model()
            optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
            loss = sum(parameter.square().sum() for parameter in model.parameters())
            loss.backward(); optimizer.step()
            manager = CheckpointManager(Path(directory), {"run": "same"})
            manager.save(model, optimizer, progress={"epoch": 1}, history={"trials": []})
            expected_python = random.random()
            expected_numpy = float(np.random.random())
            expected_torch = torch.rand(3)
            expected_parameters = {name: value.detach().clone() for name, value in model.named_parameters()}
            manager.save(model, optimizer, progress={"epoch": 2}, history={"trials": []})
            manager.latest_path.write_bytes(b"truncated")
            with torch.no_grad():
                for parameter in model.parameters():
                    parameter.add_(100)
            payload, path = manager.load(model, optimizer, map_location="cpu")
            self.assertEqual(manager.previous_path, path)
            self.assertEqual(1, payload["progress"]["epoch"])
            for name, parameter in model.named_parameters():
                torch.testing.assert_close(expected_parameters[name], parameter)
            self.assertEqual(expected_python, random.random())
            self.assertEqual(expected_numpy, float(np.random.random()))
            torch.testing.assert_close(expected_torch, torch.rand(3))
            incompatible = CheckpointManager(Path(directory), {"run": "changed"})
            with self.assertRaisesRegex(RuntimeError, "identity"):
                incompatible.load(model, optimizer, map_location="cpu")

    def test_batch_resume_matches_uninterrupted_training(self) -> None:
        def run_model(interrupt: bool, root: Path | None = None):
            seed_training(41)
            model = full_model(dropout=0.2)
            optimizer = torch.optim.Adam(model.parameters(), lr=0.005)
            issue, repository = samplers()
            saved_state = None
            manager = CheckpointManager(root, {"experiment": "deterministic"}) if root else None

            def checkpoint(progress, state):
                nonlocal saved_state
                if interrupt and progress.batch == 1:
                    manager.save(model, optimizer, progress=state.__dict__, history={})
                    raise KeyboardInterrupt

            try:
                result = train_coder_epoch(
                    model, optimizer=optimizer, criterion=BPRLoss(),
                    developer_ids=np.asarray([0, 0, 1, 1]), issue_sampler=issue,
                    repository_sampler=repository, repository_weight=0.2, epoch=0,
                    batch_size=2, seed=41, device=torch.device("cpu"),
                    checkpoint_callback=checkpoint,
                )
            except KeyboardInterrupt:
                resumed = full_model(dropout=0.2)
                resumed_optimizer = torch.optim.Adam(resumed.parameters(), lr=0.005)
                payload, _ = manager.load(resumed, resumed_optimizer, map_location="cpu")
                saved_state = BatchResumeState(**payload["progress"])
                result = train_coder_epoch(
                    resumed, optimizer=resumed_optimizer, criterion=BPRLoss(),
                    developer_ids=np.asarray([0, 0, 1, 1]), issue_sampler=issue,
                    repository_sampler=repository, repository_weight=0.2, epoch=0,
                    batch_size=2, seed=41, device=torch.device("cpu"), resume_state=saved_state,
                )
                model = resumed
            return model, result

        continuous, continuous_result = run_model(False)
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            resumed, resumed_result = run_model(True, Path(directory))
        for first, second in zip(continuous.parameters(), resumed.parameters(), strict=True):
            torch.testing.assert_close(first, second, rtol=0, atol=0)
        self.assertEqual(continuous_result, resumed_result)

    def test_all_trainable_full_model_branches_receive_gradients(self) -> None:
        seed_training(3)
        model = full_model()
        issue, repository = samplers()
        train_coder_epoch(
            model, optimizer=torch.optim.Adam(model.parameters(), lr=0.001), criterion=BPRLoss(),
            developer_ids=np.asarray([0, 0, 1, 1]), issue_sampler=issue,
            repository_sampler=repository, repository_weight=0.5, epoch=0,
            batch_size=4, seed=3, device=torch.device("cpu"),
        )
        groups = {
            "issue_lightgcn": model.issue_graph.embedding.weight,
            "repository_lightgcn": model.repository_graph.embedding.weight,
            "gat": model.structural_encoder.node_embeddings.weight,
            "user_mlp": model.user_fusion[0].weight,
            "issue_mlp": model.issue_fusion[0].weight,
        }
        for name, parameter in groups.items():
            self.assertIsNotNone(parameter.grad, name)
            self.assertGreater(float(parameter.grad.abs().sum()), 0.0, name)
        self.assertFalse(model.semantic_embeddings.requires_grad)

    def test_embedding_chunks_survive_interruption(self) -> None:
        items = [segment_issue_text(IssueText(i, f"title {i}", "body"), max_segments=2)
                 for i in range(4)]
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            cache = EmbeddingCache(Path(directory))
            with self.assertRaises(KeyboardInterrupt):
                cache.encode_or_reuse(
                    items, InterruptingEncoder(fail_after=1), dataset_version="v",
                    mapping_sha256="m", max_segments=2, batch_size=10,
                    segment_batch_size=10, chunk_size=2,
                )
            self.assertEqual(1, len(list(cache.shard_dir.glob("*.npy"))))
            encoder = InterruptingEncoder()
            _, stats = cache.encode_or_reuse(
                items, encoder, dataset_version="v", mapping_sha256="m",
                max_segments=2, batch_size=10, segment_batch_size=10, chunk_size=2,
            )
            self.assertEqual({"reused": 2, "encoded": 2}, stats)
            status, reasons = cache.inspect(
                dataset_version="v", mapping_sha256="m", model_name="fake",
                revision="fixed", dimension=3, max_segments=2, row_count=4,
            )
            self.assertEqual(("CACHE_READY", []), (status, reasons))
            np.save(cache.row_mapping_path, np.asarray([1, 0, 2, 3], dtype=np.int64))
            status, reasons = cache.inspect(
                dataset_version="v", mapping_sha256="m", model_name="fake",
                revision="fixed", dimension=3, max_segments=2, row_count=4,
            )
            self.assertEqual("CACHE_INVALID", status)
            self.assertTrue(any("row mapping" in reason for reason in reasons))

    def test_atomic_npy_creates_parent_and_never_promotes_partial_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            target = Path(directory) / "missing" / "nested" / "chunk.npy"
            expected = np.arange(6, dtype=np.float32).reshape(2, 3)
            EmbeddingCache._atomic_npy(target, expected)
            np.testing.assert_array_equal(np.load(target), expected)

            def fail_after_partial_write(path, _value):
                Path(path).write_bytes(b"partial")
                raise OSError("synthetic interrupted write")

            with mock.patch(
                "coder_issue.models.text_encoder.np.save",
                side_effect=fail_after_partial_write,
            ):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    EmbeddingCache._atomic_npy(target, np.zeros((2, 3), dtype=np.float32))

            np.testing.assert_array_equal(np.load(target), expected)
            self.assertEqual([], list(target.parent.glob(".tmp-*.npy")))

    def test_windows_length_safe_chunk_and_temporary_names(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            base = Path(directory)
            padding_length = 175 - len(str(base)) - len("\\shards") - 1
            self.assertGreater(padding_length, 0)
            cache = EmbeddingCache(base / ("x" * padding_length))
            compact, legacy = cache._shard_paths(
                start=0,
                count=256,
                chunk_hash="a" * 64,
            )
            old_temporary = legacy.with_name(
                f".{legacy.name}.{'b' * 32}.tmp.npy"
            )
            short_temporary = cache.shard_dir / f".tmp-{'b' * 32}.npy"

            self.assertEqual(175, len(str(cache.shard_dir)))
            self.assertEqual(266, len(str(legacy)))
            self.assertEqual(308, len(str(old_temporary)))
            self.assertEqual(245, len(str(compact)))
            self.assertEqual(217, len(str(short_temporary)))
            EmbeddingCache._atomic_npy(compact, np.ones((1, 3), dtype=np.float32))
            np.testing.assert_array_equal(
                np.load(compact), np.ones((1, 3), dtype=np.float32)
            )

    def test_legacy_hexadecimal_chunk_is_still_reused(self) -> None:
        items = [
            segment_issue_text(IssueText(i, f"title {i}", "body"), max_segments=2)
            for i in range(2)
        ]
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            cache = EmbeddingCache(Path(directory))
            hashes = [_text_hash(item) for item in items]
            chunk_hash = hashlib.sha256("\0".join(hashes).encode("ascii")).hexdigest()
            _, legacy = cache._shard_paths(start=0, count=2, chunk_hash=chunk_hash)
            source_encoder = InterruptingEncoder()
            chunk = cache._encode_issue_chunk(
                items,
                source_encoder,
                segment_batch_size=10,
            )
            EmbeddingCache._atomic_npy(legacy, chunk)

            encoder = InterruptingEncoder()
            _, stats = cache.encode_or_reuse(
                items,
                encoder,
                dataset_version="v",
                mapping_sha256="m",
                max_segments=2,
                batch_size=10,
                segment_batch_size=10,
                chunk_size=2,
            )
            self.assertEqual(0, encoder.calls)
            self.assertEqual({"reused": 2, "encoded": 0}, stats)


if __name__ == "__main__":
    unittest.main()
