"""固定原排序器，在截止时点全部已获证候选上作开发评价；不重新训练。"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import numpy as np
from pfirec_inspired_ranker import build,rows,sha,write,lgb,torch
from audit_v5_publication import time
from diagnose_pfirec_ranker import ci


def eligible_pool(candidates,cutoff,excluded):
    at=time(cutoff)
    return sorted(c['issue_key'] for c in candidates if c['issue_key'] not in excluded and time(c['available_from'])<=at<time(c['available_until']))


def rank_of(keys,scores,positive):
    if positive not in keys:return None
    i=keys.index(positive)
    return int(1+sum(s>=scores[i] for j,s in enumerate(scores) if j!=i))


def aggregate(records):
    by=defaultdict(list)
    for r in records:by[r['github_numeric_id']].append(1/r['rank'])
    return dict(outcomes=len(records),developers=len(by),developer_macro_mrr=statistics.mean(statistics.mean(v) for v in by.values()),
        mrr=statistics.mean(1/r['rank'] for r in records),recall5=statistics.mean(r['rank']<=5 for r in records),
        ndcg5=statistics.mean(1/np.log2(r['rank']+1) if r['rank']<=5 else 0 for r in records),
        recall20=statistics.mean(r['rank']<=20 for r in records),recall50=statistics.mean(r['rank']<=50 for r in records),
        recall100=statistics.mean(r['rank']<=100 for r in records),
        same_repo_mrr=statistics.mean(1/r['same_repo_rank'] for r in records),
        mean_same_repo_controls=statistics.mean(r['same_repo_candidates']-1 for r in records))


def run(root,models,dataset,features,profiles,output):
    torch.set_num_threads(1)
    old=json.loads((models/'protocol.json').read_text())
    assert all(sha(Path(p))==h for p,h in old['input_sha256'].items())
    allsamples=rows(dataset/'samples.jsonl');valid=[r for r in allsamples if r['split']=='validation']
    candidates_list=rows(dataset/'candidates.jsonl');candidates={r['issue_key']:r for r in candidates_list}
    lineage={r['profile_version']:r for r in rows(profiles/'lineage.jsonl')}
    with np.load(features/'text_features.npz',allow_pickle=False) as f:arrays={k:f[k].copy() for k in f.files}
    # Same known TRAIN positive exclusion as the original sample builder.
    base=root/'data/midterm/working/rc1_training_inputs_20261106_v1'
    original_profiles={r['profile_version']:r for r in rows(base/'profiles.jsonl') if r['split']=='train'}
    labels={r['outcome_id']:r for r in rows(base/'advanced_issue_outcomes.jsonl')}
    actual=json.loads((root/'data/midterm/working/patch_semantics_v6_runs_20261107_v2/outcome_availability.json').read_text())
    known=defaultdict(set)
    for r in actual['train']:known[original_profiles[r['profile_version']]['github_numeric_id']].add(labels[r['outcome_id']]['issue_key'])
    queries={r['profile_version']:r for r in valid}
    plans={v:eligible_pool(candidates_list,r['as_of'],known[r['github_numeric_id']]) for v,r in queries.items()}
    covered=[r for r in valid if r['positive_issue_key'] in plans[r['profile_version']]]
    if len(covered)!=len(valid):raise ValueError('Target not eligible; do not force positive into candidate pool')
    have=set(arrays['issue_keys'].astype(str))
    if any(k not in have for keys in plans.values() for k in keys):raise ValueError('Missing candidate embeddings; encode before evaluation')
    output.mkdir(parents=True,exist_ok=False)
    hashes={str(p):sha(p) for p in [models/'protocol.json',dataset/'samples.jsonl',dataset/'candidates.jsonl',features/'text_features.npz',profiles/'lineage.jsonl',Path(__file__)]}
    for p in models.glob('*/model.txt'):hashes[str(p)]=sha(p)
    protocol=dict(arms=['cosine']+old['arms'],seeds=old['seeds'],models_frozen=True,retuning=False,
        cohort='same209 development outcomes',candidate_rule='all admitted exact-cutoff historical snapshots, excluding known TRAIN positives; never inject target',
        pool_sizes=dict(min=min(map(len,plans.values())),max=max(map(len,plans.values())),mean=statistics.mean(map(len,plans.values()))),
        query_profiles=len(plans),feature_chunking='21-wide feature implementation reused exactly; final partial chunk padded and then discarded before scoring',
        input_sha256=hashes,evaluate_test=False,external_evaluation_read=False,independent_evaluation=False)
    write(output/'protocol.json',protocol);write(output/'candidate_plans.json',plans)
    # Exact original 21-candidate reproduction before widening the pool.
    oldx,oldcos,_=build(valid,arrays,lineage,candidates)
    for arm in old['arms']:
        for seed in old['seeds']:
            d=models/f'{arm}-{seed}';m=lgb.Booster(model_file=str(d/'model.txt'))
            delta=m.predict(oldx);score=10*oldcos.reshape(-1)+np.clip(delta,-1,1) if arm=='cosine_residual' else delta
            rank=(1+(score.reshape(-1,21)[:,1:]>=score.reshape(-1,21)[:,:1]).sum(1)).tolist()
            assert rank==[r['rank'] for r in json.loads((d/'ranks.json').read_text())]
    packs=[];slices={}
    for v,keys in plans.items():
        start=len(packs)*21
        for offset in range(0,len(keys),21):
            chunk=keys[offset:offset+21];chunk=chunk+[chunk[-1]]*(21-len(chunk))
            packs.append(dict(profile_version=v,positive_issue_key=chunk[0],comparison_issue_keys=chunk[1:]))
        slices[v]=(start,start+len(keys))
    matrix,cosine,_=build(packs,arrays,lineage,candidates)
    cosine=cosine.reshape(-1)
    records={}
    def score_records(scores):
        result=[]
        for r in valid:
            v=r['profile_version'];keys=plans[v];start,end=slices[v];values=scores[start:end]
            assert len(keys)==len(values) and len(keys)==len(set(keys)) and np.isfinite(values).all()
            repo=r['positive_issue_key'].split('#')[0]
            same=[i for i,k in enumerate(keys) if k.split('#')[0]==repo]
            result.append(dict(outcome_id=r['outcome_id'],profile_version=v,github_numeric_id=r['github_numeric_id'],
                rank=rank_of(keys,values,r['positive_issue_key']),pool_size=len(keys),
                same_repo_rank=rank_of([keys[i] for i in same],values[same],r['positive_issue_key']),same_repo_candidates=len(same)))
        return result
    records['cosine']=score_records(cosine);write(output/'cosine.json',records['cosine'])
    summaries={};paired={}
    for arm in old['arms']:
        metrics=[];differences=[]
        for seed in old['seeds']:
            m=lgb.Booster(model_file=str(models/f'{arm}-{seed}/model.txt'))
            delta=m.predict(matrix);score=10*cosine+np.clip(delta,-1,1) if arm=='cosine_residual' else delta
            result=score_records(score);records[arm,seed]=result;write(output/f'{arm}-{seed}.json',result)
            met=aggregate(result);metrics.append(met)
            for a,b in zip(result,records['cosine']):differences.append(dict(github_numeric_id=a['github_numeric_id'],delta=1/a['rank']-1/b['rank']))
            print(json.dumps(dict(arm=arm,seed=seed,metrics=met)),flush=True)
        summaries[arm]={k:statistics.mean(m[k] for m in metrics) for k in metrics[0]}
        paired[arm]=ci(differences)
    summary=dict(baseline=aggregate(records['cosine']),means=summaries,paired_macro_CI=paired,
        original21_reproduced=True,candidate_coverage=len(covered)/len(valid),
        caveat='Fixed development cohort and reconstructed admitted pool only, not all historical GitHub Issues or independent test; no model reselection.')
    write(output/'summary.json',summary)
    assert all(sha(Path(p))==h for p,h in hashes.items())
    print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','models','dataset','features','profiles','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.root,a.models,a.dataset,a.features,a.profiles,a.output)
