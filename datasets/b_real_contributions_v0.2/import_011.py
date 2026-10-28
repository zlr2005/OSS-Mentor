"""Import a verified normalized B delivery into 011 tables, with resumable chunks."""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

sys.dont_write_bytecode=True


def sibling(name):
    spec=importlib.util.spec_from_file_location('b_delivery_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


TABLES={
    'repositories':('midterm_dataset_repository',('repository_key',)),
    'developers':('midterm_dataset_developer',('developer_key',)),
    'files':('midterm_dataset_file',('file_key','repository_key')),
    'code_documents':('midterm_dataset_code',('file_version_key','file_key','commit_sha')),
    'contributions':('midterm_dataset_contribution',('record_key','developer_key','file_version_key','event_at')),
    'project_behaviors':('midterm_dataset_behavior',('record_key','developer_key','repository_key','event_at')),
    'issues':('midterm_dataset_issue',('issue_key','repository_key')),
    'issue_file_links':('midterm_dataset_issue_file',('record_key','issue_key','file_key','available_at')),
    'file_aliases':('midterm_dataset_file_alias',('record_key','file_key')),
}


def now():return datetime.now(timezone.utc).isoformat()


def import_delivery(root,database,chunk_size=500):
    if type(chunk_size) is not int or chunk_size<1:raise ValueError('chunk_size must be positive')
    root=Path(root).resolve();database=Path(database).resolve()
    if database.name in {'oss_mentor.sqlite3','oss_mentor_web.sqlite3'}:
        raise ValueError('Use a dedicated B dataset database')
    sibling('verify').verify(root)  # Validate before touching the database.
    reader=sibling('reader');manifest,records=reader.load_records(root)
    version=manifest['dataset_version'];manifest_json=reader.canonical(manifest)
    manifest_hash=hashlib.sha256(manifest_json.encode()).hexdigest()
    batch_id=hashlib.sha256(reader.canonical([version,manifest_hash]).encode()).hexdigest()
    items=[('assets',item['path'],None) for item in sorted(manifest['files'],key=lambda item:item['path'])]
    asset_index={item['path']:item for item in manifest['files']}
    for kind in TABLES:
        items.extend((kind,ordinal,row) for ordinal,row in enumerate(records[kind]))
    database.parent.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(database)
    connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        connection.executescript((root/'011_midterm_datasets.sql').read_text(encoding='utf-8'))
        with connection:
            previous=connection.execute('SELECT manifest_sha256 FROM midterm_dataset_snapshot WHERE dataset_version=?',(version,)).fetchone()
            if previous and previous[0]!=manifest_hash:raise ValueError('Dataset version already registered with different content')
            if not previous:
                connection.execute('INSERT INTO midterm_dataset_snapshot VALUES (?,?,?,?,?,?)',
                                   (version,manifest['schema_version'],manifest_hash,manifest_json,0,now()))
                connection.execute('INSERT INTO midterm_dataset_import (batch_id,dataset_version,status,total_items,duplicates_json,updated_at) VALUES (?,?,\'running\',?,?,?)',
                                   (batch_id,version,len(items),reader.canonical({k:0 for k in TABLES}),now()))
            batch=connection.execute('SELECT * FROM midterm_dataset_import WHERE dataset_version=?',(version,)).fetchone()
            if batch['total_items']!=len(items) or batch['batch_id']!=batch_id:
                raise ValueError('Import plan/version conflict')
        try:
            while batch['status']!='completed':
                start=batch['checkpoint'];end=min(start+chunk_size,len(items))
                with connection:
                    for kind,ordinal,row in items[start:end]:
                        if kind=='assets':
                            data=(root/ordinal).read_bytes()
                            if len(data)!=asset_index[ordinal]['size_bytes'] or hashlib.sha256(data).hexdigest()!=asset_index[ordinal]['sha256']:
                                raise ValueError('Asset changed during import')
                            connection.execute('INSERT INTO midterm_dataset_asset VALUES (?,?,?,?)',
                                               (version,ordinal,hashlib.sha256(data).hexdigest(),data))
                        else:
                            table,fields=TABLES[kind]
                            keys=[reader.record_key(kind,row) if name=='record_key' else row[name] for name in fields]
                            columns=('dataset_version',)+fields+('ordinal','payload_json')
                            connection.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                                               [version,*keys,ordinal,reader.canonical(row)])
                    connection.execute('UPDATE midterm_dataset_import SET checkpoint=?,status=?,error_type=NULL,updated_at=? WHERE dataset_version=?',
                                       (end,'completed' if end==len(items) else 'running',now(),version))
                batch=connection.execute('SELECT * FROM midterm_dataset_import WHERE dataset_version=?',(version,)).fetchone()
        except Exception as error:
            with connection:
                connection.execute('UPDATE midterm_dataset_import SET status=\'failed\',error_type=?,updated_at=? WHERE dataset_version=?',
                                   (type(error).__name__,now(),version))
            raise
        counts={kind:connection.execute(f'SELECT count(*) FROM {table} WHERE dataset_version=?',(version,)).fetchone()[0]
                for kind,(table,_) in TABLES.items()}
        if counts!={kind:len(rows) for kind,rows in records.items()}:raise ValueError('Imported entity count mismatch')
        if connection.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Foreign key violation')
        return {'dataset_version':version,'batch_id':batch_id,'status':batch['status'],'checkpoint':batch['checkpoint'],
                'total_items':batch['total_items'],'entity_counts':counts,'foreign_key_errors':0}
    finally:connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--chunk-size',type=int,default=500)
    args=parser.parse_args()
    print(json.dumps(import_delivery(Path(__file__).parent,args.database,args.chunk_size),ensure_ascii=False,indent=2))
