"""Versioned, time-safe inputs for advanced profile-adapter training.

The module deliberately separates evidence-derived profile snapshots from
online user-confirmed snapshots.  It also treats unselected historical issues
as sampled, unobserved comparisons rather than proven negative feedback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import random
import re
from typing import Any, Iterable, Mapping, Sequence

import pyarrow.parquet as pq
import torch

from coder_issue.models.profile import (
    PROFILE_TRAINING_SCHEMA_VERSION,
    ProfileAdapterConfig,
    ProfileTensors,
    tensorize_training_profile,
)


OUTCOME_SCHEMA_VERSION = "advanced-issue-outcome-v0.1"
CANDIDATE_SCHEMA_VERSION = "advanced-historical-candidate-v0.1"
_ISSUE_KEY = re.compile(r"^[^/#\s]+/[^#\s]+#[1-9][0-9]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class DataFailure:
    source: str
    record_id: str
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "record_id": self.record_id,
            "code": self.code,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class TemporalProfile:
    developer_key: str
    profile_key: str
    profile_version: str
    profile_hash: str
    as_of: datetime
    tensors: ProfileTensors


@dataclass(frozen=True, slots=True)
class IssueOutcome:
    outcome_id: str
    developer_key: str
    issue_key: str
    outcome_type: str
    interaction_at: datetime
    outcome_at: datetime
    verification_status: str
    source_url: str


@dataclass(frozen=True, slots=True)
class HistoricalCandidate:
    issue_key: str
    available_from: datetime
    available_until: datetime | None
    content_sha256: str

    def available_at(self, moment: datetime) -> bool:
        return self.available_from <= moment and (
            self.available_until is None or moment < self.available_until
        )


@dataclass(frozen=True, slots=True)
class TrainingPair:
    pair_id: str
    outcome_id: str
    developer_key: str
    profile_key: str
    profile_version: str
    profile_hash: str
    profile_as_of: datetime
    outcome_at: datetime
    outcome_type: str
    positive_issue_key: str
    comparison_issue_key: str
    weight: float
    values: torch.Tensor
    observed_mask: torch.Tensor


@dataclass(frozen=True, slots=True)
class PairBuildConfig:
    observation_window_days: int
    negative_samples_per_positive: int
    seed: int
    outcome_weights: Mapping[str, float]
    accepted_verification_statuses: tuple[str, ...] = ("verified",)

    def __post_init__(self) -> None:
        if self.observation_window_days <= 0:
            raise ValueError("observation_window_days must be positive")
        if self.negative_samples_per_positive <= 0:
            raise ValueError("negative_samples_per_positive must be positive")
        if not self.outcome_weights:
            raise ValueError("outcome_weights must not be empty")
        if any(not key or value <= 0 for key, value in self.outcome_weights.items()):
            raise ValueError("outcome weights must use non-empty keys and positive values")


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def normalize_issue_key(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("issue_key must be a string")
    normalized = value.strip().casefold()
    if not _ISSUE_KEY.fullmatch(normalized):
        raise ValueError("issue_key must use owner/repository#number")
    return normalized


def normalize_developer_key(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("developer_key must be a non-empty verified identity")
    return value.strip().casefold()


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _records(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.suffix.casefold() == ".parquet":
        return [dict(row) for row in pq.read_table(path).to_pylist()]
    if path.suffix.casefold() not in {".jsonl", ".ndjson"}:
        raise ValueError(f"unsupported input format: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            rows.append(row)
    return rows


def load_temporal_profiles(
    path: Path,
    *,
    adapter_config: ProfileAdapterConfig,
) -> tuple[list[TemporalProfile], list[DataFailure]]:
    profiles: list[TemporalProfile] = []
    failures: list[DataFailure] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(_records(path), 1):
        record_id = str(row.get("profile_version") or index)
        try:
            if row.get("schema_version") != PROFILE_TRAINING_SCHEMA_VERSION:
                raise ValueError("unsupported training profile schema_version")
            tensors = tensorize_training_profile(row, config=adapter_config)
            developer_key = normalize_developer_key(tensors.developer_key)
            profile_hash = _required_text(row, "profile_hash").casefold()
            if not _SHA256.fullmatch(profile_hash):
                raise ValueError("profile_hash must be a lowercase SHA-256")
            identity = (tensors.profile_key, tensors.profile_version)
            if identity in seen:
                raise ValueError("duplicate profile_key/profile_version")
            profile = TemporalProfile(
                developer_key=developer_key,
                profile_key=tensors.profile_key,
                profile_version=tensors.profile_version,
                profile_hash=profile_hash,
                as_of=parse_timestamp(row.get("as_of"), "as_of"),
                tensors=tensors,
            )
            seen.add(identity)
            profiles.append(profile)
        except (TypeError, ValueError) as exc:
            failures.append(DataFailure(str(path), record_id, "invalid_profile", str(exc)))
    profiles.sort(key=lambda item: (item.developer_key, item.as_of, item.profile_version))
    return profiles, failures


def load_issue_outcomes(path: Path) -> tuple[list[IssueOutcome], list[DataFailure]]:
    outcomes: list[IssueOutcome] = []
    failures: list[DataFailure] = []
    seen: set[str] = set()
    for index, row in enumerate(_records(path), 1):
        record_id = str(row.get("outcome_id") or index)
        try:
            if row.get("schema_version") != OUTCOME_SCHEMA_VERSION:
                raise ValueError("unsupported outcome schema_version")
            outcome_id = _required_text(row, "outcome_id")
            if outcome_id in seen:
                raise ValueError("duplicate outcome_id")
            outcome = IssueOutcome(
                outcome_id=outcome_id,
                developer_key=normalize_developer_key(row.get("developer_key")),
                issue_key=normalize_issue_key(row.get("issue_key")),
                outcome_type=_required_text(row, "outcome_type"),
                interaction_at=parse_timestamp(row.get("interaction_at"), "interaction_at"),
                outcome_at=parse_timestamp(row.get("outcome_at"), "outcome_at"),
                verification_status=_required_text(row, "verification_status"),
                source_url=_required_text(row, "source_url"),
            )
            if outcome.outcome_at < outcome.interaction_at:
                raise ValueError("outcome_at must not be earlier than interaction_at")
            seen.add(outcome_id)
            outcomes.append(outcome)
        except (TypeError, ValueError) as exc:
            failures.append(DataFailure(str(path), record_id, "invalid_outcome", str(exc)))
    outcomes.sort(key=lambda item: (item.outcome_at, item.outcome_id))
    return outcomes, failures


def load_historical_candidates(
    path: Path,
) -> tuple[list[HistoricalCandidate], list[DataFailure]]:
    candidates: list[HistoricalCandidate] = []
    failures: list[DataFailure] = []
    seen: set[str] = set()
    for index, row in enumerate(_records(path), 1):
        record_id = str(row.get("issue_key") or index)
        try:
            if row.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
                raise ValueError("unsupported candidate schema_version")
            issue_key = normalize_issue_key(row.get("issue_key"))
            if issue_key in seen:
                raise ValueError("duplicate issue_key")
            content_sha256 = _required_text(row, "content_sha256").casefold()
            if not _SHA256.fullmatch(content_sha256):
                raise ValueError("content_sha256 must be a lowercase SHA-256")
            available_from = parse_timestamp(row.get("available_from"), "available_from")
            raw_until = row.get("available_until")
            available_until = (
                parse_timestamp(raw_until, "available_until") if raw_until is not None else None
            )
            if available_until is not None and available_until <= available_from:
                raise ValueError("available_until must be later than available_from")
            candidate = HistoricalCandidate(
                issue_key=issue_key,
                available_from=available_from,
                available_until=available_until,
                content_sha256=content_sha256,
            )
            seen.add(issue_key)
            candidates.append(candidate)
        except (TypeError, ValueError) as exc:
            failures.append(DataFailure(str(path), record_id, "invalid_candidate", str(exc)))
    candidates.sort(key=lambda item: item.issue_key)
    return candidates, failures


def _repository(issue_key: str) -> str:
    return issue_key.rsplit("#", 1)[0]


def _stable_sample(items: Sequence[str], count: int, *, seed: int, key: str) -> list[str]:
    digest = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], 16)
    generator = random.Random(seed ^ digest)
    selected = list(items)
    generator.shuffle(selected)
    return selected[:count]


def build_training_pairs(
    profiles: Sequence[TemporalProfile],
    outcomes: Sequence[IssueOutcome],
    candidates: Sequence[HistoricalCandidate],
    *,
    encoded_issue_keys: Iterable[str],
    config: PairBuildConfig,
) -> tuple[list[TrainingPair], list[DataFailure], dict[str, int]]:
    """Join snapshots to later outcomes and sample time-valid comparisons."""

    encoded = {normalize_issue_key(key) for key in encoded_issue_keys}
    candidate_by_key = {item.issue_key: item for item in candidates}
    profiles_by_developer: dict[str, list[TemporalProfile]] = {}
    for profile in profiles:
        profiles_by_developer.setdefault(profile.developer_key, []).append(profile)
    positive_by_developer: dict[str, list[IssueOutcome]] = {}
    for outcome in outcomes:
        positive_by_developer.setdefault(outcome.developer_key, []).append(outcome)

    pairs: list[TrainingPair] = []
    failures: list[DataFailure] = []
    counters = {
        "profiles": len(profiles),
        "outcomes": len(outcomes),
        "candidates": len(candidates),
        "eligible_outcomes": 0,
        "pairs": 0,
    }
    window = timedelta(days=config.observation_window_days)

    for outcome in outcomes:
        try:
            if outcome.outcome_type not in config.outcome_weights:
                raise ValueError("outcome_type is not enabled by training configuration")
            if outcome.verification_status not in config.accepted_verification_statuses:
                raise ValueError("outcome verification_status is not accepted")
            possible_profiles = [
                profile
                for profile in profiles_by_developer.get(outcome.developer_key, ())
                if profile.as_of < outcome.interaction_at
                and outcome.outcome_at <= profile.as_of + window
            ]
            if not possible_profiles:
                raise ValueError("no earlier profile inside the observation window")
            profile = max(possible_profiles, key=lambda item: (item.as_of, item.profile_version))
            positive_candidate = candidate_by_key.get(outcome.issue_key)
            if positive_candidate is None:
                raise ValueError("positive issue is absent from historical candidates")
            if not positive_candidate.available_at(profile.as_of):
                raise ValueError("positive issue was not available at profile_as_of")
            if outcome.issue_key not in encoded:
                raise ValueError("positive issue has no frozen embedding")

            future_positives = {
                item.issue_key
                for item in positive_by_developer.get(outcome.developer_key, ())
                if profile.as_of < item.outcome_at <= profile.as_of + window
            }
            available = [
                item.issue_key
                for item in candidates
                if item.issue_key in encoded
                and item.issue_key not in future_positives
                and item.available_at(profile.as_of)
            ]
            same_repository = [
                key for key in available if _repository(key) == _repository(outcome.issue_key)
            ]
            remaining = [key for key in available if key not in set(same_repository)]
            ordered_pool = [*same_repository, *remaining]
            comparisons = _stable_sample(
                ordered_pool,
                config.negative_samples_per_positive,
                seed=config.seed,
                key=outcome.outcome_id,
            )
            if len(comparisons) < config.negative_samples_per_positive:
                raise ValueError("not enough time-valid encoded comparison issues")
            counters["eligible_outcomes"] += 1
            for comparison in comparisons:
                pair_id = hashlib.sha256(
                    f"{outcome.outcome_id}\0{profile.profile_hash}\0{comparison}".encode("utf-8")
                ).hexdigest()
                pairs.append(
                    TrainingPair(
                        pair_id=pair_id,
                        outcome_id=outcome.outcome_id,
                        developer_key=outcome.developer_key,
                        profile_key=profile.profile_key,
                        profile_version=profile.profile_version,
                        profile_hash=profile.profile_hash,
                        profile_as_of=profile.as_of,
                        outcome_at=outcome.outcome_at,
                        outcome_type=outcome.outcome_type,
                        positive_issue_key=outcome.issue_key,
                        comparison_issue_key=comparison,
                        weight=float(config.outcome_weights[outcome.outcome_type]),
                        values=profile.tensors.values.detach().clone(),
                        observed_mask=profile.tensors.observed_mask.detach().clone(),
                    )
                )
        except ValueError as exc:
            failures.append(
                DataFailure("pair_builder", outcome.outcome_id, "ineligible_outcome", str(exc))
            )
    counters["pairs"] = len(pairs)
    pairs.sort(key=lambda item: (item.outcome_at, item.outcome_id, item.comparison_issue_key))
    return pairs, failures, counters


def split_pairs_by_time(
    pairs: Sequence[TrainingPair],
    *,
    train_end: datetime,
    validation_end: datetime,
) -> dict[str, list[TrainingPair]]:
    if validation_end <= train_end:
        raise ValueError("validation_end must be later than train_end")
    result = {"train": [], "validation": [], "test": []}
    for pair in pairs:
        if pair.outcome_at <= train_end:
            result["train"].append(pair)
        elif pair.outcome_at <= validation_end:
            result["validation"].append(pair)
        else:
            result["test"].append(pair)
    return result
