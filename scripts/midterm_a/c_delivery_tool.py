"""Standalone reader/verifier/SQLite builder for C's candidate snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def read_table(root,name):
    root=Path(root);manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for relative in manifest['table_files'][name]:
        for line in (root/relative).read_text(encoding='utf-8').splitlines():yield json.loads(line)


def verify(root):
    root=Path(root).resolve();data=(root/'manifest.json').read_bytes()
    if hashlib.sha256(data).hexdigest()!=(root/'manifest.sha256').read_text().split()[0]:raise ValueError('Manifest checksum mismatch')
    m=json.loads(data);seen=set()
    for item in m['files']:
        p=(root/item['path']).resolve()
        if not p.is_relative_to(root) or item['path'] in seen:raise ValueError('Unsafe/duplicate asset path')
        seen.add(item['path']);b=p.read_bytes()
        if hashlib.sha256(b).hexdigest()!=item['sha256'] or len(b)!=item['size_bytes']:raise ValueError('Asset checksum mismatch')
    tables={name:list(read_table(root,name)) for name in m['table_files']}
    for name,rows in tables.items():
        if len(rows)!=m['table_counts'][name] or not set(m['table_files'][name])<=seen:raise ValueError('Table count/checksum membership mismatch')
    repositories={r['repository_id'] for r in tables['repository']}
    candidates={r['task_candidate_id'] for r in tables['task_candidate']}
    if len(repositories)!=len(tables['repository']) or len(candidates)!=len(tables['task_candidate']):raise ValueError('Duplicate entity ID')
    if any(r['repository_id'] not in repositories for r in tables['task_candidate']):raise ValueError('Broken repository reference')
    if any(r['task_candidate_id'] not in candidates for r in tables['task_skill_requirement']):raise ValueError('Broken requirement reference')
    return {'status':'delivery_verified','dataset_version':m['dataset_version'],'table_counts':m['table_counts'],
            'freshness':m['freshness'],'scope':'Frozen candidate evidence; refresh expired state before online recommendation.'}


def build_database(root,database):
    root=Path(root);verify(root);database=Path(database)
    if database.exists():raise ValueError('Choose a new database file')
    database.parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(database)
    try:
        c.execute('PRAGMA foreign_keys=ON');c.executescript((root/'schema.sql').read_text(encoding='utf-8'))
        with c:
            for table in ('repository','task_candidate','task_skill_requirement'):
                for row in read_table(root,table):
                    columns=list(row)
                    c.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",list(row.values()))
        if c.execute('PRAGMA foreign_key_check').fetchall() or c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database integrity failed')
    finally:c.close()
    return {'status':'database_built','database':str(database)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--database',type=Path)
    args=parser.parse_args();root=Path(__file__).parent
    print(json.dumps(build_database(root,args.database) if args.database else verify(root),ensure_ascii=False,indent=2))
