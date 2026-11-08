"""Prepare fixed read-only opened-Issue scopes for the new Oct 19-22 gap."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from optimized_teacher_collection import DEFAULT_BUDGET, POLICY, make_batch


def prepare(output):
    if output.exists():
        raise FileExistsError(output)
    read = lambda p: json.loads(p.read_text(encoding='utf-8-sig'))
    context = Path('data/growth_projects_20261023_v1')
    evidence = read(context/'final_evidence.json')
    audit = read(context/'results/repository_audit.json')
    eligible = {r['repository'] for r in audit if r['metadata_passed']}
    sources = read(context/'source_repositories.json')
    root = Path('D:/OSS-Mentor/data/teacher_clickhouse')
    baseline = set()
    manifest = []
    covered = []
    zone = timezone(timedelta(hours=8))
    start = int(datetime(2026,10,19,tzinfo=zone).timestamp())
    end = int(datetime(2026,10,23,tzinfo=zone).timestamp())
    if datetime.now(timezone.utc).timestamp() < end:
        raise ValueError('Cannot query future dates')
    for p in sorted(root.glob('*/issues_discovered.json')):
        raw = p.read_bytes()
        for r in json.loads(raw.decode('utf-8-sig')):
            baseline.add((str(r['repo_id']),int(r['issue_number'])))
        manifest.append(dict(path=str(p),sha256=hashlib.sha256(raw).hexdigest()))
        status_path = p.parent/'status.json'
        if not status_path.exists():
            continue
        status = read(status_path)
        if status.get('status') in ('running','waiting_for_credentials','connecting'):
            raise ValueError('Collection is active: '+p.parent.name)
        for b in status.get('batches',[]):
            q = b.get('batch', {})
            if b.get('status')=='exported' and q.get('discovery_filter','all_opened')=='all_opened' and not b.get('possibly_truncated'):
                covered.append(q)
    source_db = Path('D:/OSS-Mentor/data/working/merged_candidates_v0.12.sqlite3')
    with sqlite3.connect(source_db.as_uri()+'?mode=ro',uri=True) as db:
        for repo_id,number in db.execute('SELECT r.github_repository_id,t.issue_number FROM task_candidate t JOIN repository r USING(repository_id)'):
            if repo_id:
                baseline.add((str(repo_id),number))
    batches=[]
    for s in sources:
        repo=s['full_name']
        if repo not in eligible:
            continue
        envelope=evidence['repositories'][repo]
        if datetime.now(timezone.utc)-datetime.fromisoformat(envelope['observed_at'].replace('Z','+00:00'))>timedelta(days=7):
            raise ValueError('Refresh repository seeds before discovery')
        p=envelope['payload']
        if s.get('github_repository_id') and str(s['github_repository_id'])!=str(p['id']):
            raise ValueError('Seed identity mismatch')
        if any(int(q.get('repo_id',0))==p['id'] and q.get('start_epoch',end)<=start and q.get('end_epoch',start)>=end for q in covered):
            raise ValueError('Range was already queried: '+repo)
        seed=dict(repository=repo,repo_id=p['id'],owner_id=p['owner']['id'],
                  discovery_filter='all_opened',query_layout='action_prewhere_v2',event_action='opened')
        batch=make_batch(seed,202610,start,end)
        batch['collection_reason']='new_recent_opened_issue_gap_after_previous_cutoff'
        batches.append(batch)
    assert len({b['key'] for b in batches})==len(batches)
    value=dict(policy=POLICY,prepared_at=datetime.now(timezone.utc).isoformat(),budget=DEFAULT_BUDGET,
        batches=batches,baseline_keys=[list(k) for k in sorted(baseline)],source_manifest=manifest,
        recommendation_target=10000,historical_eligible_baseline=8270,
        scope=dict(start_inclusive='2026-10-19T00:00:00+08:00',end_exclusive='2026-10-23T00:00:00+08:00',
                   repositories=len(batches),filter='all_opened',requires_github_screening=True),
        limitations=['Raw discovered identities are not eligible recommendations.',
                     'Earlier 8270 candidates need freshness review before claiming 10000 currently usable tasks.',
                     'Issue source stays teacher ClickHouse; GitHub is for verification only.',
                     'Result saturation splits ranges; server guards and failures still stop the run.'])
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return dict(batches=len(batches),baseline_unique=len(baseline),scope=value['scope'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(prepare(parser.parse_args().output),ensure_ascii=False))
