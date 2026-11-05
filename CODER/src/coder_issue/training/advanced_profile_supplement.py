"""Prepare a time-safe Adapter training supplement from A's evidence handoff.

The command deliberately rebuilds the 23 profile features from historical code
bytes.  A's ``research_preview`` rows are not consumed.  Candidate Issue text is
kept as a versioned snapshot so the same Issue may safely appear at more than
one historical cutoff.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from coder_issue.training.advanced_issue_inputs import (
    _content_sha256,
    encode_candidates,
)
from coder_issue.training.advanced_profile_data import normalize_issue_key
from oss_mentor.profiling.temporal_training import (
    TEMPORAL_PROFILE_RULES_VERSION,
    build_temporal_training_profile,
    canonical_json_bytes,
    load_questionnaire_contract,
    sha256_file,
)


SUPPLEMENT_DATASET_VERSION = "temporal_xy_2024_2026_v3+supplement_20261105_v1"
SUPPLEMENT_SCHEMA_VERSION = "advanced-adapter-supplement-v0.1"


@dataclass(frozen=True, slots=True)
class ContributionMaterial:
    github_numeric_id: int
    repository: str
    commit_sha: str
    path: str
    event_at: str
    author_at: str | None
    content_sha256: str
    root: Path
    content_location: str
    evidence_ref: str
    file_version_key: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.repository, self.commit_sha, self.path)


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty timestamp")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            result.append(value)
    return result


def _write_json(path: Path, payload: Any) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with Path(path).open("wb") as handle:
        for row in rows:
            handle.write(canonical_json_bytes(dict(row)) + b"\n")


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes source root: {relative}")
    return path


def _verify_manifest_file(root: Path, relative: str) -> Path:
    manifest = _read_json(root / "manifest.json")
    expected = manifest.get("files_sha256", {}).get(relative)
    if not isinstance(expected, str):
        raise ValueError(f"manifest does not cover {root / relative}")
    path = _inside(root, relative)
    if sha256_file(path) != expected:
        raise ValueError(f"manifest hash mismatch: {path}")
    return path


def _history_roots(repo_root: Path, handoff_root: Path) -> list[Path]:
    source_manifest = _read_json(handoff_root / "source_manifest.json")
    roots = [handoff_root.parent / "merged_ab_history_2024_2026_v6"]
    for relative in source_manifest.get("source_roots", {}).values():
        root = _inside(repo_root, str(relative))
        if (root / "historical_contributions.jsonl").is_file():
            roots.append(root)
    unique = sorted({path.resolve() for path in roots}, key=str)
    for root in unique:
        if not (root / "manifest.json").is_file():
            raise FileNotFoundError(root / "manifest.json")
    return unique


def _material_from_direct(root: Path, row: Mapping[str, Any]) -> ContributionMaterial:
    content_location = str(row["content_location"])
    evidence_ref = str(row["evidence_ref"])
    content_path = _inside(root, content_location)
    evidence_path = _inside(root, evidence_ref)
    if not content_path.is_file() or not evidence_path.is_file():
        raise ValueError(
            "historical contribution is missing content or identity evidence"
        )
    expected = str(row["content_sha256"]).casefold()
    if sha256_file(content_path) != expected:
        raise ValueError(f"historical content hash mismatch: {content_path}")
    key = [str(row["repository"]).casefold(), str(row["commit_sha"]), str(row["path"])]
    event_at = _timestamp(row["event_at"], "event_at").isoformat()
    author_at = (
        _timestamp(row["author_at"], "author_at").isoformat()
        if row.get("author_at")
        else None
    )
    return ContributionMaterial(
        github_numeric_id=int(row["github_numeric_id"]),
        repository=str(row["repository"]),
        commit_sha=str(row["commit_sha"]),
        path=str(row["path"]),
        event_at=event_at,
        author_at=author_at,
        content_sha256=expected,
        root=root,
        content_location=content_location,
        evidence_ref=evidence_ref,
        file_version_key=str(
            row.get("file_version_key") or "history-file:" + _digest(key)
        ),
    )


def _materials(
    repo_root: Path, roots: Sequence[Path]
) -> dict[int, list[ContributionMaterial]]:
    by_identity: dict[int, dict[tuple[str, str, str], ContributionMaterial]] = (
        defaultdict(dict)
    )
    for root in roots:
        merged = (root / "file_contributions.jsonl").is_file()
        name = (
            "file_contributions.jsonl" if merged else "historical_contributions.jsonl"
        )
        path = _verify_manifest_file(root, name)
        mapping = _read_json(root / "manifest.json").get("source_roots", {})
        for row in _rows(path):
            if (
                not row.get("event_at")
                or row.get("content_available", True) is not True
            ):
                continue
            identity = row.get("github_numeric_id")
            if isinstance(identity, bool) or not isinstance(identity, int):
                continue
            if merged:
                options = [
                    source
                    for source in row.get("sources", [])
                    if isinstance(source, dict)
                    and source.get("content_location")
                    and source.get("evidence_ref")
                ]
                if not options:
                    continue
                material = None
                for source in sorted(
                    options,
                    key=lambda item: (
                        str(item.get("source")),
                        str(item.get("content_location")),
                        str(item.get("evidence_ref")),
                    ),
                ):
                    origin_relative = mapping.get(str(source["source"]))
                    if not isinstance(origin_relative, str):
                        continue
                    origin = _inside(repo_root, origin_relative)
                    try:
                        material = _material_from_direct(
                            origin,
                            {
                                **source,
                                "github_numeric_id": row["github_numeric_id"],
                                "repository": row["repository"],
                                "commit_sha": row["commit_sha"],
                                "path": row["path"],
                                "event_at": row["event_at"],
                                "author_at": row.get("author_at"),
                                "content_sha256": row["content_sha256"],
                            },
                        )
                        break
                    except (FileNotFoundError, KeyError, ValueError):
                        continue
                if material is None:
                    continue
            else:
                try:
                    material = _material_from_direct(root, row)
                except (FileNotFoundError, KeyError, ValueError):
                    continue
            existing = by_identity[material.github_numeric_id].get(material.key)
            if existing is not None and (
                existing.content_sha256 != material.content_sha256
                or existing.event_at != material.event_at
            ):
                raise ValueError(f"conflicting historical contribution: {material.key}")
            by_identity[material.github_numeric_id].setdefault(material.key, material)
    return {
        identity: sorted(values.values(), key=lambda item: item.key)
        for identity, values in by_identity.items()
    }


def _formal_profiles(
    *,
    repo_root: Path,
    handoff_root: Path,
    questionnaire_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    outcomes = {
        row["outcome_id"]: row for row in _rows(handoff_root / "outcome_index.jsonl")
    }
    pairs = _rows(handoff_root / "comparison_index.jsonl")
    groups: dict[tuple[int, str], str] = {}
    for pair in pairs:
        outcome = outcomes[str(pair["outcome_id"])]
        key = (int(outcome["github_numeric_id"]), str(outcome["profile_as_of"]))
        groups[key] = str(outcome["developer_key"]).strip().casefold()

    roots = _history_roots(repo_root, handoff_root)
    history = _materials(repo_root, roots)
    questionnaire = load_questionnaire_contract(questionnaire_path)
    profiles: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for (identity, as_of), expected_login in sorted(
        groups.items(), key=lambda item: item[0]
    ):
        cutoff = _timestamp(as_of, "profile_as_of")
        eligible = [
            item
            for item in history.get(identity, [])
            if _timestamp(item.event_at, "event_at") < cutoff
            and (
                item.author_at is None
                or _timestamp(item.author_at, "author_at") < cutoff
            )
        ]
        record_id = f"{identity}@{as_of}"
        try:
            if not eligible:
                raise ValueError(
                    "profile has no attributable pre-cutoff historical code"
                )
            source_roots: dict[str, Path] = {}
            contribution_index: dict[tuple[str, str, str], dict[str, Any]] = {}
            for index, item in enumerate(eligible):
                source_name = f"source_{index:04d}"
                source_roots[source_name] = item.root
                contribution_index[item.key] = {
                    "github_numeric_id": identity,
                    "repository": item.repository,
                    "commit_sha": item.commit_sha,
                    "path": item.path,
                    "event_at": item.event_at,
                    "content_available": True,
                    "content_sha256": item.content_sha256,
                    "sources": [
                        {
                            "source": source_name,
                            "github_numeric_id": identity,
                            "event_at": item.event_at,
                            "content_sha256": item.content_sha256,
                            "content_location": item.content_location,
                            "evidence_ref": item.evidence_ref,
                            "file_version_key": item.file_version_key,
                        }
                    ],
                }
            profile, audit = build_temporal_training_profile(
                {
                    "github_numeric_id": identity,
                    "cutoff_at": as_of,
                    "split": "train",
                    "x_contribution_keys": [list(item.key) for item in eligible],
                },
                contributions_by_key=contribution_index,
                source_roots=source_roots,
                questionnaire=questionnaire,
                delivery_status="research_frozen_candidate",
                dataset_version=SUPPLEMENT_DATASET_VERSION,
                rules_version=TEMPORAL_PROFILE_RULES_VERSION,
            )
            if profile["developer_key"] != expected_login:
                raise ValueError(
                    "verified contribution identity disagrees with outcome developer_key"
                )
            profiles.append(profile)
            audits.append(audit)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            failures.append(
                {
                    "record_id": record_id,
                    "code": "profile_generation_failed",
                    "detail": str(exc),
                }
            )
    profiles.sort(key=lambda row: (row["as_of"], row["github_numeric_id"]))
    audits.sort(key=lambda row: (row["as_of"], row["github_numeric_id"]))
    return profiles, audits, failures


def _candidate_key(row: Mapping[str, Any]) -> str:
    return "historical-candidate:" + _digest(
        {
            "issue_key": row["issue_key"],
            "as_of": row["as_of"],
            "content_sha256": row["content_sha256"],
        }
    )


def _candidate_row(
    raw: Mapping[str, Any],
    *,
    role: str,
    evidence_ref: str,
) -> dict[str, Any]:
    issue_key = normalize_issue_key(raw.get("issue_key"))
    as_of = str(raw["as_of"])
    _timestamp(as_of, "candidate.as_of")
    title = raw.get("title_at_cutoff")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("candidate title_at_cutoff must be non-empty")
    body = (
        raw.get("body_at_cutoff") if isinstance(raw.get("body_at_cutoff"), str) else ""
    )
    expected = str(raw["content_sha256"]).casefold()
    if _content_sha256(title, body) != expected:
        raise ValueError(f"candidate content hash mismatch: {issue_key}@{as_of}")
    row = {
        "schema_version": "advanced-versioned-historical-snapshot-v0.1",
        "issue_key": issue_key,
        "as_of": as_of,
        "available_from": as_of,
        "available_until": (
            _timestamp(as_of, "candidate.as_of") + timedelta(seconds=1)
        ).isoformat(),
        "content_sha256": expected,
        "title": title,
        "body_text": body,
        "source_url": str(raw["source_url"]),
        "evidence_ref": evidence_ref,
        "snapshot_role": role,
        "availability_policy": "exact_verified_snapshot_second",
    }
    row["candidate_key"] = _candidate_key(row)
    return row


def _outcomes_and_candidates(
    *,
    repo_root: Path,
    handoff_root: Path,
    profiles: Sequence[Mapping[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    outcome_index = {
        str(row["outcome_id"]): row
        for row in _rows(handoff_root / "outcome_index.jsonl")
    }
    pairs = _rows(handoff_root / "comparison_index.jsonl")
    profile_by_identity = {
        (int(row["github_numeric_id"]), str(row["as_of"])): row for row in profiles
    }
    controls_by_root: dict[Path, dict[tuple[str, str], dict[str, Any]]] = {}
    candidates: dict[str, dict[str, Any]] = {}
    outcomes: list[dict[str, Any]] = []
    pair_plan: list[dict[str, Any]] = []
    cutoff_counts: Counter[str] = Counter()

    def retain_candidate(candidate: dict[str, Any]) -> None:
        existing = candidates.get(candidate["candidate_key"])
        if existing is None:
            candidates[candidate["candidate_key"]] = candidate
            return
        stable_fields = (
            "issue_key",
            "as_of",
            "available_from",
            "available_until",
            "content_sha256",
            "title",
            "body_text",
            "source_url",
        )
        if any(existing[field] != candidate[field] for field in stable_fields):
            raise ValueError("conflicting content for one versioned candidate key")

    for pair in sorted(pairs, key=lambda row: str(row["outcome_id"])):
        raw_outcome = outcome_index[str(pair["outcome_id"])]
        identity = int(raw_outcome["github_numeric_id"])
        as_of = str(pair["as_of"])
        profile = profile_by_identity[(identity, as_of)]
        developer_key = str(profile["developer_key"])
        if developer_key != str(raw_outcome["developer_key"]).strip().casefold():
            raise ValueError(
                "outcome developer_key does not match the verified profile"
            )

        outcome = {
            "schema_version": "advanced-issue-outcome-v0.1",
            "outcome_id": str(raw_outcome["outcome_id"]),
            "developer_key": developer_key,
            "issue_key": normalize_issue_key(raw_outcome["issue_key"]),
            "outcome_type": str(raw_outcome["outcome_type"]),
            "interaction_at": str(raw_outcome["interaction_at"]),
            "outcome_at": str(raw_outcome["outcome_at"]),
            "verification_status": str(raw_outcome["verification_status"]),
            "source_url": str(raw_outcome["pr_url"]),
        }
        outcomes.append(outcome)

        positive_raw = dict(raw_outcome["issue_content"])
        positive_raw["issue_key"] = raw_outcome["issue_key"]
        positive = _candidate_row(
            positive_raw,
            role="positive",
            evidence_ref=f"{raw_outcome.get('source_root')}:{raw_outcome['evidence_ref']}",
        )
        retain_candidate(positive)

        history_root = _inside(repo_root, str(pair["history_source_root"]))
        if history_root not in controls_by_root:
            controls_path = _verify_manifest_file(
                history_root, "historical_controls.jsonl"
            )
            controls_by_root[history_root] = {
                (normalize_issue_key(row["issue_key"]), str(row["as_of"])): row
                for row in _rows(controls_path)
            }
        comparison_rows: list[dict[str, str]] = []
        for issue_key_value in pair["comparison_issue_keys"]:
            issue_key = normalize_issue_key(issue_key_value)
            raw_control = controls_by_root[history_root][(issue_key, as_of)]
            control = _candidate_row(
                raw_control,
                role="unlabeled_control",
                evidence_ref=(
                    history_root.relative_to(repo_root).as_posix()
                    + ":"
                    + str(raw_control["evidence_ref"])
                ),
            )
            retain_candidate(control)
            comparison_rows.append(
                {
                    "issue_key": issue_key,
                    "candidate_key": str(control["candidate_key"]),
                }
            )

        interaction = _timestamp(outcome["interaction_at"], "interaction_at")
        cutoff = _timestamp(as_of, "profile_as_of")
        policy = (
            "pre_interaction_minus_1s"
            if (interaction - cutoff).total_seconds() == 1
            else "calendar_quarter"
        )
        cutoff_counts[policy] += 1
        pair_plan.append(
            {
                "schema_version": "advanced-supplement-pair-plan-v0.1",
                "outcome_id": outcome["outcome_id"],
                "developer_key": developer_key,
                "profile_key": profile["profile_key"],
                "profile_version": profile["profile_version"],
                "profile_hash": profile["profile_hash"],
                "profile_as_of": as_of,
                "positive_issue_key": outcome["issue_key"],
                "positive_candidate_key": positive["candidate_key"],
                "comparisons": comparison_rows,
                "cutoff_policy": policy,
            }
        )

    exclusions = _rows(handoff_root / "evidence_gaps.jsonl")
    pair_plan.sort(key=lambda row: row["outcome_id"])
    outcomes.sort(key=lambda row: row["outcome_id"])
    candidate_rows = sorted(candidates.values(), key=lambda row: row["candidate_key"])
    return outcomes, candidate_rows, pair_plan, exclusions


def _encode(
    candidates: Sequence[Mapping[str, Any]],
    *,
    model_dir: Path,
    mapping_dir: Path,
    output_path: Path,
    device: str,
    max_segments: int,
    segment_batch_size: int,
) -> dict[str, Any]:
    vectors, metadata = encode_candidates(
        candidates,
        model_dir=model_dir,
        mapping_dir=mapping_dir,
        device=device,
        max_segments=max_segments,
        segment_batch_size=segment_batch_size,
    )
    with output_path.open("wb") as handle:
        np.savez_compressed(
            handle,
            issue_keys=np.asarray([row["candidate_key"] for row in candidates]),
            issue_embeddings=vectors,
            content_sha256=np.asarray([row["content_sha256"] for row in candidates]),
        )
    with np.load(output_path) as check:
        if check["issue_embeddings"].shape != vectors.shape:
            raise RuntimeError("supplement issue cache verification failed")
    return metadata


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    handoff_root = Path(args.handoff_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be absent or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    handoff_manifest = _read_json(handoff_root / "manifest.json")
    for relative, expected in handoff_manifest.get("files_sha256", {}).items():
        path = _inside(handoff_root, str(relative))
        if sha256_file(path) != expected:
            raise ValueError(f"handoff manifest hash mismatch: {path}")

    profiles, audits, profile_failures = _formal_profiles(
        repo_root=repo_root,
        handoff_root=handoff_root,
        questionnaire_path=Path(args.questionnaire).resolve(),
    )
    if profile_failures:
        raise RuntimeError(
            f"formal profile generation failed for {len(profile_failures)} rows"
        )
    outcomes, candidates, pair_plan, exclusions = _outcomes_and_candidates(
        repo_root=repo_root,
        handoff_root=handoff_root,
        profiles=profiles,
    )

    paths = {
        "profiles.jsonl": profiles,
        "profile_audit.jsonl": audits,
        "profile_failures.jsonl": profile_failures,
        "outcomes.jsonl": outcomes,
        "candidate_snapshots.jsonl": candidates,
        "pair_plan.jsonl": pair_plan,
        "exclusions.jsonl": exclusions,
    }
    for name, rows in paths.items():
        _write_jsonl(output_dir / name, rows)

    cache_path = output_dir / "supplement_issue_embeddings.npz"
    encoding = _encode(
        candidates,
        model_dir=Path(args.model_dir),
        mapping_dir=Path(args.mapping_dir),
        output_path=cache_path,
        device=args.device,
        max_segments=args.max_segments,
        segment_batch_size=args.segment_batch_size,
    )
    policy_counts = Counter(row["cutoff_policy"] for row in pair_plan)
    observed = [sum(bool(value) for value in row["observed_mask"]) for row in profiles]
    manifest = {
        "schema_version": SUPPLEMENT_SCHEMA_VERSION,
        "dataset_version": SUPPLEMENT_DATASET_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": args.source_commit,
        "source_handoff": str(handoff_root),
        "source_handoff_manifest_sha256": sha256_file(handoff_root / "manifest.json"),
        "profile_state": "research_frozen",
        "delivery_status": "research_frozen_candidate",
        "training_allowed": False,
        "statistics": {
            "profiles": len(profiles),
            "developers": len({row["developer_key"] for row in profiles}),
            "outcomes": len(outcomes),
            "candidate_versions": len(candidates),
            "comparison_pairs": sum(len(row["comparisons"]) for row in pair_plan),
            "excluded_outcomes": len(exclusions),
            "cutoff_policies": dict(sorted(policy_counts.items())),
            "observed_dimensions_min": min(observed),
            "observed_dimensions_mean": sum(observed) / len(observed),
            "observed_dimensions_max": max(observed),
            "unique_model_inputs": len(
                {_canonical([row["values"], row["observed_mask"]]) for row in profiles}
            ),
        },
        "encoding": encoding,
        "parameters": {
            "max_segments": args.max_segments,
            "segment_batch_size": args.segment_batch_size,
            "device": args.device,
        },
        "artifacts": {name: sha256_file(output_dir / name) for name in paths}
        | {"supplement_issue_embeddings.npz": sha256_file(cache_path)},
        "limitations": [
            "Commit event time is used; first public availability is not independently established.",
            "Unlabeled controls are comparison samples, not explicit negative feedback.",
            "Quarter-cutoff and pre-interaction-cutoff rows must be reported separately.",
            "This candidate bundle requires C validation before training promotion.",
        ],
    }
    _write_json(output_dir / "supplement_manifest.json", manifest)
    (output_dir / "_PREPARED").write_text(
        sha256_file(output_dir / "supplement_manifest.json") + "\n", encoding="ascii"
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--handoff-root", required=True)
    parser.add_argument("--questionnaire", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--mapping-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-segments", type=int, default=12)
    parser.add_argument("--segment-batch-size", type=int, default=128)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_segments <= 0 or args.segment_batch_size <= 0:
        raise SystemExit("segment limits must be positive")
    print(json.dumps(prepare(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
