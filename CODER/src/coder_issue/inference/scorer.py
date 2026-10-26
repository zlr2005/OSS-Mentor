"""Read-only CODER scorer for the OSS-Mentor service boundary."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pyarrow.parquet as pq
import torch

from ..models.profile import (
    ProfileResidualAdapter,
    tensorize_confirmed_profile,
)


@dataclass(frozen=True, slots=True)
class ModelScore:
    task_candidate_id: int
    score: float
    coverage_state: str


@dataclass(frozen=True, slots=True)
class ModelScoreBatch:
    scores: tuple[ModelScore, ...]
    model_version: str
    dataset_version: str
    graph_version: str
    encoder_version: str
    profile_mode: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PairedModelScore:
    task_candidate_id: int
    coverage_state: str
    raw_score_original: float
    raw_score_profile: float
    rank_original: int
    rank_profile: int


@dataclass(frozen=True, slots=True)
class PairedModelScoreBatch:
    scores: tuple[PairedModelScore, ...]
    model_version: str
    profile_adapter_version: str
    profile_key: str
    profile_version: str


def _issue_key(candidate: dict[str, Any]) -> str:
    repository = str(candidate.get("repository") or candidate.get("repository_full_name") or "")
    return f"{repository.strip().casefold()}#{int(candidate['issue_number'])}"


class CoderIssueScorer:
    """Score candidates from frozen exported vectors; never trains or downloads."""

    def __init__(
        self,
        model_dir: Path,
        mapping_dir: Path,
        *,
        candidate_cache: Path | None = None,
        profile_adapter_dir: Path | None = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.manifest = json.loads(
            (self.model_dir / "model_manifest.json").read_text(encoding="utf-8")
        )
        if self.manifest.get("variant") != "coder_issue_repo":
            raise ValueError("platform scoring requires a coder_issue_repo bundle")
        with np.load(self.model_dir / "embeddings.npz") as bundle:
            self.users = np.asarray(bundle["user_embeddings"], dtype=np.float32)
            self.issues = np.asarray(bundle["issue_embeddings"], dtype=np.float32)
        developers = pq.read_table(Path(mapping_dir) / "developer_mapping.parquet")
        issues = pq.read_table(Path(mapping_dir) / "issue_mapping.parquet")
        self.developer_ids = {
            str(key).casefold(): int(value)
            for key, value in zip(developers["developer_key"].to_pylist(), developers["developer_id"].to_pylist())
        }
        self.issue_ids = {
            str(key).casefold(): int(value)
            for key, value in zip(issues["issue_key"].to_pylist(), issues["issue_id"].to_pylist())
        }
        self.cached_candidates: dict[str, np.ndarray] = {}
        if candidate_cache is not None:
            with np.load(candidate_cache) as cache:
                keys = cache["issue_keys"].astype(str).tolist()
                vectors = np.asarray(cache["issue_embeddings"], dtype=np.float32)
            self.cached_candidates = dict(zip(keys, vectors, strict=True))
        self.cold_start_user = self.users.mean(axis=0)
        self.profile_adapter: ProfileResidualAdapter | None = None
        self.profile_adapter_manifest: dict[str, Any] | None = None
        if profile_adapter_dir is not None:
            adapter_root = Path(profile_adapter_dir)
            self.profile_adapter_manifest = json.loads(
                (adapter_root / "adapter_manifest.json").read_text(encoding="utf-8")
            )
            self.profile_adapter = ProfileResidualAdapter.load_bundle(
                adapter_root,
                expected_base_model_version=str(self.manifest["model_version"]),
            )
            if self.profile_adapter.config.embedding_dim != self.users.shape[1]:
                raise ValueError("profile adapter dimension does not match CODER user vectors")

    def _base_user(self, developer_key: str | None) -> tuple[np.ndarray, str, list[str]]:
        normalized = (developer_key or "").strip().casefold()
        developer_id = self.developer_ids.get(normalized)
        user = self.users[developer_id] if developer_id is not None else self.cold_start_user
        profile_mode = "known_history" if developer_id is not None else "cold_start_population"
        warnings = [] if developer_id is not None else ["coder_unknown_developer_population_fallback"]
        return user, profile_mode, warnings

    @staticmethod
    def _validate_profile_identity(
        profile_developer_key: str | None,
        request_developer_key: str | None,
    ) -> None:
        request_key = (request_developer_key or "").strip().casefold() or None
        if profile_developer_key is not None and profile_developer_key != request_key:
            raise ValueError(
                "enhanced profile developer_key conflicts with the authenticated developer_key"
            )

    def _candidate_vectors(
        self, candidates: Sequence[dict[str, Any]]
    ) -> list[tuple[int, np.ndarray, str]]:
        rows: list[tuple[int, np.ndarray, str]] = []
        missing: list[str] = []
        for candidate in candidates:
            key = _issue_key(candidate)
            issue_id = self.issue_ids.get(key)
            if issue_id is not None:
                vector = self.issues[issue_id]
                coverage = "fixed_train_graph"
            elif key in self.cached_candidates:
                vector = self.cached_candidates[key]
                coverage = "platform_candidate_cache"
            else:
                missing.append(key)
                continue
            rows.append((int(candidate["task_candidate_id"]), vector, coverage))
        if missing:
            raise RuntimeError(
                "CODER candidate cache is incomplete; run prepare_platform_coder_cache.py; "
                f"missing={missing[:10]}"
            )
        return rows

    @staticmethod
    def _rank(raw_by_candidate: Sequence[tuple[int, float]]) -> dict[int, int]:
        ordered = sorted(raw_by_candidate, key=lambda item: (-item[1], item[0]))
        return {candidate_id: index + 1 for index, (candidate_id, _) in enumerate(ordered)}

    def score(
        self,
        *,
        profile: Any,
        developer_key: str | None,
        candidates: Sequence[dict[str, Any]],
    ) -> ModelScoreBatch:
        user, profile_mode, warnings = self._base_user(developer_key)
        if self.profile_adapter is not None:
            if not isinstance(profile, Mapping):
                raise ValueError("a confirmed enhanced-profile snapshot is required by this adapter")
            tensors = tensorize_confirmed_profile(profile, config=self.profile_adapter.config)
            self._validate_profile_identity(tensors.developer_key, developer_key)
            with torch.inference_mode():
                fused = self.profile_adapter.fuse(
                    torch.from_numpy(np.asarray(user, dtype=np.float32)).unsqueeze(0),
                    tensors.values.unsqueeze(0),
                    tensors.observed_mask.unsqueeze(0),
                )
            user = fused.squeeze(0).numpy()
            profile_mode += "_profile"
            if not bool(tensors.observed_mask.any()):
                warnings.append("coder_profile_has_no_observed_features")
        vector_rows = self._candidate_vectors(candidates)
        rows = [
            (candidate_id, float(np.dot(user, vector)), coverage)
            for candidate_id, vector, coverage in vector_rows
        ]
        raw_values = np.asarray([row[1] for row in rows], dtype=np.float64)
        center = float(raw_values.mean()) if len(raw_values) else 0.0
        scale = float(raw_values.std()) if len(raw_values) > 1 else 0.0
        output = tuple(
            ModelScore(
                candidate_id,
                0.5 if scale == 0.0 else 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, (raw - center) / scale)))),
                coverage,
            )
            for candidate_id, raw, coverage in rows
        )
        return ModelScoreBatch(
            scores=output,
            model_version=str(self.manifest["model_version"]),
            dataset_version=str(self.manifest["dataset_version"]),
            graph_version=str(self.manifest["graph_version"]),
            encoder_version=str(self.manifest["encoder_version"]),
            profile_mode=profile_mode,
            warnings=tuple(warnings),
        )

    def score_paired(
        self,
        *,
        profile: Mapping[str, Any],
        developer_key: str | None,
        candidates: Sequence[dict[str, Any]],
    ) -> PairedModelScoreBatch:
        """Score one frozen candidate set with and without only the profile residual."""

        if self.profile_adapter is None or self.profile_adapter_manifest is None:
            raise RuntimeError("paired profile scoring requires a profile adapter bundle")
        base_user, _, _ = self._base_user(developer_key)
        tensors = tensorize_confirmed_profile(profile, config=self.profile_adapter.config)
        self._validate_profile_identity(tensors.developer_key, developer_key)
        with torch.inference_mode():
            fused_user = self.profile_adapter.fuse(
                torch.from_numpy(np.asarray(base_user, dtype=np.float32)).unsqueeze(0),
                tensors.values.unsqueeze(0),
                tensors.observed_mask.unsqueeze(0),
            ).squeeze(0).numpy()
        vector_rows = self._candidate_vectors(candidates)
        original = [
            (candidate_id, float(np.dot(base_user, vector)))
            for candidate_id, vector, _ in vector_rows
        ]
        profiled = [
            (candidate_id, float(np.dot(fused_user, vector)))
            for candidate_id, vector, _ in vector_rows
        ]
        original_by_id = dict(original)
        profiled_by_id = dict(profiled)
        original_ranks = self._rank(original)
        profile_ranks = self._rank(profiled)
        scores = tuple(
            PairedModelScore(
                task_candidate_id=candidate_id,
                coverage_state=coverage,
                raw_score_original=original_by_id[candidate_id],
                raw_score_profile=profiled_by_id[candidate_id],
                rank_original=original_ranks[candidate_id],
                rank_profile=profile_ranks[candidate_id],
            )
            for candidate_id, _, coverage in vector_rows
        )
        return PairedModelScoreBatch(
            scores=scores,
            model_version=str(self.manifest["model_version"]),
            profile_adapter_version=str(
                self.profile_adapter_manifest.get("adapter_version", "unversioned")
            ),
            profile_key=tensors.profile_key,
            profile_version=tensors.profile_version,
        )
