"""Command-line interface for the OSS-Mentor pilot collector."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from oss_mentor.api import serve
from oss_mentor.candidate_refresh import CandidateRefresher
from oss_mentor.candidate_report import (
    build_candidate_report,
    render_candidate_report_markdown,
)
from oss_mentor.candidate_sync import CandidateSynchronizer
from oss_mentor.collector.ecosystems_client import (
    EcosystemsApiError,
    EcosystemsClient,
)
from oss_mentor.collector.config import (
    ConfigError,
    RepositoryConfig,
    Settings,
    load_repositories,
)
from oss_mentor.collector.github_client import (
    GitHubApiError,
    GitHubClient,
    RateLimitExceeded,
)
from oss_mentor.collector.raw_store import RawStore
from oss_mentor.collector.repository_collector import RepositoryCollector
from oss_mentor.collector.source_comparison import IssueSourceComparator
from oss_mentor.developer_profiles import load_profiles
from oss_mentor.matching import rank_for_profile
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.task_features import extract_task_features, infer_skill_requirements


def _json_dump(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _apply_path_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    updated = settings
    if getattr(args, "config", None):
        updated = replace(
            updated,
            repository_config_path=Path(args.config).expanduser().resolve(),
        )
    if getattr(args, "raw_dir", None):
        updated = replace(updated, raw_root=Path(args.raw_dir).expanduser().resolve())
    return updated


def _select_repositories(
    settings: Settings, args: argparse.Namespace
) -> list[RepositoryConfig]:
    all_enabled = bool(getattr(args, "all_enabled", False))
    if all_enabled and (
        getattr(args, "repo", None) or getattr(args, "include_disabled", False)
    ):
        raise ConfigError(
            "--all-enabled cannot be combined with --repo or --include-disabled"
        )
    repositories = load_repositories(
        settings.repository_config_path,
        wave=None if all_enabled else args.wave,
        enabled_only=not getattr(args, "include_disabled", False),
    )
    requested = set(getattr(args, "repo", None) or [])
    if requested:
        repositories = [
            repository
            for repository in repositories
            if repository.full_name in requested
        ]
        missing = sorted(requested.difference(r.full_name for r in repositories))
        if missing:
            raise ConfigError(
                "requested repositories are not selected by the current wave/config: "
                + ", ".join(missing)
            )
    return repositories


def _database_path(settings: Settings, args: argparse.Namespace) -> Path:
    return (
        Path(args.database).expanduser().resolve()
        if getattr(args, "database", None)
        else settings.repo_root / "data" / "oss_mentor.sqlite3"
    )


def _candidate_store(settings: Settings, args: argparse.Namespace) -> SQLiteCandidateStore:
    return SQLiteCandidateStore(
        _database_path(settings, args),
        settings.repo_root / "db" / "sqlite" / "001_mvp.sql",
    )


def _write_json(path: str | None, payload: object) -> None:
    if not path:
        return
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repository_summary(repository: RepositoryConfig) -> dict[str, object]:
    return {
        "wave": repository.wave,
        "full_name": repository.full_name,
        "github_repository_id": repository.github_repository_id,
        "ecosystem": repository.ecosystem,
        "license_spdx": repository.license_spdx,
        "pilot_role": repository.pilot_role,
        "code_access_policy": repository.code_access_policy,
        "candidate_labels": list(repository.candidate_labels),
    }


def command_list_repositories(settings: Settings, args: argparse.Namespace) -> int:
    for repository in _select_repositories(settings, args):
        _json_dump(_repository_summary(repository))
    return 0


def command_collect_repositories(settings: Settings, args: argparse.Namespace) -> int:
    repositories = _select_repositories(settings, args)
    plans = {
        repository.full_name: [
            {
                "endpoint": endpoint.name,
                "path": endpoint.path,
                "paginated": endpoint.paginated,
                "params": endpoint.params or {},
            }
            for endpoint in RepositoryCollector.plan(repository)
        ]
        for repository in repositories
    }

    if args.dry_run:
        _json_dump(
            {
                "mode": "dry-run",
                "wave": args.wave,
                "repository_count": len(repositories),
                "raw_root": str(settings.raw_root),
                "repositories": [
                    {
                        **_repository_summary(repository),
                        "requests": plans[repository.full_name],
                    }
                    for repository in repositories
                ],
            }
        )
        return 0

    if not args.allow_network:
        print(
            "Refusing network collection without --allow-network. "
            "Use --dry-run to inspect the plan.",
            file=sys.stderr,
        )
        return 2
    if settings.github_token is None and not args.allow_anonymous:
        print(
            "GITHUB_TOKEN is not set. Provide one read-only team credential, or "
            "explicitly pass --allow-anonymous for a small public smoke test.",
            file=sys.stderr,
        )
        return 2

    client = GitHubClient(
        api_base=settings.github_api_base,
        api_version=settings.github_api_version,
        user_agent=settings.user_agent,
        token=settings.github_token,
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_base_seconds=settings.backoff_base_seconds,
    )
    raw_store = RawStore(
        settings.raw_root,
        api_version=settings.github_api_version,
    )
    collector = RepositoryCollector(client, raw_store)
    collection_run_id = uuid4()
    failures = 0

    _json_dump(
        {
            "event": "collection_started",
            "collection_run_id": str(collection_run_id),
            "repository_count": len(repositories),
            "api_version": settings.github_api_version,
        }
    )
    for repository in repositories:
        try:
            result = collector.collect(
                repository,
                collection_run_id=collection_run_id,
            )
            _json_dump(
                {
                    "event": "repository_collected",
                    "collection_run_id": str(collection_run_id),
                    "repository": repository.full_name,
                    "request_count": result.request_count,
                    "raw_paths": [str(record.path) for record in result.raw_records],
                }
            )
        except (GitHubApiError, OSError, ValueError) as exc:
            failures += 1
            _json_dump(
                {
                    "event": "repository_failed",
                    "collection_run_id": str(collection_run_id),
                    "repository": repository.full_name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    _json_dump(
        {
            "event": "collection_finished",
            "collection_run_id": str(collection_run_id),
            "repository_count": len(repositories),
            "failure_count": failures,
        }
    )
    return 1 if failures else 0


def command_compare_issue_sources(settings: Settings, args: argparse.Namespace) -> int:
    repository = _select_repositories(settings, args)[0]
    if not args.allow_network:
        print(
            "Refusing network comparison without --allow-network.",
            file=sys.stderr,
        )
        return 2
    if settings.github_token is None and not args.allow_anonymous:
        print(
            "GITHUB_TOKEN is not set. Provide one or explicitly pass "
            "--allow-anonymous for a small public comparison.",
            file=sys.stderr,
        )
        return 2

    github_client = GitHubClient(
        api_base=settings.github_api_base,
        api_version=settings.github_api_version,
        user_agent=settings.user_agent,
        token=settings.github_token,
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_base_seconds=settings.backoff_base_seconds,
    )
    ecosystems_client = EcosystemsClient(
        api_base=settings.ecosystems_issues_api_base,
        user_agent=settings.user_agent,
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_base_seconds=settings.backoff_base_seconds,
    )
    try:
        comparison = IssueSourceComparator(
            ecosystems_client, github_client
        ).compare(repository.full_name, sample_size=args.sample_size)
    except (EcosystemsApiError, GitHubApiError) as exc:
        _json_dump(
            {
                "event": "source_comparison_failed",
                "repository": repository.full_name,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        return 1
    if args.output:
        destination = Path(args.output).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(
                comparison.report,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    _json_dump(comparison.report)
    return 0


def command_sync_candidates(settings: Settings, args: argparse.Namespace) -> int:
    repositories = _select_repositories(settings, args)
    if not repositories:
        raise ConfigError("no repositories selected")
    if args.dry_run:
        _json_dump(
            {
                "mode": "dry-run",
                "repository_count": len(repositories),
                "limit_per_repo": args.limit_per_repo,
                "estimated_requests": {
                    "github_max": sum(
                        1
                        + len(repository.candidate_labels)
                        + args.limit_per_repo * 2
                        for repository in repositories
                    ),
                    "ecosystems_max": sum(
                        2 + len(repository.candidate_labels)
                        for repository in repositories
                    ),
                },
                "repositories": [
                    {
                        "full_name": repository.full_name,
                        "candidate_labels": list(repository.candidate_labels),
                    }
                    for repository in repositories
                ],
            }
        )
        return 0
    if not args.allow_network:
        print("Refusing candidate sync without --allow-network.", file=sys.stderr)
        return 2
    if settings.github_token is None and (
        len(repositories) > 1 or not args.allow_anonymous
    ):
        print(
            "GITHUB_TOKEN is not set. Batch candidate sync requires a read-only "
            "token; --allow-anonymous is limited to a one-repository smoke test.",
            file=sys.stderr,
        )
        return 2

    github_client = GitHubClient(
        api_base=settings.github_api_base,
        api_version=settings.github_api_version,
        user_agent=settings.user_agent,
        token=settings.github_token,
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_base_seconds=settings.backoff_base_seconds,
    )
    ecosystems_client = EcosystemsClient(
        api_base=settings.ecosystems_issues_api_base,
        user_agent=settings.user_agent,
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_base_seconds=settings.backoff_base_seconds,
    )
    store = _candidate_store(settings, args)
    store.initialize()
    synchronizer = CandidateSynchronizer(
        ecosystems_client, github_client, store
    )
    started_at = _utc_now()
    repository_reports: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    discovered_count = hydrated_count = timeline_checked_count = 0
    unavailable_count = 0
    successful_count = 0
    rate_limited = False

    for index, repository in enumerate(repositories):
        github_before = github_client.request_count
        ecosystems_before = ecosystems_client.request_count
        try:
            result = synchronizer.sync(
                repository.full_name,
                limit=args.limit_per_repo,
                hydrate_github=not args.no_github_hydration,
                candidate_labels=repository.candidate_labels,
                ecosystem=repository.ecosystem,
                primary_language=repository.primary_language,
            )
        except RateLimitExceeded as exc:
            rate_limited = True
            error = {
                "repository": repository.full_name,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            errors.append(error)
            repository_reports.append({**error, "status": "failed"})
            for skipped in repositories[index + 1 :]:
                repository_reports.append(
                    {"repository": skipped.full_name, "status": "skipped_rate_limit"}
                )
            break
        except (EcosystemsApiError, GitHubApiError, OSError, ValueError) as exc:
            error = {
                "repository": repository.full_name,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            errors.append(error)
            repository_reports.append({**error, "status": "failed"})
            continue

        successful_count += 1
        discovered_count += result.discovered_count
        hydrated_count += result.hydrated_count
        timeline_checked_count += result.timeline_checked_count
        repository_unavailable = int(getattr(result, "unavailable_count", 0))
        unavailable_count += repository_unavailable
        repository_reports.append(
            {
                "repository": repository.full_name,
                "status": "completed",
                "discovered_count": result.discovered_count,
                "hydrated_count": result.hydrated_count,
                "timeline_checked_count": result.timeline_checked_count,
                "unavailable_count": repository_unavailable,
                "warnings": list(getattr(result, "warnings", ())),
                "github_request_count": github_client.request_count - github_before,
                "ecosystems_request_count": ecosystems_client.request_count
                - ecosystems_before,
            }
        )

    failed_count = len(repositories) - successful_count
    status = "completed" if failed_count == 0 else ("partial" if successful_count else "failed")
    report = {
        "run_id": str(uuid4()),
        "status": status,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "repository_count": len(repositories),
        "successful_repository_count": successful_count,
        "failed_repository_count": failed_count,
        "rate_limited": rate_limited,
        "github_request_count": github_client.request_count,
        "github_rate_limit_remaining": getattr(
            github_client, "rate_limit_remaining", None
        ),
        "ecosystems_request_count": ecosystems_client.request_count,
        "discovered_count": discovered_count,
        "hydrated_count": hydrated_count,
        "timeline_checked_count": timeline_checked_count,
        "unavailable_count": unavailable_count,
        "repositories": repository_reports,
        "errors": errors,
        "database_summary": store.summary(),
    }
    _write_json(args.output, report)
    _json_dump(report)
    return 0 if status == "completed" else 1


def command_refresh_candidates(settings: Settings, args: argparse.Namespace) -> int:
    repositories = _select_repositories(settings, args)
    if not repositories:
        raise ConfigError("no repositories selected")
    store = _candidate_store(settings, args)
    if args.dry_run:
        stale = store.stale_candidates(
            repositories=[repository.full_name for repository in repositories],
            older_than_hours=args.older_than_hours,
            limit=args.limit,
        )
        _json_dump(
            {
                "mode": "dry-run",
                "repository_count": len(repositories),
                "candidate_count": len(stale),
                "older_than_hours": args.older_than_hours,
                "limit": args.limit,
                "estimated_github_requests_max": len(repositories) + 2 * len(stale),
            }
        )
        return 0
    if not args.allow_network:
        print("Refusing candidate refresh without --allow-network.", file=sys.stderr)
        return 2
    if settings.github_token is None:
        print("GITHUB_TOKEN is required for candidate refresh.", file=sys.stderr)
        return 2

    github_client = GitHubClient(
        api_base=settings.github_api_base,
        api_version=settings.github_api_version,
        user_agent=settings.user_agent,
        token=settings.github_token,
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_base_seconds=settings.backoff_base_seconds,
    )
    ecosystems_client = EcosystemsClient(
        api_base=settings.ecosystems_issues_api_base,
        user_agent=settings.user_agent,
        timeout_seconds=settings.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_base_seconds=settings.backoff_base_seconds,
    )
    synchronizer = CandidateSynchronizer(ecosystems_client, github_client, store)
    started_at = _utc_now()
    result = CandidateRefresher(synchronizer, store).refresh(
        repositories,
        older_than_hours=args.older_than_hours,
        limit=args.limit,
    )
    status = "completed"
    if result.rate_limited or result.failed_count:
        status = "partial" if result.refreshed_count or result.unavailable_count else "failed"
    report = {
        "run_id": str(uuid4()),
        "status": status,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "repository_count": len(repositories),
        "selected_count": result.selected_count,
        "refreshed_count": result.refreshed_count,
        "unavailable_count": result.unavailable_count,
        "failed_count": result.failed_count,
        "timeline_checked_count": result.timeline_checked_count,
        "rate_limited": result.rate_limited,
        "github_request_count": github_client.request_count,
        "github_rate_limit_remaining": getattr(
            github_client, "rate_limit_remaining", None
        ),
        "ecosystems_request_count": ecosystems_client.request_count,
        "repositories_refreshed": list(result.repositories_refreshed),
        "errors": list(result.errors),
        "database_summary": store.summary(),
    }
    _write_json(args.output, report)
    _json_dump(report)
    return 0 if status == "completed" else 1


def command_candidate_report(settings: Settings, args: argparse.Namespace) -> int:
    store = _candidate_store(settings, args)
    report = build_candidate_report(store)
    _write_json(args.output, report)
    markdown_path = (
        Path(args.markdown_output).expanduser().resolve()
        if args.markdown_output
        else settings.repo_root / "docs" / "candidate_pool_report_v0.3.md"
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(
        render_candidate_report_markdown(report), encoding="utf-8"
    )
    _json_dump(report)
    return 0


def command_list_candidates(settings: Settings, args: argparse.Namespace) -> int:
    database_path = (
        Path(args.database).expanduser().resolve()
        if args.database
        else settings.repo_root / "data" / "oss_mentor.sqlite3"
    )
    if not database_path.is_file():
        raise ConfigError(f"SQLite database does not exist: {database_path}")
    store = SQLiteCandidateStore(
        database_path,
        settings.repo_root / "db" / "sqlite" / "001_mvp.sql",
    )
    for candidate in store.list_candidates(
        eligibility=args.eligibility,
        newcomer_only=args.newcomer_only,
        limit=args.limit,
    ):
        candidate.pop("labels_json", None)
        candidate.pop("ineligibility_reasons_json", None)
        candidate.pop("warnings_json", None)
        _json_dump(candidate)
    return 0


def _sqlite_store(settings: Settings, database: str | None) -> SQLiteCandidateStore:
    database_path = (
        Path(database).expanduser().resolve()
        if database
        else settings.repo_root / "data" / "oss_mentor.sqlite3"
    )
    return SQLiteCandidateStore(
        database_path,
        settings.repo_root / "db" / "sqlite" / "001_mvp.sql",
    )


def command_extract_features(settings: Settings, args: argparse.Namespace) -> int:
    store = _sqlite_store(settings, args.database)
    if not store.database_path.is_file():
        raise ConfigError(f"SQLite database does not exist: {store.database_path}")
    store.initialize()
    for repository in load_repositories(
        settings.repository_config_path, enabled_only=True
    ):
        store.update_repository_metadata(
            full_name=repository.full_name,
            github_repository_id=repository.github_repository_id,
            ecosystem=repository.ecosystem,
            primary_language=repository.primary_language,
        )
    records = store.feature_records()
    with store.connect() as connection:
        for record in records:
            features = extract_task_features(record)
            store.update_features(
                connection,
                task_candidate_id=int(record["task_candidate_id"]),
                features=features,
            )
            store.replace_skill_requirements(
                connection,
                task_candidate_id=int(record["task_candidate_id"]),
                requirements=infer_skill_requirements(record, features),
                feature_version=features.task_feature_version,
            )
    _json_dump(
        {
            "event": "task_features_extracted",
            "database_path": str(store.database_path),
            "candidate_count": len(records),
        }
    )
    return 0


def command_rank_candidates(settings: Settings, args: argparse.Namespace) -> int:
    store = _sqlite_store(settings, args.database)
    if not store.database_path.is_file():
        raise ConfigError(f"SQLite database does not exist: {store.database_path}")
    store.initialize()
    for candidate in store.rank_candidates(track=args.track, limit=args.limit):
        candidate.pop("labels_json", None)
        candidate.pop("task_types_json", None)
        candidate.pop("feature_evidence_json", None)
        _json_dump(candidate)
    return 0


def command_import_profiles(settings: Settings, args: argparse.Namespace) -> int:
    store = _sqlite_store(settings, args.database)
    profiles = load_profiles(Path(args.file).expanduser().resolve())
    for profile in profiles:
        store.upsert_profile(profile)
    _json_dump(
        {
            "event": "developer_profiles_imported",
            "database_path": str(store.database_path),
            "profile_count": len(profiles),
            "profile_keys": [profile.profile_key for profile in profiles],
        }
    )
    return 0


def command_match_candidates(settings: Settings, args: argparse.Namespace) -> int:
    store = _sqlite_store(settings, args.database)
    if not store.database_path.is_file():
        raise ConfigError(f"SQLite database does not exist: {store.database_path}")
    try:
        profile = store.profile_for_matching(args.profile)
        matches = rank_for_profile(
            profile, store.matchable_candidates(), limit=args.limit
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    for result in matches:
        _json_dump(
            {
                "profile_key": args.profile,
                "repository": result.repository,
                "issue_number": result.issue_number,
                "title": result.title,
                "html_url": result.html_url,
                "track": result.track,
                "match_score": result.match_score,
                "skill_coverage": result.skill_coverage,
                "maximum_skill_gap": result.maximum_skill_gap,
                "skill_gaps": list(result.skill_gaps),
                "reasons": list(result.reasons),
                "match_version": result.match_version,
            }
        )
    return 0


def command_serve_api(settings: Settings, args: argparse.Namespace) -> int:
    store = _sqlite_store(settings, args.database)
    if not store.database_path.is_file():
        raise ConfigError(f"SQLite database does not exist: {store.database_path}")
    loopback_names = {"127.0.0.1", "::1", "localhost"}
    if args.host not in loopback_names and not args.allow_remote:
        raise ConfigError(
            "refusing non-loopback API binding without explicit --allow-remote"
        )
    store.initialize()
    _json_dump(
        {
            "event": "api_started",
            "host": args.host,
            "port": args.port,
            "database_path": str(store.database_path),
            "cors_origin": args.cors_origin,
        }
    )
    serve(
        store,
        host=args.host,
        port=args.port,
        cors_origin=args.cors_origin,
        static_root=settings.repo_root / "web",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oss-mentor",
        description="OSS-Mentor GitHub pilot data collector",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_selection_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--wave", type=int, default=1, choices=(1, 2, 3, 4, 5))
        command.add_argument(
            "--repo",
            action="append",
            help="Exact owner/repo to select; repeat for multiple repositories.",
        )
        command.add_argument("--config", help="Override pilot repository CSV path.")
        command.add_argument(
            "--include-disabled", action="store_true", help="Include disabled rows."
        )

    list_command = subparsers.add_parser(
        "list-repositories", help="Validate and print selected repository rows."
    )
    add_selection_arguments(list_command)
    list_command.set_defaults(handler=command_list_repositories)

    collect_command = subparsers.add_parser(
        "collect-repositories",
        help="Collect repository, community, language, and label raw responses.",
    )
    add_selection_arguments(collect_command)
    collect_command.add_argument("--raw-dir", help="Override raw output directory.")
    collect_command.add_argument(
        "--dry-run", action="store_true", help="Print requests without network access."
    )
    collect_command.add_argument(
        "--allow-network",
        action="store_true",
        help="Required safety flag for real network collection.",
    )
    collect_command.add_argument(
        "--allow-anonymous",
        action="store_true",
        help="Allow unauthenticated public smoke test; subject to low rate limits.",
    )
    collect_command.set_defaults(handler=command_collect_repositories)

    compare_command = subparsers.add_parser(
        "compare-issue-sources",
        help="Compare Ecosyste.ms issue metadata with current GitHub data.",
    )
    add_selection_arguments(compare_command)
    compare_command.add_argument(
        "--sample-size", type=int, default=10, choices=range(1, 51)
    )
    compare_command.add_argument(
        "--output", help="Optional path for a reproducible JSON report."
    )
    compare_command.add_argument("--allow-network", action="store_true")
    compare_command.add_argument("--allow-anonymous", action="store_true")
    compare_command.set_defaults(handler=command_compare_issue_sources)

    sync_command = subparsers.add_parser(
        "sync-candidates",
        help="Discover candidates via Ecosyste.ms and persist verified GitHub data in SQLite.",
    )
    add_selection_arguments(sync_command)
    sync_command.add_argument(
        "--all-enabled",
        action="store_true",
        help="Select every enabled repository across all configured waves.",
    )
    sync_command.add_argument(
        "--limit",
        "--limit-per-repo",
        dest="limit_per_repo",
        type=int,
        default=20,
        choices=range(1, 101),
    )
    sync_command.add_argument("--database", help="SQLite database path.")
    sync_command.add_argument("--output", help="Optional JSON run report path.")
    sync_command.add_argument(
        "--dry-run", action="store_true", help="Print the plan without network access."
    )
    sync_command.add_argument(
        "--no-github-hydration",
        action="store_true",
        help="Store discovery records as unknown without current GitHub verification.",
    )
    sync_command.add_argument("--allow-network", action="store_true")
    sync_command.add_argument("--allow-anonymous", action="store_true")
    sync_command.set_defaults(handler=command_sync_candidates)

    refresh_command = subparsers.add_parser(
        "refresh-candidates",
        help="Refresh stale candidate eligibility and repository health from GitHub.",
    )
    add_selection_arguments(refresh_command)
    refresh_command.add_argument("--all-enabled", action="store_true")
    refresh_command.add_argument(
        "--older-than-hours", type=int, default=24, choices=range(1, 24 * 365 + 1)
    )
    refresh_command.add_argument("--limit", type=int, default=500, choices=range(1, 5001))
    refresh_command.add_argument("--database", help="SQLite database path.")
    refresh_command.add_argument("--output", help="Optional JSON run report path.")
    refresh_command.add_argument("--dry-run", action="store_true")
    refresh_command.add_argument("--allow-network", action="store_true")
    refresh_command.set_defaults(handler=command_refresh_candidates)

    report_command = subparsers.add_parser(
        "candidate-report", help="Generate an aggregate candidate-pool report offline."
    )
    report_command.add_argument("--database", help="SQLite database path.")
    report_command.add_argument("--output", help="Optional JSON report path.")
    report_command.add_argument(
        "--markdown-output",
        default=None,
        help="Aggregate Markdown report path.",
    )
    report_command.set_defaults(handler=command_candidate_report)

    candidate_list_command = subparsers.add_parser(
        "list-candidates", help="List normalized candidates from the local SQLite DB."
    )
    candidate_list_command.add_argument("--database", help="SQLite database path.")
    candidate_list_command.add_argument(
        "--eligibility",
        choices=("eligible", "temporarily_ineligible", "excluded", "unknown"),
    )
    candidate_list_command.add_argument("--newcomer-only", action="store_true")
    candidate_list_command.add_argument(
        "--limit", type=int, default=50, choices=range(1, 501)
    )
    candidate_list_command.set_defaults(handler=command_list_candidates)

    feature_command = subparsers.add_parser(
        "extract-features",
        help="Extract explainable task features and two-track scores locally.",
    )
    feature_command.add_argument("--database", help="SQLite database path.")
    feature_command.set_defaults(handler=command_extract_features)

    rank_command = subparsers.add_parser(
        "rank-candidates", help="Rank eligible candidates for one service track."
    )
    rank_command.add_argument("--database", help="SQLite database path.")
    rank_command.add_argument("--track", required=True, choices=("newcomer", "growth"))
    rank_command.add_argument("--limit", type=int, default=20, choices=range(1, 501))
    rank_command.set_defaults(handler=command_rank_candidates)

    import_command = subparsers.add_parser(
        "import-profiles", help="Import consent-aware or anonymous developer profiles."
    )
    import_command.add_argument("--file", required=True, help="Profile JSON path.")
    import_command.add_argument("--database", help="SQLite database path.")
    import_command.set_defaults(handler=command_import_profiles)

    match_command = subparsers.add_parser(
        "match-candidates", help="Rank eligible tasks for a local developer profile."
    )
    match_command.add_argument("--profile", required=True, help="Profile key.")
    match_command.add_argument("--database", help="SQLite database path.")
    match_command.add_argument("--limit", type=int, default=20, choices=range(1, 501))
    match_command.set_defaults(handler=command_match_candidates)

    serve_command = subparsers.add_parser(
        "serve-api", help="Serve the local read-only recommendation API."
    )
    serve_command.add_argument("--database", help="SQLite database path.")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8765, choices=range(1, 65536))
    serve_command.add_argument(
        "--cors-origin",
        help="Optional exact Access-Control-Allow-Origin value; disabled by default.",
    )
    serve_command.add_argument(
        "--allow-remote",
        action="store_true",
        help="Required to bind outside loopback; do not use without network controls.",
    )
    serve_command.set_defaults(handler=command_serve_api)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = _apply_path_overrides(Settings.from_env(), args)
        return int(args.handler(settings, args))
    except ConfigError as exc:
        parser.error(str(exc))
    return 2
