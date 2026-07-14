"""Wave-oriented repository metadata collector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from oss_mentor.collector.config import RepositoryConfig
from oss_mentor.collector.github_client import GitHubClient
from oss_mentor.collector.raw_store import RawRecord, RawStore


@dataclass(frozen=True, slots=True)
class EndpointPlan:
    name: str
    path: str
    paginated: bool = False
    params: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CollectionResult:
    repository: str
    raw_records: tuple[RawRecord, ...]

    @property
    def request_count(self) -> int:
        return len(self.raw_records)


class RepositoryCollector:
    """Collect the four repository-level endpoints required by the smoke test."""

    def __init__(self, client: GitHubClient, raw_store: RawStore) -> None:
        self.client = client
        self.raw_store = raw_store

    @staticmethod
    def plan(repository: RepositoryConfig) -> tuple[EndpointPlan, ...]:
        base = f"/repos/{repository.owner}/{repository.name}"
        return (
            EndpointPlan("repository", base),
            EndpointPlan("community_profile", f"{base}/community/profile"),
            EndpointPlan("languages", f"{base}/languages"),
            EndpointPlan(
                "labels",
                f"{base}/labels",
                paginated=True,
                params={"per_page": 100},
            ),
        )

    def collect(
        self,
        repository: RepositoryConfig,
        *,
        collection_run_id: UUID,
    ) -> CollectionResult:
        records: list[RawRecord] = []
        for endpoint in self.plan(repository):
            if endpoint.paginated:
                for response in self.client.iter_pages(
                    endpoint.path, params=endpoint.params
                ):
                    records.append(
                        self.raw_store.save(
                            response,
                            endpoint_name=endpoint.name,
                            repository_full_name=repository.full_name,
                            collection_run_id=collection_run_id,
                        )
                    )
            else:
                response = self.client.get(endpoint.path, params=endpoint.params)
                records.append(
                    self.raw_store.save(
                        response,
                        endpoint_name=endpoint.name,
                        repository_full_name=repository.full_name,
                        collection_run_id=collection_run_id,
                    )
                )
        return CollectionResult(
            repository=repository.full_name,
            raw_records=tuple(records),
        )
