"""Protected Adapter fine-tuning with RC1 replay and teacher anchoring."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import torch
from torch.nn import functional as F

from coder_issue.models.profile import ProfileAdapterConfig, ProfileResidualAdapter
from coder_issue.training.advanced_profile_data import TrainingPair
from coder_issue.training.advanced_profile_experiments import _validation_summary
from coder_issue.training.advanced_profile_pipeline import (
    AdvancedTrainingConfig,
    FrozenInputs,
    _batch_tensors,
    _set_seed,
    _sha256,
    _weighted_bpr,
    _write_json,
    evaluate_pairs,
    load_frozen_inputs,
    prepare_training_data,
    write_audit,
)
from coder_issue.training.advanced_supplement_experiment import (
    _merge_frozen,
    build_supplement_pairs,
    reweight_training_pairs,
)


def _groups(rows: Sequence[TrainingPair]) -> dict[str, list[TrainingPair]]:
    grouped: dict[str, list[TrainingPair]] = defaultdict(list)
    for row in rows:
        grouped[row.outcome_id].append(row)
    return dict(grouped)


def _epoch_replay_rows(
    base_rows: Sequence[TrainingPair],
    supplement_rows: Sequence[TrainingPair],
    *,
    supplement_fraction: float,
    seed: int,
    epoch: int,
) -> list[tuple[TrainingPair, bool]]:
    """Replay every RC1 outcome and deterministically sample the target supplement share."""
    if not 0.0 < supplement_fraction < 1.0:
        raise ValueError("supplement_fraction must be within (0, 1)")
    base = _groups(base_rows)
    supplement = _groups(supplement_rows)
    if not base or not supplement:
        raise ValueError("protected replay requires base and supplement outcomes")
    target_supplement = round(
        len(base) * supplement_fraction / (1.0 - supplement_fraction)
    )
    rng = random.Random(seed * 1_000_003 + epoch)
    base_ids = sorted(base)
    supplement_ids = sorted(supplement)
    rng.shuffle(base_ids)
    selected_supplement: list[str] = []
    while len(selected_supplement) < target_supplement:
        cycle = list(supplement_ids)
        rng.shuffle(cycle)
        selected_supplement.extend(cycle)
    selected_supplement = selected_supplement[:target_supplement]

    tagged: list[tuple[TrainingPair, bool]] = []
    for outcome_id in base_ids:
        tagged.extend((row, True) for row in base[outcome_id])
    for outcome_id in selected_supplement:
        tagged.extend((row, False) for row in supplement[outcome_id])
    rng.shuffle(tagged)
    return tagged


def _low_coverage_weight(
    rows: Sequence[TrainingPair], *, multiplier: float
) -> list[TrainingPair]:
    if multiplier < 1.0:
        raise ValueError("low coverage multiplier must be at least 1")
    return [
        replace(row, weight=row.weight * multiplier)
        if int(row.observed_mask.sum().item()) <= 3
        else row
        for row in rows
    ]


def _parameter_anchor(
    student: ProfileResidualAdapter,
    reference: Mapping[str, torch.Tensor],
) -> torch.Tensor:
    penalties = [
        (parameter - reference[name]).square().mean()
        for name, parameter in student.named_parameters()
    ]
    return torch.stack(penalties).mean()


def _margin(
    user: torch.Tensor, positive: torch.Tensor, comparison: torch.Tensor
) -> torch.Tensor:
    return (user * positive).sum(dim=-1) - (user * comparison).sum(dim=-1)


def train_protected_adapter(
    *,
    base_train: Sequence[TrainingPair],
    supplement_train: Sequence[TrainingPair],
    validation: Sequence[TrainingPair],
    test: Sequence[TrainingPair],
    frozen: FrozenInputs,
    config: AdvancedTrainingConfig,
    teacher: ProfileResidualAdapter,
    supplement_fraction: float,
    margin_anchor_weight: float,
    parameter_anchor_weight: float,
    low_coverage_multiplier: float,
    device: str | torch.device,
    evaluate_test: bool = False,
) -> tuple[ProfileResidualAdapter, dict[str, Any], dict[str, list[TrainingPair]]]:
    if margin_anchor_weight < 0 or parameter_anchor_weight < 0:
        raise ValueError("anchor weights must be non-negative")
    _set_seed(config.seed)
    target_device = torch.device(device)
    student = ProfileResidualAdapter(teacher.config)
    student.load_state_dict(teacher.state_dict())
    student.to(target_device)
    teacher = teacher.to(target_device).eval()
    for parameter in teacher.parameters():
        parameter.requires_grad_(False)
    reference = {
        name: parameter.detach().clone().to(target_device)
        for name, parameter in teacher.named_parameters()
    }

    base_equal, base_weighting = reweight_training_pairs(
        base_train, mode="outcome_equal"
    )
    supplement_equal, supplement_weighting = reweight_training_pairs(
        supplement_train, mode="outcome_equal"
    )
    base_equal = _low_coverage_weight(base_equal, multiplier=low_coverage_multiplier)
    unique_train = [*base_equal, *supplement_equal]
    splits = {
        "train": unique_train,
        "validation": list(validation),
        "test": list(test),
    }
    train_developers = {row.developer_key for row in unique_train}

    optimizer = torch.optim.AdamW(
        student.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    initial_validation = evaluate_pairs(
        student,
        splits["validation"],
        frozen,
        train_developers=train_developers,
        device=target_device,
    )
    best_validation_mrr = float(initial_validation["mrr"])
    best_state = {
        key: value.detach().cpu().clone() for key, value in student.state_dict().items()
    }
    history: list[dict[str, Any]] = [
        {
            "epoch": 0,
            "train_loss": None,
            "bpr_loss": None,
            "margin_anchor_loss": 0.0,
            "parameter_anchor_loss": 0.0,
            "validation": initial_validation,
        }
    ]
    stale_epochs = 0

    for epoch in range(1, config.epochs + 1):
        student.train()
        replay = _epoch_replay_rows(
            base_equal,
            supplement_equal,
            supplement_fraction=supplement_fraction,
            seed=config.seed,
            epoch=epoch,
        )
        totals = {"loss": 0.0, "bpr": 0.0, "margin": 0.0, "parameter": 0.0}
        total_rows = 0
        for start in range(0, len(replay), config.batch_size):
            batch = replay[start : start + config.batch_size]
            rows = [row for row, _ in batch]
            base_mask = torch.tensor(
                [is_base for _, is_base in batch],
                dtype=torch.bool,
                device=target_device,
            )
            values, observed, positive, comparison, weights = _batch_tensors(
                rows, frozen, device=target_device
            )
            optimizer.zero_grad(set_to_none=True)
            base_user = frozen.base_user.to(target_device).expand(len(rows), -1)
            user = student.fuse(base_user, values, observed)
            bpr = _weighted_bpr(
                user,
                positive,
                comparison,
                weights,
                hard_negative_weight=config.hard_negative_weight,
                hard_negative_temperature=config.hard_negative_temperature,
            )
            margin_anchor = torch.zeros((), device=target_device)
            if margin_anchor_weight and bool(base_mask.any()):
                with torch.no_grad():
                    teacher_user = teacher.fuse(base_user, values, observed)
                    teacher_margin = _margin(teacher_user, positive, comparison)[
                        base_mask
                    ]
                student_margin = _margin(user, positive, comparison)[base_mask]
                margin_anchor = F.smooth_l1_loss(
                    student_margin, teacher_margin, reduction="mean"
                )
            parameter_anchor = torch.zeros((), device=target_device)
            if parameter_anchor_weight:
                parameter_anchor = _parameter_anchor(student, reference)
            loss = (
                bpr
                + margin_anchor_weight * margin_anchor
                + parameter_anchor_weight * parameter_anchor
            )
            loss.backward()
            optimizer.step()
            batch_size = len(rows)
            total_rows += batch_size
            totals["loss"] += float(loss.item()) * batch_size
            totals["bpr"] += float(bpr.item()) * batch_size
            totals["margin"] += float(margin_anchor.item()) * batch_size
            totals["parameter"] += float(parameter_anchor.item()) * batch_size

        validation_metrics = evaluate_pairs(
            student,
            splits["validation"],
            frozen,
            train_developers=train_developers,
            device=target_device,
        )
        history.append(
            {
                "epoch": epoch,
                "train_loss": totals["loss"] / total_rows,
                "bpr_loss": totals["bpr"] / total_rows,
                "margin_anchor_loss": totals["margin"] / total_rows,
                "parameter_anchor_loss": totals["parameter"] / total_rows,
                "validation": validation_metrics,
            }
        )
        validation_mrr = float(validation_metrics["mrr"])
        if validation_mrr > best_validation_mrr:
            best_validation_mrr = validation_mrr
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in student.state_dict().items()
            }
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= config.patience:
                break

    student.load_state_dict(best_state)
    student.to("cpu").eval()
    teacher.to("cpu")
    evaluated = (
        ("train", "validation", "test") if evaluate_test else ("train", "validation")
    )
    original = {
        name: evaluate_pairs(
            None,
            splits[name],
            frozen,
            train_developers=train_developers if name != "train" else None,
            device=torch.device("cpu"),
        )
        for name in evaluated
    }
    profiled = {
        name: evaluate_pairs(
            student,
            splits[name],
            frozen,
            train_developers=train_developers if name != "train" else None,
            device=torch.device("cpu"),
        )
        for name in evaluated
    }
    metrics = {
        "schema_version": "advanced-adapter-protected-training-metrics-v0.1",
        "best_validation_mrr": best_validation_mrr,
        "best_epoch": next(
            row["epoch"]
            for row in history
            if float(row["validation"]["mrr"]) == best_validation_mrr
        ),
        "epochs_completed": len(history) - 1,
        "history": history,
        "coder_original": original,
        "coder_profile": profiled,
        "replay": {
            "supplement_fraction": supplement_fraction,
            "base_outcomes": len(_groups(base_equal)),
            "supplement_outcomes": len(_groups(supplement_equal)),
            "base_weighting": base_weighting,
            "supplement_weighting": supplement_weighting,
            "low_coverage_multiplier": low_coverage_multiplier,
        },
        "protection": {
            "warm_started_from_teacher": True,
            "margin_anchor_weight": margin_anchor_weight,
            "parameter_anchor_weight": parameter_anchor_weight,
            "epoch_zero_was_checkpoint_candidate": True,
        },
    }
    return student, metrics, splits


def run(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config)
    config = AdvancedTrainingConfig.from_path(config_path)
    config = replace(
        config,
        seed=args.seed,
        adapter_version=f"advanced-adapter-protected-{args.experiment}-s{args.seed}",
        learning_rate=args.learning_rate,
        epochs=args.epochs,
        patience=args.patience,
    )
    base_frozen = load_frozen_inputs(Path(args.model_dir), Path(args.base_issue_cache))
    supplement_cache = Path(args.supplement_issue_cache)
    frozen = _merge_frozen(base_frozen, supplement_cache)
    base_splits, base_failures, base_report = prepare_training_data(
        profiles_path=Path(args.base_profiles),
        outcomes_path=Path(args.base_outcomes),
        candidates_path=Path(args.base_candidates),
        frozen=base_frozen,
        config=config,
    )
    supplement_pairs, supplement_failures, supplement_report = build_supplement_pairs(
        supplement_dir=Path(args.supplement_dir),
        frozen=frozen,
        config=config,
        cutoff_policy="calendar_quarter",
        pair_plan_path=Path(args.supplement_pair_plan),
        candidates_path=Path(args.supplement_candidates),
    )
    if supplement_failures:
        raise RuntimeError(
            f"supplement audit failed with {len(supplement_failures)} failures"
        )
    teacher = ProfileResidualAdapter.load_bundle(
        Path(args.teacher_adapter_dir),
        expected_base_model_version=str(frozen.model_manifest["model_version"]),
    )
    if teacher.config.embedding_dim != int(frozen.base_user.shape[0]):
        raise ValueError("teacher adapter embedding dimension is incompatible")
    expected_adapter = ProfileAdapterConfig(
        embedding_dim=int(frozen.base_user.shape[0]),
        hidden_dim=config.hidden_dim,
        residual_scale=config.residual_scale,
        gate_mode=config.gate_mode,
    )
    if teacher.config != expected_adapter:
        raise ValueError(
            "teacher adapter architecture does not match the protected training config"
        )

    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be absent or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "schema_version": "advanced-adapter-protected-audit-v0.1",
        "base": base_report,
        "supplement": supplement_report,
        "base_failures": len(base_failures),
        "supplement_failures": len(supplement_failures),
        "validation_pairs_unchanged": len(base_splits["validation"])
        == base_report["split_pairs"]["validation"],
        "test_pairs_unchanged": len(base_splits["test"])
        == base_report["split_pairs"]["test"],
    }
    write_audit(
        output_dir,
        report=audit,
        failures=[*base_failures, *supplement_failures],
    )
    teacher_splits = {
        "train": [*base_splits["train"], *supplement_pairs],
        "validation": base_splits["validation"],
        "test": base_splits["test"],
    }
    teacher_validation = _validation_summary(teacher, teacher_splits, frozen)
    _write_json(output_dir / "teacher_validation_selection.json", teacher_validation)
    student, metrics, splits = train_protected_adapter(
        base_train=base_splits["train"],
        supplement_train=supplement_pairs,
        validation=base_splits["validation"],
        test=base_splits["test"],
        frozen=frozen,
        config=config,
        teacher=teacher,
        supplement_fraction=args.supplement_fraction,
        margin_anchor_weight=args.margin_anchor_weight,
        parameter_anchor_weight=args.parameter_anchor_weight,
        low_coverage_multiplier=args.low_coverage_multiplier,
        device=args.device,
        evaluate_test=args.evaluate_test,
    )
    _write_json(output_dir / "training_metrics.json", metrics)
    validation = _validation_summary(student, splits, frozen)
    _write_json(output_dir / "validation_selection.json", validation)
    split_manifest = {
        "schema_version": "advanced-adapter-protected-split-v0.1",
        "protocol": "rc1_replay_quarter_n2_supplement_validation_test_frozen",
        "train_pairs": len(splits["train"]),
        "train_outcomes": len(_groups(splits["train"])),
        "validation_pairs": len(splits["validation"]),
        "test_pairs": len(splits["test"]),
    }
    _write_json(output_dir / "split_manifest.json", split_manifest)
    student.save_bundle(
        output_dir,
        base_model_version=str(frozen.model_manifest["model_version"]),
        metadata={
            "adapter_version": config.adapter_version,
            "service_track": "growth_research",
            "experiment_protocol": split_manifest["protocol"],
            "teacher_adapter_manifest_sha256": _sha256(
                Path(args.teacher_adapter_dir) / "adapter_manifest.json"
            ),
            "teacher_adapter_weights_sha256": _sha256(
                Path(args.teacher_adapter_dir) / "weights.pt"
            ),
            "test_evaluated_during_training": args.evaluate_test,
            "training_config": {
                **asdict(config),
                "train_end": config.train_end.isoformat(),
                "validation_end": config.validation_end.isoformat(),
            },
            "protected_finetune": {
                "supplement_fraction": args.supplement_fraction,
                "margin_anchor_weight": args.margin_anchor_weight,
                "parameter_anchor_weight": args.parameter_anchor_weight,
                "low_coverage_multiplier": args.low_coverage_multiplier,
            },
            "training_inputs": {
                "base_profiles_sha256": _sha256(Path(args.base_profiles)),
                "base_outcomes_sha256": _sha256(Path(args.base_outcomes)),
                "base_candidates_sha256": _sha256(Path(args.base_candidates)),
                "base_issue_cache_sha256": _sha256(Path(args.base_issue_cache)),
                "supplement_pair_plan_sha256": _sha256(Path(args.supplement_pair_plan)),
                "supplement_candidates_sha256": _sha256(
                    Path(args.supplement_candidates)
                ),
                "supplement_issue_cache_sha256": _sha256(supplement_cache),
            },
            "random_seed": config.seed,
            "data_seed": config.data_seed,
            "split_manifest_sha256": _sha256(output_dir / "split_manifest.json"),
            "training_metrics_sha256": _sha256(output_dir / "training_metrics.json"),
            "validation_selection_sha256": _sha256(
                output_dir / "validation_selection.json"
            ),
            "teacher_validation_selection_sha256": _sha256(
                output_dir / "teacher_validation_selection.json"
            ),
            "audit_report_sha256": _sha256(output_dir / "audit_report.json"),
            "failures_sha256": _sha256(output_dir / "failures.jsonl"),
        },
    )
    return {
        "audit": audit,
        "teacher_validation": teacher_validation,
        "validation": validation,
        "metrics": {
            "best_validation_mrr": metrics["best_validation_mrr"],
            "best_epoch": metrics["best_epoch"],
            "epochs_completed": metrics["epochs_completed"],
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--teacher-adapter-dir", required=True)
    parser.add_argument("--base-profiles", required=True)
    parser.add_argument("--base-outcomes", required=True)
    parser.add_argument("--base-candidates", required=True)
    parser.add_argument("--base-issue-cache", required=True)
    parser.add_argument("--supplement-dir", required=True)
    parser.add_argument("--supplement-pair-plan", required=True)
    parser.add_argument("--supplement-candidates", required=True)
    parser.add_argument("--supplement-issue-cache", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--supplement-fraction", type=float, default=0.10)
    parser.add_argument("--margin-anchor-weight", type=float, default=0.0)
    parser.add_argument("--parameter-anchor-weight", type=float, default=0.0)
    parser.add_argument("--low-coverage-multiplier", type=float, default=1.5)
    parser.add_argument("--learning-rate", type=float, default=0.0002)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--evaluate-test", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    report = run(build_parser().parse_args(argv))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
