"""Import an offline candidate-inventory handoff into an isolated platform DB."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from oss_mentor.candidate_admission import EXPANDED_CONTENT_POLICY
from oss_mentor.candidate_freshness import (
    inventory_assumption,
    parse_timestamp,
    recommendation_is_current,
)
from oss_mentor.candidate_rules import has_beginner_label
from oss_mentor.gated_scoring import content_hash
from oss_mentor.storage.candidates import SQLiteCandidateStorage
from oss_mentor.task_features import extract_task_features
from oss_mentor.task_readiness import task_review_reasons


class HandoffValidationError(ValueError):
    """The handoff is incomplete, mutated or internally inconsistent."""


_REQUIRED_ROW_FIELDS = frozenset(
    {
        "task_candidate_id",
        "source_task_candidate_ids",
        "repository",
        "issue_number",
        "github_issue_id",
        "issue_key",
        "title",
        "body_text",
        "url",
        "availability",
        "verified_at",
        "language",
        "task_types",
        "skill_requirements",
        "eligibility",
        "task_feature_version",
        "recommendable_under_assumption",
        "content_hash",
        "source_content_hash",
        "redacted_fields",
    }
)
_HANDOFF_WARNINGS = (
    "inventory_availability_assumed",
    "offline_inventory_handoff_not_online_reverified",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HandoffValidationError(f"cannot read JSON file: {path}") from exc


def _safe_manifest_path(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if root.resolve() not in path.parents:
        raise HandoffValidationError(f"manifest path escapes handoff root: {name}")
    return path


def _validate_manifest(
    handoff: Path,
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    manifest_path = handoff / "manifest.json"
    checksum_path = handoff / "manifest.sha256"
    if not manifest_path.is_file() or not checksum_path.is_file():
        raise HandoffValidationError("handoff manifest and manifest.sha256 are required")
    try:
        expected_manifest_sha = checksum_path.read_text(encoding="ascii").split()[0]
    except (OSError, IndexError) as exc:
        raise HandoffValidationError("invalid manifest.sha256") from exc
    actual_manifest_sha = _sha256(manifest_path)
    if actual_manifest_sha != expected_manifest_sha:
        raise HandoffValidationError("manifest.json checksum mismatch")

    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise HandoffValidationError("manifest files list is required")
    if manifest.get("inventory_availability_assumed") is not True:
        raise HandoffValidationError("manifest is not an inventory-assumption handoff")
    if manifest.get("credential_scan") != "passed":
        raise HandoffValidationError("manifest credential scan did not pass")

    listed = {}
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise HandoffValidationError("invalid manifest file entry")
        name = str(entry.get("path") or "")
        if not name or name in listed:
            raise HandoffValidationError(f"duplicate or empty manifest path: {name}")
        path = _safe_manifest_path(handoff, name)
        if not path.is_file():
            raise HandoffValidationError(f"manifest file is missing: {name}")
        if path.stat().st_size != int(entry.get("size_bytes", -1)):
            raise HandoffValidationError(f"manifest size mismatch: {name}")
        if _sha256(path) != str(entry.get("sha256") or ""):
            raise HandoffValidationError(f"manifest file checksum mismatch: {name}")
        listed[name] = path
    for required in ("report.json", "validation.json", "recommendable_under_assumption.json"):
        if required not in listed:
            raise HandoffValidationError(f"manifest does not include {required}")

    report = _read_json(listed["report.json"])
    validation = _read_json(listed["validation.json"])
    if not isinstance(report, dict) or not isinstance(validation, dict):
        raise HandoffValidationError("invalid handoff report or validation document")
    if report.get("inventory_availability_assumed") is not True:
        raise HandoffValidationError("report is not an inventory-assumption handoff")
    if report.get("online_reverified_this_export") is not False:
        raise HandoffValidationError("report must state that online reverification was not run")
    if validation.get("status") != "passed":
        raise HandoffValidationError("handoff validation status is not passed")
    return report, actual_manifest_sha, listed, manifest


def _validate_rows(
    rows: Any,
    *,
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise HandoffValidationError("recommendable handoff must be a non-empty list")
    if int(report.get("recommendable_under_assumption", -1)) != len(rows):
        raise HandoffValidationError("recommendable count differs from report")

    issue_keys: set[str] = set()
    issue_ids: set[str] = set()
    default_ids: set[int] = set()
    web_ids: set[int] = set()
    for row in rows:
        if not isinstance(row, dict) or not _REQUIRED_ROW_FIELDS <= set(row):
            raise HandoffValidationError("handoff row is missing required fields")
        task_id = row["task_candidate_id"]
        issue_id = row["github_issue_id"]
        mapping = row["source_task_candidate_ids"]
        if not isinstance(mapping, dict):
            raise HandoffValidationError("source task ID mapping must be an object")
        web_id = mapping.get("oss_mentor_web")
        default_id = mapping.get("oss_mentor")
        repository = row["repository"]
        issue_number = row["issue_number"]
        if not isinstance(repository, str) or "/" not in repository:
            raise HandoffValidationError("invalid repository name")
        if isinstance(issue_number, bool) or not isinstance(issue_number, int) or issue_number < 1:
            raise HandoffValidationError("invalid issue_number")
        if issue_id is not None and (
            isinstance(issue_id, bool) or not isinstance(issue_id, int) or issue_id < 1
        ):
            raise HandoffValidationError("invalid github_issue_id")
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1:
            raise HandoffValidationError("invalid default task_candidate_id")
        if default_id != task_id:
            raise HandoffValidationError("default task_candidate_id mapping changed")
        if isinstance(web_id, bool) or not isinstance(web_id, int) or web_id < 1:
            raise HandoffValidationError("web task_candidate_id mapping is required")
        if issue_id is not None and str(issue_id) not in issue_ids:
            issue_ids.add(str(issue_id))
        elif issue_id is not None:
            raise HandoffValidationError("duplicate github_issue_id")
        key = str(row["issue_key"]).strip().casefold()
        expected_key = f"{repository.casefold()}#{issue_number}"
        if not key or key != expected_key or key in issue_keys:
            raise HandoffValidationError("invalid, duplicate or empty issue_key")
        issue_keys.add(key)
        if task_id in default_ids or web_id in web_ids:
            raise HandoffValidationError("duplicate default or web candidate mapping")
        default_ids.add(task_id)
        web_ids.add(web_id)
        if row["availability"] != "available" or row["eligibility"] != "eligible":
            raise HandoffValidationError("recommendable handoff contains a non-eligible task")
        if row["recommendable_under_assumption"] is not True:
            raise HandoffValidationError("recommendable flag is not true")
        if not isinstance(row["task_types"], list) or not all(
            isinstance(value, str) and value for value in row["task_types"]
        ):
            raise HandoffValidationError("task types must be a list of names")
        if not isinstance(row["skill_requirements"], list) or not all(
            isinstance(item, dict) for item in row["skill_requirements"]
        ):
            raise HandoffValidationError("task types and skill requirements must be lists")
        names = [str(item.get("skill_name") or "").casefold() for item in row["skill_requirements"]]
        if not all(names) or len(names) != len(set(names)):
            raise HandoffValidationError("invalid or duplicate skill requirement")
        if not isinstance(row["title"], str) or not row["title"].strip():
            raise HandoffValidationError("title is required")
        if not isinstance(row["body_text"], str) or not isinstance(row["url"], str):
            raise HandoffValidationError("body_text and url must be strings")
        if parse_timestamp(row["verified_at"]) is None:
            raise HandoffValidationError("verified_at must be timezone-aware")
        if not isinstance(row["redacted_fields"], list):
            raise HandoffValidationError("redacted_fields must be a list")
        if "labels" in row:
            labels = row["labels"]
            if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
                raise HandoffValidationError("labels must be a list of strings")
        for field in ("created_at", "last_activity_at"):
            if field in row and row[field] not in (None, ""):
                if not isinstance(row[field], str) or parse_timestamp(row[field]) is None:
                    raise HandoffValidationError(f"{field} must be timezone-aware")
        if "newcomer_label_signal" in row and row["newcomer_label_signal"] not in (0, 1, False, True):
            raise HandoffValidationError("newcomer_label_signal must be 0 or 1")
        if "source_recommendable_under_assumption_v1" in row and not isinstance(
            row["source_recommendable_under_assumption_v1"], bool
        ):
            raise HandoffValidationError(
                "source_recommendable_under_assumption_v1 must be boolean"
            )
        if "handoff_review_reasons" in row:
            if not isinstance(row["handoff_review_reasons"], list) or row["handoff_review_reasons"]:
                raise HandoffValidationError(
                    "recommendable subset contains handoff review exclusions"
                )
        computed_hash = content_hash(row)
        if computed_hash != row["content_hash"]:
            raise HandoffValidationError(f"content hash mismatch: {row['issue_key']}")
        source_hash = str(row["source_content_hash"] or "")
        if len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash.lower()):
            raise HandoffValidationError("invalid source_content_hash")
    return [dict(row) for row in rows]


def load_recommendable_handoff(handoff: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate every manifest entry and return the recommendable handoff rows."""

    handoff = Path(handoff).resolve()
    report, manifest_sha, files, manifest = _validate_manifest(handoff)
    rows = _validate_rows(
        _read_json(files["recommendable_under_assumption.json"]),
        report=report,
    )
    return rows, {
        "dataset_version": str(manifest.get("dataset_version") or handoff.name),
        "manifest_sha256": manifest_sha,
        "report": report,
    }


def _feature_values(
    row: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = {
        "title": row["title"],
        "body_text": row["body_text"],
        "labels": list(row.get("labels") or []),
        "primary_language": row["language"],
        "comment_count": 0,
        "candidate_eligibility": "eligible",
        # A's recommendable subset already passed the source platform admission
        # gate. Preserve that decision while keeping all uncertainty visible.
        "admission_policy": EXPANDED_CONTENT_POLICY,
    }
    features = extract_task_features(record)
    evidence = dict(features.feature_evidence)
    evidence["a_inventory_handoff"] = {
        "source_task_candidate_ids": row["source_task_candidate_ids"],
        "handoff_content_hash": row["content_hash"],
        "source_content_hash": row["source_content_hash"],
        "redacted_fields": row["redacted_fields"],
        "source_newcomer_label_signal": row.get("newcomer_label_signal"),
        "labels_exported": "labels" in row,
        "created_at_exported": "created_at" in row,
        "last_activity_at_exported": "last_activity_at" in row,
        "task_feature_version": row["task_feature_version"],
        "features_recomputed_from_delivery_text": True,
        "admission_policy_source": "recommendable_under_assumption",
        "inventory_availability_assumed": True,
        "online_reverified_this_export": False,
    }
    return features.to_dict(), evidence


def _row_warnings(row: dict[str, Any]) -> list[str]:
    warnings = list(_HANDOFF_WARNINGS)
    if "labels" not in row:
        warnings.extend((
            "handoff_omits_issue_labels_and_activity",
            "newcomer_signal_not_reconstructable",
        ))
    elif row.get("created_at") in (None, ""):
        warnings.append("handoff_created_at_missing")
    if "last_activity_at" not in row or row.get("last_activity_at") in (None, ""):
        warnings.append("handoff_last_activity_missing")
    if row["redacted_fields"]:
        warnings.append("handoff_public_text_redacted")
    return warnings


def import_recommendable_handoff(
    handoff: Path,
    database: Path,
    *,
    assume_inventory_available: bool,
    allow_existing: bool = False,
) -> dict[str, Any]:
    """Import A's recommendable JSON rows without modifying the handoff package."""

    if not assume_inventory_available:
        raise ValueError("--assume-inventory-available is required for this handoff")
    handoff = Path(handoff).resolve()
    database = Path(database).resolve()
    if database.exists() and not allow_existing:
        raise FileExistsError("isolated destination already exists; pass --allow-existing explicitly")
    rows, source = load_recommendable_handoff(handoff)
    report = source["report"]
    manifest_sha = source["manifest_sha256"]

    root = Path(__file__).resolve().parents[2]
    store = SQLiteCandidateStorage(database, root / "db/sqlite/001_mvp.sql")
    store.initialize()
    counts: Counter[str] = Counter()
    now = datetime.now(timezone.utc).isoformat()
    imported_ids: list[int] = []
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            feature_values, evidence = _feature_values(row)
            repository_id = store.upsert_repository(
                connection,
                full_name=row["repository"],
                github_repository_id=None,
                html_url=f"https://github.com/{row['repository']}",
                ecosystems_last_synced_at=None,
                primary_language=row["language"],
                maintenance_status="unknown",
            )
            existing = connection.execute(
                """
                SELECT tc.*
                FROM task_candidate AS tc
                JOIN repository AS r USING(repository_id)
                WHERE tc.github_issue_id = ?
                   OR (r.full_name = ? COLLATE NOCASE AND tc.issue_number = ?)
                """,
                (row["github_issue_id"], row["repository"], row["issue_number"]),
            ).fetchall()
            if any(
                (
                    item["github_issue_id"] is not None
                    and str(item["github_issue_id"]) != str(row["github_issue_id"])
                )
                or int(item["issue_number"]) != int(row["issue_number"])
                for item in existing
            ):
                raise HandoffValidationError(
                    f"destination identity conflict: {row['issue_key']}"
                )
            current = existing[0] if existing else None
            source_time = parse_timestamp(row["verified_at"])
            current_time = parse_timestamp(current["github_verified_at"]) if current else None
            if current_time and (not source_time or current_time > source_time):
                counts["skipped_existing_newer"] += 1
                imported_ids.append(int(current["task_candidate_id"]))
                connection.execute(
                    """
                    UPDATE task_candidate
                    SET inventory_source = ?, inventory_record_json = ?
                    WHERE task_candidate_id = ?
                    """,
                    (
                        manifest_sha,
                        json.dumps(row, ensure_ascii=False),
                        int(current["task_candidate_id"]),
                    ),
                )
                continue

            warnings = _row_warnings(row)
            if "labels" in row:
                newcomer_signal = int(has_beginner_label(row["labels"]))
            else:
                newcomer_signal = int(current["newcomer_label_signal"]) if current else 0
            values = (
                repository_id,
                row["issue_number"],
                row["github_issue_id"],
                row["url"],
                row.get("created_at") or "",
                None,
                row["title"],
                row["body_text"],
                json.dumps(row.get("labels") or [], ensure_ascii=False, sort_keys=True),
                "open",
                "unassigned",
                0,
                0,
                0,
                row.get("last_activity_at"),
                "github_rest",
                row["verified_at"],
                row["verified_at"],
                "eligible",
                "[]",
                json.dumps(warnings, ensure_ascii=False),
                newcomer_signal,
                "candidate-eligibility-v0.1",
                now,
                int(feature_values["has_reproduction_steps"]),
                int(feature_values["has_acceptance_criteria"]),
                int(feature_values["has_expected_behavior"]),
                int(feature_values["has_affected_module_hint"]),
                json.dumps(row["task_types"], ensure_ascii=False),
                feature_values["text_clarity_score"],
                feature_values["estimated_code_difficulty"],
                feature_values["estimated_setup_difficulty"],
                feature_values["estimated_project_context_difficulty"],
                feature_values["estimated_collaboration_difficulty"],
                feature_values["estimated_effort_bucket"],
                feature_values["novice_fit_probability"],
                feature_values["newcomer_score"],
                feature_values["growth_value_score"],
                json.dumps(evidence, ensure_ascii=False),
                now,
                row["task_feature_version"],
                "available",
                "[]",
                row["verification_expires_at"],
                "availability_assumed_offline_handoff",
                manifest_sha,
                json.dumps(row, ensure_ascii=False),
            )
            columns = (
                "repository_id,issue_number,github_issue_id,html_url,created_at,"
                "author_association,title,body_text,labels_json,state,"
                "assignment_state,is_locked,has_linked_open_pr,comment_count,"
                "last_activity_at,source_system,source_fetched_at,github_verified_at,"
                "candidate_eligibility,ineligibility_reasons_json,warnings_json,"
                "newcomer_label_signal,feature_definition_version,normalized_at,"
                "has_reproduction_steps,has_acceptance_criteria,"
                "has_expected_behavior,has_affected_module_hint,task_types_json,"
                "text_clarity_score,estimated_code_difficulty,"
                "estimated_setup_difficulty,estimated_project_context_difficulty,"
                "estimated_collaboration_difficulty,estimated_effort_bucket,"
                "novice_fit_probability,newcomer_score,growth_value_score,"
                "feature_evidence_json,feature_extracted_at,task_feature_version,"
                "candidate_availability,availability_reasons_json,"
                "verification_expires_at,inventory_review_status,inventory_source,"
                "inventory_record_json"
            )
            assignments = ",".join(
                f"{column}=excluded.{column}"
                for column in columns.split(",")
                if column not in {"repository_id", "issue_number"}
            )
            connection.execute(
                f"INSERT INTO task_candidate({columns}) "
                f"VALUES({','.join('?' for _ in values)}) "
                f"ON CONFLICT(repository_id,issue_number) DO UPDATE SET {assignments}",
                values,
            )
            task_candidate_id = int(
                connection.execute(
                    "SELECT task_candidate_id FROM task_candidate "
                    "WHERE repository_id = ? AND issue_number = ?",
                    (repository_id, row["issue_number"]),
                ).fetchone()[0]
            )
            connection.execute(
                "DELETE FROM task_skill_requirement WHERE task_candidate_id = ?",
                (task_candidate_id,),
            )
            connection.executemany(
                """
                INSERT INTO task_skill_requirement(
                    task_candidate_id, skill_name, minimum_level, importance,
                    requirement_source, feature_version
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        task_candidate_id,
                        item["skill_name"],
                        int(item["minimum_level"]),
                        float(item["importance"]),
                        str(item["requirement_source"]),
                        str(item["feature_version"]),
                    )
                    for item in row["skill_requirements"]
                ],
            )
            counts["updated" if current else "inserted"] += 1
            imported_ids.append(task_candidate_id)

        summary = {
            "status": "passed",
            "handoff_path": str(handoff),
            "handoff_manifest_sha256": manifest_sha,
            "required_field_missing_counts": report.get("required_field_missing_counts", {}),
            "source_rows": len(rows),
            "counts": dict(counts),
            "inventory_availability_assumed": True,
            "online_reverified": False,
        }
        connection.execute(
            """
            INSERT INTO candidate_inventory_import(
                snapshot_path, snapshot_sha256, imported_at, summary_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                str(handoff),
                manifest_sha,
                now,
                json.dumps(summary, ensure_ascii=False),
            ),
        )

    with inventory_assumption(True):
        matchable_ids = {
            int(task["task_candidate_id"]) for task in store.matchable_candidates()
        }
        handoff_rows: list[dict[str, Any]] = []
        with store.connect() as connection:
            candidates = connection.execute(
                """
                SELECT tc.*, r.full_name AS repository, r.primary_language,
                       r.maintenance_status
                FROM task_candidate AS tc
                JOIN repository AS r USING(repository_id)
                WHERE tc.inventory_source = ?
                """,
                (manifest_sha,),
            ).fetchall()
            requirements: dict[int, list[dict[str, Any]]] = {}
            for item in connection.execute("SELECT * FROM task_skill_requirement"):
                requirements.setdefault(int(item["task_candidate_id"]), []).append(dict(item))
        for candidate in candidates:
            record = dict(candidate)
            record["requirements"] = requirements.get(int(record["task_candidate_id"]), [])
            record["task_types"] = json.loads(record["task_types_json"])
            record["feature_evidence"] = json.loads(record["feature_evidence_json"])
            handoff_rows.append(record)
        review_reasons = Counter(
            reason for record in handoff_rows for reason in task_review_reasons(record)
        )
        blocked = [
            f"{record['repository']}#{record['issue_number']}"
            for record in handoff_rows
            if task_review_reasons(record)
        ]
        handoff_matchable = sum(
            int(record["task_candidate_id"]) in matchable_ids for record in handoff_rows
        )
    with inventory_assumption(False):
        strict_matchable = sum(
            recommendation_is_current(record)
            and not task_review_reasons(record)
            for record in handoff_rows
        )
    result = {
        "status": "passed",
        "database_path": str(database),
        "dataset_version": source["dataset_version"],
        "handoff_manifest_sha256": manifest_sha,
        "source_rows": len(rows),
        "source_repositories": len({row["repository"] for row in rows}),
        "source_default_id_mappings": sum(
            row["source_task_candidate_ids"]["oss_mentor"] == row["task_candidate_id"]
            for row in rows
        ),
        "source_web_id_mappings": sum(
            int(row["source_task_candidate_ids"]["oss_mentor_web"]) > 0
            for row in rows
        ),
        "redacted_rows": sum(bool(row["redacted_fields"]) for row in rows),
        "rows_with_labels_field": sum("labels" in row for row in rows),
        "rows_with_created_at": sum(bool(row.get("created_at")) for row in rows),
        "rows_with_last_activity_at": sum(
            bool(row.get("last_activity_at")) for row in rows
        ),
        "source_newcomer_label_signals": sum(
            bool(row.get("newcomer_label_signal")) for row in rows
        ),
        "recomputed_newcomer_label_signals": sum(
            int(record["newcomer_label_signal"]) for record in handoff_rows
        ),
        "source_newcomer_signal_comparison_available": all(
            "newcomer_label_signal" in row for row in rows
        ),
        "source_newcomer_signal_mismatches": (
            [
                row["issue_key"]
                for row in rows
                if bool(row["newcomer_label_signal"]) != bool(has_beginner_label(row["labels"]))
            ]
            if all("labels" in row and "newcomer_label_signal" in row for row in rows)
            else []
        ),
        "import_counts": dict(counts),
        "target_candidate_count": len(imported_ids),
        "handoff_candidate_count": len(handoff_rows),
        "handoff_matchable_under_assumption": handoff_matchable,
        "handoff_matchable_without_assumption": strict_matchable,
        "handoff_review_blocked": len(blocked),
        "handoff_review_reasons": dict(review_reasons),
        "handoff_review_blocked_issue_keys": blocked[:50],
        "inventory_availability_assumed": True,
        "online_reverified": False,
        "limitations": [
            "The handoff is not a fresh online GitHub inventory verification.",
            (
                "The handoff omits labels and activity metadata; newcomer gating cannot be reconstructed."
                if any("labels" not in row for row in rows)
                else "Newcomer labels are recomputed from exported labels; this is not an online freshness check."
            ),
            "Difficulty and clarity features are recomputed from handoff text, while task types and skill requirements are preserved.",
        ],
    }
    return result


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Write a JSON report atomically."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
