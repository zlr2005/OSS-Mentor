"""固定分组诊断：不按结果选择样本，不重新拟合模型。"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
from pfirec_inspired_ranker import rows,sha,write
from diagnose_pfirec_ranker import ci


def summarize(records):
    if not records:return None
    by=defaultdict(list)
    for r in records:by[r['github_numeric_id']].append(1/r['rank'])
    nontrivial=[r for r in records if r['same_repo_candidates']>1]
    return dict(outcomes=len(records),developers=len(by),macro_mrr=statistics.mean(statistics.mean(v) for v in by.values()),
        mrr=statistics.mean(1/r['rank'] for r in records),recall5=statistics.mean(r['rank']<=5 for r in records),
        same_repo_nontrivial_outcomes=len(nontrivial),same_repo_nontrivial_mrr=statistics.mean(1/r['same_repo_rank'] for r in nontrivial) if nontrivial else None)


def run(expanded,dataset,profiles,output):
    protocol=json.loads((expanded/'protocol.json').read_text())
    assert all(sha(Path(p))==h for p,h in protocol['input_sha256'].items())
    allsamples=rows(dataset/'samples.jsonl');train=[r for r in allsamples if r['split']=='train'];valid=[r for r in allsamples if r['split']=='validation']
    train_users={r['github_numeric_id'] for r in train};repos={r['positive_issue_key'].split('#')[0] for r in train}
    pmap={p['profile_version']:p for p in rows(profiles/'profiles.jsonl')};lin={r['profile_version']:r for r in rows(profiles/'lineage.jsonl')}
    groups=defaultdict(set)
    for r in valid:
        groups['overall'].add(r['outcome_id'])
        for label,yes in [('no_public_train_outcome',r['github_numeric_id'] not in train_users),
                          ('new_positive_repository',r['positive_issue_key'].split('#')[0] not in repos),
                          ('target_repository_unseen_in_X',not any(k[0]==r['positive_issue_key'].split('#')[0] for k in lin[r['profile_version']]['retained_X'])),
                          ('low_profile_coverage',sum(pmap[r['profile_version']]['observed_mask'])<=3)]:
            if yes:groups[label].add(r['outcome_id'])
    baseline=json.loads((expanded/'cosine.json').read_text());result={}
    for label,ids in groups.items():
        base=[r for r in baseline if r['outcome_id'] in ids]
        arms={}
        for arm in ['from_scratch','cosine_residual']:
            metrics=[];delta=[]
            for seed in protocol['seeds']:
                ranked=[r for r in json.loads((expanded/f'{arm}-{seed}.json').read_text()) if r['outcome_id'] in ids]
                metrics.append(summarize(ranked));by={r['outcome_id']:r for r in base}
                delta.extend(dict(github_numeric_id=r['github_numeric_id'],delta=1/r['rank']-1/by[r['outcome_id']]['rank']) for r in ranked)
            means={k:statistics.mean(m[k] for m in metrics) for k in ('macro_mrr','mrr','recall5')}
            arms[arm]=dict(means=means,paired_macro_CI=ci(delta),same_repo_nontrivial_mrr=statistics.mean(m['same_repo_nontrivial_mrr'] for m in metrics) if metrics[0]['same_repo_nontrivial_mrr'] is not None else None)
        result[label]=dict(baseline=summarize(base),arms=arms)
    output.mkdir(parents=True,exist_ok=False);write(output/'groups.json',result)
    print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('expanded','dataset','profiles','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.expanded,a.dataset,a.profiles,a.output)
