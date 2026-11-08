"""固定树集成Top50＋本地TinyBERT零样本精排；不插入正例、不拟合验证。"""
import argparse
import json
import os
from pathlib import Path
import time
import numpy as np
from pfirec_inspired_ranker import rows,sha,write,build,lgb,torch
from evaluate_expanded_pool import aggregate


def reordered_pool(keys,retriever,reranker,k=50,rrf=False):
    top=sorted(range(len(keys)),key=lambda i:(-float(retriever[i]),keys[i]))[:k]
    if len(reranker)!=len(top):raise ValueError('Exactly the retrieved candidates must be scored')
    ce_order=sorted(range(len(top)),key=lambda j:(-float(reranker[j]),keys[top[j]]))
    if rrf:
        ranks={j:r+1 for r,j in enumerate(ce_order)}
        order=sorted(range(len(top)),key=lambda j:(-(1/(60+j+1)+1/(60+ranks[j])),keys[top[j]]))
    else:order=ce_order
    tail=sorted(set(range(len(keys)))-set(top),key=lambda i:(-float(retriever[i]),keys[i]))
    return [top[j] for j in order]+tail


def run(expanded,models,dataset,features,profiles,reranker,output):
    torch.set_num_threads(1)
    ep=json.loads((expanded/'protocol.json').read_text())
    assert all(sha(Path(p))==h for p,h in ep['input_sha256'].items())
    download=json.loads((reranker/'download_manifest.json').read_text())
    assert all(sha(reranker/n)==r['sha256'] for n,r in download['files'].items())
    plans=json.loads((expanded/'candidate_plans.json').read_text());samples=[r for r in rows(dataset/'samples.jsonl') if r['split']=='validation']
    lin={r['profile_version']:r for r in rows(profiles/'lineage.jsonl')};candidates={r['issue_key']:r for r in rows(dataset/'candidates.jsonl')}
    packs=[];slices={}
    for v,keys in plans.items():
        start=len(packs)*21
        for offset in range(0,len(keys),21):
            chunk=keys[offset:offset+21];chunk+=([chunk[-1]]*(21-len(chunk)))
            packs.append(dict(profile_version=v,positive_issue_key=chunk[0],comparison_issue_keys=chunk[1:]))
        slices[v]=start,start+len(keys)
    with np.load(features/'text_features.npz',allow_pickle=False) as f:a={k:f[k].copy() for k in f.files}
    matrix,_,_=build(packs,a,lin,candidates)
    raw=[lgb.Booster(model_file=str(models/f'from_scratch-{seed}/model.txt')).predict(matrix) for seed in ep['seeds']]
    retrieval={}
    for v,(start,end) in slices.items():
        values=np.stack([s[start:end] for s in raw]);values=(values-values.mean(1,keepdims=True))/values.std(1,keepdims=True).clip(1e-8)
        retrieval[v]=values.mean(0)
    output.mkdir(parents=True,exist_ok=False)
    protocol=dict(retriever='frozen five-seed from_scratch LambdaMART, within-query z-score then mean',top_k=50,
        reranker=download['repository'],revision=download['revision'],training='none; zero shot pretrained English passage search model',
        arms=['tree_ensemble','zero_shot_ce_top50','rrf_equal_top50'],rrf_constant=60,rrf_weight='equal, fixed before scores',
        query_token_cap=96,pair_token_cap=384,score='raw classification logit, not success probability',
        selection='no model, weight, prompt or threshold tuning on these reranking results',
        outside_top50='original retriever tail preserved; no positive insertion',
        input_sha256={str(p):sha(p) for p in [expanded/'protocol.json',expanded/'candidate_plans.json',reranker/'download_manifest.json',Path(__file__)]},
        evaluate_test=False,external_evaluation_read=False,research_data_uploaded=False,independent_evaluation=False)
    write(output/'protocol.json',protocol)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    torch.set_num_threads(4)
    from transformers import AutoTokenizer,AutoModelForSequenceClassification
    tokenizer=AutoTokenizer.from_pretrained(reranker,local_files_only=True,trust_remote_code=False)
    model=AutoModelForSequenceClassification.from_pretrained(reranker,local_files_only=True,trust_remote_code=False);model.eval()
    selected=json.loads((features/'selected_files.json').read_text())
    root=Path(__file__).resolve().parents[2]
    documents=json.loads((root/'data/midterm/working/full_x_materials_20261107_v1/documents.json').read_text())
    pairs=[];lookup=[]
    for v,keys in plans.items():
        history=lin[v]['retained_X']
        repos=sorted({k[0] for k in history})[:3];paths=[k[2] for k in history][:6]
        snippets=[]
        for key in selected[v]:
            d=documents[key];path=Path(d['content_path'])
            if sha(path)!=d['content_sha256']:raise ValueError('History bytes changed')
            snippets.append(path.read_text(encoding='utf-8',errors='replace')[:180])
        query='Find an open-source issue matching prior contribution evidence. Repositories: '+', '.join(repos)+'. Files: '+', '.join(paths)+'. Evidence: '+' '.join(snippets)
        ids=tokenizer.encode(query,add_special_tokens=False)[:96];query=tokenizer.decode(ids,skip_special_tokens=True)
        top=sorted(range(len(keys)),key=lambda i:(-float(retrieval[v][i]),keys[i]))[:50]
        for i in top:
            c=candidates[keys[i]]
            passage='Repository: '+keys[i].split('#')[0]+'. Issue: '+c['title']+'\n'+c['body_text'][:8192]
            pairs.append((query,passage));lookup.append((v,i))
    write(output/'pair_plan.json',[dict(profile_version=v,issue_key=plans[v][i]) for v,i in lookup])
    scores=[];started=time.monotonic()
    with torch.inference_mode():
        for start in range(0,len(pairs),32):
            chunk=pairs[start:start+32]
            tokens=tokenizer([r[0] for r in chunk],[r[1] for r in chunk],padding=True,truncation=True,max_length=384,return_tensors='pt')
            out=model(**tokens).logits.reshape(-1)
            if not torch.isfinite(out).all():raise ValueError('Nonfinite reranker scores')
            scores.extend(out.tolist())
            if start%640==0:print(json.dumps(dict(scored=min(start+32,len(pairs)),total=len(pairs))),flush=True)
    by={v:[] for v in plans}
    for (v,i),score in zip(lookup,scores):by[v].append(score)
    np.save(output/'cross_encoder_logits.npy',np.asarray(scores,dtype=np.float32))
    orders={}
    for v,keys in plans.items():
        orders['tree_ensemble',v]=sorted(range(len(keys)),key=lambda i:(-float(retrieval[v][i]),keys[i]))
        orders['zero_shot_ce_top50',v]=reordered_pool(keys,retrieval[v],by[v])
        orders['rrf_equal_top50',v]=reordered_pool(keys,retrieval[v],by[v],rrf=True)
    summaries={};all_records={}
    for arm in protocol['arms']:
        result=[]
        for r in samples:
            v=r['profile_version'];keys=plans[v];order=orders[arm,v];ranked=[keys[i] for i in order]
            if r['positive_issue_key'] not in ranked:raise ValueError('Uncovered positive; do not inject it')
            repo=r['positive_issue_key'].split('#')[0];same=[k for k in ranked if k.split('#')[0]==repo]
            result.append(dict(outcome_id=r['outcome_id'],profile_version=v,github_numeric_id=r['github_numeric_id'],rank=ranked.index(r['positive_issue_key'])+1,
                pool_size=len(keys),same_repo_rank=same.index(r['positive_issue_key'])+1,same_repo_candidates=len(same)))
        write(output/(arm+'.json'),result);summaries[arm]=aggregate(result);all_records[arm]=result
    from diagnose_pfirec_ranker import ci
    paired={arm:ci([dict(github_numeric_id=a['github_numeric_id'],delta=1/a['rank']-1/b['rank']) for a,b in zip(all_records[arm],all_records['tree_ensemble'])]) for arm in protocol['arms'][1:]}
    summary=dict(means=summaries,paired_macro_CI_vs_ensemble=paired,pairs=len(pairs),seconds=time.monotonic()-started,
        no_prediction_time_labels_read=True,independent_evaluation=False,production_model_changed=False)
    write(output/'summary.json',summary)
    assert all(sha(Path(p))==h for p,h in protocol['input_sha256'].items())
    print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('expanded','models','dataset','features','profiles','reranker','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.expanded,a.models,a.dataset,a.features,a.profiles,a.reranker,a.output)
