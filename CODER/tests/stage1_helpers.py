from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

from bson import BSON, ObjectId


def write_bson(path: Path, documents: list[dict]) -> None:
    with path.open("wb") as stream:
        for document in documents:
            stream.write(BSON.encode(document))


def dataset_snapshot(
    number: int,
    before: datetime,
    *,
    created: datetime,
    closed: datetime | None,
    labels: list[str],
) -> dict:
    return {
        "_id": ObjectId(),
        "owner": "Example",
        "name": "Repo",
        "number": number,
        "created_at": created,
        "closed_at": closed,
        "before": before,
        "title": f"Issue {number}",
        "body": "Synthetic test body",
        "labels": labels,
        "label_category": {"gfi": int("good first issue" in labels)},
        "n_stars": 10 + number,
        "n_contributors": 2,
        "n_commits": 20,
        "n_pulls": 3,
        "n_open_issues": 4,
        "events": [],
        "comments": [],
        "comment_users": [],
        "event_users": [],
        "resolver_commit_num": 999,
    }


def resolved_issue(number: int, created: datetime, resolved: datetime, resolver: str) -> dict:
    return {
        "_id": ObjectId(),
        "owner": "Example",
        "name": "Repo",
        "number": number,
        "created_at": created,
        "resolved_at": resolved,
        "resolver": resolver,
        "resolver_commit_num": 999,
        "events": [
            {
                "type": "commented",
                "time": created,
                "actor": resolver,
                "commenter": resolver,
                "assignee": None,
                "label": None,
                "commit": None,
                "comment": "not exported to normalized event payload",
                "source": None,
            }
        ],
    }


def create_test_project(root: Path, *, reverse: bool = False, conflict: bool = False) -> Path:
    configs = root / "configs"
    coder = root / "CODER"
    configs.mkdir(parents=True)
    coder.mkdir()
    artifact_root = root / "data" / "midterm"
    dataset_path = coder / "dataset.bson"
    resolved_path = coder / "resolved_issue.bson"

    issue_times = [
        (1, datetime(2020, 1, 1), datetime(2020, 6, 1), "Alice"),
        (2, datetime(2020, 2, 1), datetime(2021, 6, 1), "Bob"),
        (3, datetime(2020, 3, 1), datetime(2022, 2, 1), "Carol"),
    ]
    dataset_documents: list[dict] = []
    for number, created, closed, _resolver in issue_times:
        dataset_documents.append(
            dataset_snapshot(
                number, created, created=created, closed=closed, labels=["good first issue"]
            )
        )
        dataset_documents.append(
            dataset_snapshot(
                number, closed, created=created, closed=closed, labels=["help wanted"]
            )
        )
    dataset_documents.append(
        dataset_snapshot(
            4,
            datetime(2020, 4, 1),
            created=datetime(2020, 4, 1),
            closed=None,
            labels=[],
        )
    )
    resolved_documents = [
        resolved_issue(number, created, resolved, resolver)
        for number, created, resolved, resolver in issue_times
    ]
    if conflict:
        resolved_documents.append(
            resolved_issue(1, datetime(2020, 1, 1), datetime(2020, 7, 1), "Eve")
        )
    resolved_documents.append(
        resolved_issue(99, datetime(2020, 1, 1), datetime(2020, 8, 1), "Missing")
    )
    if reverse:
        dataset_documents.reverse()
        resolved_documents.reverse()
    write_bson(dataset_path, dataset_documents)
    write_bson(resolved_path, resolved_documents)

    def manifest_entry(path: Path) -> dict:
        payload = path.read_bytes()
        return {
            "name": path.name,
            "path": path.relative_to(root).as_posix(),
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    (coder / "data_manifest.json").write_text(
        json.dumps(
            {
                "dataset_version": "synthetic-raw-v1",
                "files": [manifest_entry(dataset_path), manifest_entry(resolved_path)],
            }
        ),
        encoding="utf-8",
    )
    (configs / "base.toml").write_text(
        """
schema_version = 1
config_name = "test"
seed = 7
dataset_version = "synthetic-raw-v1"
[paths]
repository_root = ".."
artifact_root = "data/midterm"
normalized_dir = "normalized"
embeddings_dir = "embeddings"
graphs_dir = "graphs"
models_dir = "models"
runs_dir = "runs"
raw_data_files = ["CODER/dataset.bson", "CODER/resolved_issue.bson"]
[logging]
level = "INFO"
format = "text"
[manifest]
output = "CODER/data_manifest.json"
chunk_size_bytes = 8
""".strip(),
        encoding="utf-8",
    )
    stage_config = configs / "data_preparation.toml"
    stage_config.write_text(
        """
schema_version = 1
config_name = "synthetic-m0-v1"
source_dataset_version = "synthetic-raw-v1"
output_version = "synthetic-m0-v1"
[inputs]
base_config = "base.toml"
raw_manifest = "../CODER/data_manifest.json"
dataset_file_index = 0
resolved_issue_file_index = 1
[processing]
batch_size = 2
progress_every = 100
max_document_bytes = 1048576
parquet_compression = "zstd"
verify_sha256 = true
[split]
protocol = "fixed_train_graph"
train_before = "2021-01-01T00:00:00Z"
valid_before = "2022-01-01T00:00:00Z"
time_source = "resolution_proxy"
[candidates]
scope = "historical_resolved_issue_subset"
supported_modes = ["same_repo", "cross_repo"]
seed = 7
""".strip(),
        encoding="utf-8",
    )
    artifact_root.mkdir(parents=True)
    return stage_config
