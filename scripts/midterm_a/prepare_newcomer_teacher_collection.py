"""Prepare a label-directed teacher plan and local recheck queue without network access."""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

from optimized_teacher_collection import POLICY, DEFAULT_BUDGET, NEWCOMER_LABEL_GROUPS, make_batch
import re


def label_group(labels):
    normalized={re.sub(r'[-_ ]+',' ',label).strip().lower() for label in labels}
    for group,choices in NEWCOMER_LABEL_GROUPS.items():
        if normalized.intersection(choices):return group
    return None


def prepare(workspace, output):
    output.mkdir(parents=True,exist_ok=False)
    seeds={}
    for path in sorted((workspace/'config/midterm_datasets').glob('teacher_round*.json')):
        value=json.loads(path.read_text(encoding='utf-8-sig'))
        for row in value.get('repositories',[]):
            if all(k in row for k in ('name','id','owner','language')):
                seeds[row['name']]=dict(repository=row['name'],repo_id=row['id'],owner_id=row['owner'],language=row['language'])
    source=workspace/'tmp/github_share_v0.12/oss-mentor-issues-v0.12/candidates.jsonl'
    rows=[json.loads(line) for line in source.read_text(encoding='utf-8').splitlines() if line.strip()]
    queue=[]
    for row in rows:
        group=label_group(row['labels'])
        if group:
            queue.append(dict(repository=row['repository'],issue_number=row['issue_number'],
                github_issue_id=row['github_issue_id'],html_url=row['html_url'],labels=row['labels'],
                primary_language=row['primary_language'],discovery_group=group,
                previous_verified_at=row['github_verified_at'],requires_current_state_recheck=True,
                beginner_difficulty_confirmed=False))
    counts=Counter(row['repository'] for row in queue if row['discovery_group']=='newcomer')
    languages=('Python','JavaScript','TypeScript','Rust','Go','Java')
    selected={language:sorted((s for s in seeds.values() if s['language']==language),
        key=lambda s:(-counts[s['repository']],s['repository']))[:12] for language in languages}
    zone=timezone(timedelta(hours=8));batches=[]
    # Recent months first, interleave languages, explicit newcomer signals before
    # help wanted. Do not spend the first budget on large historical repositories.
    for group in ('newcomer','help_wanted'):
        for month in (202610,202609,202608):
            start=datetime(2026,month%100,1,tzinfo=zone)
            end=datetime(2026,10,19,tzinfo=zone) if month==202610 else datetime(2026,month%100+1,1,tzinfo=zone)
            for index in range(12):
                for language in languages:
                    if index>=len(selected[language]):continue
                    seed=dict(selected[language][index],discovery_filter=group)
                    batch=make_batch(seed,month,int(start.timestamp()),int(end.timestamp()))
                    batch.update(collection_reason='newcomer_label_directed',language=language)
                    batches.append(batch)
    plan=dict(policy=POLICY,budget=DEFAULT_BUDGET,batches=batches,baseline_keys=[],
        purpose='Prioritize beginner recommendation discovery; help wanted requires separate difficulty review',
        baseline_note='No teacher corpus baseline supplied; net_new_issues is within this run only.',
        limitations=['Teacher events are historical signals, never proof of current availability or beginner difficulty.',
            'Recent label additions are searched across all IssuesEvent actions; action coverage is unverified.',
            'Cutoff remains 2026-10-19 Asia/Shanghai to preserve the existing audited collector time boundary.',
            'Repository metadata is reused locally; collection scope does not prove newcomer task supply.'])
    (output/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'recheck_queue.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in sorted(queue,key=lambda r:(r['discovery_group']!='newcomer',r['repository'],r['issue_number']))),encoding='utf-8')
    summary=dict(remote_requests=0,batches=len(batches),repositories=sum(map(len,selected.values())),
        repository_languages={k:len(v) for k,v in selected.items()},
        local_recheck_groups=dict(Counter(r['discovery_group'] for r in queue)),
        current_newcomer_recommendations_added=0)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,default=Path.cwd())
    parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(prepare(**vars(parser.parse_args())),ensure_ascii=False))
