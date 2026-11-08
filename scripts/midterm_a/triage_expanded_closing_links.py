"""Read closing-reference counts for all remaining expanded PR candidates."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
from pathlib import Path

import collect_targeted_adapter as t


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-network', action='store_true')
    args = parser.parse_args()
    base = t.PARENT.parent
    source = base / 'advanced_adapter_expanded_20261104_v1'
    plan = t.b.read(source/'collection_plan.json')
    priors = [t.PARENT]+[Path(k) for k in plan['prior_supplements']]
    selected = [s for root in priors+[source] for s in t.b.read(root/'selected_prs.json')]
    index = t.b.rows(source/'discovery_index.jsonl')
    candidates = t.selector.choose_training_cohort(index, selected, t.b.read(t.b.SEEDS),
                                                   plan['targets']['accounts'], len(index))
    t.b.OUT = base/'expanded_closing_link_triage_20261104_v1'
    t.b.OUT.mkdir(exist_ok=True)
    api = t.b.API(args.allow_network)
    def collect(chunk):
        fields = []
        for i, seed in enumerate(chunk):
            owner, name = seed['repository'].split('/')
            fields.append(f'q{i}: repository(owner:'+json.dumps(owner)+',name:'+json.dumps(name)
                +f') {{ pullRequest(number:{seed["number"]}) {{ url merged createdAt mergedAt '
                + t.b.AUTHOR+' closingIssuesReferences(first:1) { totalCount } } }')
        payload, ref = api.call(query='query { '+' '.join(fields)+' }')
        results = []
        for i, seed in enumerate(chunk):
            repo = payload['data'][f'q{i}']
            pr = repo.get('pullRequest') if repo else None
            if not pr or pr['url'] != seed['url'] or (pr.get('author') or {}).get('databaseId') != seed['author']['databaseId']:
                raise ValueError('Triage PR identity mismatch')
            results.append(dict(pr_url=seed['url'], repository=seed['repository'],
                github_numeric_id=seed['author']['databaseId'],
                closing_issue_count=pr['closingIssuesReferences']['totalCount'], evidence_ref=ref,
                status='references_require_full_event_verification' if pr['closingIssuesReferences']['totalCount'] else 'no_current_closing_reference',
                outcome_verified=False, training_ready=False))
        return results
    records, failures = [], []
    chunks = [candidates[i:i+20] for i in range(0,len(candidates),20)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(collect, chunk):chunk for chunk in chunks}
        for done, future in enumerate(as_completed(futures),1):
            try:
                records.extend(future.result())
            except Exception as exc:
                failures.append(dict(pr_urls=[s['url'] for s in futures[future]],reason=type(exc).__name__))
            t.b.lines('triage.jsonl', records)
            t.b.lines('failures.jsonl', failures)
            if done%20==0 or done==len(chunks):
                print(json.dumps(dict(done=done,total=len(chunks),failures=len(failures))),flush=True)
    positive = {r['pr_url'] for r in records if r['closing_issue_count']}
    t.b.save('positive_candidates.json',[s for s in candidates if s['url'] in positive])
    summary = dict(candidates=len(candidates),checked=len(records),
        with_closing_references=len(positive),failed_batches=len(failures),training_ready=False)
    t.b.save('summary.json',summary)
    t.b.save('manifest.json',dict(source_plan_sha256=t.b.digest((source/'collection_plan.json').read_bytes()),
        collector_sha256=t.b.digest(Path(__file__).read_bytes()),
        files_sha256={p.relative_to(t.b.OUT).as_posix():t.b.digest(p.read_bytes())
                      for p in t.b.OUT.rglob('*') if p.is_file() and p.name!='manifest.json'}))
    print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    main()
