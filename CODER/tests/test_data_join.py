from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pyarrow.parquet as pq

from coder_issue.data.prepare_data import prepare
from coder_issue.data.schema import load_preparation_settings

from stage1_helpers import create_test_project


def test_full_synthetic_join_deduplicates_snapshots_and_splits_by_time() -> None:
    with tempfile.TemporaryDirectory() as directory:
        config = create_test_project(Path(directory))
        settings = load_preparation_settings(config)
        quality = prepare(settings)
        assert quality["raw_data"]["successfully_joined_issue_count"] == 3
        assert quality["raw_data"]["unmatched_resolved_issue_count"] == 1
        assert quality["entities_and_relations"]["issue_snapshots"] == 7
        assert pq.read_table(settings.normalized_output / "developer_issue_edges.parquet").num_rows == 3
        split_manifest = json.loads(
            (settings.graph_output / "split_manifest.json").read_text(encoding="utf-8")
        )
        assert split_manifest["statistics"]["train"]["interaction_count"] == 1
        assert split_manifest["statistics"]["valid"]["interaction_count"] == 1
        assert split_manifest["statistics"]["test"]["interaction_count"] == 1


def test_creation_snapshot_does_not_expose_closed_at_or_resolver() -> None:
    with tempfile.TemporaryDirectory() as directory:
        settings = load_preparation_settings(create_test_project(Path(directory)))
        prepare(settings)
        snapshots = pq.read_table(settings.normalized_output / "issue_snapshots.parquet")
        assert "resolver" not in snapshots.column_names
        rows = snapshots.to_pylist()
        assert all(
            row["closed_at"] is None
            for row in rows
            if row["snapshot_type"] == "creation_snapshot"
        )


def test_conflicting_resolution_is_counted_and_not_silently_selected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        settings = load_preparation_settings(
            create_test_project(Path(directory), conflict=True)
        )
        quality = prepare(settings)
        assert quality["raw_data"]["conflicting_resolved_issue_count"] == 1
        assert quality["interactions"]["developer_issue_edges"] == 2
