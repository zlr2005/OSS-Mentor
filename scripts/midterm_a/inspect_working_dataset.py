"""Validate and summarize an unfrozen working dataset; never freeze or export it."""

import argparse
import gzip
import hashlib
import json
import platform
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from oss_mentor.datasets.snapshot import load_snapshot
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.storage.datasets import DatasetStore


def summarize(directory: Path) -> dict:
    manifest, records, assets = load_snapshot(directory)
    if manifest.get("dataset_lifecycle") != "working" or manifest.get("frozen") is not False:
        raise ValueError("Expected explicitly unfrozen working data")
    contributions = records["contributions"]
    code = {row["file_version_key"]: row for row in records["code_documents"]}
    available = sum(row["availability"] == "available" for row in code.values())
    confirmed = sum(row["author_association_status"] == "verified" for row in contributions)
    developers = records["developers"]
    selected = set(manifest["source_provenance"]["selection"]["commit_shas"])
    repository = records["repositories"][0]
    commit_list_paths = {"/repos/" + repository["full_name"] + "/commits",
                         "/repositories/" + repository["source_repository_id"] + "/commits"}
    metadata_shas, api_pages, non_issue_references = set(), 0, set()
    for name, content in assets.items():
        if name.startswith("provenance/") and name.endswith(".json.gz"):
            envelope = json.loads(gzip.decompress(content))
            payload = envelope["payload"]
            path = urlsplit(envelope["metadata"]["source_url"]).path
            if isinstance(payload, list) and path in commit_list_paths:
                api_pages += 1
                metadata_shas.update(row["sha"] for row in payload)
            if isinstance(payload, dict) and "pull_request" in payload and path.rsplit("/", 2)[-2] == "issues":
                non_issue_references.add(payload["number"])
    report = {
        "schema_version": "midterm-a-working-quality-v1", "dataset_version": manifest["dataset_version"],
        "dataset_lifecycle": "working", "frozen": False, "team_acceptance": "not_requested",
        "input_directory": str(directory.resolve()), "python_version": platform.python_version(),
        "manifest_sha256": hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest(),
        "entity_counts": manifest["entity_counts"], "time_coverage": manifest["time_coverage"],
        "selected_commits": len(selected),
        "excluded_merge_commits": len(manifest["source_provenance"]["excluded_commits"]),
        "commits_with_selected_python_files": len({row["commit_sha"] for row in contributions}),
        "unique_developer_logical_file_pairs": len({(r["developer_key"], r["file_key"]) for r in contributions}),
        "code_availability": dict(Counter(row["availability"] for row in code.values())),
        "code_unavailable_reasons": dict(Counter(row["exclusion_reason"] for row in code.values() if row["availability"] != "available")),
        "code_availability_rate": available / len(code) if code else None,
        "developer_identity_status": dict(Counter(row["identity_status"] for row in developers)),
        "contribution_identity_status": dict(Counter(row["author_association_status"] for row in contributions)),
        "contribution_identity_association_rate": confirmed / len(contributions) if contributions else None,
        "code_available_and_identity_confirmed_contributions": sum(
            row["author_association_status"] == "verified" and code[row["file_version_key"]]["availability"] == "available"
            for row in contributions),
        "api_metadata_pages": api_pages, "selected_commits_with_api_metadata": len(selected & metadata_shas),
        "asset_count": len(assets), "asset_bytes": sum(map(len, assets.values())),
        "quality_gaps": manifest["quality_gaps"],
    }
    if "issue_enrichment" in manifest["source_provenance"]:
        audit = manifest["source_provenance"]["issue_enrichment"]
        report["issue_enrichment"] = {"selection_policy": audit["selection_policy"],
                                      "base_contribution_commits": audit["base_contribution_commits"],
                                      **audit["summary"], "source_failures": sum(bool(c["errors"]) for c in audit["cases"])}
        summary = audit["summary"]
        linked_issues = {r["issue_key"] for r in records["issue_file_links"] if r["feature_role"] == "outcome_only"}
        resolved_issues = {r["issue_key"] for r in records["issues"] if r["outcome"] is not None}
        confirmed_commits = {r["commit_sha"] for r in summary["confirmed_cases"]}
        report["issue_enrichment"]["rates_within_biased_sample"] = {
            "sampled_commit_to_pr": summary["sampled_commits_with_pr"] / summary["sampled_commits"] if summary["sampled_commits"] else None,
            "associated_pr_to_explicit_resolution": summary["prs_with_explicit_resolution"] / summary["associated_unique_prs"] if summary["associated_unique_prs"] else None,
            "resolved_issue_to_sampled_file": len(resolved_issues & linked_issues) / len(resolved_issues) if resolved_issues else None,
        }
        report["issue_enrichment"]["case_status"] = [
            {"commit_sha": case["commit_sha"], "selection_role": case["selection_role"],
             "status": "source_failed" if case["errors"] else
                       "explicit_resolution" if case["commit_sha"] in confirmed_commits else
                       "no_associated_pr" if not case["pull_requests"] else
                       "no_issue_candidate_or_verified_resolution"}
            for case in audit["cases"]]
        report["issue_enrichment"]["excluded_non_issue_references"] = [
            {"number": number, "reason": "reference_is_pull_request"} for number in sorted(non_issue_references)]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = summarize(args.directory)
    if args.database is not None:
        if not args.database.is_file():
            parser.error("Working database does not exist")
        factory = SQLiteCandidateStore(args.database, Path(__file__).resolve().parents[2] / "db/sqlite/001_mvp.sql")
        connection = factory.connect()
        try:
            report["database_import"] = DatasetStore(connection).status(report["dataset_version"])
            report["database_integrity"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
            report["foreign_key_violations"] = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
        finally:
            connection.close()
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
