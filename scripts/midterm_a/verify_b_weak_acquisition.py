"""Verify every acquired relation against its retained public API field projection."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path

def verify(root):
    root=Path(root);raw=(root/'manifest.json').read_bytes();manifest=json.loads(raw)
    assert hashlib.sha256(raw).hexdigest()==(root/'manifest.sha256').read_text().split()[0]
    for entry in manifest['files']:
        path=(root/entry['path']).resolve()
        assert path.is_relative_to(root.resolve())
        data=path.read_bytes()
        assert len(data)==entry['size_bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256']
    def rows(kind): return [json.loads(l) for l in (root/'tables'/f'{kind}.jsonl').read_text(encoding='utf-8').splitlines()]
    repositories={r['repository_key']:r for r in rows('repositories')}
    identities={r['developer_key']:r for r in rows('identity_checks')}
    users={}
    for identity in identities.values():
        envelope=json.loads((root/identity['source_location']).read_bytes());user=envelope['payload']
        assert envelope['status_code']==200 and user['id']==identity['github_numeric_id'] and user['type']=='User'
        users[identity['developer_key']]=user
    envelopes={};behavior_count=Counter();per_developer=Counter();seen=set()
    for row in rows('weak_interest_behaviors'):
        user=users[row['developer_key']];assert row['identity_source_location']==identities[row['developer_key']]['source_location']
        source=row['source_location']
        if source not in envelopes:
            e=json.loads((root/source).read_bytes())
            assert e['status_code']==200 and e['source_url'].startswith('https://api.github.com/users/')
            starred='/starred?' in e['source_url']
            envelopes[source]=(e,{str((v['repo'] if starred else v)['id']):v for v in e['payload']})
        e,index=envelopes[source];repo=repositories[row['repository_key']]
        item=index[repo['source_repository_id']];raw_repo=item['repo'] if row['behavior_type']=='star' else item
        assert raw_repo['private'] is False and row['observed_at']==e['observed_at']
        if row['behavior_type']=='star': assert row['event_at']==item['starred_at'] and not row['event_time_unknown']
        else:
            assert raw_repo['owner']['id']==user['id'] and row['event_at'] is None and row['event_time_unknown']
            assert row['behavior_type']==('fork' if raw_repo['fork'] else 'owned_repository')
        identity=(row['developer_key'],row['repository_key'],row['behavior_type'],row['event_at'])
        assert identity not in seen;seen.add(identity)
        behavior_count[row['behavior_type']]+=1;per_developer[row['developer_key']]+=1
    assert dict(behavior_count)==manifest['behavior_counts']
    coverage=rows('coverage');by_developer=defaultdict(dict)
    for row in coverage: by_developer[row['developer_key']][row['endpoint']]=row
    fully_complete=[];complete_empty=[];partial=[];failed=[]
    for key in identities:
        endpoints=by_developer[key]
        if all(endpoints.get(kind,{}).get('status')=='completed' for kind in ('starred','repos')):
            fully_complete.append(key)
            if not per_developer[key]:complete_empty.append(key)
        elif any(e['status']=='failed' for e in endpoints.values()): failed.append(key)
        else: partial.append(key)
    result={'status':'acquisition_verified','behavior_counts':dict(behavior_count),'records':sum(behavior_count.values()),
            'identity_checks':len(identities),'accounts_with_records':len(per_developer),
            'fully_paginated_accounts':len(fully_complete),'complete_empty_accounts':len(complete_empty),
            'partial_accounts':len(partial),'accounts_with_failed_behavior_endpoint':len(failed),
            'identity_failures':sum(r['endpoint']=='identity' for r in coverage),
            'endpoint_statuses':dict(Counter(r['status'] for r in coverage)),
            'truncated_endpoints':sum(r.get('truncated',False) for r in coverage),
            'excluded_accounts':len(rows('excluded_accounts')) if (root/'tables/excluded_accounts.jsonl').exists() else 0}
    (root/'verification_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    print(json.dumps(verify(parser.parse_args().root),ensure_ascii=False,indent=2))
