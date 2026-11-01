"""Version and run identity conventions for reproducible artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import platform
import re
import subprocess
import sys

from .config import Settings


@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    created_at: str
    config_name: str
    dataset_version: str
    code_version: str
    seed: int
    python_version: str
    platform: str

    def as_dict(self) -> dict[str, str | int]:
        return asdict(self)


def git_code_version(repository_root: Path) -> str:
    """Return the current commit plus a dirty marker, or ``unknown``."""

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{commit}-dirty" if dirty else commit


def build_run_metadata(settings: Settings, now: datetime | None = None) -> RunMetadata:
    """Build the metadata saved beside every future run artifact."""

    created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    code_version = git_code_version(settings.paths.repository_root)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", settings.config_name).strip("-")
    run_id = f"{created:%Y%m%dT%H%M%SZ}-{code_version}-{safe_name}"
    return RunMetadata(
        run_id=run_id,
        created_at=created.isoformat(),
        config_name=settings.config_name,
        dataset_version=settings.dataset_version,
        code_version=code_version,
        seed=settings.seed,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
    )
