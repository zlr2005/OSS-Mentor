"""Offline draft import/export using D's existing SQLite initialization factory."""

import argparse
import json
import sqlite3
from pathlib import Path

from oss_mentor.datasets.normalization import normalize_records
from oss_mentor.datasets.snapshot import load_snapshot
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.storage.datasets import DatasetStore


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True,
                        help="Use a dedicated midterm/test database, not the existing live database")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import")
    importer.add_argument("directory", type=Path)
    importer.add_argument("--chunk-size", type=int, default=100)
    status = commands.add_parser("status")
    status.add_argument("dataset_version")
    exporter = commands.add_parser("export")
    exporter.add_argument("dataset_version")
    exporter.add_argument("target", type=Path)
    args = parser.parse_args()
    if args.command == "import" and args.chunk_size < 1:
        parser.error("--chunk-size must be positive")
    database = args.database.resolve()
    if database == (ROOT / "data/oss_mentor.sqlite3").resolve():
        parser.error("Choose an independent midterm/test database; the existing live database is excluded")
    if args.command != "import" and not database.is_file():
        parser.error("Database does not exist; run import first")
    try:
        if args.command == "import":
            # Reject invalid input before initialization creates/modifies any DB.
            _, records, _ = load_snapshot(args.directory)
            normalize_records(records)
        factory = SQLiteCandidateStore(database, ROOT / "db/sqlite/001_mvp.sql")
        if args.command == "import":
            factory.initialize()
        connection = factory.connect()
        try:
            store = DatasetStore(connection)
            if args.command == "import":
                report = store.import_snapshot(args.directory, chunk_size=args.chunk_size)
            elif args.command == "status":
                report = store.status(args.dataset_version)
            else:
                report = store.export_snapshot(args.dataset_version, args.target)
        finally:
            connection.close()
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
