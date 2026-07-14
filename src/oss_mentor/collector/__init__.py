"""GitHub collection primitives for the OSS-Mentor pilot."""

from oss_mentor.collector.config import RepositoryConfig, Settings, load_repositories
from oss_mentor.collector.github_client import GitHubClient, GitHubResponse
from oss_mentor.collector.raw_store import RawRecord, RawStore

__all__ = [
    "GitHubClient",
    "GitHubResponse",
    "RawRecord",
    "RawStore",
    "RepositoryConfig",
    "Settings",
    "load_repositories",
]

