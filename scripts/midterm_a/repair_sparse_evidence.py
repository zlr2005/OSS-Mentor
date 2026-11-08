"""Repair every unpaired supplementary window, reusing verified older code."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

import backfill_adapter_2024_2026 as b
import deepen_adapter_evidence as deep


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-network',action='store_true')
    parser.add_argument('--source',type=Path)
    parser.add_argument('--run-id',default='sparse_evidence_repair_20261104_v1')
    parser.add_argument('--expanded',action='store_true')
    parser.add_argument('--include-open',action='store_true')
    args=parser.parse_args()
    base=b.OUT.parent
    source=args.source.resolve() if args.source else base/'sparse_expansion_20261104_handoff'
    if not source.is_relative_to(b.ROOT) or not args.run_id.replace('_','').isalnum():
        parser.error('Project source and simple run ID required')
    outcomes=b.rows(source/'outcome_index.jsonl')
    paired={r['outcome_id'] for r in b.rows(source/'comparison_index.jsonl')}
    grouped=defaultdict(list)
    for row in outcomes:
        if row['outcome_id'] not in paired:
            grouped[row['github_numeric_id'],row['profile_as_of'],row['repository']].append(row)
    inputs=[base/'merged_ab_history_2024_2026_v6']+[base/n for n in
        ('earlier_sparse_history_2022_2023_v1','preinteraction_sparse_history_20261104_v1','earlier_sparse_history_2008_2021_v1')]
    if args.expanded:
        inputs += [base/'sparse_evidence_repair_20261104_v1',base/'sparse_closing_recovery_history_20261104_v1']
    if args.include_open:
        inputs.append(base/'sparse_evidence_repair_20261105_v2')
    history=defaultdict(list)
    for root in inputs:
        name='file_contributions.jsonl' if root==inputs[0] else 'historical_contributions.jsonl'
        for row in b.rows(root/name):
            if row.get('content_available',True) and row.get('event_at'):
                history[row['github_numeric_id']].append(dict(row,contribution_origin_root=root.relative_to(b.ROOT).as_posix()))
    positives=defaultdict(set)
    for row in outcomes+b.rows(inputs[0]/'verified_outcomes.jsonl'):
        positives[row['github_numeric_id']].add(row['issue_key'])
    b.OUT=deep.OUT=base/args.run_id;b.OUT.mkdir(exist_ok=True)
    api=b.API(args.allow_network)
    b.save('collection_plan.json',dict(source_root=source.relative_to(b.ROOT).as_posix(),
        source_manifest_sha256=b.digest((source/'manifest.json').read_bytes()),
        existing_history_manifests={p.relative_to(b.ROOT).as_posix():b.digest((p/'manifest.json').read_bytes()) for p in inputs},
        windows=len(grouped),expanded=args.expanded,include_open=args.include_open,
        history_selection='Verified older files, then target repository and bounded alternatives; expanded mode starts in 2008 and inspects up to 300 candidate Issues.'))
    def collect(item):
        (identity,as_of,repo),rows=item
        window=dict(github_numeric_id=identity,login=rows[0]['developer_key'],as_of=as_of,
            repository=repo,known_commits=[],identity_evidence=rows[0]['seed_identity']['identity_evidence'])
        key=b.digest(b.canonical([identity,as_of,repo]))
        cached=b.OUT/'windows'/f'{key}.json'
        if cached.exists():return b.read(cached)
        older=sorted((r for r in history[identity] if b.dt(r['event_at'])<b.dt(as_of)
                      and (not r.get('author_at') or b.dt(r['author_at'])<b.dt(as_of))),key=lambda r:r['event_at'],reverse=True)[:3]
        # The date-only search is a superset; exact timestamp replay follows.
        query_texts=[f'repo:{repo} is:issue created:<={as_of[:10]} closed:>={as_of[:10]} sort:created-desc']
        if args.include_open:
            query_texts.insert(0,f'repo:{repo} is:issue is:open created:<={as_of[:10]} sort:created-desc')
        controls=[]
        coverage=[]
        for query_text in query_texts:
            cursor=None
            for page in range(1,4 if args.expanded else 2):
                after=',after:'+json.dumps(cursor) if cursor else ''
                query='query { q0: search(query:'+json.dumps(query_text)+f',type:ISSUE,first:{100 if args.expanded else 60}'+after+') { '+b.PAGE+' nodes { ... on Issue { '+b.ISSUE_FIELDS+' } } } }'
                payload,ref=api.call(query=query)
                info=payload['data']['q0'];coverage.append(dict(page=page,query=query_text,evidence_ref=ref,**info['pageInfo']))
                for issue in info['nodes']:
                    candidate,reason=b.issue_at(issue,b.dt(as_of))
                    if candidate and candidate['issue_key'] not in positives[identity] and candidate['issue_key'] not in {c['issue_key'] for c in controls}:
                        controls.append(dict(candidate,repository=repo,evidence_ref=ref,role='unlabeled_control'))
                    if len(controls)==5:break
                if len(controls)>=5 or not info['pageInfo']['hasNextPage']:break
                cursor=info['pageInfo']['endCursor']
                if not cursor:raise ValueError('Missing candidate page cursor')
            if len(controls)>=5:break
        contributions=[];exclusions=[]
        if not older and controls:
            start='2008-01-01T00:00:00Z' if args.expanded else f'{b.dt(as_of).year-3}-01-01T00:00:00Z'
            result=deep.collect(api,window,history_pages=2 if args.include_open else 1,include_controls=False,history_start=start,commit_page_size=5 if args.include_open else 1)
            contributions+=result['contributions'];exclusions+=result['exclusions']
            if not contributions:
                search=f'author:{window["login"]} is:pr is:merged merged:<={as_of[:10]} sort:updated-desc'
                response,discovery_ref=api.call(query='query { q0: search(query:'+json.dumps(search)
                    +f',type:ISSUE,first:{30 if args.expanded else 10}) '+'{ nodes { ... on PullRequest { mergedAt '+b.AUTHOR+' repository { nameWithOwner } } } } }')
                tried={repo.casefold()}
                for node in response['data']['q0']['nodes']:
                    if not node or (node.get('author') or {}).get('databaseId')!=identity or b.dt(node['mergedAt'])>=b.dt(as_of):continue
                    alternative=node['repository']['nameWithOwner']
                    if alternative.casefold() in tried:continue
                    tried.add(alternative.casefold())
                    result=deep.collect(api,dict(window,repository=alternative),history_pages=2 if args.include_open else 1,
                        include_controls=False,history_start=start,commit_page_size=5 if args.include_open else 1)
                    contributions+=result['contributions'];exclusions+=result['exclusions']
                    if contributions or len(tried)>=(6 if args.expanded else 3):break
        result=dict(window=window,reused_contributions=older,contributions=contributions,controls=controls,exclusions=exclusions,candidate_search_coverage=coverage)
        b.save(f'windows/{key}.json',result)
        return result
    results=[];failures=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(collect,item):item[0] for item in grouped.items()}
        for done,future in enumerate(as_completed(futures),1):
            try:results.append(future.result())
            except Exception as exc:failures.append(dict(window=futures[future],reason=type(exc).__name__))
            if done%10==0 or done==len(grouped):print(json.dumps(dict(done=done,total=len(grouped),failures=len(failures))),flush=True)
    comparisons=[]
    for result in results:
        window=result['window'];key=window['github_numeric_id'],window['as_of'],window['repository']
        if result['controls'] and (result['contributions'] or result['reused_contributions']):
            for row in grouped[key]:
                comparisons.append(dict(outcome_id=row['outcome_id'],github_numeric_id=key[0],as_of=key[1],repository=key[2],
                    comparison_issue_keys=[c['issue_key'] for c in result['controls']],
                    reused_contribution_keys=[[r['contribution_origin_root'],r['repository'],r['commit_sha'],r['path']] for r in result['reused_contributions']],
                    training_ready=False,profile_state='not_built_pending_B_review'))
    b.lines('historical_contributions.jsonl',[c for r in results for c in r['contributions']])
    b.lines('historical_controls.jsonl',list({(c['issue_key'],c['as_of']):c for r in results for c in r['controls']}.values()))
    b.lines('reused_contribution_index.jsonl',[c for r in results for c in r['reused_contributions']])
    b.lines('comparison_groups.jsonl',comparisons);b.lines('failures.jsonl',failures)
    b.lines('window_gaps.jsonl',[dict(r['window'],missing_code=not(r['contributions'] or r['reused_contributions']),missing_controls=not r['controls']) for r in results if not r['controls'] or not(r['contributions'] or r['reused_contributions'])])
    b.save('summary.json',dict(windows=len(grouped),failed_windows=len(failures),repaired_outcomes=len(comparisons),training_ready=False))
    b.save('manifest.json',dict(collector_sha256=b.digest(Path(__file__).read_bytes()),files_sha256={p.relative_to(b.OUT).as_posix():b.digest(p.read_bytes()) for p in b.OUT.rglob('*') if p.is_file() and p.name!='manifest.json'}))
    deep.verify()
    print(json.dumps(b.read(b.OUT/'summary.json')),flush=True)


if __name__=='__main__':main()
