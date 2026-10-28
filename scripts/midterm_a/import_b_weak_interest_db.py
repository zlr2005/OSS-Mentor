"""Append a versioned weak-interest snapshot to an existing 011 database atomically."""
import argparse
from contextlib import closing
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

TABLES = {'repositories':'midterm_dataset_repository','developers':'midterm_dataset_developer',
          'files':'midterm_dataset_file','code_documents':'midterm_dataset_code',
          'contributions':'midterm_dataset_contribution','project_behaviors':'midterm_dataset_behavior',
          'issues':'midterm_dataset_issue','issue_file_links':'midterm_dataset_issue_file','file_aliases':'midterm_dataset_file_alias'}

def canonical(value): return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def digest(data): return hashlib.sha256(data).hexdigest()

def import_cache(database, cache, parent_manifest_path=None):
    database, cache = Path(database).resolve(), Path(cache).resolve()
    if not database.is_file(): raise ValueError('Existing database required')
    raw = (cache/'manifest.json').read_bytes()
    if digest(raw) != (cache/'manifest.sha256').read_text().split()[0]: raise ValueError('Cache manifest hash mismatch')
    acquisition = json.loads(raw)
    if acquisition['fixture_only'] is not False: raise ValueError('Real acquisition required')
    assets = {}
    for item in acquisition['files']:
        path=(cache/item['path']).resolve()
        if not path.is_relative_to(cache): raise ValueError('Cache path escapes root')
        data=path.read_bytes()
        if digest(data)!=item['sha256'] or len(data)!=item['size_bytes']: raise ValueError('Cache asset mismatch')
        assets[item['path']]=data
    def rows(kind): return [json.loads(line) for line in assets['tables/'+kind+'.jsonl'].decode().splitlines()]
    behaviors, repositories, identities, coverage = (rows(k) for k in ('weak_interest_behaviors','repositories','identity_checks','coverage'))
    if not behaviors: raise ValueError('No actual weak-interest records; database unchanged')
    if Counter(r['behavior_type'] for r in behaviors)!=acquisition['behavior_counts']: raise ValueError('Behavior counts differ')
    verified={r['developer_key']:r for r in identities if r['identity_status']=='verified'}
    allowed={r['repository_key'] for r in repositories}
    for row in behaviors:
        if row['developer_key'] not in verified or row['repository_key'] not in allowed: raise ValueError('Broken weak identity/reference')
        if row['behavior_type'] not in ('star','fork','owned_repository'): raise ValueError('Not a weak-interest behavior')
        if row['event_time_unknown'] != (row['event_at'] is None): raise ValueError('Unknown time must remain null')
        for field in ('source_location','identity_source_location'):
            if row[field] not in assets: raise ValueError('Missing acquisition evidence')
    parent=acquisition['parent_dataset_version']
    version='b-real-contributions-with-weak-'+digest(raw)[:16]
    conn=sqlite3.connect(database);conn.execute('PRAGMA foreign_keys=ON')
    try:
        old=conn.execute('SELECT manifest_json FROM midterm_dataset_snapshot WHERE dataset_version=?',(parent,)).fetchone()
        status=conn.execute('SELECT status FROM midterm_dataset_import WHERE dataset_version=?',(parent,)).fetchone()
        if not old or status != ('completed',): raise ValueError('Completed parent import required')
        parent_manifest=json.loads(old[0])
        if parent_manifest_path is not None:
            parent_bytes=Path(parent_manifest_path).read_bytes()
            if digest(parent_bytes)!=acquisition['parent_manifest_sha256'] or json.loads(parent_bytes)!=parent_manifest:
                raise ValueError('Parent manifest differs from acquisition or database')
        if conn.execute('SELECT 1 FROM midterm_dataset_snapshot WHERE dataset_version=?',(version,)).fetchone():
            return {'status':'already_imported','dataset_version':version,'database':str(database)}
        for key, identity in verified.items():
            d=conn.execute('SELECT payload_json FROM midterm_dataset_developer WHERE dataset_version=? AND developer_key=?',(parent,key)).fetchone()
            if not d or json.loads(d[0])['github_numeric_id']!=identity['github_numeric_id'] or json.loads(d[0])['identity_status']!='verified':
                raise ValueError('Parent identity mismatch')
        backup=database.with_name(database.stem+'.before_weak_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+database.suffix)
        with closing(sqlite3.connect(backup)) as target: conn.backup(target)
        before={table:conn.execute(f'SELECT COUNT(*) FROM {table} WHERE dataset_version=?',(parent,)).fetchone()[0] for table in TABLES.values()}
        now=datetime.now(timezone.utc).isoformat()
        manifest={k:v for k,v in parent_manifest.items() if k not in ('files','entity_counts','quality','dataset_version','schema_version','created_at','not_delivered')}
        manifest.update(schema_version='b-profile-evidence-db-v1',dataset_version=version,created_at=now,
                        parent_dataset_version=parent,parent_manifest_sha256=acquisition['parent_manifest_sha256'],
                        acquisition_manifest_sha256=digest(raw),fixture_only=False,
                        quality={**parent_manifest.get('quality',{}),'weak_interest_behavior_records':len(behaviors)},
                        quality_gaps=list(dict.fromkeys([*parent_manifest.get('quality_gaps',[]),*acquisition['limitations']])),
                        not_delivered=[v for v in parent_manifest.get('not_delivered',[]) if v not in ('star','fork','owned_repository_interest')]+['full_population_weak_interest'],
                        asset_scope='Inherited assets preserve parent source bytes; archived parent table assets are not current table exports. Current SQLite payload_json rows are authoritative.',
                        weak_interest_coverage=coverage,
                        acquisition_plan=acquisition)
        known_times=[r['event_at'] for r in behaviors if r['event_at'] is not None]
        parse=lambda value: datetime.fromisoformat(value.replace('Z','+00:00'))
        manifest['time_coverage']={
            'start':min([parent_manifest['time_coverage']['start'],*known_times],key=parse),
            'end':max([parent_manifest['time_coverage']['end'],*[r['observed_at'] for r in behaviors]],key=parse)}
        available={r['behavior_type'] for r in behaviors}
        signal_names={'star':'star','fork':'fork','owned_repository_interest':'owned_repository'}
        manifest['not_delivered']=[v for v in parent_manifest.get('not_delivered',[]) if signal_names.get(v) not in available]+['full_population_weak_interest']
        def location(value):
            if isinstance(value,dict):
                return {k:('weak_interest/'+v if k.endswith('_location') and isinstance(v,str) and v in assets else location(v)) for k,v in value.items()}
            if isinstance(value,list): return [location(v) for v in value]
            return value
        with conn:
            conn.execute('INSERT INTO midterm_dataset_snapshot VALUES (?,?,?,?,?,?)',(version,manifest['schema_version'],'pending','{}',0,now))
            for table in TABLES.values():
                columns=[r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
                tail=','.join(columns[1:])
                conn.execute(f'INSERT INTO {table} SELECT ?,{tail} FROM {table} WHERE dataset_version=?',(version,parent))
            conn.execute('INSERT INTO midterm_dataset_asset SELECT ?,relative_path,sha256,content FROM midterm_dataset_asset WHERE dataset_version=?',(version,parent))
            for name,data in {**assets,'manifest.json':raw}.items():
                conn.execute('INSERT INTO midterm_dataset_asset VALUES (?,?,?,?)',(version,'weak_interest/'+name,digest(data),data))
            ordinal=before['midterm_dataset_repository']
            for original in repositories:
                row=location(original)
                current=conn.execute('SELECT payload_json FROM midterm_dataset_repository WHERE dataset_version=? AND repository_key=?',(version,row['repository_key'])).fetchone()
                if current:
                    previous=json.loads(current[0])
                    if previous['source_repository_id']!=row['source_repository_id']: raise ValueError('Repository identity collision')
                    # Keep historical metadata used by the inherited contribution evidence explicit.
                    row={**previous,**row,'parent_metadata':previous}
                    conn.execute('UPDATE midterm_dataset_repository SET payload_json=? WHERE dataset_version=? AND repository_key=?',(canonical(row),version,row['repository_key']))
                else:
                    conn.execute('INSERT INTO midterm_dataset_repository VALUES (?,?,?,?)',(version,row['repository_key'],ordinal,canonical(row)));ordinal+=1
            ordinal=before['midterm_dataset_behavior']
            for original in behaviors:
                row=location(original)
                key=digest(canonical([row[k] for k in ('developer_key','repository_key','behavior_type','event_at','source')]).encode())
                row['behavior_key']=key
                conn.execute('INSERT INTO midterm_dataset_behavior VALUES (?,?,?,?,?,?,?)',(version,key,row['developer_key'],row['repository_key'],row['event_at'],ordinal,canonical(row)))
                ordinal+=1
            manifest['entity_counts']={kind:conn.execute(f'SELECT COUNT(*) FROM {table} WHERE dataset_version=?',(version,)).fetchone()[0] for kind,table in TABLES.items()}
            manifest['entity_counts']['weak_interest_behaviors']=len(behaviors)
            text=canonical(manifest);manifest_hash=digest(text.encode());batch=digest(canonical([version,manifest_hash]).encode())
            conn.execute('UPDATE midterm_dataset_snapshot SET manifest_sha256=?,manifest_json=? WHERE dataset_version=?',(manifest_hash,text,version))
            total=sum(v for k,v in manifest['entity_counts'].items() if k!='weak_interest_behaviors')+conn.execute('SELECT COUNT(*) FROM midterm_dataset_asset WHERE dataset_version=?',(version,)).fetchone()[0]
            conn.execute('INSERT INTO midterm_dataset_import VALUES (?,?,?,?,?,?,?,?)',(batch,version,'completed',total,total,'{}',None,now))
            if conn.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('Foreign key violation')
            after={table:conn.execute(f'SELECT COUNT(*) FROM {table} WHERE dataset_version=?',(parent,)).fetchone()[0] for table in TABLES.values()}
            if before!=after or conn.execute('SELECT manifest_json FROM midterm_dataset_snapshot WHERE dataset_version=?',(parent,)).fetchone()!=old:
                raise ValueError('Parent changed')
        if conn.execute('PRAGMA integrity_check').fetchone()!=('ok',): raise ValueError('Integrity check failed')
        return {'status':'completed','database':str(database),'backup':str(backup),'dataset_version':version,
                'parent_dataset_version':parent,'behavior_counts':dict(Counter(r['behavior_type'] for r in behaviors)),
                'entity_counts':manifest['entity_counts'],'verified_accounts':len(verified),
                'failed_endpoints':sum(r['status']=='failed' for r in coverage),
                'incomplete_endpoints':sum(r['status']!='completed' for r in coverage),
                'truncated_endpoints':sum(r.get('truncated',False) for r in coverage),
                'foreign_key_errors':0,'integrity_check':'ok','parent_unchanged':True}
    finally: conn.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--parent-manifest',type=Path)
    args=parser.parse_args()
    result=import_cache(args.database,args.cache,args.parent_manifest)
    result_name='database_import_result.json' if result['status']=='completed' else 'database_retry_result.json'
    (args.cache/result_name).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
