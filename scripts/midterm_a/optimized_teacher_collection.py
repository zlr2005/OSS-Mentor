"""Budgeted, resumable teacher discovery. Remote access is fixed SELECT over GET only."""
import argparse
from collections import deque
from datetime import datetime, timezone, timedelta
import getpass
import hashlib
import json
import math
from pathlib import Path
import re
import socket
import sys
import http.client
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))

ENDPOINT='http://cc-2ze7189376o5m9759.public.clickhouse.ads.aliyuncs.com:8123/'
ROOT=Path('D:/OSS-Mentor/data/teacher_clickhouse')
POLICY='teacher-budgeted-v1'
LIMIT=500
ISSUE_ACTIONS=('opened','labeled','reopened','unassigned','edited','unlabeled','assigned','closed',
               'locked','unlocked','transferred','milestoned','demilestoned','pinned','unpinned','deleted','renamed','')
NEWCOMER_LABEL_GROUPS={
    'newcomer': ('good first issue','good first contribution','first timers only',
                 'first contribution','beginner','beginner friendly','easy',
                 'difficulty: easy','easy to fix','easy pick','starter'),
    'help_wanted': ('help wanted','contribution welcome'),
}


def discovery_filter(batch):
    group=batch.get('discovery_filter','all_opened')
    if group=='all_opened':return "action='opened'"
    if group not in NEWCOMER_LABEL_GROUPS:raise ValueError('Invalid discovery filter')
    labels=','.join("'"+label+"'" for label in NEWCOMER_LABEL_GROUPS[group])
    # Search all Issue event actions: labels can be added after creation. The
    # teacher event schema may omit individual actions; no coverage is assumed.
    return ("arrayExists(label -> lowerUTF8(trimBoth(replaceRegexpAll(label, '[-_ ]+', ' '))) "
            f"IN ({labels}), `issue_labels.name`)")
# These are local ceilings, never permission to raise server/query limits.
DEFAULT_BUDGET=dict(requests_per_run=150,rows_per_run=20_000_000,bytes_per_run=536870912,
                   requests_per_hour=200,requests_per_day=500,rows_per_hour=20_000_000,rows_per_day=40_000_000,
                   bytes_per_hour=536870912,bytes_per_day=1073741824,min_interval_seconds=3,
                   soft_rows=200000,soft_bytes=16777216,soft_elapsed=6)


def stamp():return datetime.now(timezone.utc).isoformat()


def invocation_budget(base,extended_run=False):
    budget=dict(base)
    if extended_run:
        budget.update(requests_per_run=1000,rows_per_run=400_000_000,
                      bytes_per_run=10_737_418_240,soft_bytes=67_108_864)
    return budget
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def save(path,value):
    temporary=path.with_name(path.name+'.partial')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)


def sql(batch):
    name=batch['repository'];repo=batch['repo_id'];owner=batch['owner_id'];month=batch['month']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',name):raise ValueError('Invalid repository')
    if any(type(v) is not int or v<=0 for v in (repo,owner,month,batch['start_epoch'],batch['end_epoch'])):raise ValueError('Invalid integer scope')
    if month not in range(202601,202611):raise ValueError('Invalid month')
    zone=timezone(timedelta(hours=8));start=datetime(2026,month%100,1,tzinfo=zone)
    end=datetime(2026,month%100+1,1,tzinfo=zone)
    cutoff=datetime(2026,10,23,tzinfo=zone)
    if not int(start.timestamp())<=batch['start_epoch']<batch['end_epoch']<=int(min(end,cutoff).timestamp()):raise ValueError('Invalid time range')
    layout=batch.get('query_layout','legacy')
    if layout not in ('legacy','scope_prewhere_v1','action_prewhere_v2'):raise ValueError('Invalid query layout')
    action_scope=''
    if layout=='action_prewhere_v2':
        action=batch.get('event_action')
        if action not in ISSUE_ACTIONS:raise ValueError('Invalid Issue action')
        action_scope=f"AND action='{action}' "
    scope=(f"toYYYYMM(created_at)={month} AND type='IssuesEvent' "
           f"{action_scope}AND org_id IN (0,{owner}) AND repo_id={repo} AND created_at>=toDateTime({batch['start_epoch']}) "
           f"AND created_at<toDateTime({batch['end_epoch']})")
    if layout in ('scope_prewhere_v1','action_prewhere_v2'):
        group=batch.get('discovery_filter')
        opened_scope=(layout=='action_prewhere_v2' and group=='all_opened' and batch.get('event_action')=='opened')
        if group not in NEWCOMER_LABEL_GROUPS and not opened_scope:raise ValueError('Label scope or explicit opened action required')
        if layout=='scope_prewhere_v1' and batch['end_epoch']-batch['start_epoch']>86400:raise ValueError('Label prewhere batches must be at most one day')
        return ("SELECT DISTINCT platform, type, action, repo_id, repo_name, issue_id, issue_number, issue_title, "
                "`issue_labels.name`, toUnixTimestamp(created_at) AS event_epoch, "
                "formatDateTime(created_at,'%Y-%m-%dT%H:%i:%SZ','UTC') AS event_time_utc FROM opensource.events "
                f"PREWHERE {scope} WHERE {discovery_filter(batch)} "
                "LIMIT 500 SETTINGS max_threads=1, max_block_size=8192, max_execution_time=15, "
                "max_rows_to_read=1000000, max_bytes_to_read=134217728 FORMAT JSON")
    return ("SELECT DISTINCT platform, type, action, repo_id, repo_name, issue_id, issue_number, issue_title, "
            "`issue_labels.name`, toUnixTimestamp(created_at) AS event_epoch, "
            "formatDateTime(created_at,'%Y-%m-%dT%H:%i:%SZ','UTC') AS event_time_utc FROM opensource.events "
            f"WHERE toYYYYMM(created_at)={month} AND type='IssuesEvent' AND {discovery_filter(batch)} "
            f"AND org_id IN (0,{owner}) AND repo_id={repo} AND created_at>=toDateTime({batch['start_epoch']}) "
            f"AND created_at<toDateTime({batch['end_epoch']}) LIMIT 500 SETTINGS max_threads=1, max_block_size=8192, "
            "max_execution_time=15, max_rows_to_read=1000000, max_bytes_to_read=134217728 FORMAT JSON")


def make_batch(seed,month,start_epoch,end_epoch,parent=None):
    b=dict(repository=seed['repository'],repo_id=int(seed['repo_id']),owner_id=int(seed['owner_id']),month=int(month),
           start_epoch=int(start_epoch),end_epoch=int(end_epoch),result_limit=LIMIT)
    if 'discovery_filter' in seed:b['discovery_filter']=seed['discovery_filter']
    if 'query_layout' in seed:b['query_layout']=seed['query_layout']
    if 'event_action' in seed:b['event_action']=seed['event_action']
    b['key']=f"{b['repository'].replace('/','__')}_{month}_{start_epoch}_{end_epoch}"
    if b.get('discovery_filter'):b['key']+='_'+b['discovery_filter']
    if b.get('query_layout')=='action_prewhere_v2':b['key']+='_action_'+(b['event_action'] or 'empty')
    if parent:b['parent_key']=parent
    b['sql']=sql(b)
    return b


def split_batch(batch):
    duration=batch['end_epoch']-batch['start_epoch']
    step=7*86400 if duration>7*86400 else 86400 if duration>86400 else 3600 if duration>3600 else 0
    if not step:return []
    return [make_batch(batch,batch['month'],start,min(start+step,batch['end_epoch']),batch['key'])
            for start in range(batch['start_epoch'],batch['end_epoch'],step)]


def validate(payload,batch):
    if not isinstance(payload,dict) or not isinstance(payload.get('data'),list):raise ValueError('Invalid JSON envelope')
    rows=payload['data']
    integer=lambda v: type(v) is int or isinstance(v,str) and re.fullmatch(r'[0-9]+',v) is not None
    if not integer(payload.get('rows')) or len(rows)>LIMIT or int(payload['rows'])!=len(rows):raise ValueError('Invalid row count')
    for row in rows:
        if not integer(row.get('repo_id')) or not integer(row.get('event_epoch')):raise ValueError('Invalid identity or time')
        if int(row['repo_id'])!=batch['repo_id'] or row['type']!='IssuesEvent':raise ValueError('Scope mismatch')
        if batch.get('query_layout')=='action_prewhere_v2' and row.get('action')!=batch['event_action']:raise ValueError('Action mismatch')
        group=batch.get('discovery_filter','all_opened')
        discovery_filter(batch)
        if group=='all_opened':
            if row['action']!='opened':raise ValueError('Action mismatch')
        else:
            labels=row.get('issue_labels.name')
            if not isinstance(labels,list) or not all(isinstance(label,str) for label in labels):raise ValueError('Invalid labels')
            normalized={re.sub(r'[-_ ]+',' ',label).strip().lower() for label in labels}
            if not normalized.intersection(NEWCOMER_LABEL_GROUPS[group]):raise ValueError('Label scope mismatch')
        if not batch['start_epoch']<=int(row['event_epoch'])<batch['end_epoch']:raise ValueError('Time mismatch')
        if row.get('platform')=='GitHub':
            if not integer(row.get('issue_number')) or not integer(row.get('issue_id')) or int(row['issue_number'])<=0 or int(row['issue_id'])<=0:raise ValueError('Invalid Issue identity')
            if str(row.get('repo_name','')).casefold()!=batch['repository'].casefold():raise ValueError('Repository name mismatch')
    return rows


def cost(payload):
    stats=payload.get('statistics',{})
    values=[float(stats[k]) for k in ('rows_read','bytes_read','elapsed')]
    if any(not math.isfinite(v) or v<0 for v in values):raise ValueError('Missing or invalid statistics')
    if any(not v.is_integer() for v in values[:2]):raise ValueError('Fractional read count')
    return dict(rows=int(values[0]),bytes=int(values[1]),elapsed=values[2])


def budget_reason(usage,ledger,budget,now,ignore_rolling_hour=False,ignore_rolling_day=False):
    if usage['requests']>=budget['requests_per_run']:return 'run_request_budget'
    # Reserve a whole query's original hard bound before making another request.
    if usage['rows']+1000000>budget['rows_per_run']:return 'run_read_row_budget'
    if usage['bytes']+134217728>budget['bytes_per_run']:return 'run_read_byte_budget'
    if not ignore_rolling_hour and sum(r['at']>now-3600 for r in ledger)>=budget['requests_per_hour']:return 'rolling_hour_request_budget'
    if not ignore_rolling_day and sum(r['at']>now-86400 for r in ledger)>=budget['requests_per_day']:return 'rolling_day_request_budget'
    for seconds,label in ((3600,'hour'),(86400,'day')):
        if ignore_rolling_hour and label=='hour':continue
        if ignore_rolling_day and label=='day':continue
        recent=[r for r in ledger if r['at']>now-seconds]
        if sum(r['rows'] for r in recent)+1000000>budget[f'rows_per_{label}']:return f'rolling_{label}_read_budget'
        if sum(r['bytes'] for r in recent)+134217728>budget[f'bytes_per_{label}']:return f'rolling_{label}_byte_budget'
    return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def http_opener():
    # The approved teacher endpoint is HTTP. Do not instantiate unrelated TLS
    # handlers (or their environment-configured key-log files).
    opener=urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler(),NoRedirect(),urllib.request.HTTPHandler(),
                    urllib.request.HTTPDefaultErrorHandler(),urllib.request.HTTPErrorProcessor(),urllib.request.UnknownHandler()):
        opener.add_handler(handler)
    return opener


class RemoteFailure(Exception):
    def __init__(self,category,diagnostics=None):
        self.category=category;self.diagnostics=diagnostics or {};super().__init__(category)


def safe_http_diagnostics(status,body):
    result=dict(http_status=int(status))
    code=re.search(r'\bCode:\s*(\d{1,6})\b',body)
    if code:result['database_error_code']=int(code.group(1))
    # Only fixed identifiers are retained, never raw server text, user names,
    # URLs, exception messages or request headers.
    identifiers=('AUTHENTICATION_FAILED','ACCESS_DENIED','REQUIRED_PASSWORD',
                 'IP_ADDRESS_NOT_ALLOWED','QUOTA_EXCEEDED','TOO_MANY_ROWS',
                 'TOO_MANY_BYTES','TIMEOUT_EXCEEDED','MEMORY_LIMIT_EXCEEDED')
    result['recognized_errors']=[name for name in identifiers if re.search(r'\b'+name+r'\b',body)]
    return result


def fetch(opener,query,user,password):
    if query!='SELECT 1 AS ok FORMAT JSON' and not query.startswith('SELECT DISTINCT '):raise ValueError('SELECT only')
    if ';' in query:raise ValueError('Single statement only')
    request=urllib.request.Request(ENDPOINT+'?database=opensource&query='+urllib.parse.quote(query,safe=''),
        headers={'X-ClickHouse-User':user,'X-ClickHouse-Key':password},method='GET')
    try:
        with opener.open(request,timeout=35) as response:
            data=response.read(8388609)
            if len(data)>8388608:raise RemoteFailure('response_too_large')
            try:return json.loads(data)
            except (ValueError,UnicodeError):
                if re.search(rb'QUOTA_EXCEEDED|TOO_MANY_ROWS|TOO_MANY_BYTES|TIMEOUT_EXCEEDED|MEMORY_LIMIT_EXCEEDED',data):raise RemoteFailure('server_protection') from None
                raise RemoteFailure('invalid_json_response') from None
    except urllib.error.HTTPError as error:
        body=error.read(65536).decode('utf-8',errors='replace')
        details=safe_http_diagnostics(error.code,body)
        if re.search(r'QUOTA_EXCEEDED|TOO_MANY_ROWS|TOO_MANY_BYTES|TIMEOUT_EXCEEDED|MEMORY_LIMIT_EXCEEDED',body):raise RemoteFailure('server_protection',details) from None
        raise RemoteFailure(f'HTTP_{error.code}',details) from None
    except RemoteFailure:raise
    except Exception as error:
        cause=error.reason if isinstance(error,urllib.error.URLError) else error
        if isinstance(cause,socket.gaierror):category='network_dns_failure'
        elif isinstance(cause,(TimeoutError,socket.timeout)):category='network_timeout'
        elif isinstance(cause,(ConnectionError,http.client.RemoteDisconnected)):category='network_connection_failure'
        elif isinstance(cause,http.client.IncompleteRead):category='network_incomplete_response'
        elif isinstance(cause,OSError):category='network_os_error'
        elif isinstance(cause,(UnicodeError,ValueError)):category='invalid_request_encoding_or_headers'
        else:category='unexpected_transport_error'
        # Do not log exception text: it may contain request headers/credentials.
        raise RemoteFailure(category) from None


def defer_protected_repositories(pending,results):
    scopes={(r['batch']['repo_id'],r['batch'].get('query_layout','legacy')) for r in results
               if r.get('error')=='server_protection'
               and 'TOO_MANY_ROWS' in r.get('diagnostics',{}).get('recognized_errors',[])}
    protected={repo for repo,layout in scopes}
    normal=[b for b in pending if (b['repo_id'],b.get('query_layout','legacy')) not in scopes]
    deferred=[b for b in pending if (b['repo_id'],b.get('query_layout','legacy')) in scopes]
    return normal+deferred,protected,len(deferred)


def prompt_credentials():
    user=input('Username: ').strip()
    for attempt in range(3):
        if not user:
            print('Username was empty. Please enter it again.',flush=True)
            user=input('Username: ').strip()
            if not user:continue
        password=getpass.getpass('Password (hidden; paste/type it, then press Enter; not saved): ')
        if password:return user,password
        print('No password was received. Password input shows no characters; please enter it again.',flush=True)
    print('No complete login received after 3 attempts. No network requests were sent; progress is unchanged.',flush=True)
    return None,None


def run(plan_path,resume=None,ignore_rolling_hour=False,ignore_rolling_day=False,extended_run=False):
    from oss_mentor.file_lock import acquire_lock,release_lock
    plan=read(plan_path)
    if plan['policy']!=POLICY or plan['budget']!=DEFAULT_BUDGET:raise ValueError('Unrecognized budget policy')
    for b in plan['batches']:
        if b['sql']!=sql(b):raise ValueError('SQL differs from fixed template')
    if extended_run and any(b.get('query_layout') not in ('scope_prewhere_v1','action_prewhere_v2') for b in plan['batches']):
        raise ValueError('Extended runs require daily PREWHERE scopes')
    ROOT.mkdir(parents=True,exist_ok=True)
    try:lock=acquire_lock(ROOT/'optimized_collection.lock')
    except OSError:raise RuntimeError('Another optimized collector is active') from None
    try:
        # Refuse concurrent legacy collectors, including credential-waiting windows.
        for other in ROOT.glob('*/status.json'):
            if resume and other.parent.resolve()==resume.resolve():continue
            state=read(other).get('status')
            if state in ('running','waiting_for_credentials','connecting'):raise RuntimeError('Another collection is active: '+other.parent.name)
        prefix=('newcomer_' if any(b.get('discovery_filter') in NEWCOMER_LABEL_GROUPS for b in plan['batches'])
                else 'inventory_' if any(b.get('discovery_filter')=='all_opened' for b in plan['batches']) else 'round21_')
        directory=resume or ROOT/(prefix+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        if resume:
            if resume.resolve().parent!=ROOT.resolve():raise ValueError('Resume outside collection root')
            old=read(directory/'optimized_plan.json')
            if old!=plan:raise ValueError('Resume plan changed')
        else:
            directory.mkdir();save(directory/'optimized_plan.json',plan)
            save(directory/'query_plan.json',plan['batches']);save(directory/'checkpoint.json',dict(pending=plan['batches'],batches=[],unique=plan.get('initial_unique',[])))
        checkpoint=read(directory/'checkpoint.json');pending=deque(checkpoint['pending']);results=checkpoint['batches']
        ordered,protected_repositories,deferred_scope_count=defer_protected_repositories(list(pending),results)
        pending=deque(ordered)
        deferred_failures=[]
        unique={f"{r['repo_id']}:{r['issue_number']}":r for r in checkpoint['unique']}
        baseline={tuple(r) for r in plan['baseline_keys']}
        usage=dict(requests=0,rows=0,bytes=0);budget=invocation_budget(plan['budget'],extended_run);reason=None
        ledger_path=ROOT/'optimized_traffic.json';ledger=read(ledger_path) if ledger_path.exists() else []
        ledger=[r for r in ledger if r['at']>time.time()-86400]
        def persist(state):
            save(directory/'checkpoint.json',dict(pending=list(pending)+deferred_failures,batches=results,unique=list(unique.values())))
            all_plan={b['key']:b for b in plan['batches']}
            for b in list(pending)+[r['batch'] for r in results]:all_plan[b['key']]=b
            save(directory/'query_plan.json',list(all_plan.values()))
            save(directory/'issues_discovered.json',sorted(unique.values(),key=lambda r:(r['repo_name'],int(r['issue_number']))))
            save(directory/'status.json',dict(status=state,output=str(directory),observed_at=stamp(),readonly=True,method='GET',
                planned_batches=len(all_plan),processed_batches=len(results),pending_batches=len(pending)+len(deferred_failures),
                successful_batches=sum(r['status']=='exported' for r in results),failed_batches=sum(r['status']=='failed' for r in results),
                saturated_batches=sum(bool(r.get('possibly_truncated')) for r in results),unique_issues=len(unique),
                net_new_issues=sum((str(r['repo_id']),int(r['issue_number'])) not in baseline for r in unique.values()),
                usage_this_invocation=usage,budget=budget,extended_run=extended_run,protected_repository_ids=sorted(protected_repositories),deferred_protected_scopes=deferred_scope_count,ignore_rolling_hour=ignore_rolling_hour,ignore_rolling_day=ignore_rolling_day,stop_reason=reason,result_limit=LIMIT,github_verified=False,frozen=False,batches=results))
        persist('waiting_for_credentials')
        print(f'READ ONLY optimized collection: {len(pending)} pending batches; output: {directory}',flush=True)
        if deferred_scope_count:print(f'Deferred {deferred_scope_count} scopes from {len(protected_repositories)} repositories with previous TOO_MANY_ROWS errors; no scopes removed.',flush=True)
        daily=any(b.get('query_layout')=='scope_prewhere_v1' for b in plan['batches'])
        indexed=any(b.get('query_layout')=='action_prewhere_v2' for b in plan['batches'])
        print('Action scopes with PREWHERE; per-query SQL guards unchanged.' if indexed else 'Daily scope with PREWHERE; per-query SQL guards unchanged.' if daily else 'Monthly first; split only full responses. Hard query guards unchanged. Conservative local budgets apply.',flush=True)
        if extended_run:print('Extended local run: at most 1000 requests, 400M scanned rows, 10 GiB; soft byte pause 64 MiB. Per-query SQL guards unchanged.',flush=True)
        if any(b.get('query_layout')=='action_prewhere_v2' for b in plan['batches']):print('Index-aligned scope: one explicit IssuesEvent action per query; only saved pending ranges are searched.',flush=True)
        user,password=prompt_credentials()
        if not user or not password:
            reason='credentials_missing';persist('stopped_unverified');password=None;user=None;return
        opener=http_opener();last_request=0;request_interval=budget['min_interval_seconds']
        def request(query,preflight=False):
            nonlocal last_request,reason
            reason=budget_reason(usage,ledger,budget,time.time(),ignore_rolling_hour,ignore_rolling_day)
            if reason:raise RemoteFailure(reason)
            time.sleep(max(0,request_interval-(time.monotonic()-last_request)))
            # Persist worst-case reservation before GET; a crash cannot erase its traffic.
            entry=dict(at=time.time(),rows=0 if preflight else 1000000,bytes=0 if preflight else 134217728)
            ledger.append(entry);save(ledger_path,ledger);usage['requests']+=1;last_request=time.monotonic()
            try:payload=fetch(opener,query,user,password)
            except RemoteFailure:
                usage['rows']+=entry['rows'];usage['bytes']+=entry['bytes'];raise
            try:c=dict(rows=0,bytes=0,elapsed=0) if preflight else cost(payload)
            except Exception:
                usage['rows']+=entry['rows'];usage['bytes']+=entry['bytes'];raise RemoteFailure('unmeasured_read_cost') from None
            entry.update(rows=c['rows'],bytes=c['bytes']);save(ledger_path,ledger)
            usage['rows']+=c['rows'];usage['bytes']+=c['bytes']
            return payload,c
        try:
            transient={'network_dns_failure','network_timeout','network_connection_failure',
                       'network_incomplete_response','network_os_error','HTTP_502','HTTP_503','HTTP_504'}
            for attempt in range(3):
                try:
                    payload,_=request('SELECT 1 AS ok FORMAT JSON',True)
                    reason=None
                    break
                except RemoteFailure as error:
                    if error.category not in transient or attempt==2:raise
                    print(f'Connection check: {error.category}; retry {attempt+1}/2 with the same in-memory login.',flush=True)
                    time.sleep(3*(attempt+1))
            if payload.get('data')!=[{'ok':1}]:raise RemoteFailure('invalid_connection_check')
            save(directory/'connection_check.json',dict(status='passed',observed_at=stamp(),readonly=True))
            consecutive=0
            while pending:
                reason=budget_reason(usage,ledger,budget,time.time(),ignore_rolling_hour,ignore_rolling_day)
                if reason:break
                batch=pending.popleft();before=len(unique)
                try:
                    payload,c=request(batch['sql']);rows=validate(payload,batch)
                    save(directory/(batch['key']+'.json'),payload)
                    for row in rows:
                        if row.get('platform')=='GitHub' and str(row.get('repo_name','')).casefold()==batch['repository'].casefold() and int(row.get('issue_number',0))>0:
                            unique.setdefault(f"{row['repo_id']}:{row['issue_number']}",row)
                    saturated=len(rows)==LIMIT;children=split_batch(batch) if saturated else []
                    pending.extendleft(reversed(children))
                    results.append(dict(key=batch['key'],batch=batch,status='exported',events=len(rows),possibly_truncated=saturated,
                        statistics=payload['statistics'],split_children=len(children)))
                    consecutive=0
                    if not saturated and len(unique)-before<5:
                        same=[b for b in pending if b['repo_id']==batch['repo_id']]
                        pending=deque([b for b in pending if b['repo_id']!=batch['repo_id']]+same)
                    print(f"{batch['key']}: {len(rows)} rows; unique={len(unique)}; requests={usage['requests']}; scanned={c['rows']} rows/{c['bytes']} bytes/{c['elapsed']:.3f}s",flush=True)
                    # Daily PREWHERE queries can still touch many narrow index
                    # granules cheaply. Use row-only pressure to pace this mode;
                    # retain byte/time stops and every hard/cumulative guard.
                    daily_batch=batch.get('query_layout') in ('scope_prewhere_v1','action_prewhere_v2')
                    row_pressure=c['rows']>=budget['soft_rows']
                    request_interval=budget['min_interval_seconds']*(2 if daily_batch and row_pressure else 1)
                    if c['bytes']>=budget['soft_bytes'] or c['elapsed']>=budget['soft_elapsed'] or (row_pressure and not daily_batch):
                        print(f"Resource pause: scanned {c['rows']} rows, {c['bytes']} bytes in {c['elapsed']:.3f}s. Query succeeded; pending work is saved.",flush=True)
                        reason='cost_near_guard';persist('paused_budget_unverified');break
                    if daily_batch and row_pressure:
                        print(f"Row pressure: {c['rows']} rows, {c['bytes']} bytes, {c['elapsed']:.3f}s; next request interval {request_interval}s. Cumulative guards remain active.",flush=True)
                except RemoteFailure as error:
                    consecutive+=1;reason=error.category
                    if reason.endswith('_budget') or reason.startswith('rolling_'):
                        pending.appendleft(batch);persist('paused_budget_unverified');break
                    results.append(dict(key=batch['key'],batch=batch,status='failed',error=reason,diagnostics=error.diagnostics))
                    print(f"Query failed: {batch['key']}; {reason}; safe diagnostics: {json.dumps(error.diagnostics)}",flush=True)
                    # Retain failure as a pending item for explicit resume, not automatic retry.
                    deferred_failures.append(batch)
                    if reason in ('HTTP_401','HTTP_403','HTTP_429','server_protection','unmeasured_read_cost') or consecutive>=2:
                        persist('stopped_unverified');break
                except Exception:
                    reason='invalid_response_scope_or_local_error'
                    results.append(dict(key=batch['key'],batch=batch,status='failed',error=reason))
                    pending.appendleft(batch);persist('stopped_unverified');break
                persist('running')
            if not pending and deferred_failures and reason is None:reason='deferred_failed_batches'
            persist('finished_unverified' if not pending and not deferred_failures else 'paused_budget_unverified' if reason and 'budget' in reason or reason=='cost_near_guard' else 'stopped_unverified')
        except RemoteFailure as error:
            reason=error.category;limited='budget' in reason
            persist('paused_budget_unverified' if limited else 'stopped_unverified')
            save(directory/'connection_check.json',dict(status='deferred_budget' if limited else 'failed',error=reason,diagnostics=error.diagnostics,observed_at=stamp()))
            print('Connection check failed: '+reason+'; safe diagnostics: '+json.dumps(error.diagnostics),flush=True)
        finally:password=None;user=None
        print(f'State saved. {len(unique)} discovered; {len(pending)+len(deferred_failures)} pending; reason={reason}.',flush=True)
        print('Discovered identities require current-state screening before recommendation.',flush=True)
    finally:
        release_lock(lock)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True);parser.add_argument('--resume',type=Path)
    parser.add_argument('--ignore-rolling-hour-budget',action='store_true',help='Explicit local hourly budget override for this invocation; daily and query guards remain active.')
    parser.add_argument('--ignore-rolling-day-budget',action='store_true',help='Explicit local daily budget override for this invocation; run and query guards remain active.')
    parser.add_argument('--extended-run',action='store_true',help='Larger local invocation budget for daily scopes; per-query SQL guards unchanged.')
    args=parser.parse_args()
    try:run(args.plan,args.resume,args.ignore_rolling_hour_budget,args.ignore_rolling_day_budget,args.extended_run)
    except Exception as error:print('Collector could not start: '+type(error).__name__,flush=True)
    input('Finished. Press Enter to close. ')
