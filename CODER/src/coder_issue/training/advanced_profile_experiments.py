"""Run two validation-selected optimization rounds for Advanced Adapter v1."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch

from coder_issue.models.profile import ProfileAdapterConfig, ProfileResidualAdapter
from coder_issue.training.advanced_profile_evaluation import (
    _aggregate,
    _grouped,
    _pair_shortcut,
    _score_outcomes,
)
from coder_issue.training.advanced_profile_pipeline import (
    AdvancedTrainingConfig,
    FrozenInputs,
    evaluate_pairs,
    load_frozen_inputs,
    prepare_training_data,
    train_adapter,
)


EXPERIMENT_SCHEMA_VERSION = "advanced-adapter-optimization-v0.1"
SEEDS = (20261102, 20261103, 20261104, 20261105, 20261106)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _jsonable_config(config: AdvancedTrainingConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload["accepted_verification_statuses"] = list(
        config.accepted_verification_statuses
    )
    payload["train_end"] = config.train_end.isoformat()
    payload["validation_end"] = config.validation_end.isoformat()
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validation_summary(
    adapter: ProfileResidualAdapter,
    splits: Mapping[str, Sequence[Any]],
    frozen: FrozenInputs,
) -> dict[str, Any]:
    rows = _score_outcomes(
        splits,
        adapter=adapter,
        base_user=frozen.base_user,
        issue_embeddings=frozen.issue_embeddings,
        split_names=("validation",),
    )
    overall = _aggregate(rows)
    developer = _grouped(rows, "developer_seen_in_train", labels=("True", "False"))
    coverage = _grouped(
        rows,
        "profile_coverage_bucket",
        labels=("low_1_3", "medium_4_6", "high_7_plus"),
    )
    activity = _grouped(
        rows,
        "developer_activity_bucket",
        labels=("unseen_0", "low_1_2", "medium_3_5", "high_6_plus"),
    )
    same_repository = _pair_shortcut(rows, "same_repository")

    def mrr(group: Mapping[str, Any]) -> float:
        return float(group.get("mrr_profile", overall["mrr_profile"]))

    same_repository_accuracy = same_repository.get("pair_accuracy_profile")
    if same_repository_accuracy is None or int(same_repository["pairs"]) < 20:
        same_repository_accuracy = overall["pair_accuracy_profile"]
    components = {
        "overall_mrr": float(overall["mrr_profile"]),
        "unseen_developer_mrr": mrr(developer["False"]),
        "low_activity_mrr": mrr(activity["low_1_2"]),
        "low_coverage_mrr": mrr(coverage["low_1_3"]),
        "same_repository_pair_accuracy": float(same_repository_accuracy),
    }
    score = (
        0.50 * components["overall_mrr"]
        + 0.20 * components["unseen_developer_mrr"]
        + 0.15 * components["low_activity_mrr"]
        + 0.10 * components["low_coverage_mrr"]
        + 0.05 * components["same_repository_pair_accuracy"]
    )
    return {
        "selection_score": score,
        "selection_weights": {
            "overall_mrr": 0.50,
            "unseen_developer_mrr": 0.20,
            "low_activity_mrr": 0.15,
            "low_coverage_mrr": 0.10,
            "same_repository_pair_accuracy": 0.05,
        },
        "components": components,
        "overall": overall,
        "developer_history": developer,
        "profile_coverage": coverage,
        "developer_activity": activity,
        "same_repository": same_repository,
    }


def _train_trial(
    label: str,
    config: AdvancedTrainingConfig,
    splits: Mapping[str, Sequence[Any]],
    frozen: FrozenInputs,
) -> tuple[ProfileResidualAdapter, dict[str, Any]]:
    print(f"START {label} seed={config.seed}", flush=True)
    adapter, metrics = train_adapter(
        splits,
        frozen,
        config,
        device="cpu",
        evaluate_test=False,
    )
    validation = _validation_summary(adapter, splits, frozen)
    summary = {
        "label": label,
        "config": _jsonable_config(config),
        "epochs_completed": metrics["epochs_completed"],
        "best_validation_mrr_during_training": metrics["best_validation_mrr"],
        "validation": validation,
    }
    print(
        f"DONE {label} score={validation['selection_score']:.8f} "
        f"mrr={validation['overall']['mrr_profile']:.8f}",
        flush=True,
    )
    return adapter, summary


def _average_adapters(
    adapters: Sequence[ProfileResidualAdapter], config: ProfileAdapterConfig
) -> ProfileResidualAdapter:
    if not adapters:
        raise ValueError("at least one adapter is required for checkpoint averaging")
    keys = tuple(adapters[0].state_dict())
    if any(tuple(adapter.state_dict()) != keys for adapter in adapters):
        raise ValueError(
            "checkpoint averaging requires identical adapter architectures"
        )
    averaged = ProfileResidualAdapter(config)
    state = {
        key: torch.stack(
            [adapter.state_dict()[key].float() for adapter in adapters]
        ).mean(dim=0)
        for key in keys
    }
    averaged.load_state_dict(state)
    averaged.eval()
    return averaged


def _structural_specs(
    base: AdvancedTrainingConfig,
) -> list[tuple[str, AdvancedTrainingConfig]]:
    common = {
        "data_seed": base.data_seed,
        "gate_mode": "none",
        "hard_negative_weight": 0.0,
        "hard_negative_temperature": 1.0,
    }
    rows = [
        (
            "r1_scale025_h32",
            dict(
                residual_scale=0.25,
                hidden_dim=32,
                learning_rate=0.001,
                feature_dropout=0.0,
                residual_l2=0.0,
            ),
        ),
        (
            "r1_scale050_h32",
            dict(
                residual_scale=0.50,
                hidden_dim=32,
                learning_rate=0.001,
                feature_dropout=0.0,
                residual_l2=0.0,
            ),
        ),
        (
            "r1_scale075_h32",
            dict(
                residual_scale=0.75,
                hidden_dim=32,
                learning_rate=0.001,
                feature_dropout=0.0,
                residual_l2=0.0,
            ),
        ),
        (
            "r1_scale050_drop10_l2e4",
            dict(
                residual_scale=0.50,
                hidden_dim=32,
                learning_rate=0.001,
                feature_dropout=0.10,
                residual_l2=0.0001,
            ),
        ),
        (
            "r1_scale050_drop10_l2e3",
            dict(
                residual_scale=0.50,
                hidden_dim=32,
                learning_rate=0.001,
                feature_dropout=0.10,
                residual_l2=0.001,
            ),
        ),
        (
            "r1_scale050_h16_lr5e4",
            dict(
                residual_scale=0.50,
                hidden_dim=16,
                learning_rate=0.0005,
                feature_dropout=0.10,
                residual_l2=0.0001,
            ),
        ),
        (
            "r1_scale050_h64_lr5e4",
            dict(
                residual_scale=0.50,
                hidden_dim=64,
                learning_rate=0.0005,
                feature_dropout=0.10,
                residual_l2=0.0001,
            ),
        ),
        (
            "r1_scale075_lr5e4_drop10",
            dict(
                residual_scale=0.75,
                hidden_dim=32,
                learning_rate=0.0005,
                feature_dropout=0.10,
                residual_l2=0.0001,
            ),
        ),
    ]
    return [
        (
            label,
            replace(
                base,
                adapter_version=label,
                seed=SEEDS[0],
                **common,
                **overrides,
            ),
        )
        for label, overrides in rows
    ]


def _round2_specs(
    base: AdvancedTrainingConfig,
) -> list[tuple[str, AdvancedTrainingConfig]]:
    rows = [
        (
            "r2_gate",
            dict(
                gate_mode="coverage",
                hard_negative_weight=0.0,
                hard_negative_temperature=1.0,
            ),
        ),
        (
            "r2_gate_hard05",
            dict(
                gate_mode="coverage",
                hard_negative_weight=0.5,
                hard_negative_temperature=1.0,
            ),
        ),
        (
            "r2_gate_hard10",
            dict(
                gate_mode="coverage",
                hard_negative_weight=1.0,
                hard_negative_temperature=1.0,
            ),
        ),
        (
            "r2_gate_hard20",
            dict(
                gate_mode="coverage",
                hard_negative_weight=2.0,
                hard_negative_temperature=1.0,
            ),
        ),
        (
            "r2_gate_hard10_temp05",
            dict(
                gate_mode="coverage",
                hard_negative_weight=1.0,
                hard_negative_temperature=0.5,
            ),
        ),
        (
            "r2_gate_hard10_drop20",
            dict(
                gate_mode="coverage",
                hard_negative_weight=1.0,
                hard_negative_temperature=1.0,
                feature_dropout=0.20,
            ),
        ),
        (
            "r2_no_gate_hard10",
            dict(
                gate_mode="none",
                hard_negative_weight=1.0,
                hard_negative_temperature=1.0,
            ),
        ),
    ]
    return [
        (
            label,
            replace(base, adapter_version=label, seed=SEEDS[0], **overrides),
        )
        for label, overrides in rows
    ]


def _run_round(
    round_name: str,
    specs: Sequence[tuple[str, AdvancedTrainingConfig]],
    splits: Mapping[str, Sequence[Any]],
    frozen: FrozenInputs,
) -> tuple[ProfileResidualAdapter, AdvancedTrainingConfig, dict[str, Any]]:
    structural: list[
        tuple[ProfileResidualAdapter, dict[str, Any], AdvancedTrainingConfig]
    ] = []
    for label, config in specs:
        adapter, summary = _train_trial(label, config, splits, frozen)
        structural.append((adapter, summary, config))
    structural.sort(
        key=lambda item: item[1]["validation"]["selection_score"], reverse=True
    )
    top_adapter, top_summary, top_config = structural[0]

    seeded: list[
        tuple[ProfileResidualAdapter, dict[str, Any], AdvancedTrainingConfig]
    ] = [(top_adapter, top_summary, top_config)]
    for seed in SEEDS[1:]:
        config = replace(
            top_config,
            seed=seed,
            adapter_version=f"{top_config.adapter_version}-s{seed}",
        )
        adapter, summary = _train_trial(
            f"{round_name}_seed_{seed}", config, splits, frozen
        )
        seeded.append((adapter, summary, config))

    averaged = _average_adapters(
        [item[0] for item in seeded],
        top_adapter.config,
    )
    averaged_validation = _validation_summary(averaged, splits, frozen)
    averaged_summary = {
        "label": f"{round_name}_checkpoint_average",
        "config": _jsonable_config(top_config),
        "seeds": list(SEEDS),
        "validation": averaged_validation,
    }
    candidates = [(adapter, config, summary) for adapter, summary, config in seeded]
    candidates.append((averaged, top_config, averaged_summary))
    candidates.sort(
        key=lambda item: item[2]["validation"]["selection_score"], reverse=True
    )
    winner_adapter, winner_config, winner_summary = candidates[0]
    return (
        winner_adapter,
        winner_config,
        {
            "round": round_name,
            "structural_trials": [item[1] for item in structural],
            "seed_trials": [item[1] for item in seeded],
            "checkpoint_average": averaged_summary,
            "winner": winner_summary,
            "winner_is_checkpoint_average": winner_adapter is averaged,
            "winner_config": _jsonable_config(winner_config),
        },
    )


def _test_report(
    adapter: ProfileResidualAdapter | None,
    splits: Mapping[str, Sequence[Any]],
    frozen: FrozenInputs,
) -> dict[str, Any]:
    train_developers = {row.developer_key for row in splits["train"]}
    return evaluate_pairs(
        adapter,
        splits["test"],
        frozen,
        train_developers=train_developers,
        device=torch.device("cpu"),
    )


def _save_bundle(
    output_dir: Path,
    adapter: ProfileResidualAdapter,
    config: AdvancedTrainingConfig,
    validation: Mapping[str, Any],
    frozen: FrozenInputs,
    args: argparse.Namespace,
    *,
    round_name: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "experiment_config.json", _jsonable_config(config))
    _write_json(output_dir / "validation_selection.json", validation)
    adapter.save_bundle(
        output_dir,
        base_model_version=str(frozen.model_manifest["model_version"]),
        metadata={
            "adapter_version": f"advanced-adapter-v1-{round_name}-optimized",
            "service_track": "growth",
            "base_user_mode": "population_mean",
            "optimization_round": round_name,
            "selection_split": "validation",
            "base_model_manifest_sha256": frozen.model_manifest_sha256,
            "training_inputs": {
                "profiles_sha256": _sha256(Path(args.profiles)),
                "outcomes_sha256": _sha256(Path(args.outcomes)),
                "candidates_sha256": _sha256(Path(args.candidates)),
                "issue_cache_sha256": _sha256(Path(args.issue_cache)),
            },
            "experiment_config_sha256": _sha256(output_dir / "experiment_config.json"),
            "validation_selection_sha256": _sha256(
                output_dir / "validation_selection.json"
            ),
        },
    )


def _comparison_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Advanced Adapter 两轮优化性能对比",
        "",
        "所有超参数和 checkpoint 只根据 validation 选择；test 仅在两轮 winner 冻结后评估。",
        "",
        "| 模型 | Test MRR | Pair accuracy | Mean rank | Recall@5 | Recall@10 | 未见开发者 MRR |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ("coder_original", "adapter_v1", "round1_best", "round2_best"):
        row = report["test_comparison"][key]
        lines.append(
            f"| {key} | {row['mrr']:.5f} | {row['pair_accuracy']:.2%} | "
            f"{row['mean_rank']:.2f} | {row['recall@5']:.2%} | {row['recall@10']:.2%} | "
            f"{row['unseen_developer']['mrr']:.5f} |"
        )
    lines.extend(
        [
            "",
            "## 选择与限制",
            "",
            "- 第一轮：残差强度、隐藏维度、学习率、特征 dropout、残差 L2 和多 seed。",
            "- 第二轮：coverage gate、动态困难对照加权、多 seed 和 checkpoint averaging。",
            "- 原始数据、时间切分、候选集合、基础 CODER 和测试集均未改变。",
            "- 历史对照仍是未标记候选，不是用户明确负反馈。",
            "",
        ]
    )
    return "\n".join(lines)


def run_experiments(args: argparse.Namespace) -> dict[str, Any]:
    base_config = AdvancedTrainingConfig.from_path(Path(args.config))
    if base_config.data_seed != base_config.seed:
        raise ValueError(
            "base config must start with data_seed equal to the frozen sampling seed"
        )
    frozen = load_frozen_inputs(Path(args.model_dir), Path(args.issue_cache))
    splits, failures, audit = prepare_training_data(
        profiles_path=Path(args.profiles),
        outcomes_path=Path(args.outcomes),
        candidates_path=Path(args.candidates),
        frozen=frozen,
        config=base_config,
    )
    if audit["candidate_embedding_missing"] or audit["candidate_content_mismatch"]:
        raise ValueError(
            "optimization requires a complete, content-matched issue cache"
        )

    round1_adapter, round1_config, round1 = _run_round(
        "round1", _structural_specs(base_config), splits, frozen
    )
    round2_base = replace(
        round1_config,
        seed=SEEDS[0],
        data_seed=base_config.data_seed,
    )
    round2_adapter, round2_config, round2 = _run_round(
        "round2", _round2_specs(round2_base), splits, frozen
    )

    original_adapter = ProfileResidualAdapter.load_bundle(
        Path(args.original_adapter_dir),
        expected_base_model_version=str(frozen.model_manifest["model_version"]),
    )
    test_comparison = {
        "coder_original": _test_report(None, splits, frozen),
        "adapter_v1": _test_report(original_adapter, splits, frozen),
        "round1_best": _test_report(round1_adapter, splits, frozen),
        "round2_best": _test_report(round2_adapter, splits, frozen),
    }
    output_dir = Path(args.output_dir)
    _save_bundle(
        output_dir / "round1_best",
        round1_adapter,
        round1_config,
        round1["winner"]["validation"],
        frozen,
        args,
        round_name="round1",
    )
    _save_bundle(
        output_dir / "round2_best",
        round2_adapter,
        round2_config,
        round2["winner"]["validation"],
        frozen,
        args,
        round_name="round2",
    )
    report = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "selection_policy": "validation_only_group_weighted",
        "test_opened_after_winner_selection": True,
        "data_invariants": {
            "data_seed": base_config.data_seed,
            "audit": audit,
            "reconstructed_failures": len(failures),
            "split_pair_counts": {name: len(rows) for name, rows in splits.items()},
        },
        "round1": round1,
        "round2": round2,
        "test_comparison": test_comparison,
    }
    _write_json(output_dir / "optimization_report.json", report)
    (output_dir / "performance_comparison.md").write_text(
        _comparison_markdown(report), encoding="utf-8"
    )
    report["artifacts"] = {
        "optimization_report_sha256": _sha256(output_dir / "optimization_report.json"),
        "performance_comparison_sha256": _sha256(
            output_dir / "performance_comparison.md"
        ),
        "round1_weights_sha256": _sha256(output_dir / "round1_best" / "weights.pt"),
        "round2_weights_sha256": _sha256(output_dir / "round2_best" / "weights.pt"),
    }
    _write_json(output_dir / "artifact_hashes.json", report["artifacts"])
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--outcomes", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--issue-cache", required=True)
    parser.add_argument("--original-adapter-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    report = run_experiments(build_parser().parse_args(argv))
    print(
        json.dumps(
            {
                "artifacts": report["artifacts"],
                "round1_winner": report["round1"]["winner"]["label"],
                "round2_winner": report["round2"]["winner"]["label"],
                "test_comparison": report["test_comparison"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
