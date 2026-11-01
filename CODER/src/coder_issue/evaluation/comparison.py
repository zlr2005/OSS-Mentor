"""Protocol-aware summaries for completed and incomplete CODER runs."""

from __future__ import annotations

import csv
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _terminal_status(run_dir: Path) -> tuple[str, dict[str, Any]]:
    for marker, status in (
        ("_SUCCESS", "success"),
        ("_FAILED", "failed"),
        ("_CANCELLED", "cancelled"),
        ("_RUNNING", "incomplete"),
    ):
        payload = _read_json(run_dir / marker)
        if payload is not None:
            return status, dict(payload)
    return "unknown", {}


def _trial_summary(run_dir: Path, manifest: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    selection = _read_json(run_dir / "validation_selection.json")
    if isinstance(selection, list) and selection:
        best = max(selection, key=lambda item: float(item.get("score", float("-inf"))))
        trial_budget = len(selection)
    else:
        best = {}
        trial_budget = state.get("total_trials")
    return {
        "trial_budget": trial_budget,
        "best_trial": manifest.get("best_trial", best.get("trial")),
        "best_epoch": manifest.get("best_epoch", best.get("best_epoch")),
        "best_validation_ndcg@10": manifest.get(
            "best_validation_score", best.get("score", state.get("best_validation_score"))
        ),
        "training_time_seconds": state.get("elapsed_seconds"),
        "best_checkpoint": manifest.get("best_checkpoint", best.get("best_checkpoint")),
        "selected_parameters": manifest.get(
            "selected_parameters", best.get("parameters", state.get("parameters"))
        ),
    }


def _metric_group(
    metrics: dict[str, Any] | None,
    *,
    split: str,
    mode: str,
    group: str,
) -> dict[str, Any] | None:
    try:
        value = metrics["issue"][split][mode][group]
    except (KeyError, TypeError):
        return None
    return dict(value) if isinstance(value, dict) else None


def _protocol_identity(
    manifest: dict[str, Any],
    *,
    split: str,
    mode: str,
    group: str,
) -> tuple[str | None, dict[str, Any]]:
    hashes = manifest.get("input_sha256") or {}
    candidate_hash = hashes.get(f"candidate_{split}_{mode}")
    protocol = {
        "dataset_version": manifest.get("dataset_version"),
        "graph_version": manifest.get("graph_version"),
        "target": "issue",
        "split": split,
        "candidate_mode": mode,
        "group": group,
        "candidate_sha256": candidate_hash,
        "selection_candidate": (manifest.get("training_strategy") or {}).get(
            "validation_candidate", "valid_cross_repo.parquet"
        ),
        "selection_group": (manifest.get("training_strategy") or {}).get(
            "validation_group", "predictable"
        ),
        "selection_metric": (manifest.get("training_strategy") or {}).get(
            "selection_metric", "ndcg@10"
        ),
    }
    required = ("dataset_version", "graph_version", "candidate_sha256")
    if any(protocol[key] is None for key in required):
        return None, protocol
    digest = hashlib.sha256(
        json.dumps(protocol, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return digest, protocol


def build_run_comparison(
    run_dirs: Iterable[Path],
    *,
    split: str = "test",
    mode: str = "cross_repo",
    group: str = "predictable",
    cutoff: int = 10,
) -> dict[str, Any]:
    """Build rows without inventing values for incomplete or incompatible runs."""

    rows: list[dict[str, Any]] = []
    protocols: dict[str, dict[str, Any]] = {}
    for run_dir in run_dirs:
        # Do not call Path.resolve() here: the Windows sandbox/junction used by
        # Codex can mis-resolve non-ASCII workspace paths even though direct
        # filesystem access is valid.
        run_dir = Path(run_dir)
        status, state = _terminal_status(run_dir)
        manifest = _read_json(run_dir / "run_manifest.json") or {}
        metrics = _read_json(run_dir / "metrics.json")
        metric_group = _metric_group(metrics, split=split, mode=mode, group=group)
        protocol_id, protocol = _protocol_identity(
            manifest, split=split, mode=mode, group=group
        )
        if protocol_id is not None:
            protocols.setdefault(protocol_id, protocol)
        summary = _trial_summary(run_dir, manifest, state)
        missing_reason = None
        if status != "success":
            missing_reason = f"run_status={status}"
        elif metric_group is None:
            missing_reason = "requested metric group is absent"
        elif protocol_id is None:
            missing_reason = "protocol identity is incomplete"
        row = {
            "model": manifest.get("variant", state.get("variant")),
            "variant": manifest.get("variant", state.get("variant")),
            "run_id": manifest.get("run_id", state.get("run_id", run_dir.name)),
            "status": status,
            **summary,
            "protocol_id": protocol_id,
            "metrics_available": metric_group is not None,
            "missing_reason": missing_reason,
            f"recall@{cutoff}": (
                None if metric_group is None else metric_group.get(f"recall@{cutoff}")
            ),
            f"ndcg@{cutoff}": (
                None if metric_group is None else metric_group.get(f"ndcg@{cutoff}")
            ),
            f"hit@{cutoff}": (
                None if metric_group is None else metric_group.get(f"hit@{cutoff}")
            ),
            "query_count": None if metric_group is None else metric_group.get("query_count"),
            "candidate_sha256": protocol.get("candidate_sha256"),
        }
        rows.append(row)
    protocol_counts = Counter(
        row["protocol_id"]
        for row in rows
        if row["status"] == "success"
        and row["metrics_available"]
        and row["protocol_id"] is not None
    )
    comparable_protocols = {
        protocol_id for protocol_id, count in protocol_counts.items() if count >= 2
    }
    for row in rows:
        row["comparable_in_main_table"] = (
            row["status"] == "success"
            and row["metrics_available"]
            and row["protocol_id"] is not None
            and protocol_counts[row["protocol_id"]] >= 2
        )
    return {
        "schema_version": 1,
        "target": "issue",
        "split": split,
        "candidate_mode": mode,
        "group": group,
        "cutoff": cutoff,
        "protocols": protocols,
        "rows": rows,
        "main_table_protocol_count": len(comparable_protocols),
        "main_table_is_protocol_homogeneous": len(comparable_protocols) <= 1,
    }


def write_run_comparison(
    report: dict[str, Any],
    *,
    json_path: Path,
    csv_path: Path,
    overwrite: bool = False,
) -> None:
    for path in (json_path, csv_path):
        if path.exists() and not overwrite:
            raise FileExistsError(f"refusing to overwrite comparison output: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rows = list(report["rows"])
    fieldnames = list(rows[0]) if rows else ["run_id", "status"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
