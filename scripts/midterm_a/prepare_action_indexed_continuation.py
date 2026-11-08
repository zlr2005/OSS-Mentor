"""Offline continuation aligned with the observed month/type/action/org/repo sorting key."""
import argparse
from collections import defaultdict
from pathlib import Path
from optimized_teacher_collection import read,save,make_batch,ISSUE_ACTIONS,POLICY,DEFAULT_BUDGET


def merge_intervals(intervals):
    merged=[]
    for start,end in sorted(intervals):
        if not merged or start>merged[-1][1]:merged.append([start,end])
        else:merged[-1][1]=max(merged[-1][1],end)
    return merged


def prepare(previous,output):
    if output.exists():raise FileExistsError(output)
    status=read(previous/'status.json')
    if status['status'] not in ('paused_budget_unverified','stopped_unverified','finished_unverified'):raise ValueError('Stop the old run first')
    checkpoint=read(previous/'checkpoint.json');groups=defaultdict(list);seeds={}
    for batch in checkpoint['pending']:
        if batch.get('query_layout')!='scope_prewhere_v1':raise ValueError('Expected original all-action daily scope')
        group=(batch['repository'],batch['repo_id'],batch['owner_id'],batch['month'],batch['discovery_filter'])
        groups[group].append((batch['start_epoch'],batch['end_epoch']));seeds[group]=batch
    spans={group:merge_intervals(intervals) for group,intervals in groups.items()}
    batches=[]
    for action in ISSUE_ACTIONS:
        for group in sorted(spans,key=lambda g:(g[4]!='newcomer',-g[3],g[0])):
            for start,end in reversed(spans[group]):
                seed=dict(seeds[group],event_action=action,query_layout='action_prewhere_v2')
                batch=make_batch(seed,group[3],start,end)
                batch['collection_reason']='explicit_action_sorting_key_continuation'
                batches.append(batch)
    if len({b['key'] for b in batches})!=len(batches):raise ValueError('Duplicate action scope')
    plan=dict(policy=POLICY,budget=DEFAULT_BUDGET,batches=batches,
        baseline_keys=[[str(r['repo_id']),int(r['issue_number'])] for r in checkpoint['unique']],
        initial_unique=checkpoint['unique'],continuation_of=str(previous.resolve()),
        sorting_key_evidence='data/midterm/sources/teacher_clickhouse/20261015T161532Z/events_storage.json',
        action_coverage=list(ISSUE_ACTIONS),
        limitations=['Action values are an explicit discovery scope, not proven exhaustive for the teacher table.',
            'Unknown/custom action values outside action_coverage remain unqueried; no complete-collection claim.',
            'Previously completed date ranges are excluded; overlapping failed scopes are merged locally.',
            'Query scan reduction must be measured live; all original per-query guards remain active.',
            'Metadata sorting key evidence was observed on 2026-10-15; no table changes are made.',
            'Discovered identities and historical labels are not current beginner recommendations.'])
    output.parent.mkdir(parents=True,exist_ok=True);save(output,plan)
    summary=dict(previous_pending=len(checkpoint['pending']),merged_spans=sum(map(len,spans.values())),
        action_scopes=len(ISSUE_ACTIONS),new_batches=len(batches),preserved_unique=len(checkpoint['unique']),
        remote_requests=0,remote_writes=0,readonly=True)
    save(output.with_name('preparation_summary.json'),summary)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    print(prepare(**vars(parser.parse_args())))
