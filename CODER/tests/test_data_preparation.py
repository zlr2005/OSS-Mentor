from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from coder_issue.data.prepare_data import prepare
from coder_issue.data.schema import load_preparation_settings
from coder_issue.data.validation import validate_output_directories
from coder_issue.errors import DataFileError

from stage1_helpers import create_test_project


def test_mappings_are_identical_across_repeated_fresh_runs() -> None:
    snapshots: list[bytes] = []
    for reverse in (False, True):
        with tempfile.TemporaryDirectory() as directory:
            settings = load_preparation_settings(
                create_test_project(Path(directory), reverse=reverse)
            )
            prepare(settings)
            snapshots.append(
                (settings.normalized_output / "mappings/issue_mapping.parquet").read_bytes()
            )
            assert validate_output_directories(settings)["status"] == "passed"
    assert snapshots[0] == snapshots[1]


def test_completed_version_is_never_overwritten() -> None:
    with tempfile.TemporaryDirectory() as directory:
        settings = load_preparation_settings(create_test_project(Path(directory)))
        prepare(settings)
        with pytest.raises(DataFileError, match="refusing to overwrite"):
            prepare(settings)
