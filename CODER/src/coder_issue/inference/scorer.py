"""Read-only CODER scorer for the OSS-Mentor service boundary."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pyarrow.parquet as pq


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

    def score(
        self,
        *,
        profile: Any,
        developer_key: str | None,
        candidates: Sequence[dict[str, Any]],
    ) -> ModelScoreBatch:
        del profile  # Profile is applied by the platform's hard eligibility gate.
        normalized = (developer_key or "").strip().casefold()
        developer_id = self.developer_ids.get(normalized)
        user = self.users[developer_id] if developer_id is not None else self.cold_start_user
        profile_mode = "known_history" if developer_id is not None else "cold_start_population"
        warnings = [] if developer_id is not None else ["coder_unknown_developer_population_fallback"]
        rows: list[tuple[int, float, str]] = []
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
            rows.append((int(candidate["task_candidate_id"]), float(np.dot(user, vector)), coverage))
        if missing:
            raise RuntimeError(
                "CODER candidate cache is incomplete; run prepare_platform_coder_cache.py; "
                f"missing={missing[:10]}"
            )
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
