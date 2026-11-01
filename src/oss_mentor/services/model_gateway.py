"""Model gateway status for the HTTP layer.

The midterm plan requires the UI to distinguish the real CODER variants from
the legacy rule engine.  The repository currently has no trained model package
registered, so the gateway reports ``rule_legacy`` and a ``compat`` status.
When member C registers a real package, only this module needs to change.
"""

from __future__ import annotations

import copy
from typing import Any

from oss_mentor.contracts_midterm import ModelMetadata
from oss_mentor.matching import MATCH_VERSION_V3


class ModelUnavailable(RuntimeError):
    """Raised when a request requires a model that is not registered."""


class ProfileAwareScorer:
    """Pass one confirmed enhanced profile to an otherwise unchanged scorer."""

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


def scorer_with_profile(
    base_scorer: Any,
    profile_snapshot: dict[str, Any] | None,
) -> Any:
    """Select base-only scoring when no confirmed enhanced profile exists.

    C's adapter-enabled scorer requires a confirmed profile mapping.  Anonymous,
    custom and unconfirmed-profile requests must continue through the same
    frozen base model without accidentally requiring an adapter snapshot.
    """

    if base_scorer is None:
        return None
    has_adapter = getattr(base_scorer, "profile_adapter", None) is not None
    if profile_snapshot is not None:
        return ProfileAwareScorer(base_scorer, profile_snapshot)
    if not has_adapter:
        return base_scorer
    base_only = copy.copy(base_scorer)
    base_only.profile_adapter = None
    base_only.profile_adapter_manifest = None
    return base_only


def current_model_metadata() -> ModelMetadata:
    return ModelMetadata(
        variant="rule_legacy",
        version=MATCH_VERSION_V3,
        status="compat",
        dataset_version=None,
        mapping_version=None,
        feature_versions={"matching": MATCH_VERSION_V3},
        warnings=("coder_model_not_connected",),
    )
