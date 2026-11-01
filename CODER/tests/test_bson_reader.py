from __future__ import annotations

import gzip
from pathlib import Path
import tempfile

import pytest
from bson import BSON

from coder_issue.data.bson_reader import (
    BsonStreamError,
    iter_bson_batches,
    iter_bson_documents,
)


def test_reads_concatenated_bson_in_batches() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sample.bson"
        path.write_bytes(BSON.encode({"value": 1}) + BSON.encode({"value": 2}))
        assert [item["value"] for item in iter_bson_documents(path)] == [1, 2]
        assert [len(batch) for batch in iter_bson_batches(path, batch_size=1)] == [1, 1]


def test_reads_bson_gz() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sample.bson.gz"
        with gzip.open(path, "wb") as stream:
            stream.write(BSON.encode({"value": 3}))
        assert next(iter_bson_documents(path))["value"] == 3


def test_truncated_bson_has_contextual_error() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "broken.bson"
        path.write_bytes(BSON.encode({"value": 1})[:-2])
        with pytest.raises(BsonStreamError, match="truncated BSON document.*record 0"):
            list(iter_bson_documents(path))
