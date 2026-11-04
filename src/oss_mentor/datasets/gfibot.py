"""Read the published GFI-Bot resolved_issue member into an isolated working DB.

This is a paper-source staging schema, not the GitHub snapshot contract or an
online candidate inventory. PyMongo's BSON reader is an optional runtime tool.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import sqlite3
import struct
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path


ARCHIVE_SIZE = 2463994466
MEMBER = "dump/gfibot/resolved_issue.bson.gz"
CHUNK = 8388608
SOURCE_URL = "https://zenodo.org/records/6837711"
VERSION = "gfibot-6837711-resolved-issue-v1"


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_resolved(source: Path) -> tuple[Path, dict]:
    """Check the complete selected ZIP member without fetching the 2.46 GB ZIP."""
    metadata_path = source / "zenodo_record.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    files = [entry for entry in metadata["files"] if entry["key"] == "dump.zip"]
    if (metadata["id"] != 6837711 or metadata["metadata"]["license"]["id"] != "cc-by-4.0" or
            len(files) != 1 or files[0]["size"] != ARCHIVE_SIZE or
            files[0]["checksum"] != "md5:b39aada99ab926252549fb5d6cbd4e69"):
        raise ValueError("Official record metadata differs from pinned data release")
    tail = (source / "zip_tail.bin").read_bytes()
    with zipfile.ZipFile(io.BytesIO(tail)) as archive:
        info = archive.getinfo(MEMBER)
    start = info.header_offset + ARCHIVE_SIZE - len(tail)
    if start != 2314539508 or info.compress_type != 8 or info.flag_bits != 0:
        raise ValueError("Unexpected published archive layout")
    # Exactly the local header, name and compressed stream; no guessed record bytes.
    end = start + 30 + len(MEMBER.encode()) + info.compress_size - 1
    target = source / "resolved_issue.bson.gz"
    temporary = source / "resolved_issue.bson.gz.partial"
    decompressor = zlib.decompressobj(-15)
    size = crc = 0
    parts = []
    try:
        with temporary.open("wb") as output:
            for offset in range(start, end + 1, CHUNK):
                last = min(offset + CHUNK - 1, end)
                part = source / f"range_{offset}_{last}.bin"
                data = part.read_bytes()
                if len(data) != last - offset + 1:
                    raise ValueError("Incomplete ZIP range")
                parts.append({"path": part.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
                if offset == start:
                    header = struct.unpack("<4s5H3I2H", data[:30])
                    if (header[0], header[2], header[3], header[6], header[7], header[8], header[10]) != (
                            b"PK\x03\x04", 0, 8, info.CRC, info.compress_size, info.file_size, 0):
                        raise ValueError("ZIP local/central header mismatch")
                    if data[30:30 + header[9]] != MEMBER.encode():
                        raise ValueError("ZIP member name mismatch")
                    data = data[30 + header[9]:]
                decoded = decompressor.decompress(data)
                output.write(decoded)
                size += len(decoded)
                crc = zlib.crc32(decoded, crc)
            if not decompressor.eof or decompressor.unused_data or size != info.file_size or crc != info.CRC:
                raise ValueError("ZIP member length/CRC/stream integrity mismatch")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target, {
        "source_url": SOURCE_URL, "doi": "10.5281/zenodo.6837711", "paper_data_version": "0.0.3",
        "license": "CC-BY-4.0", "attribution": "Hao He, Haonan Su, Wenxin Xiao, Runzhi He, Minghui Zhou (2022), GFI-Bot dataset",
        "archive_size": ARCHIVE_SIZE, "archive_expected_md5": "b39aada99ab926252549fb5d6cbd4e69",
        "full_archive_downloaded": False, "full_archive_md5_verified": False,
        "selected_member": MEMBER, "selected_member_complete": True, "zip_crc32_verified": True,
        "member_bytes": size, "member_sha256": sha256_file(target),
        "official_record_sha256": sha256_file(metadata_path),
        "zip_tail_sha256": hashlib.sha256(tail).hexdigest(), "parts": parts,
    }


def bson_documents(stream):
    """Yield source offsets and raw records, checking truncation and BSON framing."""
    offset = 0
    while True:
        head = stream.read(4)
        if not head:
            return
        if len(head) != 4:
            raise ValueError("Truncated BSON length")
        size, = struct.unpack("<i", head)
        if not 5 <= size <= 16 * 1024 * 1024:
            raise ValueError("Invalid BSON document length")
        body = stream.read(size - 4)
        if len(body) != size - 4 or body[-1] != 0:
            raise ValueError("Truncated BSON document")
        yield offset, head + body
        offset += size


def utc(value):
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError("Invalid source time")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)  # BSON datetime is UTC.
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_resolution(document: dict) -> dict:
    owner, name, number = document.get("owner"), document.get("name"), document.get("number")
    if (not isinstance(owner, str) or not isinstance(name, str) or
            not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or
            type(number) is not int or number <= 0):
        raise ValueError("Invalid source issue identity")
    repository = f"{owner}/{name}"
    resolver = document.get("resolver")
    if resolver is not None and (not isinstance(resolver, str) or not resolver):
        raise ValueError("Invalid source resolver")
    resolved_in = document.get("resolved_in")
    if type(resolved_in) is int and resolved_in > 0:
        kind, locator = "pull_request", str(resolved_in)
    elif isinstance(resolved_in, str) and re.fullmatch(r"[0-9a-f]{40}", resolved_in):
        kind, locator = "commit", resolved_in
    else:
        kind, locator = "unknown", None
    prior = document.get("resolver_commit_num")
    if prior is not None and (type(prior) is not int or prior < 0):
        raise ValueError("Invalid prior-commit label")
    created, resolved = utc(document.get("created_at")), utc(document.get("resolved_at"))
    if created and resolved and created > resolved:
        raise ValueError("Resolution predates issue creation")
    return {
        "issue_key": f"github:{repository.lower()}#{number}", "repository_key": f"github:{repository.lower()}",
        "source_repository": repository, "number": number, "source_url": f"https://github.com/{repository}/issues/{number}",
        "created_at": created, "resolved_at": resolved, "source_resolver": resolver,
        # No numeric GitHub ID in the dump: do not merge this identity with verified accounts.
        "resolver_key": f"gfibot:6837711:login:{resolver.lower()}" if resolver else None,
        "identity_status": "source_login_only" if resolver else "unknown", "resolution_kind": kind, "resolution_locator": locator,
        "resolver_commit_num": prior, "feature_role": "outcome_only",
        "prediction_features_ready": False, "live_candidate_verified": False,
    }


def import_resolved(member: Path, database: Path, evidence: dict) -> dict:
    from bson import BSON  # Optional PyMongo dependency; never execute deserialized source code.

    if sha256_file(member) != evidence["member_sha256"] or not evidence["zip_crc32_verified"]:
        raise ValueError("Unverified member")
    if database.exists():
        raise ValueError("Choose a new working DB; existing databases are not overwritten")
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript("""
            CREATE TABLE paper_source (version TEXT PRIMARY KEY, status TEXT NOT NULL, evidence_json TEXT NOT NULL);
            CREATE TABLE paper_resolution (
              source_id TEXT PRIMARY KEY, issue_key TEXT NOT NULL UNIQUE, repository_key TEXT NOT NULL,
              resolver_key TEXT, created_at TEXT, resolved_at TEXT, resolution_kind TEXT NOT NULL,
              record_json TEXT NOT NULL, raw_offset INTEGER NOT NULL, raw_length INTEGER NOT NULL, raw_sha256 TEXT NOT NULL);
            CREATE INDEX paper_resolution_repo ON paper_resolution(repository_key);
            CREATE TABLE paper_rejected (raw_offset INTEGER PRIMARY KEY, raw_length INTEGER, raw_sha256 TEXT, reason TEXT NOT NULL);
        """)
        with connection:
            connection.execute("INSERT INTO paper_source VALUES (?, 'running', ?)", (VERSION, json.dumps(evidence, sort_keys=True)))
        seen = duplicates = rejected = 0
        with gzip.open(member, "rb") as stream:
            for offset, raw in bson_documents(stream):
                document = BSON(raw).decode()
                digest = hashlib.sha256(raw).hexdigest()
                seen += 1
                try:
                    normalized = normalize_resolution(document)
                    if document.get("_id") is None:
                        raise ValueError("Missing source document ID")
                except ValueError as error:
                    connection.execute("INSERT INTO paper_rejected VALUES (?,?,?,?)", (offset, len(raw), digest, str(error)))
                    rejected += 1
                    continue
                payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
                existing = connection.execute("SELECT source_id,raw_sha256 FROM paper_resolution WHERE source_id=? OR issue_key=?",
                                              (str(document["_id"]), normalized["issue_key"])).fetchall()
                if existing:
                    if existing == [(str(document["_id"]), digest)]:
                        duplicates += 1
                        continue
                    raise ValueError("Conflicting source identity or canonical issue; manual audit required")
                connection.execute("INSERT INTO paper_resolution VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    str(document["_id"]), normalized["issue_key"], normalized["repository_key"], normalized["resolver_key"],
                    normalized["created_at"], normalized["resolved_at"], normalized["resolution_kind"], payload, offset, len(raw), digest))
                if seen % 2000 == 0:
                    connection.commit()
        # Reading gzip to EOF above verifies its internal checksum as well as BSON framing.
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError("SQLite integrity failure")
        count, repositories, resolvers = connection.execute(
            "SELECT count(*),count(DISTINCT repository_key),count(DISTINCT resolver_key) FROM paper_resolution").fetchone()
        report = {
            "dataset_version": VERSION, "status": "completed", "lifecycle": "working", "frozen": False,
            "source_documents": seen, "historical_issues": count, "repositories": repositories,
            "source_resolver_identities": resolvers, "exact_duplicates": duplicates, "rejected_documents": rejected,
            "resolution_kinds": dict(connection.execute("SELECT resolution_kind,count(*) FROM paper_resolution GROUP BY resolution_kind")),
            "missing_resolvers": connection.execute("SELECT count(*) FROM paper_resolution WHERE resolver_key IS NULL").fetchone()[0],
            "missing_resolution_times": connection.execute("SELECT count(*) FROM paper_resolution WHERE resolved_at IS NULL").fetchone()[0],
            "paper_new_to_repository_labels": connection.execute("SELECT count(*) FROM paper_resolution WHERE json_extract(record_json,'$.resolver_commit_num')=0").fetchone()[0],
            "created_at_range": list(connection.execute("SELECT min(created_at),max(created_at) FROM paper_resolution").fetchone()),
            "resolved_at_range": list(connection.execute("SELECT min(resolved_at),max(resolved_at) FROM paper_resolution").fetchone()),
            "gzip_integrity_verified": True, "sqlite_integrity": integrity,
            "file_contribution_edges": 0, "current_open_candidates": 0, "task_feature_collection_imported": False,
            "feature_role": "outcome_only", "coder_original_dataset": False,
            "limitations": ["Paper-reported resolution; not individually reverified against GitHub",
                            "Source logins only; no confirmed numeric GitHub identities",
                            "No issue titles/bodies in this collection; features dataset still required",
                            "No file graph, historical prediction split, training or live candidate sync"],
            "source_evidence": evidence,
        }
        with connection:
            connection.execute("UPDATE paper_source SET status='completed',evidence_json=? WHERE version=?", (json.dumps(report, sort_keys=True), VERSION))
        return report
    except Exception:
        connection.rollback()
        # Consumers must gate on completed. Preserve failed inputs/rows for inspection.
        with connection:
            connection.execute("UPDATE paper_source SET status='failed' WHERE version=?", (VERSION,))
        raise
    finally:
        connection.close()
