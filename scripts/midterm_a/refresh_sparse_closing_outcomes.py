"""Refresh closing references for the five remaining accounts, in a fresh cache."""
import argparse
from pathlib import Path
import json
import shutil
import collect_targeted_adapter as t


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-network',action='store_true')
    args=parser.parse_args()
    base=t.PARENT.parent
    cohort=t.b.rows(base/'sparse_expansion_20261104_handoff/remaining_gaps.jsonl')
    ids={r['github_numeric_id'] for r in cohort}
    source=t.b.read(base/'preinteraction_sparse_supplement_20261104_v1/source_manifest.json')
    seeds={}
    for relative in source['source_roots'].values():
        for row in t.b.read(t.b.ROOT/relative/'selected_prs.json'):
            if row['author']['databaseId'] in ids and t.b.dt(row['createdAt']).year<=2025:
                seeds[row['url']]=row
    for row in t.b.read(base/'remaining_five_final_probe_20261104_v1/selected_prs.json'):
        seeds[row['url']]=row
    identities=t.b.read(t.b.SEEDS)
    teacher_files=list((base/'teacher_sparse_readonly_20261104_v1').glob('pr_*.json'))+list((base/'teacher_sparse_readonly_20261104_v2').glob('pr_*.json'))
    for path in teacher_files:
        for row in t.b.read(path)['data']:
            identity=int(row['issue_author_id'])
            if identity not in ids:continue
            url=f'https://github.com/{row["repo_name"]}/pull/{row["issue_number"]}'
            seeds.setdefault(url,dict(repository=row['repo_name'],number=int(row['issue_number']),url=url,
                author=dict(databaseId=identity,login=row.get('issue_author_login') or next(a['developer_key'] for a in cohort if a['github_numeric_id']==identity)),seed_identity=identities[str(identity)],
                discovery_source=path.relative_to(t.b.ROOT).as_posix()))
    cached=base/'sparse_closing_recovery_20261104_v2'
    t.OUT=t.b.OUT=base/'sparse_closing_recovery_20261104_v3';t.OUT.mkdir(exist_ok=True)
    cached_manifest=t.b.read(cached/'manifest.json')
    for relative,expected in cached_manifest['files_sha256'].items():
        if relative.startswith(('api_cache/','requests/')):
            source_path=cached/relative
            if t.b.digest(source_path.read_bytes())!=expected:raise ValueError('Refresh cache hash mismatch')
            destination=t.OUT/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source_path,destination)
    t.b.save('selected_prs.json',list(seeds.values()))
    t.b.save('collection_plan.json',dict(accounts=cohort,source_manifests=source,
        policy='Fresh network cache; pre-interaction cutoff; exclude all known positive Issue keys.',
        teacher_discovery='teacher_sparse_readonly_20261104_v1/pr_296798_202405.json'))
    t.b.prefetch_details(t.b.API(args.allow_network),{})
    if t.b.rows(t.OUT/'prefetch_failures.jsonl'):raise ValueError('Closing refresh reads incomplete')
    known={}
    for path in (t.OUT/'api_cache').glob('*.json'):
        for value in t.b.read(path)['payload'].get('data',{}).values():
            if isinstance(value,dict) and value.get('pullRequest'):
                known[value['pullRequest']['url']]=value['pullRequest'],path.relative_to(t.OUT).as_posix()
    for seed in seeds.values():
        actual=known[seed['url']][0]
        seed.update(createdAt=actual['createdAt'],mergedAt=actual['mergedAt'],year=t.b.dt(actual['createdAt']).year)
    t.b.save('selected_prs.json',list(seeds.values()))
    baseline=t.b.rows(base/'merged_ab_history_2024_2026_v6/verified_outcomes.jsonl')
    previous=t.b.rows(base/'sparse_expansion_20261104_handoff/outcome_index.jsonl')
    admitted={y['outcome_id'] for g in t.b.rows(base/'temporal_xy_2024_2026_v6/train.jsonl') for y in g['y']}
    admitted_rows=[r for r in baseline if r['outcome_id'] in admitted]
    positive={(r['github_numeric_id'],r['issue_key']) for r in admitted_rows+previous}
    existing={r['outcome_id'] for r in admitted_rows+previous}
    originally_verified={r['outcome_id'] for r in baseline}
    targets=dict(sparse_ids=ids,training_years=tuple(range(2008,2026)),allow_test=False,
        training_repositories=set(),profile_cutoff_policy='pre_interaction')
    outcomes=[];excluded=[]
    for seed in seeds.values():
        pr,ref=known[seed['url']]
        rows,reasons=t.outcome_rows(pr,seed,ref,targets,existing)
        for row in rows:
            if (row['github_numeric_id'],row['issue_key']) in positive:continue
            positive.add((row['github_numeric_id'],row['issue_key']))
            row.pop('new_repository_basis',None)
            row.update(training_ready=False,cutoff_policy='one_second_before_PR_created_at',
                intended_split='fresh_closing_reference_pending_B_C_review',
                recovered_existing_verified_outcome=row['outcome_id'] in originally_verified)
            outcomes.append(row)
        excluded+=reasons
    t.b.lines('verified_outcomes.jsonl',outcomes);t.b.lines('outcome_exclusions.jsonl',excluded)
    summary=dict(refreshed_prs=len(seeds),new_outcomes=len(outcomes),
        accounts_with_new_outcomes=len({r['github_numeric_id'] for r in outcomes}),
        recovered_existing_verified_outcomes=sum(r['recovered_existing_verified_outcome'] for r in outcomes),training_ready=False)
    t.b.save('summary.json',summary)
    t.b.save('manifest.json',dict(collector_sha256=t.b.digest(Path(__file__).read_bytes()),
        files_sha256={p.relative_to(t.OUT).as_posix():t.b.digest(p.read_bytes()) for p in t.OUT.rglob('*') if p.is_file() and p.name!='manifest.json'}))
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
