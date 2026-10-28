"""Create a consistent, checksummed ZIP of the approved B dataset database for Releases."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile

VERSION='b-real-contributions-with-weak-8cc664dd7446b2d8'
ALLOWED_TABLES={f'midterm_dataset_{name}' for name in ('snapshot','import','asset','repository','developer','file','code','contribution','behavior','issue','issue_file','file_alias')}

def digest_file(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()

def package(database,cache,output):
    database,cache,output=map(lambda p:Path(p).resolve(),(database,cache,output))
    output.mkdir(parents=True,exist_ok=True)
    archive=output/'oss-mentor-b-weak-interest-20261028.zip'
    if archive.exists(): raise FileExistsError(f'Release artifact already exists: {archive}')
    with tempfile.TemporaryDirectory(prefix='snapshot-',dir=output) as temporary:
        snapshot=Path(temporary)/'b_weak_interest.sqlite3'
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as source:
            tables={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables!=ALLOWED_TABLES: raise ValueError('Database contains unexpected tables; refusing to export')
            with closing(sqlite3.connect(snapshot)) as target:source.backup(target)
        with closing(sqlite3.connect(snapshot)) as connection:
            if connection.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise ValueError('Invalid snapshot')
            if connection.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Foreign key errors')
            row=connection.execute('SELECT manifest_json FROM midterm_dataset_snapshot WHERE dataset_version=?',(VERSION,)).fetchone()
            if not row:raise ValueError('Expected delivery version missing')
            manifest=json.loads(row[0])
            if connection.execute('SELECT status FROM midterm_dataset_import WHERE dataset_version=?',(VERSION,)).fetchone()!=('completed',):
                raise ValueError('Expected dataset import incomplete')
        metadata={'created_at':datetime.now(timezone.utc).isoformat(),'dataset_version':VERSION,
                  'database_file':snapshot.name,'database_sha256':digest_file(snapshot),'database_size_bytes':snapshot.stat().st_size,
                  'entity_counts':manifest['entity_counts'],'behavior_counts':manifest['acquisition_plan']['behavior_counts'],
                  'schema_version':manifest['schema_version'],
                  'scope':'Dataset-only database; includes original, pilot and expanded snapshots. Select dataset_version explicitly.'}
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as bundle:
            bundle.write(snapshot,snapshot.name)
            bundle.writestr('delivery_metadata.json',json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
            for name in ('account_coverage.csv','verification_result.json','profile_adapter_result.json'):
                bundle.write(cache/name,name)
            bundle.write(Path(__file__).resolve().parents[2]/'docs/b_weak_interest_all_accounts_2026-10-28.md','HANDOFF.md')
        # Stream the archived database back through SHA-256, verifying compression without allocating 1.2 GB.
        with zipfile.ZipFile(archive) as bundle:
            with bundle.open(snapshot.name) as member:
                if hashlib.file_digest(member,'sha256').hexdigest()!=metadata['database_sha256']:
                    raise ValueError('Archive roundtrip checksum mismatch')
    checksum=digest_file(archive)
    archive.with_suffix('.zip.sha256').write_text(checksum+'  '+archive.name+'\n',encoding='utf-8')
    (output/'delivery_metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return {'archive':str(archive),'size_bytes':archive.stat().st_size,'sha256':checksum,'dataset_version':VERSION,'archive_roundtrip':'verified'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=Path('data/b_delivery_v02_acceptance.sqlite3'))
    parser.add_argument('--cache',type=Path,default=Path('data/b_weak_interest_all_20261028'))
    parser.add_argument('--output',type=Path,default=Path('data/b_weak_github_release_20261028'))
    args=parser.parse_args();print(json.dumps(package(args.database,args.cache,args.output),ensure_ascii=False,indent=2))
