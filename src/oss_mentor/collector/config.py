"""Runtime and pilot-repository configuration."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class ConfigError(ValueError):
    """Raised when collector configuration is incomplete or inconsistent."""


def discover_repo_root(start: Path | None = None) -> Path:
    """Find the nearest parent containing ``pyproject.toml``."""

    candidate = (start or Path.cwd()).resolve()
    for directory in (candidate, *candidate.parents):
        if (directory / "pyproject.toml").is_file():
            return directory

    package_fallback = Path(__file__).resolve().parents[3]
    if (package_fallback / "pyproject.toml").is_file():
        return package_fallback
    raise ConfigError("Could not locate repository root containing pyproject.toml")


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    repo_root: Path
    github_token: str | None
    github_api_base: str
    github_api_version: str
    ecosystems_issues_api_base: str
    user_agent: str
    repository_config_path: Path
    raw_root: Path
    http_timeout_seconds: int
    max_retries: int
    backoff_base_seconds: int

    @classmethod
    def from_env(cls, repo_root: Path | None = None) -> "Settings":
        root = discover_repo_root(repo_root)
        config_value = os.getenv(
            "OSS_MENTOR_REPO_CONFIG", "config/pilot_repositories_v0.1.csv"
        )
        raw_value = os.getenv("OSS_MENTOR_RAW_DIR", "data/raw")
        return cls(
            repo_root=root,
            github_token=os.getenv("GITHUB_TOKEN") or None,
            github_api_base=os.getenv(
                "OSS_MENTOR_GITHUB_API_BASE", "https://api.github.com"
            ).rstrip("/"),
            github_api_version=os.getenv(
                "OSS_MENTOR_GITHUB_API_VERSION", "2026-03-10"
            ),
            ecosystems_issues_api_base=os.getenv(
                "OSS_MENTOR_ECOSYSTEMS_ISSUES_API_BASE",
                "https://issues.ecosyste.ms/api/v1",
            ).rstrip("/"),
            user_agent=os.getenv(
                "OSS_MENTOR_USER_AGENT", "OSS-Mentor-research/0.1.0"
            ),
            repository_config_path=_resolve_path(root, config_value),
            raw_root=_resolve_path(root, raw_value),
            http_timeout_seconds=_env_int(
                "OSS_MENTOR_HTTP_TIMEOUT_SECONDS", 30, minimum=1
            ),
            max_retries=_env_int("OSS_MENTOR_MAX_RETRIES", 5, minimum=0),
            backoff_base_seconds=_env_int(
                "OSS_MENTOR_BACKOFF_BASE_SECONDS", 1, minimum=1
            ),
        )


@dataclass(frozen=True, slots=True)
class RepositoryConfig:
    wave: int
    enabled: bool
    full_name: str
    github_repository_id: int
    ecosystem: str
    primary_language: str
    license_spdx: str
    default_branch: str
    stars_snapshot: int
    open_items_api: int
    pushed_at_utc: str
    community_health: int
    has_contributing: bool
    has_code_of_conduct: bool
    has_issue_template_community_api: bool
    has_pr_template: bool
    open_good_first_issues: int
    candidate_labels: tuple[str, ...]
    pilot_role: str
    code_access_policy: str
    verified_on: str
    notes: str

    @property
    def owner(self) -> str:
        return self.full_name.split("/", maxsplit=1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", maxsplit=1)[1]

    @classmethod
    def from_row(cls, row: dict[str, str], *, line_number: int) -> "RepositoryConfig":
        required = {
            "wave",
            "enabled",
            "full_name",
            "github_repository_id",
            "ecosystem",
            "primary_language",
            "license_spdx",
            "default_branch",
            "stars_snapshot",
            "open_items_api",
            "pushed_at_utc",
            "community_health",
            "has_contributing",
            "has_code_of_conduct",
            "has_issue_template_community_api",
            "has_pr_template",
            "open_good_first_issues",
            "candidate_labels",
            "pilot_role",
            "code_access_policy",
            "verified_on",
            "notes",
        }
        missing = sorted(required.difference(row))
        if missing:
            raise ConfigError(f"CSV is missing columns: {', '.join(missing)}")

        def parse_int(name: str, *, minimum: int = 0) -> int:
            raw = (row.get(name) or "").strip()
            try:
                value = int(raw)
            except ValueError as exc:
                raise ConfigError(
                    f"line {line_number}: {name} must be an integer, got {raw!r}"
                ) from exc
            if value < minimum:
                raise ConfigError(
                    f"line {line_number}: {name} must be >= {minimum}, got {value}"
                )
            return value

        def parse_bool(name: str) -> bool:
            raw = (row.get(name) or "").strip().lower()
            if raw in {"true", "1", "yes"}:
                return True
            if raw in {"false", "0", "no"}:
                return False
            raise ConfigError(
                f"line {line_number}: {name} must be true/false, got {raw!r}"
            )

        full_name = (row.get("full_name") or "").strip()
        if full_name.count("/") != 1 or any(
            not part.strip() for part in full_name.split("/", maxsplit=1)
        ):
            raise ConfigError(
                f"line {line_number}: full_name must be owner/repo, got {full_name!r}"
            )

        labels = tuple(
            label.strip()
            for label in (row.get("candidate_labels") or "").split("|")
            if label.strip()
        )
        return cls(
            wave=parse_int("wave", minimum=1),
            enabled=parse_bool("enabled"),
            full_name=full_name,
            github_repository_id=parse_int("github_repository_id", minimum=1),
            ecosystem=(row.get("ecosystem") or "").strip(),
            primary_language=(row.get("primary_language") or "").strip(),
            license_spdx=(row.get("license_spdx") or "").strip(),
            default_branch=(row.get("default_branch") or "").strip(),
            stars_snapshot=parse_int("stars_snapshot"),
            open_items_api=parse_int("open_items_api"),
            pushed_at_utc=(row.get("pushed_at_utc") or "").strip(),
            community_health=parse_int("community_health"),
            has_contributing=parse_bool("has_contributing"),
            has_code_of_conduct=parse_bool("has_code_of_conduct"),
            has_issue_template_community_api=parse_bool(
                "has_issue_template_community_api"
            ),
            has_pr_template=parse_bool("has_pr_template"),
            open_good_first_issues=parse_int("open_good_first_issues"),
            candidate_labels=labels,
            pilot_role=(row.get("pilot_role") or "").strip(),
            code_access_policy=(row.get("code_access_policy") or "").strip(),
            verified_on=(row.get("verified_on") or "").strip(),
            notes=(row.get("notes") or "").strip(),
        )


def _validate_unique(repositories: Iterable[RepositoryConfig]) -> None:
    names: set[str] = set()
    ids: set[int] = set()
    for repository in repositories:
        normalized_name = repository.full_name.casefold()
        if normalized_name in names:
            raise ConfigError(f"duplicate repository full_name: {repository.full_name}")
        if repository.github_repository_id in ids:
            raise ConfigError(
                "duplicate github_repository_id: "
                f"{repository.github_repository_id} ({repository.full_name})"
            )
        names.add(normalized_name)
        ids.add(repository.github_repository_id)


def load_repositories(
    path: Path,
    *,
    wave: int | None = None,
    enabled_only: bool = True,
) -> list[RepositoryConfig]:
    """Load and validate the machine-readable pilot repository list."""

    if not path.is_file():
        raise ConfigError(f"repository config does not exist: {path}")

    repositories: list[RepositoryConfig] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ConfigError(f"repository config has no header: {path}")
        for line_number, row in enumerate(reader, start=2):
            repository = RepositoryConfig.from_row(row, line_number=line_number)
            if enabled_only and not repository.enabled:
                continue
            if wave is not None and repository.wave != wave:
                continue
            repositories.append(repository)

    _validate_unique(repositories)
    if not repositories:
        suffix = f" for wave {wave}" if wave is not None else ""
        raise ConfigError(f"no repositories selected{suffix} from {path}")
    return repositories
