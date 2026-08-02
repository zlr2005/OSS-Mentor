"""Export a fixed, read-only difficulty annotation calibration set.

The exporter reads a curated set of OSS-Mentor task candidates from SQLite,
using URI ``mode=ro`` and ``PRAGMA query_only = ON``. It writes two separate
JSON files:

* an annotation file that does not expose the current difficulty prediction;
* a current-predictions file used only after human annotation is complete.

The exporter never initializes the application store, runs migrations, or
writes to SQLite.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final


ANNOTATION_SCHEMA_VERSION: Final = "difficulty_calibration_v0.1"
PREDICTION_SCHEMA_VERSION: Final = (
    "difficulty_calibration_current_predictions_v0.1"
)
ANNOTATION_GUIDE_VERSION: Final = "difficulty_annotation_guide_v0.1"
DEFAULT_DATABASE: Final = Path(
    "data/oss_mentor_task_features_v0.2_round3.sqlite3"
)
DEFAULT_ANNOTATION_OUTPUT: Final = Path(
    "data/annotations/difficulty_calibration_v0.1.json"
)
DEFAULT_PREDICTIONS_OUTPUT: Final = Path(
    "data/reports/difficulty_calibration_current_predictions_v0.1.json"
)
MAX_BODY_EXCERPT_CHARS: Final = 4000


@dataclass(frozen=True, slots=True)
class SampleSpec:
    repository: str
    issue_number: int
    sample_groups: tuple[str, ...]


FIXED_SAMPLE_SPECS: Final[tuple[SampleSpec, ...]] = (
    SampleSpec(
        "excalidraw/excalidraw",
        1007,
        ("body_missing", "feature", "newcomer"),
    ),
    SampleSpec(
        "excalidraw/excalidraw",
        5301,
        ("body_missing", "feature"),
    ),
    SampleSpec(
        "nodejs/undici",
        3276,
        ("body_missing", "refactor", "newcomer"),
    ),
    SampleSpec(
        "pytorch/ao",
        2298,
        ("body_missing", "classification_boundary"),
    ),
    SampleSpec(
        "scikit-learn/scikit-learn",
        17140,
        ("documentation_only", "code_zero_high_effort"),
    ),
    SampleSpec(
        "scikit-learn/scikit-learn",
        27441,
        ("documentation_only", "code_zero_high_effort"),
    ),
    SampleSpec(
        "pytorch/ao",
        2147,
        ("other_multi_day", "roadmap_tracker", "performance"),
    ),
    SampleSpec(
        "apache/pinot",
        6970,
        ("performance_newcomer", "bug_fix"),
    ),
    SampleSpec(
        "matplotlib/matplotlib",
        22803,
        ("performance_newcomer", "bug_fix"),
    ),
    SampleSpec(
        "prometheus/prometheus",
        9107,
        ("performance_newcomer",),
    ),
    SampleSpec(
        "prometheus/prometheus",
        10431,
        ("performance_newcomer",),
    ),
    SampleSpec(
        "pytorch/ao",
        988,
        ("performance_newcomer",),
    ),
    SampleSpec(
        "pytorch/ao",
        1224,
        ("performance_newcomer", "feature"),
    ),
    SampleSpec(
        "pytorch/ao",
        2367,
        ("performance_newcomer",),
    ),
    SampleSpec(
        "pytorch/pytorch",
        135859,
        ("performance_newcomer",),
    ),
    SampleSpec(
        "scikit-learn/scikit-learn",
        31503,
        ("performance_newcomer",),
    ),
    SampleSpec(
        "scikit-learn/scikit-learn",
        31554,
        ("performance_newcomer",),
    ),
    SampleSpec(
        "quarkusio/quarkus",
        42510,
        ("performance_newcomer",),
    ),
    SampleSpec(
        "apache/dubbo",
        12414,
        ("setup_body_keyword", "bug_fix"),
    ),
    SampleSpec(
        "excalidraw/excalidraw",
        9281,
        ("setup_body_keyword", "bug_fix"),
    ),
    SampleSpec(
        "jenkinsci/jenkins",
        26249,
        ("setup_body_keyword", "performance"),
    ),
    SampleSpec(
        "kubernetes/kubernetes",
        82440,
        ("setup_body_keyword", "bug_fix"),
    ),
    SampleSpec(
        "apache/pinot",
        16231,
        ("context_broad_label", "bug_fix"),
    ),
    SampleSpec(
        "matplotlib/matplotlib",
        24404,
        ("context_broad_label", "bug_fix"),
    ),
    SampleSpec(
        "pandas-dev/pandas",
        65326,
        ("context_broad_label",),
    ),
    SampleSpec(
        "wagtail/wagtail",
        14318,
        ("context_broad_label",),
    ),
    SampleSpec(
        "apache/pinot",
        16584,
        ("performance_non_newcomer", "feature"),
    ),
    SampleSpec(
        "excalidraw/excalidraw",
        7237,
        ("performance_non_newcomer", "bug_fix"),
    ),
    SampleSpec(
        "excalidraw/excalidraw",
        11273,
        ("performance_non_newcomer",),
    ),
    SampleSpec(
        "nodejs/undici",
        4122,
        ("performance_non_newcomer",),
    ),
    SampleSpec(
        "apache/pinot",
        13263,
        ("testing_control",),
    ),
    SampleSpec(
        "elastic/elasticsearch",
        92947,
        ("testing_control",),
    ),
    SampleSpec(
        "excalidraw/excalidraw",
        6294,
        ("build_tooling_control",),
    ),
    SampleSpec(
        "eslint/eslint",
        17733,
        ("build_tooling_control",),
    ),
    SampleSpec(
        "nodejs/undici",
        5466,
        ("refactor_high_context",),
    ),
    SampleSpec(
        "pandas-dev/pandas",
        62022,
        ("refactor_high_context",),
    ),
)

_EXPECTED_SAMPLE_COUNT: Final = 36
if len(FIXED_SAMPLE_SPECS) != _EXPECTED_SAMPLE_COUNT:
    raise RuntimeError(
        f"Expected {_EXPECTED_SAMPLE_COUNT} fixed samples, "
        f"got {len(FIXED_SAMPLE_SPECS)}"
    )

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_DETAILS_RE = re.compile(r"<details\b[^>]*>.*?</details>", re.IGNORECASE | re.DOTALL)
_FENCED_CODE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"[ \t]+")
_EXCESS_BLANK_RE = re.compile(r"\n{3,}")
_STACK_LINE_RE = re.compile(
    r"^\s*(?:at\s+\S+|File\s+\".*\",\s+line\s+\d+|"
    r"Traceback\s+\(most recent call last\)|Caused by:|Suppressed:|"
    r"\.{3}\s+\d+\s+more)\s*$",
    re.IGNORECASE,
)
_TIMESTAMP_LOG_RE = re.compile(
    r"^\s*(?:\[?\d{4}-\d{2}-\d{2}[T ][^\]]+\]?|"
    r"\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"
    r"(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\b",
    re.IGNORECASE,
)
_TEMPLATE_LINE_RE = re.compile(
    r"^\s*(?:_?No response_?|N/?A|Not applicable|"
    r"Replace this line with.*|Paste.*here|"
    r"-\s*\[[ xX]\]\s*I have (?:checked|confirmed).*)\s*$",
    re.IGNORECASE,
)


def readonly_uri(database_path: str | Path) -> str:
    """Return a SQLite URI that enforces read-only mode."""

    resolved = Path(database_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {resolved}")
    return f"{resolved.as_uri()}?mode=ro"


def connect_readonly(database_path: str | Path) -> sqlite3.Connection:
    """Open SQLite in read-only and query-only mode."""

    connection = sqlite3.connect(readonly_uri(database_path), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _parse_string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
    else:
        return []

    if not isinstance(parsed, (list, tuple)):
        return []
    cleaned = {
        item.strip()
        for item in parsed
        if isinstance(item, str) and item.strip()
    }
    return sorted(cleaned, key=str.casefold)


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return int(number) if number.is_integer() else None
    return None


def _coerce_optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 4)
    if isinstance(value, str):
        try:
            return round(float(value.strip()), 4)
        except ValueError:
            return None
    return None


def make_body_excerpt(body_text: Any, *, max_chars: int = MAX_BODY_EXCERPT_CHARS) -> str:
    """Return a deterministic, bounded excerpt with bulky noise removed."""

    if not 1 <= max_chars <= MAX_BODY_EXCERPT_CHARS:
        raise ValueError(
            f"max_chars must be between 1 and {MAX_BODY_EXCERPT_CHARS}"
        )
    if not isinstance(body_text, str) or not body_text.strip():
        return ""

    text = body_text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HTML_COMMENT_RE.sub(" ", text)
    text = _DETAILS_RE.sub("\n[details omitted]\n", text)
    text = _FENCED_CODE_RE.sub("\n[code block omitted]\n", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _URL_RE.sub("[link]", text)

    retained_lines: list[str] = []
    omitted_log_run = False
    for raw_line in text.splitlines():
        line = _WHITESPACE_RE.sub(" ", raw_line).strip()
        if _TEMPLATE_LINE_RE.match(line):
            continue
        is_log_line = bool(
            _STACK_LINE_RE.match(line) or _TIMESTAMP_LOG_RE.match(line)
        )
        if is_log_line:
            if not omitted_log_run:
                retained_lines.append("[log lines omitted]")
                omitted_log_run = True
            continue
        omitted_log_run = False
        retained_lines.append(line)

    excerpt = "\n".join(retained_lines).strip()
    excerpt = _EXCESS_BLANK_RE.sub("\n\n", excerpt)
    if len(excerpt) <= max_chars:
        return excerpt

    candidate = excerpt[: max_chars - 2].rstrip()
    last_break = max(candidate.rfind("\n"), candidate.rfind(" "))
    if last_break >= int(max_chars * 0.75):
        candidate = candidate[:last_break].rstrip()
    return f"{candidate} …"


def _sample_sort_key(spec: SampleSpec) -> tuple[str, int]:
    return spec.repository.casefold(), spec.issue_number


def _empty_human_annotation() -> dict[str, Any]:
    return {
        "code_difficulty": None,
        "setup_difficulty": None,
        "project_context_difficulty": None,
        "collaboration_difficulty": None,
        "effort_bucket": None,
        "annotation_confidence": None,
        "rationale": "",
        "evidence": [],
    }


def load_fixed_sample_rows(
    database_path: str | Path,
) -> dict[tuple[str, int], sqlite3.Row]:
    """Load active, eligible rows needed by the fixed calibration set."""

    connection = connect_readonly(database_path)
    try:
        rows = connection.execute(
            """
            SELECT
                tc.task_candidate_id,
                r.full_name AS repository,
                tc.issue_number,
                COALESCE(tc.html_url, '') AS html_url,
                COALESCE(tc.title, '') AS title,
                tc.body_text,
                tc.labels_json,
                tc.task_types_json,
                COALESCE(tc.comment_count, 0) AS comment_count,
                COALESCE(tc.candidate_eligibility, '') AS candidate_eligibility,
                tc.text_clarity_score,
                tc.estimated_code_difficulty,
                tc.estimated_setup_difficulty,
                tc.estimated_project_context_difficulty,
                tc.estimated_collaboration_difficulty,
                tc.estimated_effort_bucket,
                tc.novice_fit_probability,
                tc.newcomer_score,
                tc.growth_value_score,
                COALESCE(tc.task_feature_version, '') AS task_feature_version
            FROM task_candidate AS tc
            JOIN repository AS r
              ON r.repository_id = tc.repository_id
            WHERE COALESCE(r.is_archived, 0) = 0
              AND COALESCE(r.is_disabled, 0) = 0
            ORDER BY LOWER(r.full_name), tc.issue_number, tc.task_candidate_id
            """
        ).fetchall()
    finally:
        connection.close()

    rows_by_key: dict[tuple[str, int], sqlite3.Row] = {}
    duplicate_keys: list[tuple[str, int]] = []
    for row in rows:
        key = (str(row["repository"]), int(row["issue_number"]))
        if key in rows_by_key:
            duplicate_keys.append(key)
        rows_by_key[key] = row

    if duplicate_keys:
        rendered = ", ".join(
            f"{repository}#{issue_number}"
            for repository, issue_number in sorted(set(duplicate_keys))
        )
        raise RuntimeError(f"Duplicate task keys found in database: {rendered}")

    missing: list[SampleSpec] = []
    ineligible: list[SampleSpec] = []
    selected: dict[tuple[str, int], sqlite3.Row] = {}
    for spec in FIXED_SAMPLE_SPECS:
        key = (spec.repository, spec.issue_number)
        row = rows_by_key.get(key)
        if row is None:
            missing.append(spec)
            continue
        if str(row["candidate_eligibility"]).casefold() != "eligible":
            ineligible.append(spec)
            continue
        selected[key] = row

    if missing:
        rendered = ", ".join(
            f"{spec.repository}#{spec.issue_number}"
            for spec in sorted(missing, key=_sample_sort_key)
        )
        raise RuntimeError(f"Fixed calibration tasks are missing: {rendered}")
    if ineligible:
        rendered = ", ".join(
            f"{spec.repository}#{spec.issue_number}"
            for spec in sorted(ineligible, key=_sample_sort_key)
        )
        raise RuntimeError(
            f"Fixed calibration tasks are not eligible: {rendered}"
        )
    return selected


def build_annotation_documents(
    database_path: str | Path,
    *,
    max_body_chars: int = MAX_BODY_EXCERPT_CHARS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build annotation and current-prediction documents."""

    selected_rows = load_fixed_sample_rows(database_path)
    annotation_records: list[dict[str, Any]] = []
    prediction_records: list[dict[str, Any]] = []

    for spec in sorted(FIXED_SAMPLE_SPECS, key=_sample_sort_key):
        row = selected_rows[(spec.repository, spec.issue_number)]
        identity = {
            "task_candidate_id": int(row["task_candidate_id"]),
            "repository": spec.repository,
            "issue_number": spec.issue_number,
            "html_url": str(row["html_url"] or ""),
            "title": str(row["title"] or ""),
        }
        annotation_input = {
            **identity,
            "labels": _parse_string_list(row["labels_json"]),
            "task_types": _parse_string_list(row["task_types_json"]),
            "body_excerpt": make_body_excerpt(
                row["body_text"], max_chars=max_body_chars
            ),
            "comment_count": _coerce_optional_int(row["comment_count"]) or 0,
            "sample_groups": list(spec.sample_groups),
        }
        annotation_records.append(
            {
                "annotation_input": annotation_input,
                "human_annotation": _empty_human_annotation(),
            }
        )
        prediction_records.append(
            {
                **identity,
                "sample_groups": list(spec.sample_groups),
                "current_prediction": {
                    "code_difficulty": _coerce_optional_int(
                        row["estimated_code_difficulty"]
                    ),
                    "setup_difficulty": _coerce_optional_int(
                        row["estimated_setup_difficulty"]
                    ),
                    "project_context_difficulty": _coerce_optional_int(
                        row["estimated_project_context_difficulty"]
                    ),
                    "collaboration_difficulty": _coerce_optional_int(
                        row["estimated_collaboration_difficulty"]
                    ),
                    "effort_bucket": (
                        str(row["estimated_effort_bucket"])
                        if row["estimated_effort_bucket"] is not None
                        else None
                    ),
                    "text_clarity_score": _coerce_optional_float(
                        row["text_clarity_score"]
                    ),
                    "novice_fit_probability": _coerce_optional_float(
                        row["novice_fit_probability"]
                    ),
                    "newcomer_score": _coerce_optional_float(
                        row["newcomer_score"]
                    ),
                    "growth_value_score": _coerce_optional_float(
                        row["growth_value_score"]
                    ),
                    "task_feature_version": str(
                        row["task_feature_version"] or ""
                    ),
                },
            }
        )

    source_database_name = Path(database_path).name
    annotation_document = {
        "schema_version": ANNOTATION_SCHEMA_VERSION,
        "annotation_guide_version": ANNOTATION_GUIDE_VERSION,
        "source_database_name": source_database_name,
        "sample_count": len(annotation_records),
        "selection_policy": {
            "fixed_sample_set": True,
            "missing_task_policy": "fail",
            "eligible_tasks_only": True,
            "ordering": "repository_casefold_then_issue_number",
            "blind_annotation_input": True,
            "body_excerpt_max_chars": max_body_chars,
        },
        "records": annotation_records,
    }
    prediction_document = {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "source_database_name": source_database_name,
        "sample_count": len(prediction_records),
        "usage": "Compare only after human annotation is complete.",
        "records": prediction_records,
    }
    return annotation_document, prediction_document


def write_json(document: dict[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the fixed OSS-Mentor difficulty annotation set."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help="Round3 SQLite database opened read-only.",
    )
    parser.add_argument(
        "--annotation-output",
        type=Path,
        default=DEFAULT_ANNOTATION_OUTPUT,
        help="Output path for the blind annotation set.",
    )
    parser.add_argument(
        "--predictions-output",
        type=Path,
        default=DEFAULT_PREDICTIONS_OUTPUT,
        help="Output path for current model predictions.",
    )
    parser.add_argument(
        "--max-body-chars",
        type=int,
        default=MAX_BODY_EXCERPT_CHARS,
        help=f"Maximum body excerpt length, up to {MAX_BODY_EXCERPT_CHARS}.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    annotation_document, prediction_document = build_annotation_documents(
        args.database,
        max_body_chars=args.max_body_chars,
    )
    annotation_output = write_json(
        annotation_document, args.annotation_output
    )
    predictions_output = write_json(
        prediction_document, args.predictions_output
    )
    print(
        json.dumps(
            {
                "event": "difficulty_annotation_set_exported",
                "database_path": str(Path(args.database).resolve()),
                "annotation_output": str(annotation_output.resolve()),
                "predictions_output": str(predictions_output.resolve()),
                "sample_count": annotation_document["sample_count"],
                "sqlite_access_mode": "uri_mode_ro_and_query_only",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())