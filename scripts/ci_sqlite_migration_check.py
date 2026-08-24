"""CI check: apply every SQLite migration to an empty database."""

import tempfile
from pathlib import Path

from oss_mentor.sqlite_store import SQLiteCandidateStore

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        store = SQLiteCandidateStore(
            Path(tmp) / "migration_test.sqlite3",
            ROOT / "db" / "sqlite" / "001_mvp.sql",
        )
        store.initialize()
        with store.connect() as connection:
            applied = connection.execute(
                "SELECT migration_name FROM schema_migration ORDER BY migration_name"
            ).fetchall()
        applied_names = [row[0] for row in applied]
        expected = sorted(path.name for path in (ROOT / "db" / "sqlite").glob("*.sql"))
        if applied_names != expected:
            missing = sorted(set(expected) - set(applied_names))
            print(f"missing migrations: {missing}")
            return 1
        print(f"sqlite migrations ok: {len(applied_names)} applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
