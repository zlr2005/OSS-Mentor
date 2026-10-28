"""Cover every verified human in A's parent delivery before deep pagination."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
from urllib.parse import urlsplit, parse_qs
from oss_mentor.collector.github_client import GitHubClient, parse_link_header

def encode(v): return (json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
def sha(v): return hashlib.sha256(v).hexdigest()
def now(): return datetime.now(timezone.utc).isoformat()
class RequestBudgetExhausted(RuntimeError): pass

class CompressedGitHubClient(GitHubClient):
    """Negotiate gzip for large repository lists without changing response semantics."""
    def _request_headers(self, extra_headers):
        return {**super()._request_headers(extra_headers),'Accept-Encoding':'gzip'}

    @staticmethod
    def _decode_payload(body):
        return GitHubClient._decode_payload(gzip.decompress(body) if body.startswith(b'\x1f\x8b') else body)

def select_accounts(parent):
    selected=[];excluded=[]
    for d in map(json.loads,(parent/'tables/developers.jsonl').read_text(encoding='utf-8').splitlines()):
        if d['identity_status']!='verified':
            excluded.append({'developer_key':d['developer_key'],'status':'excluded','exclusion_reason':'identity_unconfirmed'});continue
        account=None
        for location in d.get('evidence_locations',[]):
            fields=json.loads((parent/location).read_bytes()).get('fields',{})
            for role in ('author','committer'):
                a=fields.get(role) or {}
                if a.get('id')==d['github_numeric_id'] and a.get('login'): account=a;break
            if account: break
        if account and account.get('type')=='User':
            selected.append({'developer_key':d['developer_key'],'github_numeric_id':d['github_numeric_id'],'login':account['login']})
        else: excluded.append({'developer_key':d['developer_key'],'status':'excluded','exclusion_reason':'non_human_account' if account else 'account_login_not_verified','account_type':account.get('type') if account else None})
    return selected,excluded

def compact(payload):
    if isinstance(payload,list):
        return [({'starred_at':v.get('starred_at'),'repo':compact(v['repo'])} if 'repo' in v else compact(v)) for v in payload]
    keys=('id','login','type','full_name','private','archived','language','fork','html_url','owner')
    return {k:(compact(v) if k=='owner' else v) for k,v in payload.items() if k in keys}

def collect(parent,output,previous,budget,workers,plan_only=False):
    output.mkdir(parents=True,exist_ok=True);evidence=output/'evidence';evidence.mkdir(exist_ok=True)
    accounts,excluded=select_accounts(parent)
    parent_manifest=json.loads((parent/'manifest.json').read_bytes())
    plan={'parent_dataset_version':parent_manifest['dataset_version'],'parent_manifest_sha256':sha((parent/'manifest.json').read_bytes()),
          'selection':'all verified human accounts, including accounts without selected file contributions',
          'accounts':accounts,'developer_keys':[a['developer_key'] for a in accounts],
          'excluded_accounts':excluded,'per_page':100,'pagination':'breadth first across accounts, then round robin next pages',
          'request_budget':budget,'workers':workers,'destination':'https://api.github.com/users/{login}[/starred|/repos]'}
    plan_path=output/'collection_plan.json'
    if plan_path.exists() and json.loads(plan_path.read_bytes())!=plan: raise ValueError('Plan changed; use another cache directory')
    plan_path.write_bytes(encode(plan))
    if plan_only: print(json.dumps({'eligible_accounts':len(accounts),'excluded_accounts':len(excluded),'plan':str(plan_path)}));return
    credentials=subprocess.run(['gh','auth','token'],capture_output=True,text=True)
    token=credentials.stdout.strip() if credentials.returncode==0 else None
    ledger_path=output/'request_meter.json'
    spent=json.loads(ledger_path.read_bytes())['requests'] if ledger_path.exists() else 0
    lock=threading.Lock();meter={'requests':spent,'cache_hits':0,'rate_remaining':None,'stop':False};local=threading.local()
    def fetch(path,params=None,headers=None):
        name=sha(encode([path,params,headers]))+'.json';file=evidence/name
        if file.exists():
            with lock: meter['cache_hits']+=1
            return json.loads(file.read_bytes()),'evidence/'+name
        old=previous/'evidence'/name
        if old.exists():
            envelope=json.loads(old.read_bytes());envelope['source_payload_sha256']=sha(encode(envelope['payload']))
            envelope['payload']=compact(envelope['payload']);envelope['projection_not_raw']=True
            file.write_bytes(encode(envelope))
            with lock: meter['cache_hits']+=1
            return envelope,'evidence/'+name
        with lock:
            if meter['requests']>=budget or meter['stop']: raise RequestBudgetExhausted()
            meter['requests']+=1
            ledger_path.write_bytes(encode({'requests':meter['requests']}))
        if not hasattr(local,'client'):
            local.client=CompressedGitHubClient(api_base='https://api.github.com',api_version='2026-03-10',user_agent='OSS-Mentor-weak-interest-all/1',token=token,timeout_seconds=20,max_retries=0)
        response=local.client.get(path,params=params,extra_headers=headers)
        with lock:
            remaining=response.headers.get('x-ratelimit-remaining')
            if remaining is not None:
                meter['rate_remaining']=int(remaining)
                if int(remaining)<100: meter['stop']=True
        envelope={'source_url':response.url,'status_code':response.status_code,'observed_at':response.fetched_at.isoformat(),
                  'link':response.headers.get('link'),'projection_not_raw':True,
                  'source_payload_sha256':sha(encode(response.payload)),
                  'payload':compact(response.payload),'scope':'public API field projection, not raw envelope; no request headers or credentials'}
        file.write_bytes(encode(envelope));return envelope,'evidence/'+name
    repositories={};behaviors={};identities={};coverage={};failures=[]
    parent_repos={r['source_repository_id']:r for r in map(json.loads,(parent/'tables/repositories.jsonl').read_text(encoding='utf-8').splitlines())}
    def request_identity(account):
        try:
            envelope,location=fetch('/users/'+account['login']);user=envelope['payload']
            if user.get('id')!=account['github_numeric_id'] or user.get('type')!='User': raise ValueError('identity_mismatch')
            return account,{'developer_key':account['developer_key'],'github_numeric_id':user['id'],'login':user['login'],'identity_status':'verified','source_location':location},None
        except Exception as e: return account,None,{'error_type':type(e).__name__,'http_status':getattr(e,'status_code',None)}
    def request_page(job):
        account,kind,page=job
        params={'per_page':100,'page':page,'sort':'created','direction':'desc'}
        if kind=='repos': params['type']='owner'
        headers={'Accept':'application/vnd.github.star+json'} if kind=='starred' else None
        try:
            response,location=fetch('/users/'+account['login']+'/'+kind,params,headers)
            if not isinstance(response['payload'],list): raise ValueError('Expected list')
            return job,response,location,None
        except Exception as e: return job,None,None,{'error_type':type(e).__name__,'http_status':getattr(e,'status_code',None)}
    def progress(phase):
        record={'phase':phase,'observed_at':now(),'accounts_total':len(accounts),'identities_verified':len(identities),
                'endpoints_seen':len(coverage),'endpoints_complete':sum(v['status']=='completed' for v in coverage.values()),
                'weak_records':len(behaviors),**meter}
        (output/'progress.json').write_bytes(encode(record));print(json.dumps(record),flush=True)
    def handle_page(result):
        (account,kind,page),response,location,error=result;key=(account['developer_key'],kind)
        state=coverage.setdefault(key,{'developer_key':account['developer_key'],'endpoint':kind,'pages':0,'returned_count':0,'status':'pending','truncated':True})
        if error:
            state.update(status='pending' if error['error_type']=='RequestBudgetExhausted' else 'failed',next_page=page,**error)
            return None
        # Validate every item before adding the page, so malformed pages cannot be marked complete.
        page_rows=[]
        for item in response['payload']:
            repo=item['repo'] if kind=='starred' else item
            if repo.get('private') is not False: continue
            if kind=='repos' and repo['owner']['id']!=account['github_numeric_id']: raise ValueError('Owner mismatch')
            rkey=parent_repos.get(str(repo['id']),{}).get('repository_key') or 'repository-'+sha(encode(['github',str(repo['id'])]).rstrip(b'\n'))
            behavior='star' if kind=='starred' else 'fork' if repo['fork'] else 'owned_repository'
            event=item.get('starred_at') if kind=='starred' else None
            page_rows.append((rkey,repo,behavior,event))
        for rkey,repo,behavior,event in page_rows:
            repositories[rkey]={'repository_key':rkey,'source_repository_id':str(repo['id']),'full_name':repo['full_name'],
                'primary_language':repo.get('language'),'is_archived':repo['archived'],'domains':[],
                'observed_at':response['observed_at'],'source_location':location,'maintenance_status':'unknown','contribution_url':None}
            identity=identities[account['developer_key']]
            row={'developer_key':account['developer_key'],'repository_key':rkey,'behavior_type':behavior,
                 'event_at':event,'event_time_unknown':event is None,'observed_at':response['observed_at'],'source':'github_rest',
                 'source_location':location,'identity_source_location':identity['source_location'],'evidence_location':repo['html_url'],
                 'interpretation':'weak repository-language interest, not ability; current fork/ownership event time unknown'}
            row['behavior_key']=sha(encode([account['developer_key'],rkey,behavior,event]))
            behaviors[row['behavior_key']]=row
        nxt=parse_link_header(response.get('link')).get('next')
        state.update(pages=page,returned_count=state['returned_count']+len(response['payload']),status='partial' if nxt else 'completed',truncated=bool(nxt),
                     source_location=location,observed_at=response['observed_at'],next_page=page+1 if nxt else None)
        for field in ('error_type','http_status'):state.pop(field,None)
        if nxt:
            parsed=urlsplit(nxt)
            allowed_paths={'/users/'+account['login']+'/'+kind,'/user/'+str(account['github_numeric_id'])+'/'+kind}
            if parsed.scheme!='https' or parsed.netloc!='api.github.com' or parsed.path not in allowed_paths:
                raise ValueError('Unexpected pagination origin/path')
            next_page=int(parse_qs(parsed.query)['page'][0])
            if next_page!=page+1: raise ValueError('Nonsequential pagination')
            return account,kind,next_page
        return None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # First pass covers all identities. Errors get one later attempt, after everyone had a chance.
        for offset in range(0,len(accounts),100):
            batch=list(pool.map(request_identity,accounts[offset:offset+100]))
            for account,identity,error in batch:
                if identity: identities[account['developer_key']]=identity
                else: failures.append((account,error))
            progress('identity_pass')
        retry=[]
        for account,identity,error in pool.map(request_identity,[a for a,e in failures]):
            if identity: identities[account['developer_key']]=identity
            else: retry.append({'developer_key':account['developer_key'],'status':'failed','endpoint':'identity',**error})
        account_map={a['developer_key']:{**a,'login':identities[a['developer_key']]['login']} for a in accounts if a['developer_key'] in identities}
        jobs=[(a,kind,1) for a in account_map.values() for kind in ('starred','repos')]
        next_jobs=[]
        for offset in range(0,len(jobs),100):
            for result in pool.map(request_page,jobs[offset:offset+100]):
                nxt=handle_page(result)
                if nxt:next_jobs.append(nxt)
            progress('first_pages')
        # Retry failures once before pagination, preserving the same page and deduplicating rows.
        jobs=[(account_map[key],kind,s['next_page']) for (key,kind),s in coverage.items() if s['status']=='failed']
        for result in pool.map(request_page,jobs):
            nxt=handle_page(result)
            if nxt: next_jobs.append(nxt)
        while next_jobs and meter['requests']<budget and not meter['stop']:
            jobs,next_jobs=next_jobs,[]
            for offset in range(0,len(jobs),100):
                for result in pool.map(request_page,jobs[offset:offset+100]):
                    nxt=handle_page(result)
                    if nxt:next_jobs.append(nxt)
                progress('pagination')
        for a,kind,page in next_jobs:
            coverage[(a['developer_key'],kind)].update(status='partial',truncated=True,next_page=page)
    tables={'repositories':list(repositories.values()),'weak_interest_behaviors':list(behaviors.values()),
            'identity_checks':list(identities.values()),'coverage':[*coverage.values(),*retry], 'excluded_accounts':excluded}
    for kind,rows in tables.items():
        path=output/'tables'/f'{kind}.jsonl';path.parent.mkdir(exist_ok=True)
        path.write_bytes(b''.join((json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n').encode() for r in rows))
    manifest={'schema_version':'b-weak-interest-acquisition-v2','dataset_version':'b-real-weak-interest-all-20261028','fixture_only':False,
              'created_at':now(),**plan,'request_statistics':meter,'entity_counts':{k:len(v) for k,v in tables.items()},
              'identity_interpretation':'Eligible means GitHub type=User with numeric-ID association verified; type=User is not human, real-name, non-automation, or ability certification.',
              'request_accounting':'Lifetime conservative request reservations across restarts; actual HTTP request count may be lower.',
              'behavior_counts':dict(Counter(r['behavior_type'] for r in behaviors.values())),
              'coverage_summary':{'eligible':len(accounts),'verified':len(identities),'excluded':len(excluded),
                  'identity_failures':len(retry),'endpoints':dict(Counter(r['status'] for r in coverage.values()))},
              'limitations':['all eligible verified-human accounts targeted; inspect per-account failures and pagination status',
                  'API quota bounds pagination; partial/pending is not empty interest','public current state only, not deleted/private/unstarred history',
                  'fork/ownership event time unknown; repository language observed now, not at star time',
                  'pagination is not an atomic historical snapshot','non-human and unconfirmed identities excluded','not ability or model-quality evidence']}
    manifest['files']=[{'path':p.relative_to(output).as_posix(),'sha256':sha(p.read_bytes()),'size_bytes':p.stat().st_size}
                       for p in sorted(output.rglob('*')) if p.is_file() and (p.parent==evidence or p.parent==output/'tables' or p.name=='collection_plan.json')]
    raw=encode(manifest);(output/'manifest.json').write_bytes(raw);(output/'manifest.sha256').write_text(sha(raw)+'  manifest.json\n',encoding='utf-8')
    progress('finished');print(json.dumps({'coverage_summary':manifest['coverage_summary'],'behavior_counts':manifest['behavior_counts']}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent',type=Path,default=Path('datasets/b_real_contributions_v0.2'))
    parser.add_argument('--output',type=Path,default=Path('data/b_weak_interest_all_20261028'))
    parser.add_argument('--previous',type=Path,default=Path('data/b_weak_interest_20261028'))
    parser.add_argument('--budget',type=int,default=4700)
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--plan-only',action='store_true')
    args=parser.parse_args()
    if args.budget<1 or not 1<=args.workers<=8: parser.error('Positive budget and 1..8 workers required')
    collect(args.parent,args.output,args.previous,args.budget,args.workers,args.plan_only)
