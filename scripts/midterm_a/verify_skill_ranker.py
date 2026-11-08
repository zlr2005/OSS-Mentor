"""Audit real TRAIN controls and measure local cached-vector scoring latency."""
import argparse
import json
from pathlib import Path
import statistics
import time
import numpy as np
from skill_ranker_experiment import rows, sha, write, build, proxy, corpus_stats, lgb, torch
from evaluate_expanded_pool import eligible_pool


def run(output):
    base=Path('data/midterm/working'); dataset=base/'public_text_dataset_20261107_v2'
    protocol=json.loads((output/'protocol.json').read_text())
    for p,h in protocol['input_sha256'].items():
        if sha(Path(p))!=h: raise ValueError(f'Input hash changed: {p}')
    samples=rows(dataset/'samples.jsonl'); train=[r for r in samples if r['split']=='train']
    c={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')}
    known={}
    for r in train: known.setdefault(r['github_numeric_id'],set()).add(r['positive_issue_key'])
    hard={r['outcome_id']:r['comparison_issue_keys'] for r in json.loads((output/'hard_train_controls.json').read_text())}
    changed=0; same_before=0; same_after=0
    pools={}
    for r in train:
        keys=hard[r['outcome_id']]; v=r['profile_version']
        if v not in pools: pools[v]=set(eligible_pool(list(c.values()),r['as_of'],set()))
        assert len(keys)==len(set(keys))==20 and set(keys)<=pools[v]
        assert not set(keys)&known[r['github_numeric_id']]
        changed += len(set(keys)-set(r['comparison_issue_keys']))
        repo=r['positive_issue_key'].split('#')[0]
        same_before += sum(k.split('#')[0]==repo for k in r['comparison_issue_keys'])
        same_after += sum(k.split('#')[0]==repo for k in keys)
    torch.set_num_threads(1)
    lin={r['profile_version']:r for r in rows(base/'public_x_profiles_20261107_v1/lineage.jsonl')}
    with np.load(base/'public_text_features_20261107_v1/text_features.npz',allow_pickle=False) as f: a={k:f[k].copy() for k in f.files}
    plans=json.loads((base/'expanded_pool_20261107_v2/candidate_plans.json').read_text())
    # Hash-selected queries; no selection by model errors. Models remain loaded, vectors local.
    queries=sorted(plans,key=lambda v:sha_bytes(v))[:10]
    latency={}
    for arm in protocol['arms']:
        model=lgb.Booster(model_file=str(output/f'{arm}-20261102/model.txt'))
        elapsed=[]; predict_only=[]
        for v in queries:
            keys=plans[v]; packs=[]; started=time.perf_counter()
            for offset in range(0,len(keys),21):
                chunk=keys[offset:offset+21]; chunk += [chunk[-1]]*(21-len(chunk))
                packs.append(dict(profile_version=v,positive_issue_key=chunk[0],comparison_issue_keys=chunk[1:]))
            x,_,_=build(packs,a,lin,c)
            if 'skills' in arm:
                stats=corpus_stats(keys,c); extra=[]
                for pack in packs:
                    for k in [pack['positive_issue_key']]+pack['comparison_issue_keys']:
                        extra.append(proxy(lin[v]['retained_X'],c[k]['title'],c[k]['body_text'],stats))
                x=np.column_stack([x,np.asarray(extra,dtype=np.float32)])
            before=time.perf_counter(); scores=model.predict(x)[:len(keys)]
            np.argsort(-scores)
            finished=time.perf_counter()
            elapsed.append(1000*(finished-started)); predict_only.append(1000*(finished-before))
        latency[arm]=dict(queries=len(queries),single_seed_feature_and_rank_ms_median=statistics.median(elapsed),
                         single_seed_feature_and_rank_ms_p95=float(np.quantile(elapsed,.95)),
                         predict_and_sort_ms_median=statistics.median(predict_only))
    result=dict(input_hashes_verified=True,train_queries=len(train),controls_checked=20*len(train),
                replaced_controls=changed, same_repo_controls_before=same_before,same_repo_controls_after=same_after,
                all_controls_unique_cutoff_eligible_and_no_known_train_positive=True,
                latency=latency,latency_scope='offline CPU, preloaded one-seed model and embeddings; includes feature construction and sort; excludes API/network/embedding generation, not end-to-end service latency')
    write(output/'verification.json',result); print(json.dumps(result,indent=2))


def sha_bytes(value):
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output)
