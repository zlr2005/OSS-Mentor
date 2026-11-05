"""Train Adapter v1 with an isolated, training-only evidence supplement.

The frozen RC1 validation and test pairs are rebuilt from their original
inputs.  Versioned supplement pairs are appended only to the training split,
which makes the resulting A/B comparison attributable to the extra training
evidence rather than to a changed evaluation candidate set.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import torch

from coder_issue.models.profile import ProfileAdapterConfig
from coder_issue.training.advanced_profile_data import (
    DataFailure,
    TrainingPair,
    load_issue_outcomes,
    load_temporal_profiles,
    parse_timestamp,
)
from coder_issue.training.advanced_profile_pipeline import (
    AdvancedTrainingConfig,
    FrozenInputs,
    _sha256,
    _write_json,
    load_frozen_inputs,
    prepare_training_data,
    train_adapter,
    write_audit,
)


def _rows(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            result.append(value)
    return result


def _load_supplement_cache(
    path: Path,
) -> tuple[dict[str, torch.Tensor], dict[str, str]]:
    with np.load(path) as payload:
        keys = payload["issue_keys"].astype(str).tolist()
        vectors = np.asarray(payload["issue_embeddings"], dtype=np.float32)
        hashes = payload["content_sha256"].astype(str).tolist()
    if vectors.ndim != 2 or vectors.shape[0] != len(keys):
        raise ValueError("supplement cache keys and embeddings are inconsistent")
    if len(keys) != len(set(keys)) or any(
        not key.startswith("historical-candidate:") for key in keys
    ):
        raise ValueError("supplement cache candidate keys are invalid or duplicated")
    if len(hashes) != len(keys) or any(
        len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in hashes
    ):
        raise ValueError("supplement cache content hashes are invalid")
    if not np.isfinite(vectors).all():
        raise ValueError("supplement cache contains non-finite embeddings")
    return (
        {key: torch.from_numpy(vector.copy()) for key, vector in zip(keys, vectors)},
        dict(zip(keys, hashes, strict=True)),
    )


def _merge_frozen(base: FrozenInputs, supplement_cache: Path) -> FrozenInputs:
    vectors, hashes = _load_supplement_cache(supplement_cache)
    if vectors and next(iter(vectors.values())).shape != base.base_user.shape:
        raise ValueError("supplement embedding dimension does not match the base model")
    overlap = set(vectors) & set(base.issue_embeddings)
    if overlap:
        raise ValueError("supplement candidate keys collide with the RC1 cache")
    return FrozenInputs(
        base_user=base.base_user,
        issue_embeddings={**base.issue_embeddings, **vectors},
        issue_content_sha256={**base.issue_content_sha256, **hashes},
        model_manifest=base.model_manifest,
        model_manifest_sha256=base.model_manifest_sha256,
    )


def build_supplement_pairs(
    *,
    supplement_dir: Path,
    frozen: FrozenInputs,
    config: AdvancedTrainingConfig,
    cutoff_policy: str = "all",
) -> tuple[list[TrainingPair], list[DataFailure], dict[str, Any]]:
    adapter_config = ProfileAdapterConfig(
        embedding_dim=int(frozen.base_user.shape[0]),
        hidden_dim=config.hidden_dim,
        residual_scale=config.residual_scale,
        gate_mode=config.gate_mode,
    )
    profiles, profile_failures = load_temporal_profiles(
        supplement_dir / "profiles.jsonl", adapter_config=adapter_config
    )
    outcomes, outcome_failures = load_issue_outcomes(supplement_dir / "outcomes.jsonl")
    profile_by_version = {row.profile_version: row for row in profiles}
    outcome_by_id = {row.outcome_id: row for row in outcomes}
    candidate_rows = {
        str(row["candidate_key"]): row
        for row in _rows(supplement_dir / "candidate_snapshots.jsonl")
    }
    failures: list[DataFailure] = [*profile_failures, *outcome_failures]
    pairs: list[TrainingPair] = []
    policies: Counter[str] = Counter()
    outcome_ids: set[str] = set()
    developer_keys: set[str] = set()

    for plan in _rows(supplement_dir / "pair_plan.jsonl"):
        if cutoff_policy != "all" and plan.get("cutoff_policy") != cutoff_policy:
            continue
        outcome_id = str(plan.get("outcome_id") or "unknown")
        try:
            outcome = outcome_by_id[outcome_id]
            profile = profile_by_version[str(plan["profile_version"])]
            if (
                profile.profile_key != plan["profile_key"]
                or profile.profile_hash != plan["profile_hash"]
            ):
                raise ValueError("pair plan profile identity or hash mismatch")
            if (
                profile.developer_key != outcome.developer_key
                or outcome.developer_key != plan["developer_key"]
            ):
                raise ValueError("pair plan developer identity mismatch")
            if profile.as_of != parse_timestamp(plan["profile_as_of"], "profile_as_of"):
                raise ValueError("pair plan profile cutoff mismatch")
            if not profile.as_of < outcome.interaction_at:
                raise ValueError("supplement profile is not earlier than interaction")
            if outcome.outcome_at > profile.as_of + timedelta(
                days=config.observation_window_days
            ):
                raise ValueError("supplement outcome exceeds the observation window")
            if outcome.outcome_at > config.train_end:
                raise ValueError("supplement outcome is not training-only")
            if outcome.outcome_type not in config.outcome_weights:
                raise ValueError("supplement outcome type is disabled")
            if outcome.verification_status not in config.accepted_verification_statuses:
                raise ValueError("supplement outcome verification is not accepted")
            if outcome.issue_key != plan["positive_issue_key"]:
                raise ValueError("supplement positive Issue mismatch")

            positive_key = str(plan["positive_candidate_key"])
            positive = candidate_rows[positive_key]
            if positive["issue_key"] != outcome.issue_key:
                raise ValueError(
                    "positive candidate does not represent the outcome Issue"
                )
            if positive["as_of"] != plan["profile_as_of"]:
                raise ValueError("positive candidate is not at the profile cutoff")
            if positive_key not in frozen.issue_embeddings:
                raise ValueError("positive candidate embedding is missing")
            if frozen.issue_content_sha256[positive_key] != positive["content_sha256"]:
                raise ValueError("positive candidate embedding content mismatch")

            comparisons = plan.get("comparisons")
            if not isinstance(comparisons, list) or not comparisons:
                raise ValueError("supplement pair plan has no comparisons")
            plan_pairs: list[TrainingPair] = []
            for comparison in comparisons:
                comparison_key = str(comparison["candidate_key"])
                candidate = candidate_rows[comparison_key]
                if candidate["issue_key"] != comparison["issue_key"]:
                    raise ValueError("comparison candidate Issue mismatch")
                if candidate["as_of"] != plan["profile_as_of"]:
                    raise ValueError(
                        "comparison candidate is not at the profile cutoff"
                    )
                if comparison_key not in frozen.issue_embeddings:
                    raise ValueError("comparison candidate embedding is missing")
                if (
                    frozen.issue_content_sha256[comparison_key]
                    != candidate["content_sha256"]
                ):
                    raise ValueError("comparison candidate embedding content mismatch")
                pair_id = hashlib.sha256(
                    (
                        f"{outcome_id}\0{profile.profile_hash}\0{positive_key}"
                        f"\0{comparison_key}"
                    ).encode("utf-8")
                ).hexdigest()
                plan_pairs.append(
                    TrainingPair(
                        pair_id=pair_id,
                        outcome_id=outcome_id,
                        developer_key=outcome.developer_key,
                        profile_key=profile.profile_key,
                        profile_version=profile.profile_version,
                        profile_hash=profile.profile_hash,
                        profile_as_of=profile.as_of,
                        outcome_at=outcome.outcome_at,
                        outcome_type=outcome.outcome_type,
                        # These fields are embedding lookup keys inside the training
                        # loop. Logical Issue keys remain in pair_plan.jsonl.
                        positive_issue_key=positive_key,
                        comparison_issue_key=comparison_key,
                        weight=float(config.outcome_weights[outcome.outcome_type]),
                        values=profile.tensors.values.detach().clone(),
                        observed_mask=profile.tensors.observed_mask.detach().clone(),
                    )
                )
            pairs.extend(plan_pairs)
            policies[str(plan["cutoff_policy"])] += 1
            outcome_ids.add(outcome_id)
            developer_keys.add(outcome.developer_key)
        except (KeyError, TypeError, ValueError) as exc:
            failures.append(
                DataFailure(
                    "supplement_pair_plan", outcome_id, "invalid_pair_plan", str(exc)
                )
            )

    pairs.sort(
        key=lambda row: (row.outcome_at, row.outcome_id, row.comparison_issue_key)
    )
    report = {
        "profiles": len(profiles),
        "outcomes": len(outcomes),
        "eligible_outcomes": len(outcome_ids),
        "developers": len(developer_keys),
        "candidate_versions": len(candidate_rows),
        "pairs": len(pairs),
        "cutoff_policies": dict(sorted(policies.items())),
        "cutoff_policy_filter": cutoff_policy,
        "failures": len(failures),
    }
    return pairs, failures, report


def run(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config)
    config = AdvancedTrainingConfig.from_path(config_path)
    if args.supplement_cutoff_policy != "all":
        config = replace(
            config,
            adapter_version=(
                f"{config.adapter_version}-{args.supplement_cutoff_policy}"
            ),
        )
    base_frozen = load_frozen_inputs(Path(args.model_dir), Path(args.base_issue_cache))
    supplement_dir = Path(args.supplement_dir)
    frozen = _merge_frozen(
        base_frozen, supplement_dir / "supplement_issue_embeddings.npz"
    )
    base_splits, base_failures, base_report = prepare_training_data(
        profiles_path=Path(args.base_profiles),
        outcomes_path=Path(args.base_outcomes),
        candidates_path=Path(args.base_candidates),
        frozen=base_frozen,
        config=config,
    )
    supplement_pairs, supplement_failures, supplement_report = build_supplement_pairs(
        supplement_dir=supplement_dir,
        frozen=frozen,
        config=config,
        cutoff_policy=args.supplement_cutoff_policy,
    )
    if supplement_failures:
        raise RuntimeError(
            f"supplement audit failed with {len(supplement_failures)} failures"
        )
    splits = {
        "train": [*base_splits["train"], *supplement_pairs],
        "validation": list(base_splits["validation"]),
        "test": list(base_splits["test"]),
    }
    report = {
        "schema_version": "advanced-adapter-supplement-audit-v0.1",
        "base": base_report,
        "supplement": supplement_report,
        "combined": {
            "split_pairs": {name: len(rows) for name, rows in splits.items()},
            "split_outcomes": {
                name: len({row.outcome_id for row in rows})
                for name, rows in splits.items()
            },
            "split_developers": {
                name: len({row.developer_key for row in rows})
                for name, rows in splits.items()
            },
            "validation_pairs_unchanged": len(splits["validation"])
            == base_report["split_pairs"]["validation"],
            "test_pairs_unchanged": len(splits["test"])
            == base_report["split_pairs"]["test"],
        },
        "base_failures": len(base_failures),
        "supplement_failures": len(supplement_failures),
    }
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be absent or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    write_audit(
        output_dir, report=report, failures=[*base_failures, *supplement_failures]
    )
    if args.mode == "audit":
        return report

    adapter, metrics = train_adapter(splits, frozen, config, device=args.device)
    _write_json(output_dir / "training_metrics.json", metrics)
    split_manifest = {
        "schema_version": "advanced-adapter-split-v0.1",
        "protocol": "rc1_validation_test_frozen_supplement_train_only",
        "train_end": config.train_end.isoformat(),
        "validation_end": config.validation_end.isoformat(),
        "counts": report["combined"]["split_pairs"],
        "outcomes": report["combined"]["split_outcomes"],
        "developers": report["combined"]["split_developers"],
    }
    _write_json(output_dir / "split_manifest.json", split_manifest)
    supplement_manifest = supplement_dir / "supplement_manifest.json"
    adapter.save_bundle(
        output_dir,
        base_model_version=str(frozen.model_manifest["model_version"]),
        metadata={
            "adapter_version": config.adapter_version,
            "service_track": "growth",
            "base_user_mode": "population_mean",
            "base_model_manifest_sha256": frozen.model_manifest_sha256,
            "experiment_protocol": "rc1_validation_test_frozen_supplement_train_only",
            "supplement_cutoff_policy": args.supplement_cutoff_policy,
            "training_config_sha256": _sha256(config_path),
            "training_inputs": {
                "base_profiles_sha256": _sha256(Path(args.base_profiles)),
                "base_outcomes_sha256": _sha256(Path(args.base_outcomes)),
                "base_candidates_sha256": _sha256(Path(args.base_candidates)),
                "base_issue_cache_sha256": _sha256(Path(args.base_issue_cache)),
                "supplement_manifest_sha256": _sha256(supplement_manifest),
                "supplement_issue_cache_sha256": _sha256(
                    supplement_dir / "supplement_issue_embeddings.npz"
                ),
            },
            "random_seed": config.seed,
            "data_seed": config.data_seed,
            "observation_window_days": config.observation_window_days,
            "split_manifest_sha256": _sha256(output_dir / "split_manifest.json"),
            "training_metrics_sha256": _sha256(output_dir / "training_metrics.json"),
            "audit_report_sha256": _sha256(output_dir / "audit_report.json"),
            "failures_sha256": _sha256(output_dir / "failures.jsonl"),
            "training_config": {
                **asdict(config),
                "train_end": config.train_end.isoformat(),
                "validation_end": config.validation_end.isoformat(),
            },
        },
    )
    return {**report, "metrics": metrics}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("audit", "train"), default="audit")
    parser.add_argument("--config", required=True)
    parser.add_argument("--base-profiles", required=True)
    parser.add_argument("--base-outcomes", required=True)
    parser.add_argument("--base-candidates", required=True)
    parser.add_argument("--base-issue-cache", required=True)
    parser.add_argument("--supplement-dir", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--supplement-cutoff-policy",
        choices=("all", "calendar_quarter", "pre_interaction_minus_1s"),
        default="all",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(json.dumps(run(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
