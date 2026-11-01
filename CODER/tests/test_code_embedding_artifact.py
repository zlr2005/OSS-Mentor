from __future__ import annotations

from pathlib import Path
import tempfile

import numpy as np

from coder_issue.models.code_encoder import (
    CodeDocument,
    CodeEmbeddingArtifact,
    CodeSegmentationConfig,
)


class FakeCodeEncoder:
    model_name = "microsoft/codebert-base"
    revision = "test-revision"
    dimension = 3

    def encode(self, segments):
        return np.asarray(
            [[len(value), sum(map(ord, value)) % 11, 1.0] for value in segments],
            dtype=np.float32,
        )


def test_code_embedding_artifact_keeps_mapping_failures_and_hashes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        artifact = CodeEmbeddingArtifact(Path(directory))
        manifest = artifact.encode(
            [
                CodeDocument("file-version:a", "def f():\n    return 1\n"),
                CodeDocument("file-version:empty", ""),
                CodeDocument("file-version:b", "x = 2\n"),
            ],
            FakeCodeEncoder(),
            artifact_version="codebert-files-v0.1",
            source_version="issue-release-v0.1",
            tokenizer={"max_length": 256, "truncation": True},
            segmentation=CodeSegmentationConfig(max_lines_per_segment=1, max_segments=8),
        )
        validated = artifact.validate()
        rows = (Path(directory) / "row_mapping.jsonl").read_text(encoding="utf-8").splitlines()
    assert manifest["input_count"] == 3
    assert manifest["success_count"] == 2
    assert manifest["failure_count"] == 1
    assert validated == manifest
    assert len(rows) == 3


def test_duplicate_file_version_keys_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        artifact = CodeEmbeddingArtifact(Path(directory))
        try:
            artifact.encode(
                [CodeDocument("same", "a"), CodeDocument("same", "b")],
                FakeCodeEncoder(),
                artifact_version="v1",
                source_version="s1",
                tokenizer={},
            )
        except ValueError as exc:
            assert "unique" in str(exc)
        else:
            raise AssertionError("duplicate file_version_key values must be rejected")
