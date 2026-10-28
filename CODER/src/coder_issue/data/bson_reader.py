"""Bounded-memory readers for concatenated MongoDB BSON exports."""

from __future__ import annotations

from collections.abc import Iterator
import gzip
from pathlib import Path
import struct
from typing import Any, BinaryIO

from bson import BSON
from bson.errors import BSONError

from coder_issue.errors import DataFileError


DEFAULT_MAX_DOCUMENT_BYTES = 64 * 1024 * 1024


class BsonStreamError(DataFileError):
    """A BSON stream is truncated, malformed, or exceeds the safety limit."""


def _open_binary(path: Path) -> BinaryIO:
    try:
        if path.name.casefold().endswith(".gz"):
            return gzip.open(path, "rb")
        return path.open("rb")
    except OSError as exc:
        raise BsonStreamError(f"cannot open BSON stream {path}: {exc}") from exc


def iter_bson_documents(
    path: str | Path,
    *,
    max_document_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
) -> Iterator[dict[str, Any]]:
    """Yield one document at a time from a BSON or BSON.GZ collection export.

    MongoDB collection exports are concatenated BSON documents. Only one encoded
    document is held in memory at once. The byte offset and record index are
    included in failures so corrupt exports are diagnosable.
    """

    if max_document_bytes < 5:
        raise ValueError("max_document_bytes must be at least 5")
    source = Path(path).resolve()
    record_index = 0
    offset = 0
    with _open_binary(source) as stream:
        while True:
            try:
                prefix = stream.read(4)
            except OSError as exc:
                raise BsonStreamError(
                    f"cannot read {source} at byte offset {offset}: {exc}"
                ) from exc
            if prefix == b"":
                return
            if len(prefix) != 4:
                raise BsonStreamError(
                    f"truncated BSON length at record {record_index}, byte offset {offset}"
                )
            (document_length,) = struct.unpack("<i", prefix)
            if document_length < 5 or document_length > max_document_bytes:
                raise BsonStreamError(
                    f"invalid BSON length {document_length} at record {record_index}, "
                    f"byte offset {offset}"
                )
            try:
                remainder = stream.read(document_length - 4)
            except OSError as exc:
                raise BsonStreamError(
                    f"cannot read BSON record {record_index} from {source}: {exc}"
                ) from exc
            if len(remainder) != document_length - 4:
                raise BsonStreamError(
                    f"truncated BSON document at record {record_index}, byte offset {offset}: "
                    f"expected {document_length} bytes"
                )
            encoded = prefix + remainder
            try:
                document = BSON(encoded).decode()
            except BSONError as exc:
                raise BsonStreamError(
                    f"invalid BSON at record {record_index}, byte offset {offset}: {exc}"
                ) from exc
            if not isinstance(document, dict):
                raise BsonStreamError(
                    f"BSON record {record_index} did not decode to a document"
                )
            yield document
            record_index += 1
            offset += document_length


def iter_bson_batches(
    path: str | Path,
    *,
    batch_size: int,
    max_document_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
) -> Iterator[list[dict[str, Any]]]:
    """Yield bounded batches while retaining streaming behavior."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    batch: list[dict[str, Any]] = []
    for document in iter_bson_documents(path, max_document_bytes=max_document_bytes):
        batch.append(document)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch
