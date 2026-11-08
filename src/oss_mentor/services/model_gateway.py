"""Model gateway status for the HTTP layer.

The midterm plan requires the UI to distinguish the real CODER variants from
the legacy rule engine.  The repository currently has no trained model package
registered, so the gateway reports ``rule_legacy`` and a ``compat`` status.
When member C registers a real package, only this module needs to change.
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict
from typing import Any

from oss_mentor.contracts_midterm import ModelMetadata
from oss_mentor.matching import MATCH_VERSION_V3
from oss_mentor.model_scoring import ModelScore, ModelScoreBatch


class ModelUnavailable(RuntimeError):
    """Raised when a request requires a model that is not registered."""


ADVANCED_ADAPTER_MODES = frozenset({"off", "shadow", "serve"})


class ProfileAwareScorer:
    """Pass one confirmed enhanced profile to an otherwise unchanged scorer."""

    advanced_adapter_mode = "serve"

    def __init__(self, base_scorer: Any, profile_snapshot: dict[str, Any]) -> None:
        self.base_scorer = base_scorer
        self.profile_snapshot = profile_snapshot

    def score(self, *, profile, developer_key, candidates):
        del profile
        return self.base_scorer.score(
            profile=self.profile_snapshot,
            developer_key=developer_key,
            candidates=candidates,
        )


class ShadowProfileScorer:
    """Run the paired profile experiment while returning the original ranking."""

    advanced_adapter_mode = "shadow"

    def __init__(self, base_scorer: Any, profile_snapshot: dict[str, Any]) -> None:
        self.base_scorer = base_scorer
        self.profile_snapshot = profile_snapshot
        self.last_paired: Any | None = None
        self.profile_mode: str | None = None

    @staticmethod
    def _normalize_scores(raw_values: list[float]) -> list[float]:
        if not raw_values:
            return []
        center = sum(raw_values) / len(raw_values)
        variance = sum((value - center) ** 2 for value in raw_values) / len(raw_values)
        scale = math.sqrt(variance)
        if scale == 0.0:
            return [0.5 for _ in raw_values]
        return [
            1.0
            / (1.0 + math.exp(-max(-40.0, min(40.0, (value - center) / scale))))
            for value in raw_values
        ]

    def score(self, *, profile, developer_key, candidates):
        paired_scorer = getattr(self.base_scorer, "score_paired", None)
        if not callable(paired_scorer):
            raise ModelUnavailable("shadow mode requires a paired CODER scorer")
        paired = paired_scorer(
            profile=self.profile_snapshot,
            developer_key=developer_key,
            candidates=candidates,
        )
        self.last_paired = paired
        rows = list(paired.scores)
        normalized = self._normalize_scores(
            [float(row.raw_score_original) for row in rows]
        )
        base_user = getattr(self.base_scorer, "_base_user", None)
        if callable(base_user):
            _, profile_mode, warnings = base_user(developer_key)
        else:
            profile_mode = (
                "known_history" if developer_key else "cold_start_population"
            )
            warnings = [] if developer_key else [
                "coder_unknown_developer_population_fallback"
            ]
        self.profile_mode = str(profile_mode)
        manifest = getattr(self.base_scorer, "manifest", {}) or {}
        return ModelScoreBatch(
            scores=tuple(
                ModelScore(
                    task_candidate_id=int(row.task_candidate_id),
                    score=value,
                    coverage_state=str(row.coverage_state),
                )
                for row, value in zip(rows, normalized, strict=True)
            ),
            model_version=str(manifest.get("model_version", "unknown")),
            dataset_version=str(manifest.get("dataset_version", "unknown")),
            graph_version=str(manifest.get("graph_version", "unknown")),
            encoder_version=str(manifest.get("encoder_version", "unknown")),
            profile_mode=str(profile_mode),
            warnings=tuple(warnings),
        )

    def paired_payload(self) -> dict[str, Any] | None:
        if self.last_paired is None:
            return None
        return {
            **asdict(self.last_paired),
            "advanced_adapter_mode": "shadow",
            "profile_mode": self.profile_mode,
        }


def scorer_with_profile(
    base_scorer: Any,
    profile_snapshot: dict[str, Any] | None,
    *,
    mode: str = "off",
    service_track: str = "growth",
) -> Any:
    """Select base-only scoring when no confirmed enhanced profile exists.

    C's adapter-enabled scorer requires a confirmed profile mapping.  Anonymous,
    custom and unconfirmed-profile requests must continue through the same
    frozen base model without accidentally requiring an adapter snapshot.
    """

    if mode not in ADVANCED_ADAPTER_MODES:
        raise ValueError("advanced adapter mode must be off, shadow or serve")
    if base_scorer is None:
        return None
    has_adapter = getattr(base_scorer, "profile_adapter", None) is not None
    if mode != "off" and service_track == "growth" and profile_snapshot is not None:
        if not has_adapter:
            raise ModelUnavailable(
                f"{mode} mode requires a loaded advanced adapter bundle"
            )
        if mode == "shadow":
            return ShadowProfileScorer(base_scorer, profile_snapshot)
        return ProfileAwareScorer(base_scorer, profile_snapshot)
    if not has_adapter:
        return base_scorer
    base_only = copy.copy(base_scorer)
    base_only.profile_adapter = None
    base_only.profile_adapter_manifest = None
    return base_only


def current_model_metadata(model_scorer: Any | None = None) -> ModelMetadata:
    """Describe the scorer that is actually serving recommendation requests."""

    if model_scorer is not None:
        nested_model = getattr(model_scorer, "model", None)
        manifest = getattr(nested_model, "manifest", None)
        if not isinstance(manifest, dict):
            manifest = getattr(model_scorer, "manifest", None)
        if isinstance(manifest, dict) and manifest.get("model_version"):
            variant = str(
                getattr(model_scorer, "scoring_engine", None) or "coder_issue_repo"
            )
            feature_versions = {
                key: str(manifest[key])
                for key in ("feature_schema", "graph_version", "encoder_version")
                if manifest.get(key)
            }
            warnings = []
            if manifest.get("scores_are_probabilities") is False:
                warnings.append("model_scores_are_not_probabilities")
            if manifest.get("independently_tested") is False:
                warnings.append("model_not_independently_tested")
            return ModelMetadata(
                variant=variant,
                version=str(manifest["model_version"]),
                status="active",
                dataset_version=(
                    str(manifest["dataset_version"])
                    if manifest.get("dataset_version")
                    else None
                ),
                mapping_version=(
                    str(manifest["mapping_version"])
                    if manifest.get("mapping_version")
                    else None
                ),
                feature_versions=feature_versions,
                warnings=tuple(warnings),
            )
    return ModelMetadata(
        variant="rule_legacy",
        version=MATCH_VERSION_V3,
        status="compat",
        dataset_version=None,
        mapping_version=None,
        feature_versions={"matching": MATCH_VERSION_V3},
        warnings=("coder_model_not_connected",),
    )
