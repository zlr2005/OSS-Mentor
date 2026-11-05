"""Export every local Advanced Adapter experiment into comparable tables."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

KEY_METRICS = (
    "mrr",
    "mrr@5",
    "mrr@10",
    "mrr@20",
    "ndcg@5",
    "ndcg@10",
    "ndcg@20",
    "recall@5",
    "recall@10",
    "recall@20",
    "mean_rank",
    "pair_accuracy",
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"expected JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.absolute().relative_to(root.absolute()).as_posix()


def _flatten(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from _flatten(value[key], child)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{prefix}[{index}]"
            yield from _flatten(item, child)
        return
    yield prefix, value


def _metric_rows(
    *,
    run_id: str,
    record_kind: str,
    source_file: str,
    payload: Mapping[str, Any],
    source_section: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric_path, value in _flatten(payload):
        if value is None or isinstance(value, (str, int, float, bool)):
            rows.append(
                {
                    "run_id": run_id,
                    "record_kind": record_kind,
                    "source_file": source_file,
                    "source_section": source_section,
                    "metric_path": metric_path,
                    "value_type": type(value).__name__,
                    "value": value,
                }
            )
    return rows


def _nested(payload: Mapping[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _config_from_manifest(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    training = manifest.get("training_config", {})
    if isinstance(training, Mapping) and training:
        return training
    metadata = manifest.get("metadata", {})
    if not isinstance(metadata, Mapping):
        metadata = {}
    training = metadata.get("training_config", {})
    if isinstance(training, Mapping) and training:
        return training
    config = manifest.get("config", {})
    return config if isinstance(config, Mapping) else {}


def _metric_value(metrics: Mapping[str, Any] | None, name: str) -> Any:
    return metrics.get(name) if isinstance(metrics, Mapping) else None


def _base_run_row(*, run_id: str, record_kind: str, source_file: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "record_kind": record_kind,
        "source_file": source_file,
        "schema_version": None,
        "adapter_version": None,
        "seed": None,
        "data_seed": None,
        "hidden_dim": None,
        "residual_scale": None,
        "gate_mode": None,
        "feature_dropout": None,
        "hard_negative_weight": None,
        "hard_negative_temperature": None,
        "learning_rate": None,
        "weight_decay": None,
        "supplement_fraction": None,
        "margin_anchor_weight": None,
        "parameter_anchor_weight": None,
        "low_coverage_multiplier": None,
        "max_recall_at_5_drop": None,
        "best_epoch": None,
        "epochs_completed": None,
        "selected_validation_mrr": None,
        "best_nonzero_validation_mrr": None,
        "selection_score": None,
        "low_activity_mrr": None,
        "low_coverage_mrr": None,
        "unseen_developer_mrr": None,
        "same_repository_pair_accuracy": None,
        "weights_sha256": None,
        "final_is_rc1_weights": None,
        "test_was_evaluated": False,
        "decision": "experimental",
    }


def _add_split_metrics(
    row: dict[str, Any],
    *,
    split: str,
    original: Mapping[str, Any] | None,
    profile: Mapping[str, Any] | None,
) -> None:
    for variant, metrics in (("original", original), ("profile", profile)):
        for metric in KEY_METRICS:
            row[f"{split}_{variant}_{metric}"] = _metric_value(metrics, metric)
        row[f"{split}_{variant}_seen_developer_mrr"] = _nested(
            metrics or {}, "seen_developer", "mrr"
        )
        row[f"{split}_{variant}_unseen_developer_mrr"] = _nested(
            metrics or {}, "unseen_developer", "mrr"
        )


def _load_local_run(
    metrics_path: Path, *, repo_root: Path, rc1_weights_sha256: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_dir = metrics_path.parent
    run_id = _relative(run_dir, repo_root)
    source_file = _relative(metrics_path, repo_root)
    metrics = _read_json(metrics_path)
    manifest_path = run_dir / "adapter_manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    config = _config_from_manifest(manifest)
    metadata = manifest.get("metadata", {})
    if not isinstance(metadata, Mapping):
        metadata = {}
    protected = manifest.get(
        "protected_finetune", metadata.get("protected_finetune", {})
    )
    if not isinstance(protected, Mapping):
        protected = {}

    row = _base_run_row(
        run_id=run_id, record_kind="local_training", source_file=source_file
    )
    row.update(
        {
            "schema_version": metrics.get("schema_version"),
            "adapter_version": manifest.get("adapter_version"),
            "seed": config.get(
                "seed", manifest.get("random_seed", metadata.get("random_seed"))
            ),
            "data_seed": config.get(
                "data_seed", manifest.get("data_seed", metadata.get("data_seed"))
            ),
            "hidden_dim": config.get("hidden_dim"),
            "residual_scale": config.get("residual_scale"),
            "gate_mode": config.get("gate_mode"),
            "feature_dropout": config.get("feature_dropout"),
            "hard_negative_weight": config.get("hard_negative_weight"),
            "hard_negative_temperature": config.get("hard_negative_temperature"),
            "learning_rate": config.get("learning_rate"),
            "weight_decay": config.get("weight_decay"),
            "supplement_fraction": protected.get("supplement_fraction"),
            "margin_anchor_weight": protected.get("margin_anchor_weight"),
            "parameter_anchor_weight": protected.get("parameter_anchor_weight"),
            "low_coverage_multiplier": protected.get("low_coverage_multiplier"),
            "max_recall_at_5_drop": protected.get("max_recall_at_5_drop"),
            "best_epoch": metrics.get("best_epoch"),
            "epochs_completed": metrics.get("epochs_completed"),
            "selected_validation_mrr": metrics.get("best_validation_mrr"),
            "test_was_evaluated": "test" in (metrics.get("coder_profile", {}) or {}),
        }
    )
    history = metrics.get("history", [])
    if isinstance(history, list):
        nonzero = [
            item
            for item in history
            if isinstance(item, Mapping)
            and isinstance(item.get("epoch"), int)
            and item["epoch"] > 0
        ]
        if nonzero:
            row["best_nonzero_validation_mrr"] = max(
                float(_nested(item, "validation", "mrr"))
                for item in nonzero
                if _nested(item, "validation", "mrr") is not None
            )

    original = metrics.get("coder_original", {})
    profile = metrics.get("coder_profile", {})
    for split in ("train", "validation", "test"):
        _add_split_metrics(
            row,
            split=split,
            original=original.get(split) if isinstance(original, Mapping) else None,
            profile=profile.get(split) if isinstance(profile, Mapping) else None,
        )

    selection_path = run_dir / "validation_selection.json"
    selection = _read_json(selection_path) if selection_path.exists() else {}
    row.update(
        {
            "selection_score": selection.get("selection_score"),
            "low_activity_mrr": _nested(selection, "components", "low_activity_mrr"),
            "low_coverage_mrr": _nested(selection, "components", "low_coverage_mrr"),
            "unseen_developer_mrr": _nested(
                selection, "components", "unseen_developer_mrr"
            ),
            "same_repository_pair_accuracy": _nested(
                selection, "components", "same_repository_pair_accuracy"
            ),
        }
    )
    weights_path = run_dir / "weights.pt"
    if weights_path.exists():
        weights_sha256 = _sha256(weights_path)
        row["weights_sha256"] = weights_sha256
        row["final_is_rc1_weights"] = weights_sha256 == rc1_weights_sha256
    if row["final_is_rc1_weights"]:
        row["decision"] = "rc1" if "round2_best" in run_id else "fallback_to_rc1"

    long_rows = _metric_rows(
        run_id=run_id,
        record_kind="local_training",
        source_file=source_file,
        payload=metrics,
        source_section="training_metrics",
    )
    if selection:
        long_rows.extend(
            _metric_rows(
                run_id=run_id,
                record_kind="local_training",
                source_file=_relative(selection_path, repo_root),
                payload=selection,
                source_section="validation_selection",
            )
        )
    return row, long_rows


def _optimization_trial_row(
    *,
    trial: Mapping[str, Any],
    round_name: str,
    category: str,
    report_path: Path,
    repo_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    label = str(trial.get("label", "unlabeled"))
    config = trial.get("config", {})
    if not isinstance(config, Mapping):
        config = {}
    run_id = f"optimization/{round_name}/{category}/{label}-s{config.get('seed')}"
    source_file = _relative(report_path, repo_root)
    validation = trial.get("validation", {})
    if not isinstance(validation, Mapping):
        validation = {}
    overall = validation.get("overall", {})
    if not isinstance(overall, Mapping):
        overall = {}
    components = validation.get("components", {})
    if not isinstance(components, Mapping):
        components = {}

    row = _base_run_row(
        run_id=run_id,
        record_kind=f"optimization_{category}",
        source_file=source_file,
    )
    row.update(
        {
            "schema_version": "advanced-adapter-optimization-v0.1",
            "adapter_version": config.get("adapter_version"),
            "seed": config.get("seed"),
            "data_seed": config.get("data_seed"),
            "hidden_dim": config.get("hidden_dim"),
            "residual_scale": config.get("residual_scale"),
            "gate_mode": config.get("gate_mode"),
            "feature_dropout": config.get("feature_dropout"),
            "hard_negative_weight": config.get("hard_negative_weight"),
            "hard_negative_temperature": config.get("hard_negative_temperature"),
            "learning_rate": config.get("learning_rate"),
            "weight_decay": config.get("weight_decay"),
            "epochs_completed": trial.get("epochs_completed"),
            "selected_validation_mrr": components.get(
                "overall_mrr", trial.get("best_validation_mrr_during_training")
            ),
            "selection_score": validation.get("selection_score"),
            "low_activity_mrr": components.get("low_activity_mrr"),
            "low_coverage_mrr": components.get("low_coverage_mrr"),
            "unseen_developer_mrr": components.get("unseen_developer_mrr"),
            "same_repository_pair_accuracy": components.get(
                "same_repository_pair_accuracy"
            ),
        }
    )
    _add_split_metrics(
        row,
        split="validation",
        original={
            "mrr": overall.get("mrr_original"),
            "recall@5": overall.get("recall@5_original"),
            "recall@10": overall.get("recall@10_original"),
            "recall@20": overall.get("recall@20_original"),
            "mean_rank": overall.get("mean_rank_original"),
            "pair_accuracy": overall.get("pair_accuracy_original"),
        },
        profile={
            "mrr": overall.get("mrr_profile"),
            "recall@5": overall.get("recall@5_profile"),
            "recall@10": overall.get("recall@10_profile"),
            "recall@20": overall.get("recall@20_profile"),
            "mean_rank": overall.get("mean_rank_profile"),
            "pair_accuracy": overall.get("pair_accuracy_profile"),
        },
    )
    long_rows = _metric_rows(
        run_id=run_id,
        record_kind=row["record_kind"],
        source_file=source_file,
        payload=trial,
        source_section=f"{round_name}.{category}",
    )
    return row, long_rows


def _load_optimization_report(
    report_path: Path, *, repo_root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    report = _read_json(report_path)
    rows: list[dict[str, Any]] = []
    long_rows: list[dict[str, Any]] = []
    for round_name in ("round1", "round2"):
        round_payload = report.get(round_name, {})
        if not isinstance(round_payload, Mapping):
            continue
        identities: set[tuple[str, Any]] = set()
        for category in ("structural_trials", "seed_trials"):
            trials = round_payload.get(category, [])
            if not isinstance(trials, list):
                continue
            for trial in trials:
                if not isinstance(trial, Mapping):
                    continue
                config = trial.get("config", {})
                seed = config.get("seed") if isinstance(config, Mapping) else None
                identity = (str(trial.get("label")), seed)
                if identity in identities:
                    continue
                identities.add(identity)
                row, metrics = _optimization_trial_row(
                    trial=trial,
                    round_name=round_name,
                    category=(
                        "structural" if category == "structural_trials" else "seed"
                    ),
                    report_path=report_path,
                    repo_root=repo_root,
                )
                rows.append(row)
                long_rows.extend(metrics)
        average = round_payload.get("checkpoint_average")
        if isinstance(average, Mapping):
            row, metrics = _optimization_trial_row(
                trial=average,
                round_name=round_name,
                category="checkpoint_average",
                report_path=report_path,
                repo_root=repo_root,
            )
            rows.append(row)
            long_rows.extend(metrics)

    test_comparison = report.get("test_comparison", {})
    if isinstance(test_comparison, Mapping):
        source_file = _relative(report_path, repo_root)
        for label, metrics in sorted(test_comparison.items()):
            if not isinstance(metrics, Mapping):
                continue
            run_id = f"optimization/test/{label}"
            row = _base_run_row(
                run_id=run_id,
                record_kind="test_evaluation",
                source_file=source_file,
            )
            row.update(
                {
                    "schema_version": report.get("schema_version"),
                    "adapter_version": label,
                    "test_was_evaluated": True,
                    "decision": "rc1" if label == "round2_best" else "comparison",
                }
            )
            _add_split_metrics(
                row,
                split="test",
                original=None,
                profile=metrics,
            )
            rows.append(row)
            long_rows.extend(
                _metric_rows(
                    run_id=run_id,
                    record_kind="test_evaluation",
                    source_file=source_file,
                    payload=metrics,
                    source_section=f"test_comparison.{label}",
                )
            )
    return rows, long_rows


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    preferred = [
        key
        for key in (
            "run_id",
            "record_kind",
            "source_file",
            "source_section",
            "metric_path",
            "value_type",
            "value",
        )
        if key in fields
    ]
    fields = preferred + [key for key in fields if key not in preferred]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _write_markdown(
    path: Path,
    *,
    rows: Sequence[Mapping[str, Any]],
    metric_count: int,
    rc1_weights_sha256: str,
) -> None:
    local = [row for row in rows if row["record_kind"] == "local_training"]
    optimization = [
        row for row in rows if str(row["record_kind"]).startswith("optimization_")
    ]
    tests = [row for row in rows if row["record_kind"] == "test_evaluation"]
    lines = [
        "# Advanced Adapter 全实验对比索引",
        "",
        "生成日期：2026-11-05",
        "",
        "## 覆盖范围",
        "",
        f"- 独立训练目录：{len(local)}；",
        f"- RC1 优化内部去重试验/派生产物：{len(optimization)}；",
        f"- 冻结 test 对照记录：{len(tests)}；",
        f"- 全指标长表记录数：{metric_count}；",
        f"- RC1 weights SHA-256：`{rc1_weights_sha256}`。",
        "",
        "`runs.csv` 是一行一个实验的宽表；`metrics_long.csv` 保存训练历史、validation 分组、",
        "test 和嵌套指标的全部标量值。以下只展示用于快速比较的核心列，不能替代全指标长表。",
        "",
        "本地存在完整实验目录时，可从仓库根目录重新生成：",
        "",
        "```powershell",
        "& '.\\CODER\\.venv\\Scripts\\python.exe' '.\\CODER\\scripts\\export_adapter_experiment_comparison.py'",
        "```",
        "",
        "## 独立训练目录",
        "",
        "| Run | Seed | Best epoch | Val MRR | Best nonzero MRR | Recall@5 | Recall@10 | Pair acc | Unseen MRR | Selection | Decision |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in sorted(local, key=lambda item: str(item["run_id"])):
        lines.append(
            "| "
            + " | ".join(
                (
                    str(row["run_id"]).replace("|", "\\|"),
                    _fmt(row.get("seed")),
                    _fmt(row.get("best_epoch")),
                    _fmt(row.get("validation_profile_mrr")),
                    _fmt(row.get("best_nonzero_validation_mrr")),
                    _fmt(row.get("validation_profile_recall@5")),
                    _fmt(row.get("validation_profile_recall@10")),
                    _fmt(row.get("validation_profile_pair_accuracy")),
                    _fmt(row.get("unseen_developer_mrr")),
                    _fmt(row.get("selection_score")),
                    _fmt(row.get("decision")),
                )
            )
            + " |"
        )
    lines.extend(
        (
            "",
            "## RC1 优化内部试验",
            "",
            "| Run | Seed | Val MRR | Recall@5 | Recall@10 | Pair acc | Selection |",
            "|---|---:|---:|---:|---:|---:|---:|",
        )
    )
    for row in sorted(optimization, key=lambda item: str(item["run_id"])):
        lines.append(
            "| "
            + " | ".join(
                (
                    str(row["run_id"]).replace("|", "\\|"),
                    _fmt(row.get("seed")),
                    _fmt(row.get("validation_profile_mrr")),
                    _fmt(row.get("validation_profile_recall@5")),
                    _fmt(row.get("validation_profile_recall@10")),
                    _fmt(row.get("validation_profile_pair_accuracy")),
                    _fmt(row.get("selection_score")),
                )
            )
            + " |"
        )
    lines.extend(
        (
            "",
            "## 冻结 Test 对照",
            "",
            "| Model | MRR | MRR@5 | NDCG@10 | Recall@5 | Recall@10 | Mean rank | Pair acc |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        )
    )
    for row in sorted(tests, key=lambda item: str(item["run_id"])):
        lines.append(
            "| "
            + " | ".join(
                (
                    str(row.get("adapter_version")),
                    _fmt(row.get("test_profile_mrr")),
                    _fmt(row.get("test_profile_mrr@5")),
                    _fmt(row.get("test_profile_ndcg@10")),
                    _fmt(row.get("test_profile_recall@5")),
                    _fmt(row.get("test_profile_recall@10")),
                    _fmt(row.get("test_profile_mean_rank")),
                    _fmt(row.get("test_profile_pair_accuracy")),
                )
            )
            + " |"
        )
    lines.extend(
        (
            "",
            "## 解释",
            "",
            "- `fallback_to_rc1` 表示实验完成了训练，但发布门最终保存的是 epoch 0 RC1 精确权重；",
            "- `experimental` 表示本地研究产物，不批准 D 接入；",
            "- `test_evaluation` 只来自已经按协议解锁的冻结测试；补采保护实验没有打开 test；",
            "- 同一优化 trial 若同时出现在 structural 和 seed 列表中，导出时按 `(label, seed)` 去重；",
            "- 所有未在 Markdown 展开的指标均保留在 `metrics_long.csv`。",
        )
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).absolute()
    models_root = (repo_root / args.models_root).absolute()
    optimization_report = (repo_root / args.optimization_report).absolute()
    rc1_weights = (repo_root / args.rc1_weights).absolute()
    output_dir = (repo_root / args.output_dir).absolute()
    output_dir.mkdir(parents=True, exist_ok=True)
    rc1_weights_sha256 = _sha256(rc1_weights)

    rows: list[dict[str, Any]] = []
    long_rows: list[dict[str, Any]] = []
    for metrics_path in sorted(models_root.rglob("training_metrics.json")):
        row, metrics = _load_local_run(
            metrics_path,
            repo_root=repo_root,
            rc1_weights_sha256=rc1_weights_sha256,
        )
        rows.append(row)
        long_rows.extend(metrics)
    optimization_rows, optimization_metrics = _load_optimization_report(
        optimization_report, repo_root=repo_root
    )
    rows.extend(optimization_rows)
    long_rows.extend(optimization_metrics)
    rows.sort(key=lambda item: str(item["run_id"]))
    long_rows.sort(
        key=lambda item: (
            str(item["run_id"]),
            str(item["source_section"]),
            str(item["metric_path"]),
        )
    )

    runs_path = output_dir / "runs.csv"
    metrics_path = output_dir / "metrics_long.csv"
    markdown_path = output_dir / "README.md"
    _write_csv(runs_path, rows)
    _write_csv(metrics_path, long_rows)
    _write_markdown(
        markdown_path,
        rows=rows,
        metric_count=len(long_rows),
        rc1_weights_sha256=rc1_weights_sha256,
    )
    manifest = {
        "schema_version": "advanced-adapter-experiment-inventory-v0.1",
        "generated_at": "2026-11-05",
        "models_root": _relative(models_root, repo_root),
        "optimization_report": _relative(optimization_report, repo_root),
        "rc1_weights_sha256": rc1_weights_sha256,
        "run_records": len(rows),
        "local_training_records": sum(
            row["record_kind"] == "local_training" for row in rows
        ),
        "optimization_records": sum(
            str(row["record_kind"]).startswith("optimization_") for row in rows
        ),
        "test_evaluation_records": sum(
            row["record_kind"] == "test_evaluation" for row in rows
        ),
        "metric_records": len(long_rows),
        "artifacts": {
            "runs.csv": _sha256(runs_path),
            "metrics_long.csv": _sha256(metrics_path),
            "README.md": _sha256(markdown_path),
        },
    }
    manifest_path = output_dir / "inventory_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--models-root", default="data/models")
    parser.add_argument(
        "--optimization-report",
        default="data/models/advanced_adapter_optimization_v1/optimization_report.json",
    )
    parser.add_argument(
        "--rc1-weights",
        default="data/models/advanced_adapter_optimization_v1/round2_best/weights.pt",
    )
    parser.add_argument(
        "--output-dir", default="docs/artifacts/advanced_adapter_experiments_20261105"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    manifest = export(build_parser().parse_args(argv))
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
