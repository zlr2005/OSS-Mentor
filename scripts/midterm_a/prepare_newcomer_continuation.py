"""Create a separate daily plan from saved pending ranges; never edit the old run."""
import argparse
from pathlib import Path
from optimized_teacher_collection import read, save, make_batch, POLICY, DEFAULT_BUDGET


def continuation(previous, output):
    if output.exists():raise FileExistsError(output)
    status=read(previous/'status.json')
    if status['status'] not in ('paused_budget_unverified','stopped_unverified','finished_unverified'):
        raise ValueError('Previous run must be stopped')
    checkpoint=read(previous/'checkpoint.json')
    batches=[]
    for old in checkpoint['pending']:
        if not old.get('discovery_filter'):raise ValueError('Expected label-directed scope')
        for start in range(old['start_epoch'],old['end_epoch'],86400):
            seed=dict(old,query_layout='scope_prewhere_v1')
            batch=make_batch(seed,old['month'],start,min(start+86400,old['end_epoch']),old['key'])
            batch['collection_reason']='daily_continuation_after_cost_pause'
            batch['language']=old.get('language')
            batches.append(batch)
    # Stable sorting keeps the prior language/repository order within each day.
    batches.sort(key=lambda b:(b['discovery_filter']!='newcomer',-b['month'],-b['start_epoch']))
    if len({b['key'] for b in batches})!=len(batches):raise ValueError('Overlapping pending ranges')
    output.parent.mkdir(parents=True,exist_ok=True)
    plan=dict(policy=POLICY,budget=DEFAULT_BUDGET,batches=batches,
        baseline_keys=[[str(r['repo_id']),int(r['issue_number'])] for r in checkpoint['unique']],
        initial_unique=checkpoint['unique'],continuation_of=str(previous.resolve()),
        prior_completed_batches=len(checkpoint['batches']),
        limitations=['PREWHERE and daily scopes may reduce read cost; real cost is not yet verified.',
            'Original guards and shared traffic ledger remain active; cost_near_guard still pauses.',
            'Completed ranges are preserved in the old run and not repeated.',
            'Discovered identities still require current-state and beginner difficulty review.'])
    save(output,plan)
    return dict(batches=len(batches),preserved_completed=len(checkpoint['batches']),
                initial_unique=len(checkpoint['unique']),remote_requests=0)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    print(continuation(**vars(parser.parse_args())))
