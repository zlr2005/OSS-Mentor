from __future__ import annotations

import hashlib
import json

from coder_issue.training.advanced_issue_inputs import prepare_candidate_rows


def _hash(title: str, body: str) -> str:
    payload = (
        json.dumps(
            {"title": title, "body": body},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _snapshot(*, as_of: str, title: str = "Title", body: str = "Body") -> dict[str, object]:
    return {
        "issue_key": "Owner/Repo#12",
        "as_of": as_of,
        "title_at_cutoff": title,
        "body_at_cutoff": body,
        "content_sha256": _hash(title, body),
        "source_url": "https://github.com/owner/repo/issues/12",
        "evidence_ref": "api_cache/example.json",
        "role": "unlabeled_control",
    }


def test_prepares_exact_cutoff_candidate_and_reports_duplicate() -> None:
    candidates, exclusions, stats = prepare_candidate_rows(
        [
            _snapshot(as_of="2025-04-01T00:00:00Z"),
            _snapshot(as_of="2025-07-01T00:00:00Z", title="Changed"),
        ]
    )

    assert len(candidates) == 1
    assert candidates[0]["issue_key"] == "owner/repo#12"
    assert candidates[0]["available_from"] == "2025-04-01T00:00:00+00:00"
    assert candidates[0]["available_until"] == "2025-04-01T00:00:01+00:00"
    assert exclusions[0]["code"] == "duplicate_issue_snapshot_not_selected"
    assert stats["excluded_duplicate_snapshots"] == 1
    assert stats["multi_hash_issue_keys"] == 1


def test_rejects_snapshot_with_mismatched_content_hash() -> None:
    row = _snapshot(as_of="2025-04-01T00:00:00Z")
    row["content_sha256"] = "0" * 64

    candidates, exclusions, stats = prepare_candidate_rows([row])

    assert candidates == []
    assert exclusions[0]["code"] == "invalid_historical_snapshot"
    assert stats["invalid_snapshots"] == 1


def test_preserves_title_whitespace_used_by_content_hash() -> None:
    row = _snapshot(as_of="2025-04-01T00:00:00Z", title="  Title  ")

    candidates, exclusions, _ = prepare_candidate_rows([row])

    assert not exclusions
    assert candidates[0]["title"] == "  Title  "
