"""Build deterministic, cutoff-safe stratified negatives for Adapter supplements."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from coder_issue.training.advanced_profile_data import normalize_issue_key
from coder_issue.training.advanced_profile_pipeline import load_frozen_inputs
from coder_issue.training.advanced_profile_supplement import (
    _candidate_row,
    _encode,
    _inside,
    _rows,
    _verify_manifest_file,
    _write_json,
    _write_jsonl,
)
from oss_mentor.profiling.temporal_training import sha256_file


def _repository(issue_key: str) -> str:
    return issue_key.rsplit("#", 1)[0]


def _stable_order(seed: int, outcome_id: str, candidate_key: str) -> str:
    return hashlib.sha256(f"{seed}\0{outcome_id}\0{candidate_key}".encode()).hexdigest()


def _retain(candidates: dict[str, dict[str, Any]], candidate: dict[str, Any]) -> None:
    existing = candidates.get(str(candidate["candidate_key"]))
    if existing is None:
        candidates[str(candidate["candidate_key"])] = candidate
        return
    for field in ("issue_key", "as_of", "content_sha256", "title", "body_text"):
        if existing[field] != candidate[field]:
            raise ValueError(
                f"conflicting candidate snapshot: {candidate['candidate_key']}"
            )


def _raw_controls(*, repo_root: Path, handoff_root: Path) -> list[dict[str, Any]]:
    index = _rows(handoff_root / "comparison_index.jsonl")
    output: list[dict[str, Any]] = []
    for relative in sorted({str(row["history_source_root"]) for row in index}):
        root = _inside(repo_root, relative)
        path = _verify_manifest_file(root, "historical_controls.jsonl")
        for raw in _rows(path):
            output.append(
                _candidate_row(
                    raw,
                    role="stratified_control_pool",
                    evidence_ref=(
                        root.relative_to(repo_root).as_posix()
                        + ":"
                        + str(raw["evidence_ref"])
                    ),
                )
            )
    return output


def _load_vectors(path: Path) -> dict[str, np.ndarray]:
    with np.load(path) as payload:
        keys = payload["issue_keys"].astype(str).tolist()
        vectors = np.asarray(payload["issue_embeddings"], dtype=np.float32)
    return dict(zip(keys, vectors, strict=True))


def _take(
    pool: Sequence[dict[str, Any]],
    *,
    count: int,
    selected: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in pool:
        key = str(row["candidate_key"])
        if key in selected:
            continue
        selected.add(key)
        result.append(row)
        if len(result) == count:
            break
    return result


def build(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    supplement_dir = Path(args.supplement_dir).resolve()
    handoff_root = Path(args.handoff_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be absent or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    plans = _rows(supplement_dir / "pair_plan.jsonl")
    outcomes = _rows(supplement_dir / "outcomes.jsonl")
    candidates: dict[str, dict[str, Any]] = {}
    for row in _rows(supplement_dir / "candidate_snapshots.jsonl"):
        _retain(candidates, row)
    for row in _raw_controls(repo_root=repo_root, handoff_root=handoff_root):
        _retain(candidates, row)

    candidate_rows = sorted(candidates.values(), key=lambda row: row["candidate_key"])
    candidate_path = output_dir / "candidate_snapshots.jsonl"
    _write_jsonl(candidate_path, candidate_rows)
    cache_path = output_dir / "supplement_issue_embeddings.npz"
    encoding = _encode(
        candidate_rows,
        model_dir=Path(args.model_dir),
        mapping_dir=Path(args.mapping_dir),
        output_path=cache_path,
        device=args.device,
        max_segments=args.max_segments,
        segment_batch_size=args.segment_batch_size,
    )
    vectors = _load_vectors(cache_path)
    frozen = load_frozen_inputs(Path(args.model_dir), Path(args.base_issue_cache))
    base_user = frozen.base_user.detach().cpu().numpy()

    by_cutoff: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        by_cutoff[str(row["as_of"])].append(row)
    outcome_by_id = {str(row["outcome_id"]): row for row in outcomes}
    positives_by_developer: dict[str, set[str]] = defaultdict(set)
    for row in outcomes:
        positives_by_developer[str(row["developer_key"])].add(
            normalize_issue_key(row["issue_key"])
        )

    quarter_plans: list[dict[str, Any]] = []
    shortages: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    for plan in plans:
        if plan["cutoff_policy"] != "calendar_quarter":
            continue
        outcome_id = str(plan["outcome_id"])
        outcome = outcome_by_id[outcome_id]
        positive_key = str(plan["positive_candidate_key"])
        positive_vector = vectors[positive_key]
        positive_norm = float(np.linalg.norm(positive_vector)) or 1.0
        positive_repo = _repository(str(plan["positive_issue_key"]))
        known_positives = positives_by_developer[str(outcome["developer_key"])]
        pool = [
            row
            for row in by_cutoff[str(plan["profile_as_of"])]
            if row["candidate_key"] != positive_key
            and normalize_issue_key(row["issue_key"]) not in known_positives
        ]
        if len(pool) < 20:
            raise ValueError(f"fewer than 20 cutoff-safe controls for {outcome_id}")

        base_sorted = sorted(
            pool,
            key=lambda row: (
                -float(np.dot(base_user, vectors[str(row["candidate_key"])])),
                str(row["candidate_key"]),
            ),
        )
        same_repo = [
            row
            for row in base_sorted
            if _repository(str(row["issue_key"])) == positive_repo
        ]
        cross_repo = [
            row for row in pool if _repository(str(row["issue_key"])) != positive_repo
        ]
        semantic_sorted = sorted(
            cross_repo,
            key=lambda row: (
                -float(
                    np.dot(positive_vector, vectors[str(row["candidate_key"])])
                    / (
                        positive_norm
                        * (
                            float(np.linalg.norm(vectors[str(row["candidate_key"])]))
                            or 1.0
                        )
                    )
                ),
                str(row["candidate_key"]),
            ),
        )
        random_sorted = sorted(
            pool,
            key=lambda row: _stable_order(
                args.seed, outcome_id, str(row["candidate_key"])
            ),
        )
        selected: set[str] = set()
        groups = [
            ("same_repository", _take(same_repo, count=5, selected=selected)),
            (
                "semantic_technology_proxy",
                _take(semantic_sorted, count=5, selected=selected),
            ),
            ("base_coder_hard", _take(base_sorted, count=5, selected=selected)),
            ("random", _take(random_sorted, count=5, selected=selected)),
        ]
        chosen = [(label, row) for label, rows in groups for row in rows]
        if len(chosen) < 20:
            fill = _take(random_sorted, count=20 - len(chosen), selected=selected)
            chosen.extend(("shortfall_fill", row) for row in fill)
        if len(chosen) != 20:
            raise RuntimeError(f"failed to construct 20 controls for {outcome_id}")
        if len(groups[0][1]) < 5:
            shortages.append(
                {
                    "outcome_id": outcome_id,
                    "category": "same_repository",
                    "requested": 5,
                    "available": len(groups[0][1]),
                    "reason": "fewer than five verified snapshots exist at the exact cutoff",
                }
            )
        comparisons = []
        for label, row in chosen:
            category_counts[label] += 1
            comparisons.append(
                {
                    "issue_key": row["issue_key"],
                    "candidate_key": row["candidate_key"],
                    "negative_category": label,
                    "weight_multiplier": 1.0,
                }
            )
        quarter_plans.append(
            {
                **plan,
                "comparisons": comparisons,
                "negative_protocol": "n2_cutoff_safe_stratified_v0.1",
                "negative_seed": args.seed,
            }
        )

    preinteraction = [
        {
            **plan,
            "negative_protocol": "n0_original_controls_v0.1",
            "comparisons": [
                {
                    **comparison,
                    "negative_category": "original_control",
                    "weight_multiplier": 1.0,
                }
                for comparison in plan["comparisons"]
            ],
        }
        for plan in plans
        if plan["cutoff_policy"] == "pre_interaction_minus_1s"
    ]
    hybrid = sorted(
        [*quarter_plans, *preinteraction], key=lambda row: str(row["outcome_id"])
    )
    quarter_path = output_dir / "quarter_n2_pair_plan.jsonl"
    hybrid_path = output_dir / "hybrid_n2_quarter_n0_preinteraction_pair_plan.jsonl"
    shortage_path = output_dir / "stratification_shortfalls.jsonl"
    _write_jsonl(quarter_path, quarter_plans)
    _write_jsonl(hybrid_path, hybrid)
    _write_jsonl(shortage_path, shortages)

    manifest = {
        "schema_version": "advanced-adapter-stratified-negatives-v0.1",
        "created_at": datetime.now(UTC).isoformat(),
        "seed": args.seed,
        "protocol": {
            "same_repository": 5,
            "semantic_technology_proxy": 5,
            "base_coder_hard": 5,
            "random": 5,
            "total": 20,
        },
        "statistics": {
            "candidate_versions": len(candidate_rows),
            "quarter_outcomes": len(quarter_plans),
            "preinteraction_outcomes_in_hybrid": len(preinteraction),
            "quarter_pairs": sum(len(row["comparisons"]) for row in quarter_plans),
            "category_counts": dict(sorted(category_counts.items())),
            "strict_5_same_repository_outcomes": len(quarter_plans) - len(shortages),
            "same_repository_shortfall_outcomes": len(shortages),
        },
        "encoding": encoding,
        "artifacts": {
            path.name: sha256_file(path)
            for path in (
                candidate_path,
                cache_path,
                quarter_path,
                hybrid_path,
                shortage_path,
            )
        },
        "limitations": [
            "The technology stratum uses cutoff-visible Issue embedding similarity because versioned language metadata is unavailable.",
            "Some repositories have fewer than five verified Issue snapshots at the exact cutoff; deficits are filled deterministically and listed.",
            "Pre-interaction cutoffs have only their original 1-5 exact-time controls, so the hybrid plan keeps N0 for those rows rather than inventing historical text.",
        ],
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    (output_dir / "_SUCCESS").write_text(
        sha256_file(manifest_path) + "\n", encoding="ascii"
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--handoff-root", required=True)
    parser.add_argument("--supplement-dir", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--mapping-dir", required=True)
    parser.add_argument("--base-issue-cache", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-segments", type=int, default=12)
    parser.add_argument("--segment-batch-size", type=int, default=128)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    report = build(build_parser().parse_args(argv))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
