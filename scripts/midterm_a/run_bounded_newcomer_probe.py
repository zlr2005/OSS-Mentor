"""At most two small, serialized teacher SELECTs; no full-run resume or automatic retries."""
import argparse
import hashlib
from datetime import datetime,timezone
from pathlib import Path
import time
from optimized_teacher_collection import (ROOT,read,save,sql,fetch,http_opener,
    prompt_credentials,validate,cost,RemoteFailure)

MAX_TASK_QUERIES=2
MAX_ROWS=100_000
MAX_BYTES=8_388_608
INTERVAL=15


def probe_sql(batch):
    if batch.get('query_layout')!='action_prewhere_v2':raise ValueError('Explicit action scope required')
    query=sql(batch)
    replacements={'LIMIT 500':'LIMIT 20','max_execution_time=15':'max_execution_time=3',
                  'max_rows_to_read=1000000':'max_rows_to_read=100000',
                  'max_bytes_to_read=134217728':'max_bytes_to_read=8388608'}
    for old,new in replacements.items():
        if query.count(old)!=1:raise ValueError('Unexpected SQL template')
        query=query.replace(old,new)
    return query


def select_tasks(previous,tasks_path=None):
    checkpoint=previous/'checkpoint.json'
    if tasks_path is None:
        return read(checkpoint)['pending'][:MAX_TASK_QUERIES]
    plan=read(tasks_path)
    if Path(plan['source_run']).resolve()!=previous.resolve():raise ValueError('Probe source mismatch')
    if plan['source_checkpoint_sha256']!=hashlib.sha256(checkpoint.read_bytes()).hexdigest():
        raise ValueError('Probe source checkpoint changed')
    tasks=plan['batches']
    if not isinstance(tasks,list) or not 1<=len(tasks)<=MAX_TASK_QUERIES:raise ValueError('Probe task cap exceeded')
    if len({b['key'] for b in tasks})!=len(tasks):raise ValueError('Duplicate probe scope')
    for batch in tasks:
        if batch.get('sql')!=sql(batch):raise ValueError('Probe SQL differs from fixed template')
        probe_sql(batch)
    return tasks


def run(previous,workspace,tasks_path=None):
    import msvcrt
    if previous.resolve().parent!=ROOT.resolve():raise ValueError('Unknown source root')
    if read(previous/'status.json')['status'] not in ('stopped_unverified','paused_budget_unverified','finished_unverified'):
        raise ValueError('Source run is active')
    for statefile in ROOT.glob('*/status.json'):
        if read(statefile).get('status') in ('running','connecting','waiting_for_credentials'):
            raise ValueError('Another collector is active')
    tasks=select_tasks(previous,tasks_path)
    queries=[probe_sql(batch) for batch in tasks]
    original_checkpoint_sha256=hashlib.sha256((previous/'checkpoint.json').read_bytes()).hexdigest()
    lock=(ROOT/'optimized_collection.lock').open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    prefix='inventory_bounded_probe_' if tasks_path else 'newcomer_bounded_probe_'
    output=workspace/'data'/(prefix+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    output.mkdir(parents=True,exist_ok=False)
    state=dict(status='waiting_for_credentials',source_run=str(previous),task_query_cap=MAX_TASK_QUERIES,
        query_guards=dict(rows=MAX_ROWS,bytes=MAX_BYTES,seconds=3,result_rows=20),
        request_interval_seconds=INTERVAL,readonly=True,remote_writes=0,main_checkpoint_modified=False,
        source_checkpoint_sha256=original_checkpoint_sha256,
        task_plan=str(tasks_path) if tasks_path else None,
        task_results=[],requests=0,scope='bounded diagnostic sample, not a full collection round')
    save(output/'query_plan.json',[dict(batch=b,query=q) for b,q in zip(tasks,queries)])
    def persist():save(output/'status.json',state)
    persist()
    print(f'BOUNDED READ ONLY probe: at most {len(tasks)} task queries plus one SELECT 1. Output: {output}',flush=True)
    print('Per query: 100000 scanned rows, 8 MiB, 3 seconds, 20 returned rows. Minimum 15 seconds between requests. No retries.',flush=True)
    user=password=None
    try:
        user,password=prompt_credentials()
        if not user or not password:state.update(status='stopped',reason='credentials_missing');return
        opener=http_opener();ledger_path=ROOT/'optimized_traffic.json'
        ledger=read(ledger_path) if ledger_path.exists() else []
        last_request=None
        def request(query,preflight=False):
            nonlocal last_request
            if state['requests']>=MAX_TASK_QUERIES+1:raise ValueError('Probe request cap reached')
            if last_request is not None:time.sleep(max(0,INTERVAL-(time.monotonic()-last_request)))
            entry=dict(at=time.time(),rows=0 if preflight else MAX_ROWS,bytes=0 if preflight else MAX_BYTES,
                       mode='bounded_probe',output=str(output))
            ledger.append(entry);save(ledger_path,ledger)
            state['requests']+=1;last_request=time.monotonic();persist()
            payload=fetch(opener,query,user,password)
            measured=dict(rows=0,bytes=0,elapsed=0) if preflight else cost(payload)
            entry.update(rows=measured['rows'],bytes=measured['bytes']);save(ledger_path,ledger)
            return payload,measured
        state.update(status='connecting',current_query='connection_check');persist()
        connection,_=request('SELECT 1 AS ok FORMAT JSON',True)
        if connection.get('data')!=[dict(ok=1)]:raise RemoteFailure('invalid_connection_check')
        state['status']='running';persist()
        for batch,query in zip(tasks,queries):
            state['current_query']=batch['key'];persist()
            payload,measured=request(query)
            rows=validate(payload,batch)
            if len(rows)>20:raise ValueError('Probe response exceeded cap')
            save(output/(batch['key']+'.json'),dict(batch=batch,query=query,payload=payload,observed_at=datetime.now(timezone.utc).isoformat()))
            state['task_results'].append(dict(key=batch['key'],events=len(rows),statistics=measured,possibly_truncated=len(rows)==20))
            persist();print(f"{batch['key']}: {len(rows)} rows; cost={measured}",flush=True)
            if measured['rows']>=MAX_ROWS or measured['bytes']>=MAX_BYTES or measured['elapsed']>=3:
                state.update(status='stopped',reason='probe_cost_guard');return
        state['status']='completed'
    except RemoteFailure as error:
        state.update(status='stopped',reason=error.category,diagnostics=error.diagnostics)
        if state.get('current_query') not in (None,'connection_check'):
            state['task_results'].append(dict(key=state['current_query'],status='failed',error=error.category,diagnostics=error.diagnostics))
        print('Stopped: '+error.category+' '+str(error.diagnostics),flush=True)
    except Exception as error:
        state.update(status='stopped',reason=type(error).__name__)
    finally:
        user=password=None
        state['source_checkpoint_sha256_after']=hashlib.sha256((previous/'checkpoint.json').read_bytes()).hexdigest()
        state['main_checkpoint_modified']=state['source_checkpoint_sha256_after']!=original_checkpoint_sha256
        persist()
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()
        print(f"Probe ended: {state['status']}; source collection checkpoint unchanged.",flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous',type=Path,required=True);parser.add_argument('--workspace',type=Path,default=Path.cwd())
    parser.add_argument('--tasks-path',type=Path,help='Validated local plan selecting at most two diagnostic scopes.')
    run(**vars(parser.parse_args()))
    input('Finished. Press Enter to close. ')
