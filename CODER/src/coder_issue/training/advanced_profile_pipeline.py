"""Auditable training pipeline for the advanced 23-feature profile adapter."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
from typing import Any, Mapping, Sequence

import numpy as np
import torch
from torch.nn import functional as F

from coder_issue.models.profile import ProfileAdapterConfig, ProfileResidualAdapter
from coder_issue.training.advanced_profile_data import (
    DataFailure,
    PairBuildConfig,
    TrainingPair,
    build_training_pairs,
    load_historical_candidates,
    load_issue_outcomes,
    load_temporal_profiles,
    normalize_issue_key,
    parse_timestamp,
    split_pairs_by_time,
)


CONFIG_SCHEMA_VERSION = "advanced-adapter-training-config-v0.1"


@dataclass(frozen=True, slots=True)
class AdvancedTrainingConfig:
    adapter_version: str
    seed: int
    data_seed: int
    hidden_dim: int
    residual_scale: float
    gate_mode: str
    feature_dropout: float
    residual_l2: float
    hard_negative_weight: float
    hard_negative_temperature: float
    observation_window_days: int
    negative_samples_per_positive: int
    outcome_weights: Mapping[str, float]
    accepted_verification_statuses: tuple[str, ...]
    train_end: datetime
    validation_end: datetime
    epochs: int
    batch_size: int
    learning_rate: float
    weight_decay: float
    patience: int

    @classmethod
    def from_path(cls, path: Path) -> "AdvancedTrainingConfig":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
            raise ValueError(
                "unsupported advanced adapter training config schema_version"
            )
        statuses = payload.get("accepted_verification_statuses", ["verified"])
        if (
            not isinstance(statuses, list)
            or not statuses
            or any(not isinstance(item, str) or not item for item in statuses)
        ):
            raise ValueError(
                "accepted_verification_statuses must be a non-empty string list"
            )
        weights = payload.get("outcome_weights")
        if not isinstance(weights, dict):
            raise ValueError("outcome_weights must be an object")
        config = cls(
            adapter_version=str(payload["adapter_version"]),
            seed=int(payload["seed"]),
            data_seed=int(payload.get("data_seed", payload["seed"])),
            hidden_dim=int(payload["hidden_dim"]),
            residual_scale=float(payload["residual_scale"]),
            gate_mode=str(payload.get("gate_mode", "none")),
            feature_dropout=float(payload.get("feature_dropout", 0.0)),
            residual_l2=float(payload.get("residual_l2", 0.0)),
            hard_negative_weight=float(payload.get("hard_negative_weight", 0.0)),
            hard_negative_temperature=float(
                payload.get("hard_negative_temperature", 1.0)
            ),
            observation_window_days=int(payload["observation_window_days"]),
            negative_samples_per_positive=int(payload["negative_samples_per_positive"]),
            outcome_weights={str(key): float(value) for key, value in weights.items()},
            accepted_verification_statuses=tuple(statuses),
            train_end=parse_timestamp(payload["train_end"], "train_end"),
            validation_end=parse_timestamp(payload["validation_end"], "validation_end"),
            epochs=int(payload["epochs"]),
            batch_size=int(payload["batch_size"]),
            learning_rate=float(payload["learning_rate"]),
            weight_decay=float(payload["weight_decay"]),
            patience=int(payload["patience"]),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.adapter_version:
            raise ValueError("adapter_version must not be empty")
        if self.hidden_dim <= 0 or self.epochs <= 0 or self.batch_size <= 0:
            raise ValueError("hidden_dim, epochs and batch_size must be positive")
        if self.residual_scale < 0 or self.learning_rate <= 0 or self.weight_decay < 0:
            raise ValueError("invalid residual_scale, learning_rate or weight_decay")
        if self.gate_mode not in {"none", "coverage"}:
            raise ValueError("gate_mode must be none or coverage")
        if not 0.0 <= self.feature_dropout < 1.0:
            raise ValueError("feature_dropout must be within [0, 1)")
        if self.residual_l2 < 0 or self.hard_negative_weight < 0:
            raise ValueError(
                "residual_l2 and hard_negative_weight must be non-negative"
            )
        if self.hard_negative_temperature <= 0:
            raise ValueError("hard_negative_temperature must be positive")
        if self.patience <= 0:
            raise ValueError("patience must be positive")
        PairBuildConfig(
            observation_window_days=self.observation_window_days,
            negative_samples_per_positive=self.negative_samples_per_positive,
            seed=self.seed,
            outcome_weights=self.outcome_weights,
            accepted_verification_statuses=self.accepted_verification_statuses,
        )
        if self.validation_end <= self.train_end:
            raise ValueError("validation_end must be later than train_end")


@dataclass(frozen=True, slots=True)
class FrozenInputs:
    base_user: torch.Tensor
    issue_embeddings: Mapping[str, torch.Tensor]
    issue_content_sha256: Mapping[str, str]
    model_manifest: Mapping[str, Any]
    model_manifest_sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)


def load_frozen_inputs(model_dir: Path, issue_cache: Path) -> FrozenInputs:
    model_dir = Path(model_dir)
    manifest_path = model_dir / "model_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("variant") != "coder_issue_repo":
        raise ValueError("advanced adapter requires a coder_issue_repo base model")
    with np.load(model_dir / "embeddings.npz") as payload:
        users = np.asarray(payload["user_embeddings"], dtype=np.float32)
    if users.ndim != 2 or not users.shape[0] or not users.shape[1]:
        raise ValueError("base user embeddings must be a non-empty matrix")
    base_user = torch.from_numpy(users.mean(axis=0).astype(np.float32, copy=False))

    with np.load(issue_cache) as payload:
        keys = [
            normalize_issue_key(item)
            for item in payload["issue_keys"].astype(str).tolist()
        ]
        vectors = np.asarray(payload["issue_embeddings"], dtype=np.float32)
        if "content_sha256" not in payload.files:
            raise ValueError("historical issue cache must contain content_sha256")
        content_hashes = payload["content_sha256"].astype(str).tolist()
    if vectors.ndim != 2 or len(keys) != vectors.shape[0]:
        raise ValueError("issue cache keys and embeddings are inconsistent")
    if len(content_hashes) != len(keys) or any(
        len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in content_hashes
    ):
        raise ValueError("issue cache content_sha256 values are inconsistent")
    if vectors.shape[1] != users.shape[1]:
        raise ValueError("issue cache dimension does not match base user embeddings")
    if len(keys) != len(set(keys)):
        raise ValueError("issue cache contains duplicate issue keys")
    if not np.isfinite(vectors).all():
        raise ValueError("issue cache contains non-finite embeddings")
    return FrozenInputs(
        base_user=base_user,
        issue_embeddings={
            key: torch.from_numpy(vector.copy()) for key, vector in zip(keys, vectors)
        },
        issue_content_sha256=dict(zip(keys, content_hashes, strict=True)),
        model_manifest=manifest,
        model_manifest_sha256=_sha256(manifest_path),
    )


def prepare_training_data(
    *,
    profiles_path: Path,
    outcomes_path: Path,
    candidates_path: Path,
    frozen: FrozenInputs,
    config: AdvancedTrainingConfig,
) -> tuple[dict[str, list[TrainingPair]], list[DataFailure], dict[str, Any]]:
    adapter_config = ProfileAdapterConfig(
        embedding_dim=int(frozen.base_user.shape[0]),
        hidden_dim=config.hidden_dim,
        residual_scale=config.residual_scale,
        gate_mode=config.gate_mode,
    )
    profiles, profile_failures = load_temporal_profiles(
        profiles_path, adapter_config=adapter_config
    )
    outcomes, outcome_failures = load_issue_outcomes(outcomes_path)
    candidates, candidate_failures = load_historical_candidates(candidates_path)
    missing_embedding_failures = [
        DataFailure(
            "issue_cache",
            candidate.issue_key,
            "issue_embedding_missing",
            "historical candidate has no frozen issue embedding",
        )
        for candidate in candidates
        if candidate.issue_key not in frozen.issue_embeddings
    ]
    content_failures = [
        DataFailure(
            "issue_cache",
            candidate.issue_key,
            "issue_embedding_content_mismatch",
            "candidate content_sha256 does not match the frozen issue embedding cache",
        )
        for candidate in candidates
        if candidate.issue_key in frozen.issue_embeddings
        and frozen.issue_content_sha256[candidate.issue_key] != candidate.content_sha256
    ]
    valid_encoded_keys = {
        candidate.issue_key
        for candidate in candidates
        if candidate.issue_key in frozen.issue_embeddings
        and frozen.issue_content_sha256[candidate.issue_key] == candidate.content_sha256
    }
    pairs, pair_failures, counters = build_training_pairs(
        profiles,
        outcomes,
        candidates,
        encoded_issue_keys=valid_encoded_keys,
        config=PairBuildConfig(
            observation_window_days=config.observation_window_days,
            negative_samples_per_positive=config.negative_samples_per_positive,
            seed=config.data_seed,
            outcome_weights=config.outcome_weights,
            accepted_verification_statuses=config.accepted_verification_statuses,
        ),
    )
    splits = split_pairs_by_time(
        pairs, train_end=config.train_end, validation_end=config.validation_end
    )
    counters.update(
        {
            "encoded_candidates": len(valid_encoded_keys),
            "candidate_embedding_missing": len(missing_embedding_failures),
            "candidate_content_mismatch": len(content_failures),
        }
    )
    failures = [
        *profile_failures,
        *outcome_failures,
        *candidate_failures,
        *missing_embedding_failures,
        *content_failures,
        *pair_failures,
    ]
    report = {
        "schema_version": "advanced-adapter-audit-v0.1",
        **counters,
        "failures": len(failures),
        "split_pairs": {name: len(rows) for name, rows in splits.items()},
        "split_outcomes": {
            name: len({row.outcome_id for row in rows}) for name, rows in splits.items()
        },
        "split_developers": {
            name: len({row.developer_key for row in rows})
            for name, rows in splits.items()
        },
    }
    return splits, failures, report


def _batch_indices(
    length: int, batch_size: int, *, seed: int, epoch: int
) -> list[list[int]]:
    generator = random.Random(seed + epoch)
    indices = list(range(length))
    generator.shuffle(indices)
    return [
        indices[start : start + batch_size] for start in range(0, length, batch_size)
    ]


def _batch_tensors(
    rows: Sequence[TrainingPair],
    frozen: FrozenInputs,
    *,
    device: torch.device,
) -> tuple[torch.Tensor, ...]:
    return (
        torch.stack([row.values for row in rows]).to(device),
        torch.stack([row.observed_mask for row in rows]).to(device),
        torch.stack(
            [frozen.issue_embeddings[row.positive_issue_key] for row in rows]
        ).to(device),
        torch.stack(
            [frozen.issue_embeddings[row.comparison_issue_key] for row in rows]
        ).to(device),
        torch.tensor([row.weight for row in rows], dtype=torch.float32, device=device),
    )


def _weighted_bpr(
    user: torch.Tensor,
    positive: torch.Tensor,
    comparison: torch.Tensor,
    weights: torch.Tensor,
    *,
    hard_negative_weight: float = 0.0,
    hard_negative_temperature: float = 1.0,
) -> torch.Tensor:
    positive_scores = (user * positive).sum(dim=-1)
    comparison_scores = (user * comparison).sum(dim=-1)
    margin = positive_scores - comparison_scores
    if hard_negative_weight:
        difficulty = torch.sigmoid(-margin.detach() / hard_negative_temperature)
        weights = weights * (1.0 + hard_negative_weight * difficulty)
    return (-F.logsigmoid(margin) * weights).sum() / weights.sum()


def evaluate_pairs(
    adapter: ProfileResidualAdapter | None,
    rows: Sequence[TrainingPair],
    frozen: FrozenInputs,
    *,
    train_developers: set[str] | None = None,
    device: torch.device,
) -> dict[str, Any]:
    if not rows:
        return {"pairs": 0, "outcomes": 0}
    groups: dict[str, list[TrainingPair]] = defaultdict(list)
    for row in rows:
        groups[row.outcome_id].append(row)
    ranks: list[int] = []
    pair_correct = 0
    pair_total = 0
    group_rows: list[dict[str, Any]] = []
    if adapter is not None:
        adapter.eval()
    with torch.inference_mode():
        for outcome_id, group in sorted(groups.items()):
            first = group[0]
            values = first.values.unsqueeze(0).to(device)
            observed = first.observed_mask.unsqueeze(0).to(device)
            base_user = frozen.base_user.unsqueeze(0).to(device)
            user = (
                adapter.fuse(base_user, values, observed).squeeze(0)
                if adapter is not None
                else base_user.squeeze(0)
            )
            positive_score = float(
                torch.dot(
                    user, frozen.issue_embeddings[first.positive_issue_key].to(device)
                ).item()
            )
            comparison_scores = [
                float(
                    torch.dot(
                        user,
                        frozen.issue_embeddings[item.comparison_issue_key].to(device),
                    ).item()
                )
                for item in group
            ]
            rank = 1 + sum(score >= positive_score for score in comparison_scores)
            ranks.append(rank)
            pair_correct += sum(positive_score > score for score in comparison_scores)
            pair_total += len(comparison_scores)
            group_rows.append(
                {
                    "outcome_id": outcome_id,
                    "developer_key": first.developer_key,
                    "rank": rank,
                    "candidate_count": len(comparison_scores) + 1,
                    "unseen_developer": bool(
                        train_developers is not None
                        and first.developer_key not in train_developers
                    ),
                }
            )

    metrics: dict[str, Any] = {
        "pairs": pair_total,
        "outcomes": len(ranks),
        "pair_accuracy": pair_correct / pair_total,
        "mrr": sum(1.0 / rank for rank in ranks) / len(ranks),
        "mean_rank": sum(ranks) / len(ranks),
    }
    for cutoff in (5, 10, 20):
        hit = sum(rank <= cutoff for rank in ranks) / len(ranks)
        ndcg = sum(
            (1.0 / np.log2(rank + 1)) if rank <= cutoff else 0.0 for rank in ranks
        ) / len(ranks)
        metrics[f"recall@{cutoff}"] = hit
        metrics[f"hit@{cutoff}"] = hit
        metrics[f"mrr@{cutoff}"] = sum(
            (1.0 / rank) if rank <= cutoff else 0.0 for rank in ranks
        ) / len(ranks)
        metrics[f"ndcg@{cutoff}"] = float(ndcg)
    if train_developers is not None:
        for label, selected in (
            (
                "seen_developer",
                [row for row in group_rows if not row["unseen_developer"]],
            ),
            (
                "unseen_developer",
                [row for row in group_rows if row["unseen_developer"]],
            ),
        ):
            selected_ranks = [int(row["rank"]) for row in selected]
            metrics[label] = {
                "outcomes": len(selected_ranks),
                "mrr": (
                    sum(1.0 / rank for rank in selected_ranks) / len(selected_ranks)
                    if selected_ranks
                    else None
                ),
            }
    return metrics


def train_adapter(
    splits: Mapping[str, Sequence[TrainingPair]],
    frozen: FrozenInputs,
    config: AdvancedTrainingConfig,
    *,
    device: str | torch.device = "cpu",
    evaluate_test: bool = True,
) -> tuple[ProfileResidualAdapter, dict[str, Any]]:
    if not splits["train"]:
        raise ValueError("training split has no eligible pairs")
    if not splits["validation"]:
        raise ValueError("validation split has no eligible pairs")
    _set_seed(config.seed)
    target_device = torch.device(device)
    adapter = ProfileResidualAdapter(
        ProfileAdapterConfig(
            embedding_dim=int(frozen.base_user.shape[0]),
            hidden_dim=config.hidden_dim,
            residual_scale=config.residual_scale,
            gate_mode=config.gate_mode,
        )
    ).to(target_device)
    optimizer = torch.optim.AdamW(
        adapter.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    best_state: dict[str, torch.Tensor] | None = None
    best_validation_mrr = float("-inf")
    stale_epochs = 0
    history: list[dict[str, Any]] = []
    train_developers = {row.developer_key for row in splits["train"]}

    for epoch in range(1, config.epochs + 1):
        adapter.train()
        total_loss = 0.0
        total_rows = 0
        for indices in _batch_indices(
            len(splits["train"]), config.batch_size, seed=config.seed, epoch=epoch
        ):
            rows = [splits["train"][index] for index in indices]
            values, observed, positive, comparison, weights = _batch_tensors(
                rows, frozen, device=target_device
            )
            if config.feature_dropout:
                keep = (
                    torch.rand(observed.shape, device=target_device)
                    >= config.feature_dropout
                )
                observed = observed & keep
            optimizer.zero_grad(set_to_none=True)
            base = frozen.base_user.to(target_device).expand(len(rows), -1)
            user = adapter.fuse(base, values, observed)
            loss = _weighted_bpr(
                user,
                positive,
                comparison,
                weights,
                hard_negative_weight=config.hard_negative_weight,
                hard_negative_temperature=config.hard_negative_temperature,
            )
            if config.residual_l2:
                residual = user - base
                loss = loss + config.residual_l2 * residual.square().sum(dim=-1).mean()
            loss.backward()
            optimizer.step()
            total_loss += float(loss.item()) * len(rows)
            total_rows += len(rows)
        validation = evaluate_pairs(
            adapter,
            splits["validation"],
            frozen,
            train_developers=train_developers,
            device=target_device,
        )
        epoch_row = {
            "epoch": epoch,
            "train_loss": total_loss / total_rows,
            "validation": validation,
        }
        history.append(epoch_row)
        validation_mrr = float(validation["mrr"])
        if validation_mrr > best_validation_mrr:
            best_validation_mrr = validation_mrr
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in adapter.state_dict().items()
            }
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= config.patience:
                break
    if best_state is None:
        raise RuntimeError("training did not produce a checkpoint")
    adapter.load_state_dict(best_state)
    adapter.to("cpu").eval()
    metrics = {
        "schema_version": "advanced-adapter-training-metrics-v0.1",
        "best_validation_mrr": best_validation_mrr,
        "epochs_completed": len(history),
        "history": history,
    }
    evaluated_splits = (
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
        for name in evaluated_splits
    }
    profiled = {
        name: evaluate_pairs(
            adapter,
            splits[name],
            frozen,
            train_developers=train_developers if name != "train" else None,
            device=torch.device("cpu"),
        )
        for name in evaluated_splits
    }
    metrics["coder_original"] = original
    metrics["coder_profile"] = profiled
    metrics["paired_delta"] = {
        name: {
            key: (
                float(profiled[name][key]) - float(original[name][key])
                if profiled[name].get(key) is not None
                and original[name].get(key) is not None
                else None
            )
            for key in ("pair_accuracy", "mrr", "mean_rank")
            if key in profiled[name] or key in original[name]
        }
        for name in evaluated_splits
    }
    return adapter, metrics


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_audit(
    output_dir: Path,
    *,
    report: Mapping[str, Any],
    failures: Sequence[DataFailure],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "audit_report.json", report)
    with (output_dir / "failures.jsonl").open("w", encoding="utf-8") as handle:
        for failure in failures:
            handle.write(
                json.dumps(failure.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            )


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config)
    config = AdvancedTrainingConfig.from_path(config_path)
    frozen = load_frozen_inputs(Path(args.model_dir), Path(args.issue_cache))
    splits, failures, report = prepare_training_data(
        profiles_path=Path(args.profiles),
        outcomes_path=Path(args.outcomes),
        candidates_path=Path(args.candidates),
        frozen=frozen,
        config=config,
    )
    output_dir = Path(args.output_dir)
    if any(
        (output_dir / name).exists()
        for name in ("weights.pt", "adapter_manifest.json", "_SUCCESS")
    ):
        raise FileExistsError(
            "output_dir already contains an adapter bundle; use a new run directory"
        )
    write_audit(output_dir, report=report, failures=failures)
    if args.mode == "audit":
        return report

    adapter, metrics = train_adapter(splits, frozen, config, device=args.device)
    _write_json(output_dir / "training_metrics.json", metrics)
    split_manifest = {
        "schema_version": "advanced-adapter-split-v0.1",
        "train_end": config.train_end.isoformat(),
        "validation_end": config.validation_end.isoformat(),
        "counts": report["split_pairs"],
        "outcomes": report["split_outcomes"],
        "developers": report["split_developers"],
    }
    _write_json(output_dir / "split_manifest.json", split_manifest)
    adapter.save_bundle(
        output_dir,
        base_model_version=str(frozen.model_manifest["model_version"]),
        metadata={
            "adapter_version": config.adapter_version,
            "service_track": "growth",
            "base_user_mode": "population_mean",
            "base_model_manifest_sha256": frozen.model_manifest_sha256,
            "training_config_sha256": _sha256(config_path),
            "training_inputs": {
                "profiles_sha256": _sha256(Path(args.profiles)),
                "outcomes_sha256": _sha256(Path(args.outcomes)),
                "candidates_sha256": _sha256(Path(args.candidates)),
                "issue_cache_sha256": _sha256(Path(args.issue_cache)),
            },
            "random_seed": config.seed,
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
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--outcomes", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--issue-cache", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="cpu")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_pipeline(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
