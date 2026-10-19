from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import torch
from torch import nn

from coder_issue.errors import ConfigurationError
from coder_issue.models.coder import CoderConfig, CoderIssueModel
from coder_issue.models.gat import (
    StructuralGATConfig,
    StructuralGATEncoder,
    bidirectional_structural_edges,
)
from coder_issue.models.lightgcn import normalized_bipartite_adjacency
from coder_issue.training.config import load_neural_settings
from coder_issue.training.pipeline import validation_candidate_path
from coder_issue.training.selection import (
    EarlyStoppingPolicy,
    ValidationSchedule,
    restore_best_checkpoint,
    run_training_trial,
    save_best_checkpoint,
)
from coder_issue.training.trainer import EpochResult


CODER_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class TinyConfig:
    width: int = 1


class TinyModel(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.config = TinyConfig()
        self.weight = nn.Parameter(torch.zeros(1))


def _run_tiny_trial(
    root: Path,
    *,
    scores: dict[int, float],
    epochs: int,
    interval: int = 1,
    start: int = 1,
    enabled: bool = True,
    patience: int = 2,
    min_delta: float = 0.0,
    min_epochs: int = 1,
):
    model = TinyModel()
    train_calls: list[int] = []
    validation_modes: list[tuple[bool, bool]] = []

    def train_epoch(epoch: int) -> EpochResult:
        model.train()
        with torch.no_grad():
            model.weight.fill_(float(epoch))
        train_calls.append(epoch)
        return EpochResult(float(epoch), None, 1, 0)

    def evaluate(current: TinyModel):
        validation_modes.append((current.training, torch.is_grad_enabled()))
        epoch = int(current.weight.item())
        return {"predictable": {"ndcg@10": scores[epoch]}}

    result = run_training_trial(
        model,
        epochs=epochs,
        schedule=ValidationSchedule(interval, start),
        policy=EarlyStoppingPolicy(enabled, patience, min_delta, min_epochs),
        checkpoint_path=root / "best.pt",
        checkpoint_metadata={"variant": "tiny"},
        train_epoch=train_epoch,
        evaluator=evaluate,
        selection_metric="ndcg@10",
    )
    return model, result, train_calls, validation_modes


def _issue_graph() -> torch.Tensor:
    return normalized_bipartite_adjacency(
        torch.tensor([0, 0, 1]),
        torch.tensor([0, 1, 2]),
        num_users=2,
        num_items=4,
    )


def _structural() -> StructuralGATEncoder:
    edges = bidirectional_structural_edges(
        torch.tensor([[0, 0], [1, 0], [2, 1], [3, 1]]),
        torch.tensor([[0, 0], [1, 1], [2, 0]]),
        num_repositories=2,
        num_issues=4,
        num_labels=2,
    )
    return StructuralGATEncoder(
        StructuralGATConfig(2, 4, 2, 4, 2, 2, 0.0), edges
    )


class TrainingSelectionTests(unittest.TestCase):
    def test_periodic_validation_selects_and_restores_best_epoch(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            model, result, train_calls, modes = _run_tiny_trial(
                Path(directory),
                scores={5: 0.2, 10: 0.8, 12: 0.7},
                epochs=12,
                interval=5,
                start=5,
                enabled=False,
            )
        self.assertEqual([5, 10, 12], [row["epoch"] for row in result.validation_history])
        self.assertEqual(10, result.best_epoch)
        self.assertEqual(12, result.epochs_trained)
        self.assertEqual(10.0, float(model.weight.item()))
        self.assertEqual(list(range(1, 13)), train_calls)
        self.assertTrue(all(not training and not grad for training, grad in modes))
        self.assertTrue(model.training)

    def test_early_stopping_counts_validations_and_keeps_earlier_tie(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            model, result, train_calls, _ = _run_tiny_trial(
                Path(directory),
                scores={1: 0.5, 2: 0.5, 3: 0.4, 4: 0.9},
                epochs=4,
                patience=2,
                min_epochs=3,
            )
        self.assertTrue(result.stopped_early)
        self.assertEqual("early_stopping", result.stop_reason)
        self.assertEqual(1, result.best_epoch)
        self.assertEqual(3, result.epochs_trained)
        self.assertEqual([1, 2, 3], train_calls)
        self.assertEqual(1.0, float(model.weight.item()))

    def test_continuous_improvement_does_not_stop(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            _, result, train_calls, _ = _run_tiny_trial(
                Path(directory),
                scores={epoch: epoch / 10 for epoch in range(1, 7)},
                epochs=6,
                patience=2,
                min_epochs=2,
            )
        self.assertFalse(result.stopped_early)
        self.assertEqual("max_epochs", result.stop_reason)
        self.assertEqual(6, result.best_epoch)
        self.assertEqual(list(range(1, 7)), train_calls)

    def test_disabled_early_stopping_runs_max_epochs(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            _, result, train_calls, _ = _run_tiny_trial(
                Path(directory),
                scores={epoch: 0.1 for epoch in range(1, 7)},
                epochs=6,
                enabled=False,
                patience=1,
            )
        self.assertFalse(result.stopped_early)
        self.assertEqual(6, result.epochs_trained)
        self.assertEqual(list(range(1, 7)), train_calls)

    def test_final_epoch_is_validated_when_not_on_periodic_boundary(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            _, result, _, _ = _run_tiny_trial(
                Path(directory),
                scores={5: 0.1, 7: 0.2},
                epochs=7,
                interval=5,
                start=5,
                enabled=False,
            )
        self.assertEqual([5, 7], [row["epoch"] for row in result.validation_history])
        self.assertEqual(7, result.best_epoch)

    def test_selection_is_reproducible(self) -> None:
        outputs = []
        for _ in range(2):
            with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
                model, result, _, _ = _run_tiny_trial(
                    Path(directory),
                    scores={1: 0.1, 2: 0.3, 3: 0.2},
                    epochs=3,
                    enabled=False,
                )
                outputs.append((result.best_epoch, result.best_validation_score, model.weight.item()))
        self.assertEqual(outputs[0], outputs[1])

    def test_validation_never_steps_the_optimizer(self) -> None:
        model = TinyModel()
        optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
        optimizer_steps = 0
        steps_seen_by_validation: list[int] = []

        def train_epoch(epoch: int) -> EpochResult:
            nonlocal optimizer_steps
            model.train()
            optimizer.zero_grad(set_to_none=True)
            loss = (model.weight - float(epoch)).square().sum()
            loss.backward()
            optimizer.step()
            optimizer_steps += 1
            return EpochResult(float(loss.detach()), None, 1, 0)

        def evaluate(current: TinyModel):
            self.assertFalse(current.training)
            self.assertFalse(torch.is_grad_enabled())
            steps_seen_by_validation.append(optimizer_steps)
            return {"predictable": {"ndcg@10": 0.5}}

        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            result = run_training_trial(
                model,
                epochs=3,
                schedule=ValidationSchedule(1, 1),
                policy=EarlyStoppingPolicy(False, 1, 0.0, 1),
                checkpoint_path=Path(directory) / "best.pt",
                checkpoint_metadata={},
                train_epoch=train_epoch,
                evaluator=evaluate,
                selection_metric="ndcg@10",
            )
        self.assertEqual(3, optimizer_steps)
        self.assertEqual([1, 2, 3, 3], steps_seen_by_validation)
        self.assertEqual(1, result.best_epoch)

    def test_non_finite_validation_score_fails(self) -> None:
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            with self.assertRaisesRegex(ValueError, "finite"):
                _run_tiny_trial(
                    Path(directory), scores={1: float("nan")}, epochs=1
                )

    def test_checkpoint_roundtrip_supports_lightgcn_and_structure(self) -> None:
        variants = (
            CoderIssueModel(
                CoderConfig(2, 4, 2, embedding_dim=4, issue_layers=1),
                issue_adjacency=_issue_graph(),
                issue_to_repository=torch.tensor([0, 0, 1, 1]),
            ),
            CoderIssueModel(
                CoderConfig(
                    2,
                    4,
                    2,
                    embedding_dim=4,
                    issue_layers=1,
                    structure_layers=2,
                    structure_heads=2,
                    structure_dropout=0.0,
                    use_structure=True,
                ),
                issue_adjacency=_issue_graph(),
                issue_to_repository=torch.tensor([0, 0, 1, 1]),
                structural_encoder=_structural(),
            ),
        )
        with tempfile.TemporaryDirectory(dir=CODER_ROOT) as directory:
            root = Path(directory)
            for index, model in enumerate(variants):
                model.eval()
                with torch.inference_mode():
                    expected = model.score_known_users(
                        torch.tensor([0]), torch.tensor([0, 1, 2])
                    ).clone()
                checkpoint = root / f"variant-{index}.pt"
                save_best_checkpoint(
                    checkpoint,
                    model,
                    epoch=5,
                    validation_score=0.25,
                    metadata={"variant": model.config.variant},
                )
                with torch.no_grad():
                    for parameter in model.parameters():
                        parameter.add_(10)
                restored = restore_best_checkpoint(checkpoint, model, map_location="cpu")
                with torch.inference_mode():
                    actual = model.score_known_users(
                        torch.tensor([0]), torch.tensor([0, 1, 2])
                    )
                torch.testing.assert_close(expected, actual)
                self.assertEqual(5, restored["epoch"])

    def test_selection_path_is_only_frozen_cross_repo_validation(self) -> None:
        settings = SimpleNamespace(candidate_dir=Path("frozen-candidates"))
        path = validation_candidate_path(settings)
        self.assertEqual(Path("frozen-candidates/valid_cross_repo.parquet"), path)
        self.assertNotIn("test", path.name)

    def test_invalid_selection_configuration_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "validation_interval_epochs"):
            ValidationSchedule(0, 1)
        with self.assertRaisesRegex(ValueError, "early_stopping_patience"):
            EarlyStoppingPolicy(True, 0, 0.0, 1)
        with self.assertRaisesRegex(ValueError, "early_stopping_min_delta"):
            EarlyStoppingPolicy(True, 1, -0.1, 1)

        source = (CODER_ROOT / "configs/coder_multigranular.toml").read_text(encoding="utf-8")
        invalid = source.replace(
            "validation_interval_epochs = 5", "validation_interval_epochs = 0"
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".toml",
                dir=CODER_ROOT / "configs",
                encoding="utf-8",
                delete=False,
            ) as stream:
                stream.write(invalid)
                temporary_path = Path(stream.name)
            with self.assertRaisesRegex(ConfigurationError, "validation_interval_epochs"):
                load_neural_settings(temporary_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
