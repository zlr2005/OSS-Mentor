"""Validated enhanced-profile input and a residual CODER user adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch
from torch import Tensor, nn


PROFILE_EXPORT_SCHEMA_VERSION = "enhanced-profile-export-v0.1"
PROFILE_FEATURE_SCHEMA_VERSION = "enhanced-profile-features-v0.1"
PROFILE_FEATURE_NAMES = (
    "capability.change_scope",
    "capability.code_change",
    "capability.debugging",
    "capability.documentation",
    "capability.documentation_structure",
    "capability.documentation_validation",
    "capability.open_source_experience",
    "capability.reproduction",
    "capability.test_design",
    "capability.testing",
    "constraint.os.linux",
    "constraint.os.macos",
    "constraint.os.windows",
    "constraint.weekly_time",
    "current_interest.task.documentation",
    "current_interest.task.implementation",
    "current_interest.task.quality",
    "preference.language.go",
    "preference.language.java",
    "preference.language.javascript",
    "preference.language.python",
    "preference.language.rust",
    "preference.language.typescript",
)
INTEREST_FEATURES = frozenset(
    name for name in PROFILE_FEATURE_NAMES if name.startswith("current_interest.")
)


@dataclass(frozen=True)
class ProfileAdapterConfig:
    feature_schema_version: str = PROFILE_FEATURE_SCHEMA_VERSION
    feature_names: tuple[str, ...] = PROFILE_FEATURE_NAMES
    embedding_dim: int = 32
    hidden_dim: int = 32
    residual_scale: float = 1.0

    def __post_init__(self) -> None:
        if not self.feature_names or len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("feature_names must be non-empty and unique")
        if self.embedding_dim <= 0 or self.hidden_dim <= 0:
            raise ValueError("adapter dimensions must be positive")
        if self.residual_scale < 0:
            raise ValueError("residual_scale must be non-negative")


@dataclass(frozen=True)
class ProfileTensors:
    values: Tensor
    observed_mask: Tensor
    profile_key: str
    profile_version: str
    developer_key: str | None


def _require_sequence(snapshot: Mapping[str, Any], field: str, length: int) -> Sequence[Any]:
    value = snapshot.get(field)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != length:
        raise ValueError(f"{field} must contain exactly {length} entries")
    return value


def tensorize_confirmed_profile(
    snapshot: Mapping[str, Any],
    *,
    config: ProfileAdapterConfig,
    device: str | torch.device = "cpu",
) -> ProfileTensors:
    """Validate B's confirmed snapshot and return only model-consumed tensors."""

    if snapshot.get("schema_version") != PROFILE_EXPORT_SCHEMA_VERSION:
        raise ValueError("unsupported enhanced profile export schema_version")
    if snapshot.get("profile_state") != "confirmed":
        raise ValueError("only confirmed profile snapshots can be scored")
    if snapshot.get("feature_schema_version") != config.feature_schema_version:
        raise ValueError("profile feature_schema_version does not match adapter")
    names = tuple(_require_sequence(snapshot, "feature_names", len(config.feature_names)))
    if names != config.feature_names:
        raise ValueError("profile feature_names or their order do not match adapter")

    raw_values = _require_sequence(snapshot, "values", len(names))
    raw_observed = _require_sequence(snapshot, "observed_mask", len(names))
    raw_locked = _require_sequence(snapshot, "locked_mask", len(names))
    raw_evidence = _require_sequence(snapshot, "evidence_refs", len(names))
    if any(
        isinstance(refs, (str, bytes))
        or not isinstance(refs, Sequence)
        or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
        for refs in raw_evidence
    ):
        raise ValueError("evidence_refs entries must be sequences of non-empty strings")
    if any(not isinstance(value, bool) for value in raw_observed):
        raise ValueError("observed_mask entries must be boolean")
    if any(not isinstance(value, bool) for value in raw_locked):
        raise ValueError("locked_mask entries must be boolean")

    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw_values):
        raise ValueError("profile values must be JSON numbers")
    values = torch.as_tensor(raw_values, dtype=torch.float32, device=device)
    observed = torch.as_tensor(raw_observed, dtype=torch.bool, device=device)
    if not bool(torch.isfinite(values).all()):
        raise ValueError("profile values must be finite")
    if bool(((values < 0.0) | (values > 1.0)).any()):
        raise ValueError("profile values must be within [0, 1]")
    if bool((values[~observed] != 0.0).any()):
        raise ValueError("unobserved values must use a zero placeholder")

    interests = snapshot.get("current_interests")
    if isinstance(interests, (str, bytes)) or not isinstance(interests, Sequence):
        raise ValueError("current_interests must be a sequence")
    expected_interests = {
        name
        for index, name in enumerate(names)
        if name in INTEREST_FEATURES and raw_observed[index] and float(raw_values[index]) > 0.0
    }
    if any(not isinstance(item, str) for item in interests) or len(interests) != len(set(interests)):
        raise ValueError("current_interests must contain unique strings")
    if set(interests) != expected_interests:
        raise ValueError("current_interests do not match the ordered feature arrays")

    profile_key = snapshot.get("profile_key")
    profile_version = snapshot.get("profile_version")
    if not isinstance(profile_key, str) or not profile_key.strip():
        raise ValueError("profile_key must be a non-empty string")
    if not isinstance(profile_version, str) or not profile_version.strip():
        raise ValueError("profile_version must be a non-empty string")
    raw_developer_key = snapshot.get("developer_key")
    if raw_developer_key is not None and (
        not isinstance(raw_developer_key, str) or not raw_developer_key.strip()
    ):
        raise ValueError("developer_key must be null or a non-empty string")
    normalized_developer_key = (
        raw_developer_key.strip().casefold() if raw_developer_key is not None else None
    )
    if normalized_developer_key == profile_key.strip().casefold():
        raise ValueError("developer_key must not reuse the platform profile_key")
    as_of = snapshot.get("as_of")
    if not isinstance(as_of, str):
        raise ValueError("as_of must be an ISO-8601 timestamp")
    try:
        parsed_as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("as_of must be an ISO-8601 timestamp") from exc
    if parsed_as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    return ProfileTensors(
        values,
        observed,
        profile_key.strip(),
        profile_version.strip(),
        normalized_developer_key,
    )


class ProfileResidualAdapter(nn.Module):
    """Project values plus missingness into the frozen CODER user space.

    Lock state and evidence references are deliberately validated but excluded
    from the tensor.  An all-unobserved profile always produces an exact zero
    residual, including when linear layers contain biases.
    """

    def __init__(self, config: ProfileAdapterConfig) -> None:
        super().__init__()
        self.config = config
        width = len(config.feature_names)
        self.network = nn.Sequential(
            nn.Linear(width * 2, config.hidden_dim),
            nn.ReLU(),
            nn.Linear(config.hidden_dim, config.embedding_dim),
        )

    def forward(self, values: Tensor, observed_mask: Tensor) -> Tensor:
        if values.shape != observed_mask.shape or values.shape[-1] != len(self.config.feature_names):
            raise ValueError("profile values and observed_mask have incompatible shapes")
        observed = observed_mask.to(dtype=torch.bool)
        masked_values = values.to(dtype=torch.float32) * observed.to(dtype=torch.float32)
        inputs = torch.cat((masked_values, observed.to(dtype=torch.float32)), dim=-1)
        residual = self.network(inputs) * self.config.residual_scale
        has_observation = observed.any(dim=-1, keepdim=True)
        return torch.where(has_observation, residual, torch.zeros_like(residual))

    def fuse(self, base_user: Tensor, values: Tensor, observed_mask: Tensor) -> Tensor:
        residual = self(values, observed_mask)
        if base_user.shape != residual.shape:
            raise ValueError("base user and profile residual shapes must match")
        return base_user + residual

    def save_bundle(self, path: Path, *, base_model_version: str, metadata: Mapping[str, Any] | None = None) -> None:
        path.mkdir(parents=True, exist_ok=True)
        torch.save(self.state_dict(), path / "weights.pt")
        payload = {
            "schema_version": "coder-profile-adapter-v0.1",
            "base_model_version": base_model_version,
            "config": asdict(self.config),
            **dict(metadata or {}),
        }
        (path / "adapter_manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load_bundle(
        cls,
        path: Path,
        *,
        expected_base_model_version: str,
        map_location: str | torch.device = "cpu",
    ) -> "ProfileResidualAdapter":
        manifest = json.loads((path / "adapter_manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != "coder-profile-adapter-v0.1":
            raise ValueError("unsupported profile adapter manifest")
        if manifest.get("base_model_version") != expected_base_model_version:
            raise ValueError("profile adapter was trained for another base model")
        raw_config = dict(manifest["config"])
        raw_config["feature_names"] = tuple(raw_config["feature_names"])
        adapter = cls(ProfileAdapterConfig(**raw_config))
        state = torch.load(path / "weights.pt", map_location=map_location, weights_only=True)
        adapter.load_state_dict(state)
        adapter.eval()
        return adapter
