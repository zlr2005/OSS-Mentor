"""Build an isolated, unfrozen local review database from saved GitHub evidence.

No network access. This review schema is not wired to the website candidate API.
"""

import argparse
import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def build(directory):
    archive = directory / "review_pack.base64"
    compressed = base64.b64decode(archive.read_text(encoding="ascii"), validate=True)
    raw = gzip.decompress(compressed)
    pack = json.loads(raw)
    decisions = pack["decisions"]
    source = {f"{r['repo_name']}#{r['issue_number']}": r for r in pack["source_issues"]}
    if len(source) != len(decisions) or {r['key'] for r in decisions} != set(source):
        raise ValueError("Review coverage must match distinct discovered issues")
    database = directory / "teacher_review.sqlite3"
    if database.exists():
        raise FileExistsError("Choose a fresh review directory; existing DB is preserved")
    as_of = datetime.fromisoformat(pack["observed_start"].replace("Z", "+00:00"))
    expires = (as_of + timedelta(hours=24)).isoformat()
    candidates = []
    for review in decisions:
        if review["decision"] != "availability_passed":
            continue
        key = review["key"]
        detail = pack["issue_details"][key]
        detail_at = pack.get("detail_observed_at", {}).get(key, pack["observed_start"])
        detail_expiry = (datetime.fromisoformat(detail_at.replace("Z", "+00:00")) + timedelta(hours=24)).isoformat()
        repo = pack["repository_details"][review["repository"]]
        timeline = pack["timelines"][key]
        if (detail["state"] != "open" or detail["locked"] or detail["assignees"]
                or not detail.get("body", "").strip() or not timeline["complete"]
                or str(detail["id"]) != str(source[key]["issue_id"])
                or str(repo["id"]) != str(source[key]["repo_id"])
                or review["open_pr_references"] or review["claim_signals"]):
            raise ValueError(f"Candidate invariant failed: {key}")
        candidates.append({
            "key": key, "repository": repo["full_name"], "repository_id": repo["id"],
            "issue_id": detail["id"], "issue_number": detail["number"],
            "url": detail["html_url"], "title": detail["title"], "body": detail["body"],
            "labels": [label["name"] for label in detail["labels"]],
            "repository_primary_language": repo.get("language"),
            "state": "open", "assignees": [], "locked": False,
            "issue_created_at": detail["created_at"], "issue_updated_at": detail["updated_at"],
            "verified_at": detail_at, "verification_finished_at": pack["observed_end"],
            "expires_at": detail_expiry, "availability": "availability_passed",
            "difficulty": "unverified", "source_system": "github_rest",
            "retrieval_method": "github_connector", "discovery_source": "teacher_clickhouse",
            "source_batch": pack["source_batch"], "frozen": False,
            "warnings": ["Task difficulty and personal fit have not been confirmed",
                         "No observed claim/open PR does not guarantee nobody is working on it",
                         "Recheck GitHub before participating"],
        })
    connection = sqlite3.connect(database)
    try:
        connection.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE issue_discovery(issue_key TEXT PRIMARY KEY, record_json TEXT NOT NULL);
            CREATE TABLE issue_review(issue_key TEXT PRIMARY KEY REFERENCES issue_discovery(issue_key),
                decision TEXT NOT NULL, reason TEXT NOT NULL, evidence_json TEXT NOT NULL);
            CREATE TABLE candidate_issue(issue_key TEXT PRIMARY KEY REFERENCES issue_review(issue_key),
                repository TEXT NOT NULL, issue_number INTEGER NOT NULL, title TEXT NOT NULL,
                url TEXT NOT NULL, verified_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                record_json TEXT NOT NULL, UNIQUE(repository, issue_number));
        """)
        connection.executemany("INSERT INTO issue_discovery VALUES (?,?)",
                               [(key, json.dumps(value, ensure_ascii=False)) for key, value in source.items()])
        connection.executemany("INSERT INTO issue_review VALUES (?,?,?,?)",
                               [(r["key"], r["decision"], r["reason"], json.dumps(r, ensure_ascii=False)) for r in decisions])
        connection.executemany("INSERT INTO candidate_issue VALUES (?,?,?,?,?,?,?,?)", [
            (r["key"], r["repository"], r["issue_number"], r["title"], r["url"], r["verified_at"],
             r["expires_at"], json.dumps(r, ensure_ascii=False)) for r in candidates])
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity failure")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite foreign-key failure")
        if connection.execute("SELECT count(*) FROM candidate_issue").fetchone()[0] != len(candidates):
            raise ValueError("SQLite candidate count mismatch")
    finally:
        connection.close()
    (directory / "review_pack.json.gz").write_bytes(compressed)
    dump(directory / "candidates.json", candidates)
    dump(directory / "audit.json", decisions)
    lines = ["# Teacher discovery: availability-checked working candidates", "",
             f"Checked: {pack['observed_start']}; expires: {expires}", "",
             "Difficulty and personal fit remain unverified. Recheck GitHub before participating.",
             "This is an isolated review database, not connected to the website. Data is not frozen.", ""]
    for candidate in sorted(candidates, key=lambda item: item["key"]):
        title = candidate["title"].replace("\n", " ").replace("[", "\\[").replace("]", "\\]")
        lines.append(f"- [{candidate['key']}: {title}]({candidate['url']})")
    (directory / "candidates.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = {
        "source_batch": pack["source_batch"], "frozen": False,
        "review_policy": "teacher-github-review-v1; automated availability plus conservative textual/manual exclusions",
        "counts": dict(Counter(r["decision"] for r in decisions)),
        "reasons": dict(Counter(r["reason"] for r in decisions)),
        "candidate_repositories": dict(Counter(r["repository"] for r in candidates)),
        "source_issues": len(source), "details_fetched": len(pack["issue_details"]),
        "discovery_summary": pack.get("discovery_summary"),
        "source_scope": pack.get("scope"),
        "inventory_coverage": pack.get("inventory_coverage"),
        "timelines_checked": len(pack["timelines"]), "referenced_prs_checked": len(pack["pull_requests"]),
        "unverified_details": pack["detail_failures"], "verified_at": pack["observed_start"],
        "expires_at": expires, "sqlite_integrity": "ok", "foreign_key_violations": 0,
        "schema": "isolated teacher review schema; no website integration or merge with previous candidate pool",
        "limitations": ["Discovery may contain failed or saturated ranges; inspect the parent batch status.json",
                        "Connector response headers unavailable; timeline pagination stops after a short page",
                        "Text-based claim detection can miss or overflag statements",
                        "Teacher event time is not used as authoritative GitHub creation time"],
        "evidence_sha256": hashlib.sha256(raw).hexdigest(),
    }
    dump(directory / "report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.directory), ensure_ascii=False, indent=2))
