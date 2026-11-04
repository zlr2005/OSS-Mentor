"""Offline source validation and isolated import/idempotency checks for B's pilot."""
import argparse
import hashlib
import json
from pathlib import Path

from oss_mentor.datasets.snapshot import load_snapshot, validate_snapshot
from oss_mentor.sqlite_store import SQLiteCandidateStore
from oss_mentor.storage.datasets import DatasetStore

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    verified_files = 0
    for line in (root / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        checksum, relative = line.split('  ', 1)
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError('File checksum/path mismatch')
        verified_files += 1
    database = root / 'validation.sqlite3'
    store = SQLiteCandidateStore(database, ROOT / 'db/sqlite/001_mvp.sql')
    store.initialize()
    results = []
    for snapshot in sorted((root / 'snapshots').iterdir()):
        validation = validate_snapshot(snapshot)
        manifest, _, _ = load_snapshot(snapshot)
        with store.connect() as connection:
            datasets = DatasetStore(connection)
            first = datasets.import_snapshot(snapshot, chunk_size=10)
            second = datasets.import_snapshot(snapshot, chunk_size=3)
            if first != second or first['status'] != 'completed':
                raise ValueError('Import/idempotency validation failed')
            integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
            foreign_key_errors = connection.execute('PRAGMA foreign_key_check').fetchall()
            if integrity != 'ok' or foreign_key_errors:
                raise ValueError('Database integrity validation failed')
        results.append({'repository': manifest['source_provenance']['selection']['repository'],
                        'dataset_version': manifest['dataset_version'], 'validation': validation,
                        'import_status': first['status'], 'idempotent': True,
                        'integrity_check': integrity, 'foreign_key_error_count': len(foreign_key_errors)})
    report = {'verified_files': verified_files, 'database': str(database), 'snapshots': results}
    (root / 'verification.json').write_bytes((json.dumps(report, ensure_ascii=False, indent=2)+'\n').encode())
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
