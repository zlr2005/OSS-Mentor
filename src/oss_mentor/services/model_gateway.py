"""Model gateway status for the HTTP layer.

The midterm plan requires the UI to distinguish the real CODER variants from
the legacy rule engine.  The repository currently has no trained model package
registered, so the gateway reports ``rule_legacy`` and a ``compat`` status.
When member C registers a real package, only this module needs to change.
"""

from __future__ import annotations

from oss_mentor.contracts_midterm import ModelMetadata
from oss_mentor.matching import MATCH_VERSION_V3


class ModelUnavailable(RuntimeError):
    """Raised when a request requires a model that is not registered."""


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
