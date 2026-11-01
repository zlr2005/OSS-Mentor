"""Atomic, identity-checked checkpoints for resumable training."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random
import time
from typing import Any
import uuid
from dataclasses import asdict, is_dataclass

import numpy as np
import torch
from torch import nn


CHECKPOINT_SCHEMA_VERSION = 2
STATIC_STATE_SUFFIXES = (
    ".adjacency",
    ".edge_index",
    "issue_to_repository",
    "semantic_embeddings",
)


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def capture_rng_state() -> dict[str, Any]:
    numpy_state = np.random.get_state()
    payload: dict[str, Any] = {
        "python": random.getstate(),
        "numpy": {
            "bit_generator": numpy_state[0],
            "state": torch.from_numpy(numpy_state[1].copy()),
            "position": int(numpy_state[2]),
            "has_gauss": int(numpy_state[3]),
            "cached_gaussian": float(numpy_state[4]),
        },
        "torch_cpu": torch.get_rng_state(),
    }
    if torch.cuda.is_available():
        payload["torch_cuda"] = torch.cuda.get_rng_state_all()
    return payload


def restore_rng_state(payload: dict[str, Any]) -> None:
    random.setstate(tuple(payload["python"]))
    numpy_state = payload["numpy"]
    np.random.set_state((
        str(numpy_state["bit_generator"]),
        numpy_state["state"].cpu().numpy().astype(np.uint32, copy=False),
        int(numpy_state["position"]),
        int(numpy_state["has_gauss"]),
        float(numpy_state["cached_gaussian"]),
    ))
    torch.set_rng_state(payload["torch_cpu"].cpu())
    if "torch_cuda" in payload:
        if not torch.cuda.is_available():
            raise RuntimeError("checkpoint contains CUDA RNG state but CUDA is unavailable")
        torch.cuda.set_rng_state_all(payload["torch_cuda"])


def trainable_state_dict(model: nn.Module) -> dict[str, torch.Tensor]:
    """Exclude immutable graph/text buffers which are rebuilt from hashed inputs."""

    return {
        key: value.detach().cpu()
        for key, value in model.state_dict().items()
        if not key.endswith(STATIC_STATE_SUFFIXES)
    }


def load_trainable_state_dict(model: nn.Module, state: dict[str, torch.Tensor]) -> None:
    incompatible = model.load_state_dict(state, strict=False)
    unexpected = list(incompatible.unexpected_keys)
    invalid_missing = [
        key for key in incompatible.missing_keys
        if not key.endswith(STATIC_STATE_SUFFIXES)
    ]
    if unexpected or invalid_missing:
        raise ValueError(
            f"checkpoint model state mismatch: missing={invalid_missing}, unexpected={unexpected}"
        )


def _atomic_torch_save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        torch.save(payload, temporary)
        # Validate the complete temporary file before it becomes resumable.
        torch.load(temporary, map_location="cpu", weights_only=True)
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


class CheckpointManager:
    """Maintain latest/previous recovery points with strict experiment identity."""

    def __init__(self, directory: Path, identity: dict[str, Any]) -> None:
        self.directory = directory
        self.identity = identity
        self.identity_sha256 = canonical_sha256(identity)
        self.latest_path = directory / "latest.pt"
        self.previous_path = directory / "previous.pt"

    def save(
        self,
        model: nn.Module,
        optimizer: torch.optim.Optimizer,
        *,
        progress: dict[str, Any],
        history: dict[str, Any],
        scheduler: object | None = None,
    ) -> Path:
        model_config = getattr(model, "config", None)
        if is_dataclass(model_config):
            model_config = asdict(model_config)
        payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "identity": self.identity,
            "identity_sha256": self.identity_sha256,
            "model_state_dict": trainable_state_dict(model),
            "model_config": model_config,
            "optimizer_state_dict": optimizer.state_dict(),
            "scheduler_state_dict": (
                scheduler.state_dict() if scheduler is not None else None
            ),
            "progress": progress,
            "history": history,
            "rng_state": capture_rng_state(),
        }
        staging = self.directory / ".latest.staging.pt"
        _atomic_torch_save(staging, payload)
        if self.latest_path.is_file():
            os.replace(self.latest_path, self.previous_path)
        os.replace(staging, self.latest_path)
        return self.latest_path

    def load(
        self,
        model: nn.Module,
        optimizer: torch.optim.Optimizer,
        *,
        map_location: str | torch.device,
        scheduler: object | None = None,
    ) -> tuple[dict[str, Any], Path]:
        errors: list[str] = []
        for path in (self.latest_path, self.previous_path):
            if not path.is_file():
                continue
            try:
                payload = torch.load(path, map_location=map_location, weights_only=True)
                if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                    raise ValueError("unsupported checkpoint schema")
                if payload.get("identity_sha256") != self.identity_sha256:
                    raise ValueError("checkpoint experiment identity does not match current run")
                if payload.get("identity") != self.identity:
                    raise ValueError("checkpoint identity payload is inconsistent")
                current_config = getattr(model, "config", None)
                if is_dataclass(current_config):
                    current_config = asdict(current_config)
                if payload.get("model_config") != current_config:
                    raise ValueError("checkpoint model architecture does not match target model")
                load_trainable_state_dict(model, payload["model_state_dict"])
                optimizer.load_state_dict(payload["optimizer_state_dict"])
                saved_scheduler = payload.get("scheduler_state_dict")
                if saved_scheduler is not None:
                    if scheduler is None:
                        raise ValueError("checkpoint requires a scheduler")
                    scheduler.load_state_dict(saved_scheduler)
                restore_rng_state(payload["rng_state"])
                return payload, path
            except Exception as exc:
                errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
        detail = "; ".join(errors) if errors else "no latest or previous checkpoint exists"
        raise RuntimeError(f"no valid recovery checkpoint: {detail}")

    def peek(self, *, map_location: str | torch.device = "cpu") -> tuple[dict[str, Any], Path]:
        """Read and validate metadata, falling back from a corrupt latest file."""

        errors: list[str] = []
        for path in (self.latest_path, self.previous_path):
            if not path.is_file():
                continue
            try:
                payload = torch.load(path, map_location=map_location, weights_only=True)
                if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                    raise ValueError("unsupported checkpoint schema")
                if payload.get("identity_sha256") != self.identity_sha256:
                    raise ValueError("checkpoint experiment identity does not match current run")
                if payload.get("identity") != self.identity:
                    raise ValueError("checkpoint identity payload is inconsistent")
                return payload, path
            except Exception as exc:
                errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
        detail = "; ".join(errors) if errors else "no latest or previous checkpoint exists"
        raise RuntimeError(f"no valid recovery checkpoint: {detail}")
