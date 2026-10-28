"""Cache bounded public weak-interest acquisition for import into an existing database."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from oss_mentor.collector.github_client import GitHubClient

def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode()

def sha(data): return hashlib.sha256(data).hexdigest()

def collect(parent, output, limit, plan_only=False):
    output.mkdir(parents=True, exist_ok=True)
    parent_manifest = json.loads((parent/'manifest.json').read_bytes())
    developers = [json.loads(l) for l in (parent/'tables/developers.jsonl').read_text(encoding='utf-8').splitlines()]
    contributions = [json.loads(l) for l in (parent/'tables/contributions.jsonl').read_text(encoding='utf-8').splitlines()]
    counts = Counter(c['developer_key'] for c in contributions)
    selected = []
    for d in sorted(developers, key=lambda d:(-counts[d['developer_key']], d['developer_key'])):
        if d['identity_status'] != 'verified' or not counts[d['developer_key']]: continue
        account = None
        for location in d.get('evidence_locations', []):
            fields = json.loads((parent/location).read_bytes()).get('fields', {})
            for role in ('author', 'committer'):
                candidate = fields.get(role) or {}
                if candidate.get('id') == d['github_numeric_id'] and candidate.get('type') == 'User':
                    account = candidate
                    break
            if account: break
        if account: selected.append({**d, 'login':account['login']})
        if len(selected) == limit: break
    plan = {'parent_dataset_version':parent_manifest['dataset_version'],
            'parent_manifest_sha256':sha((parent/'manifest.json').read_bytes()),
            'selection':'top contribution count, verified human account in parent evidence; tie by developer_key',
            'accounts':[{'developer_key':d['developer_key'],'github_numeric_id':d['github_numeric_id'],'login':d['login']} for d in selected],
            'developer_keys':[d['developer_key'] for d in selected],
            'per_page':100,'max_pages_per_endpoint':1,'limit':limit}
    plan_path = output/'collection_plan.json'
    if plan_path.exists() and json.loads(plan_path.read_bytes()) != plan: raise ValueError('Existing plan differs')
    plan_path.write_bytes(encode(plan))
    if plan_only:
        print(json.dumps(plan,ensure_ascii=False,indent=2))
        return
    credential = subprocess.run(['gh','auth','token'], capture_output=True, text=True)
    token = credential.stdout.strip() if credential.returncode == 0 else None
    client = GitHubClient(api_base='https://api.github.com',api_version='2026-03-10',
                          user_agent='OSS-Mentor-weak-interest/1',token=token,timeout_seconds=15,max_retries=0)
    cache = output/'evidence';cache.mkdir(exist_ok=True)
    def fetch(path, params=None, headers=None):
        identity = sha(encode([path,params,headers]))
        file = cache/(identity+'.json')
        if file.exists(): return json.loads(file.read_bytes()), 'evidence/'+file.name
        response = client.get(path, params=params, extra_headers=headers)
        envelope = {'source_url':response.url,'status_code':response.status_code,
                    'observed_at':response.fetched_at.isoformat(),
                    'link':response.headers.get('link'), 'payload':response.payload,
                    'scope':'public REST response payload; no request headers or credentials'}
        file.write_bytes(encode(envelope))
        return envelope, 'evidence/'+file.name
    repositories = {};behaviors = [];coverage = [];identities = []
    for d in selected:
        try:
            envelope, identity_location = fetch('/users/'+d['login'])
            user = envelope['payload']
            if user.get('id') != d['github_numeric_id'] or user.get('type') != 'User':
                coverage.append({'developer_key':d['developer_key'],'status':'identity_mismatch'});continue
            login = user['login']
            identities.append({'developer_key':d['developer_key'],'github_numeric_id':user['id'],
                               'login':login,'identity_status':'verified','source_location':identity_location})
            for kind in ('starred','repos'):
                params = {'per_page':100,'page':1, 'sort':'created','direction':'desc'}
                if kind == 'repos': params['type']='owner'
                headers = {'Accept':'application/vnd.github.star+json'} if kind == 'starred' else None
                try:
                    response, location = fetch('/users/'+login+'/'+kind,params,headers)
                    if not isinstance(response['payload'], list): raise ValueError('Expected list')
                    coverage.append({'developer_key':d['developer_key'],'endpoint':kind,'status':'completed',
                                     'returned_count':len(response['payload']),
                                     'truncated':'rel="next"' in (response['link'] or ''),
                                     'source_location':location,'observed_at':response['observed_at']})
                    for item in response['payload']:
                        repo = item['repo'] if kind == 'starred' else item
                        if repo.get('private') is not False: continue
                        if kind == 'repos' and repo['owner']['id'] != user['id']: raise ValueError('Owner mismatch')
                        rkey = 'repository-'+sha(encode(['github',str(repo['id'])]).rstrip(b'\n'))
                        # Reuse the canonical parent key by numeric repository ID when possible (merged below).
                        event = item.get('starred_at') if kind == 'starred' else None
                        behavior = 'star' if kind == 'starred' else 'fork' if repo.get('fork') else 'owned_repository'
                        repositories[rkey] = {'repository_key':rkey,'source_repository_id':str(repo['id']),
                            'full_name':repo['full_name'],'primary_language':repo.get('language'),
                            'is_archived':repo['archived'],'domains':[], 'observed_at':response['observed_at'],
                            'source_location':location,'maintenance_status':'unknown', 'contribution_url':None}
                        row = {'developer_key':d['developer_key'],'repository_key':rkey,'behavior_type':behavior,
                               'event_at':event,'event_time_unknown':event is None,'observed_at':response['observed_at'],
                               'source':'github_rest','source_location':location,'identity_source_location':identity_location,
                               'evidence_location':repo['html_url'],
                               'interpretation':'weak repository-language interest, not ability; current owner/fork state has unknown event time'}
                        row['behavior_key']=sha(encode([d['developer_key'],rkey,behavior,event]))
                        behaviors.append(row)
                except Exception as error:
                    coverage.append({'developer_key':d['developer_key'],'endpoint':kind,'status':'failed',
                                     'error_type':type(error).__name__,'http_status':getattr(error,'status_code',None)})
        except Exception as error:
            coverage.append({'developer_key':d['developer_key'],'status':'failed','error_type':type(error).__name__,
                             'http_status':getattr(error,'status_code',None)})
        print(json.dumps({'developer_key':d['developer_key'],'weak_records_so_far':len(behaviors),'coverage_rows':len(coverage)}),flush=True)
    parent_repos = {r['source_repository_id']:r for r in (json.loads(l) for l in (parent/'tables/repositories.jsonl').read_text(encoding='utf-8').splitlines())}
    keys = {key:parent_repos.get(repo['source_repository_id'],repo)['repository_key'] for key,repo in repositories.items()}
    for row in behaviors: row['repository_key']=keys[row['repository_key']]
    for row in repositories.values(): row['repository_key']=keys[row['repository_key']]
    tables = {'repositories':list(repositories.values()),'weak_interest_behaviors':behaviors,
              'identity_checks':identities,'coverage':coverage}
    for kind, rows in tables.items():
        path=output/'tables'/f'{kind}.jsonl';path.parent.mkdir(exist_ok=True)
        path.write_bytes(b''.join((json.dumps(r,sort_keys=True,ensure_ascii=False)+'\n').encode() for r in rows))
    manifest = {'schema_version':'b-weak-interest-supplement-v1','dataset_version':'b-real-weak-interest-v0.1-20261028',
                'fixture_only':False,'created_at':datetime.now(timezone.utc).isoformat(),**plan,
                'entity_counts':{k:len(v) for k,v in tables.items()},
                'behavior_counts':dict(Counter(r['behavior_type'] for r in behaviors)),
                'limitations':['bounded verified-human sample; selection bias','one page per endpoint; inspect coverage.truncated',
                               'public current state only; no deleted/private/unstarred history','fork and ownership event times unknown',
                               'repository primary language is observed now, not historical at star time','not ability or model-quality evidence']}
    manifest['files']=[{'path':p.relative_to(output).as_posix(),'sha256':sha(p.read_bytes()),'size_bytes':p.stat().st_size}
                       for p in sorted(output.rglob('*')) if p.is_file() and p.name not in ('manifest.json','manifest.sha256')]
    data=encode(manifest);(output/'manifest.json').write_bytes(data)
    (output/'manifest.sha256').write_text(sha(data)+'  manifest.json\n',encoding='utf-8')
    print(json.dumps({k:manifest[k] for k in ('dataset_version','entity_counts','behavior_counts')},ensure_ascii=False),flush=True)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent',type=Path,default=Path('datasets/b_real_contributions_v0.2'))
    parser.add_argument('--output',type=Path,default=Path('data/b_weak_interest_20261028'))
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--plan-only',action='store_true',help='Write account/request plan without credentials or network access')
    args=parser.parse_args()
    if args.limit < 1: parser.error('limit must be positive')
    collect(args.parent,args.output,args.limit,args.plan_only)
