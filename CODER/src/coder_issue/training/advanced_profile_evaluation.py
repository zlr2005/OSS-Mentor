"""Reproducible grouped evaluation for a frozen Advanced Adapter bundle."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import torch

from coder_issue.models.profile import ProfileResidualAdapter
from coder_issue.training.advanced_profile_data import TrainingPair
from coder_issue.training.advanced_profile_pipeline import (
    AdvancedTrainingConfig,
    load_frozen_inputs,
    prepare_training_data,
)


REPORT_SCHEMA_VERSION = "advanced-adapter-evaluation-v0.1"
PAIRED_SCHEMA_VERSION = "advanced-adapter-paired-outcome-v0.1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repository(issue_key: str) -> str:
    return issue_key.rsplit("#", 1)[0]


def _coverage_bucket(observed_dimensions: int) -> str:
    if observed_dimensions <= 3:
        return "low_1_3"
    if observed_dimensions <= 6:
        return "medium_4_6"
    return "high_7_plus"


def _activity_bucket(train_outcomes: int) -> str:
    if train_outcomes == 0:
        return "unseen_0"
    if train_outcomes <= 2:
        return "low_1_2"
    if train_outcomes <= 5:
        return "medium_3_5"
    return "high_6_plus"


def _rank(positive: float, comparisons: Sequence[float]) -> int:
    return 1 + sum(score >= positive for score in comparisons)


def _score_outcomes(
    splits: Mapping[str, Sequence[TrainingPair]],
    *,
    adapter: ProfileResidualAdapter,
    base_user: torch.Tensor,
    issue_embeddings: Mapping[str, torch.Tensor],
) -> list[dict[str, Any]]:
    train_outcome_ids = {row.outcome_id for row in splits["train"]}
    train_developers = {row.developer_key for row in splits["train"]}
    train_repositories = {
        _repository(row.positive_issue_key) for row in splits["train"]
    }
    activity = Counter(
        next(
            row.developer_key for row in splits["train"] if row.outcome_id == outcome_id
        )
        for outcome_id in train_outcome_ids
    )
    output: list[dict[str, Any]] = []
    adapter.eval()
    with torch.inference_mode():
        for split_name in ("train", "validation", "test"):
            groups: dict[str, list[TrainingPair]] = defaultdict(list)
            for row in splits[split_name]:
                groups[row.outcome_id].append(row)
            for outcome_id, group in sorted(groups.items()):
                first = group[0]
                fused = adapter.fuse(
                    base_user.unsqueeze(0),
                    first.values.unsqueeze(0),
                    first.observed_mask.unsqueeze(0),
                ).squeeze(0)
                positive = issue_embeddings[first.positive_issue_key]
                original_positive = float(torch.dot(base_user, positive).item())
                profile_positive = float(torch.dot(fused, positive).item())
                original_comparisons = [
                    float(
                        torch.dot(
                            base_user, issue_embeddings[row.comparison_issue_key]
                        ).item()
                    )
                    for row in group
                ]
                profile_comparisons = [
                    float(
                        torch.dot(
                            fused, issue_embeddings[row.comparison_issue_key]
                        ).item()
                    )
                    for row in group
                ]
                original_rank = _rank(original_positive, original_comparisons)
                profile_rank = _rank(profile_positive, profile_comparisons)
                repository = _repository(first.positive_issue_key)
                observed_dimensions = int(first.observed_mask.sum().item())
                pair_groups = {
                    "same_repository": [
                        index
                        for index, row in enumerate(group)
                        if _repository(row.comparison_issue_key) == repository
                    ],
                    "cross_repository": [
                        index
                        for index, row in enumerate(group)
                        if _repository(row.comparison_issue_key) != repository
                    ],
                }
                pair_diagnostics = {}
                for label, indices in pair_groups.items():
                    pair_diagnostics[label] = {
                        "pairs": len(indices),
                        "original_correct": sum(
                            original_positive > original_comparisons[index]
                            for index in indices
                        ),
                        "profile_correct": sum(
                            profile_positive > profile_comparisons[index]
                            for index in indices
                        ),
                    }
                output.append(
                    {
                        "schema_version": PAIRED_SCHEMA_VERSION,
                        "split": split_name,
                        "outcome_id": outcome_id,
                        "outcome_type": first.outcome_type,
                        "outcome_at": first.outcome_at.isoformat(),
                        "developer_key": first.developer_key,
                        "developer_seen_in_train": first.developer_key
                        in train_developers,
                        "developer_train_outcomes": int(activity[first.developer_key]),
                        "developer_activity_bucket": _activity_bucket(
                            activity[first.developer_key]
                        ),
                        "positive_issue_key": first.positive_issue_key,
                        "positive_repository": repository,
                        "repository_seen_in_train": repository in train_repositories,
                        "profile_key": first.profile_key,
                        "profile_version": first.profile_version,
                        "profile_hash": first.profile_hash,
                        "profile_as_of": first.profile_as_of.isoformat(),
                        "observed_dimensions": observed_dimensions,
                        "profile_coverage_bucket": _coverage_bucket(
                            observed_dimensions
                        ),
                        "candidate_count": len(group) + 1,
                        "rank_original": original_rank,
                        "rank_profile": profile_rank,
                        "rank_improvement": original_rank - profile_rank,
                        "reciprocal_rank_original": 1.0 / original_rank,
                        "reciprocal_rank_profile": 1.0 / profile_rank,
                        "pair_diagnostics": pair_diagnostics,
                    }
                )
    return output


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"outcomes": 0}
    original_ranks = [int(row["rank_original"]) for row in rows]
    profile_ranks = [int(row["rank_profile"]) for row in rows]
    original_correct = profile_correct = pairs = 0
    for row in rows:
        for stats in row["pair_diagnostics"].values():
            pairs += int(stats["pairs"])
            original_correct += int(stats["original_correct"])
            profile_correct += int(stats["profile_correct"])
    metrics: dict[str, Any] = {
        "outcomes": len(rows),
        "pairs": pairs,
        "mrr_original": sum(1.0 / rank for rank in original_ranks) / len(rows),
        "mrr_profile": sum(1.0 / rank for rank in profile_ranks) / len(rows),
        "mean_rank_original": sum(original_ranks) / len(rows),
        "mean_rank_profile": sum(profile_ranks) / len(rows),
        "pair_accuracy_original": original_correct / pairs if pairs else None,
        "pair_accuracy_profile": profile_correct / pairs if pairs else None,
        "rank_improved": sum(
            after < before for before, after in zip(original_ranks, profile_ranks)
        ),
        "rank_unchanged": sum(
            after == before for before, after in zip(original_ranks, profile_ranks)
        ),
        "rank_worsened": sum(
            after > before for before, after in zip(original_ranks, profile_ranks)
        ),
    }
    metrics["mrr_delta"] = metrics["mrr_profile"] - metrics["mrr_original"]
    metrics["mean_rank_improvement"] = (
        metrics["mean_rank_original"] - metrics["mean_rank_profile"]
    )
    metrics["pair_accuracy_delta"] = (
        metrics["pair_accuracy_profile"] - metrics["pair_accuracy_original"]
        if pairs
        else None
    )
    for cutoff in (5, 10, 20):
        metrics[f"recall@{cutoff}_original"] = sum(
            rank <= cutoff for rank in original_ranks
        ) / len(rows)
        metrics[f"recall@{cutoff}_profile"] = sum(
            rank <= cutoff for rank in profile_ranks
        ) / len(rows)
    return metrics


def _grouped(
    rows: Sequence[Mapping[str, Any]],
    field: str,
    *,
    labels: Iterable[str] | None = None,
) -> dict[str, Any]:
    values = (
        list(labels)
        if labels is not None
        else sorted({str(row[field]) for row in rows})
    )
    return {
        value: _aggregate([row for row in rows if str(row[field]) == value])
        for value in values
    }


def _pair_shortcut(rows: Sequence[Mapping[str, Any]], label: str) -> dict[str, Any]:
    pairs = original = profiled = 0
    for row in rows:
        stats = row["pair_diagnostics"][label]
        pairs += int(stats["pairs"])
        original += int(stats["original_correct"])
        profiled += int(stats["profile_correct"])
    return {
        "pairs": pairs,
        "pair_accuracy_original": original / pairs if pairs else None,
        "pair_accuracy_profile": profiled / pairs if pairs else None,
        "pair_accuracy_delta": (profiled - original) / pairs if pairs else None,
    }


def _concentration(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row["developer_key"]) for row in rows)
    gains: dict[str, float] = defaultdict(float)
    for row in rows:
        gains[str(row["developer_key"])] += float(row["rank_improvement"])
    top_count = max(1, (len(counts) + 9) // 10)
    positive_total = sum(max(0.0, value) for value in gains.values())
    top_gain = sum(
        sorted((max(0.0, value) for value in gains.values()), reverse=True)[:top_count]
    )
    return {
        "developers": len(counts),
        "maximum_outcomes_per_developer": max(counts.values(), default=0),
        "top_10_percent_developer_outcome_share": (
            sum(sorted(counts.values(), reverse=True)[:top_count])
            / sum(counts.values())
            if counts
            else None
        ),
        "top_10_percent_developer_positive_rank_gain_share": (
            top_gain / positive_total if positive_total > 0 else None
        ),
    }


def _validate_against_training_metrics(
    report: Mapping[str, Any], metrics_path: Path | None
) -> dict[str, Any]:
    if metrics_path is None:
        return {"status": "not_requested"}
    metrics = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
    checks: dict[str, float] = {}
    for split in ("train", "validation", "test"):
        current = report["splits"][split]["overall"]
        for label, current_key, legacy_section, legacy_key in (
            ("original_mrr", "mrr_original", "coder_original", "mrr"),
            ("profile_mrr", "mrr_profile", "coder_profile", "mrr"),
            (
                "original_pair_accuracy",
                "pair_accuracy_original",
                "coder_original",
                "pair_accuracy",
            ),
            (
                "profile_pair_accuracy",
                "pair_accuracy_profile",
                "coder_profile",
                "pair_accuracy",
            ),
        ):
            delta = abs(
                float(current[current_key])
                - float(metrics[legacy_section][split][legacy_key])
            )
            checks[f"{split}.{label}"] = delta
            if delta > 1e-12:
                raise ValueError(
                    f"evaluation does not reproduce training metric: {split}.{label}"
                )
    return {
        "status": "passed",
        "maximum_absolute_delta": max(checks.values()),
        "checks": checks,
    }


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    config = AdvancedTrainingConfig.from_path(Path(args.config))
    frozen = load_frozen_inputs(Path(args.model_dir), Path(args.issue_cache))
    splits, failures, audit = prepare_training_data(
        profiles_path=Path(args.profiles),
        outcomes_path=Path(args.outcomes),
        candidates_path=Path(args.candidates),
        frozen=frozen,
        config=config,
    )
    adapter = ProfileResidualAdapter.load_bundle(
        Path(args.adapter_dir),
        expected_base_model_version=str(frozen.model_manifest["model_version"]),
    )
    rows = _score_outcomes(
        splits,
        adapter=adapter,
        base_user=frozen.base_user,
        issue_embeddings=frozen.issue_embeddings,
    )
    zero_values = torch.zeros(
        (1, len(adapter.config.feature_names)), dtype=torch.float32
    )
    zero_mask = torch.zeros_like(zero_values, dtype=torch.bool)
    with torch.inference_mode():
        fallback = adapter.fuse(frozen.base_user.unsqueeze(0), zero_values, zero_mask)
    split_reports: dict[str, Any] = {}
    for split_name in ("train", "validation", "test"):
        selected = [row for row in rows if row["split"] == split_name]
        split_reports[split_name] = {
            "overall": _aggregate(selected),
            "developer_history": _grouped(
                selected, "developer_seen_in_train", labels=("True", "False")
            ),
            "repository_history": _grouped(
                selected, "repository_seen_in_train", labels=("True", "False")
            ),
            "profile_coverage": _grouped(
                selected,
                "profile_coverage_bucket",
                labels=("low_1_3", "medium_4_6", "high_7_plus"),
            ),
            "developer_activity": _grouped(
                selected,
                "developer_activity_bucket",
                labels=("unseen_0", "low_1_2", "medium_3_5", "high_6_plus"),
            ),
            "outcome_type": _grouped(selected, "outcome_type"),
            "developer_concentration": _concentration(selected),
            "repository_shortcut_check": {
                "same_repository_comparisons": _pair_shortcut(
                    selected, "same_repository"
                ),
                "cross_repository_comparisons": _pair_shortcut(
                    selected, "cross_repository"
                ),
            },
        }
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "adapter_version": json.loads(
            (Path(args.adapter_dir) / "adapter_manifest.json").read_text(
                encoding="utf-8"
            )
        ).get("adapter_version"),
        "base_model_version": frozen.model_manifest["model_version"],
        "input_sha256": {
            "profiles": _sha256(Path(args.profiles)),
            "outcomes": _sha256(Path(args.outcomes)),
            "candidates": _sha256(Path(args.candidates)),
            "issue_cache": _sha256(Path(args.issue_cache)),
            "adapter_manifest": _sha256(
                Path(args.adapter_dir) / "adapter_manifest.json"
            ),
            "adapter_weights": _sha256(Path(args.adapter_dir) / "weights.pt"),
        },
        "audit": audit,
        "reconstructed_failures": len(failures),
        "all_unknown_profile_fallback": {
            "status": "passed"
            if torch.equal(fallback, frozen.base_user.unsqueeze(0))
            else "failed",
            "maximum_absolute_delta": float(
                (fallback - frozen.base_user.unsqueeze(0)).abs().max().item()
            ),
        },
        "splits": split_reports,
        "shortcut_limitations": {
            "language": (
                "not_measurable: the frozen historical candidate contract contains no "
                "language field; repository names are not used as a proxy"
            ),
            "repository": (
                "same- and cross-repository sampled comparisons are reported separately; "
                "they are not exposure-derived negatives"
            ),
        },
        "known_limitations": [
            "Historical comparisons are sampled unlabeled candidates, not explicit negative feedback.",
            "Offline improvement does not establish online success probability.",
            "Language shortcut analysis requires a future versioned candidate-language field.",
        ],
    }
    report["reproduction"] = _validate_against_training_metrics(
        report, Path(args.training_metrics) if args.training_metrics else None
    )
    if report["all_unknown_profile_fallback"]["status"] != "passed":
        raise ValueError("all-unknown profile does not exactly reproduce the base user")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paired_path = output_dir / "paired_outcomes.jsonl"
    with paired_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    report["paired_outcomes"] = {
        "records": len(rows),
        "sha256": _sha256(paired_path),
    }
    report_path = output_dir / "evaluation_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    success = hashlib.sha256(report_path.read_bytes()).hexdigest()
    (output_dir / "_SUCCESS").write_text(success + "\n", encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--outcomes", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--issue-cache", required=True)
    parser.add_argument("--adapter-dir", required=True)
    parser.add_argument("--training-metrics")
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    report = evaluate(build_parser().parse_args(argv))
    print(
        json.dumps(
            {
                "schema_version": report["schema_version"],
                "adapter_version": report["adapter_version"],
                "paired_outcomes": report["paired_outcomes"],
                "test": report["splits"]["test"]["overall"],
                "reproduction": report["reproduction"]["status"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
