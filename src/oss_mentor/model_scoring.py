"""Transport-neutral model scoring port used by the recommendation service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True, slots=True)
class ModelScore:
    task_candidate_id: int
    score: float
    coverage_state: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("model score must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ModelScoreBatch:
    scores: tuple[ModelScore, ...]
    model_version: str
    dataset_version: str
    graph_version: str
    encoder_version: str
    profile_mode: str
    warnings: tuple[str, ...] = ()


@runtime_checkable
class ModelScorer(Protocol):
    """B supplies profile/developer identity and eligible candidates to this port."""

    def score(
        self,
        *,
        profile: Any,
        developer_key: str | None,
        candidates: Sequence[dict[str, Any]],
    ) -> ModelScoreBatch: ...
