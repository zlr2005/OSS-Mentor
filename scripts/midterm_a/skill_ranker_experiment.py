"""Four fixed-budget development arms: path/text proxies and TRAIN-only hard controls."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import re
import statistics
import time
import numpy as np
from pfirec_inspired_ranker import build, rows, sha, write, lgb, torch
from evaluate_expanded_pool import eligible_pool, rank_of
from train_public_text_curve import metrics
from diagnose_pfirec_ranker import ci

LANG = {'python': ('.py',), 'java': ('.java',), 'javascript': ('.js', '.jsx', '.mjs'),
        'typescript': ('.ts', '.tsx'), 'rust': ('.rs',), 'go': ('.go',),
        'csharp': ('.cs',), 'cpp': ('.cpp', '.cc', '.h', '.hpp'), 'ruby': ('.rb',)}
TYPES = {'test': r'\b(test|tests|testing|pytest|junit|coverage)\b',
         'docs': r'\b(doc|docs|documentation|readme|typo)\b',
         'config': r'\b(config|configuration|yaml|toml|dependency|dependencies|build|ci)\b'}
NAMES = ['path_token_overlap', 'path_token_jaccard', 'path_bm25', 'identifier_overlap',
         'language_observed', 'language_overlap', 'language_mismatch',
         'test_history_fraction', 'test_match', 'docs_history_fraction', 'docs_match',
         'config_history_fraction', 'config_match']


def tokens(text):
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    return set(re.findall(r'[a-z][a-z0-9]{2,}', text.casefold())) - {'src', 'main', 'org', 'com', 'the', 'and', 'for'}


def proxy(history, title, body, stats):
    paths = sorted({h[2] for h in history})
    text = title+'\n'+body; lower = text.casefold()
    ht = tokens(' '.join(paths)); it = tokens(text); common = ht & it
    counts = Counter(re.findall(r'[a-z][a-z0-9]{2,}', re.sub(r'([a-z])([A-Z])', r'\1 \2', text).casefold()))
    df, n, avg = stats; length = sum(counts.values())
    bm25 = sum(math.log(1+(n-df.get(t, 0)+.5)/(df.get(t, 0)+.5))*counts[t]*2.2/
               (counts[t]+1.2*(.25+.75*length/max(avg, 1))) for t in sorted(common))
    stems = {Path(p).stem.casefold() for p in paths if len(Path(p).stem)>3}
    identifiers = set(re.findall(r'[a-z_][a-z0-9_]*', lower))
    hl = {lang for lang, suffixes in LANG.items() if any(Path(p).suffix.casefold() in suffixes for p in paths)}
    il = {lang for lang, suffixes in LANG.items() if re.search(r'\b'+lang+r'\b', lower) or any(re.search(re.escape(s)+r'\b', lower) for s in suffixes)}
    vals = [len(common)/max(len(ht), 1), len(common)/max(len(ht|it), 1), bm25,
            math.log1p(len(stems & identifiers)), float(bool(il)), len(hl & il)/max(len(il), 1), float(bool(il) and not bool(il & hl))]
    for pattern in TYPES.values():
        fraction = sum(bool(re.search(pattern, p.casefold().replace('/', ' ').replace('_', ' ').replace('.', ' '))) for p in paths)/max(len(paths), 1)
        vals += [fraction, fraction*bool(re.search(pattern, lower))]
    return vals


def corpus_stats(keys, candidates):
    df = Counter(); total = 0
    for k in keys:
        text = candidates[k]['title']+'\n'+candidates[k]['body_text']
        df.update(tokens(text)); total += len(re.findall(r'[a-z][a-z0-9]{2,}', text.casefold()))
    return df, len(keys), total/max(len(keys), 1)


def hard_controls(sample, keys, candidates, similarities, known):
    """Ten original controls plus ten cosine-hard controls; no evaluation label access."""
    positive = sample['positive_issue_key']; repo = positive.split('#')[0]
    allowed = set(keys)-known-{positive}
    original = [k for k in sample['comparison_issue_keys'] if k in allowed]
    chosen = original[:10]
    # Prioritize same-positive-repository confounders within TRAIN, then semantic similarity.
    ordered = sorted(allowed-set(chosen), key=lambda k: (k.split('#')[0] != repo, -similarities[k], k))
    chosen += ordered[:20-len(chosen)]
    if len(chosen) != 20 or len(set(chosen)) != 20:
        raise ValueError('Insufficient unique eligible TRAIN controls')
    return dict(sample, comparison_issue_keys=chosen)


def run(root, output):
    torch.set_num_threads(1)
    base = root/'data/midterm/working'
    dataset = base/'public_text_dataset_20261107_v2'
    features = base/'public_text_features_20261107_v1/text_features.npz'
    lineage_file = base/'public_x_profiles_20261107_v1/lineage.jsonl'
    expanded = base/'expanded_pool_20261107_v2'
    frozen = json.loads((expanded/'protocol.json').read_text())
    for p, h in frozen['input_sha256'].items():
        if sha(Path(p)) != h: raise ValueError(f'Frozen input changed: {p}')
    samples = rows(dataset/'samples.jsonl')
    train = [r for r in samples if r['split']=='train']; valid = [r for r in samples if r['split']=='validation']
    candidates = {r['issue_key']: r for r in rows(dataset/'candidates.jsonl')}
    lin = {r['profile_version']: r for r in rows(lineage_file)}
    with np.load(features, allow_pickle=False) as f: arrays = {k: f[k].copy() for k in f.files}
    plans = json.loads((expanded/'candidate_plans.json').read_text())
    hashes = {str(p):sha(p) for p in [dataset/'samples.jsonl', dataset/'candidates.jsonl', features, lineage_file,
               expanded/'candidate_plans.json', Path(__file__), Path(__file__).with_name('pfirec_inspired_ranker.py')]}
    output.mkdir(parents=True, exist_ok=False)
    protocol = dict(arms=['original','skills','hard','skills_hard'], seeds=list(range(20261102,20261107)),
        trees=120, checkpoint_selection=False, training_queries=len(train), validation_outcomes=len(valid),
        feature_names=NAMES, labels='TRAIN successes vs unlabeled controls, not verified negatives',
        hard_rule='10 original eligible controls plus10 same-repository-first cosine-hard controls; TRAIN positives excluded',
        feature_scope='cutoff retained paths and admitted issue title/body; no solution PR or current repository metadata',
        bm25_scope='DF and length from eligible candidates at each cutoff, no future corpus',
        independent_evaluation=False, evaluate_test=False, input_sha256=hashes)
    write(output/'protocol.json', protocol)
    known = defaultdict(set)
    for r in train: known[r['github_numeric_id']].add(r['positive_issue_key'])
    im = {str(k):i for i,k in enumerate(arrays['issue_keys'])}; pm = {str(k):i for i,k in enumerate(arrays['profile_versions'])}
    stats = {}; trainplans = {}; hard = []
    for r in train:
        v = r['profile_version']
        if v not in trainplans:
            trainplans[v] = eligible_pool(list(candidates.values()), r['as_of'], set())
        keys = trainplans[v]
        u = arrays['user'][pm[v], :384]; items = arrays['item'][[im[k] for k in keys]]
        sims = items@u/(np.maximum(np.linalg.norm(items, axis=1)*np.linalg.norm(u), 1e-8))
        hard.append(hard_controls(r, keys, candidates, dict(zip(keys,map(float,sims))), known[r['github_numeric_id']]))
    write(output/'hard_train_controls.json', [dict(outcome_id=r['outcome_id'], comparison_issue_keys=r['comparison_issue_keys']) for r in hard])
    def enriched(data):
        x, c, names = build(data, arrays, lin, candidates); extra = []
        for r in data:
            v = r['profile_version']
            if v not in stats: stats[v] = corpus_stats(trainplans[v] if v in trainplans else plans[v], candidates)
            for k in [r['positive_issue_key']]+r['comparison_issue_keys']:
                item = candidates[k]
                extra.append(proxy(lin[v]['retained_X'], item['title'], item['body_text'], stats[v]))
        return np.column_stack([x,np.asarray(extra,dtype=np.float32)]), names+NAMES
    packs = []; slices = {}
    for v, keys in plans.items():
        start = len(packs)*21
        for offset in range(0,len(keys),21):
            chunk = keys[offset:offset+21]; chunk += [chunk[-1]]*(21-len(chunk))
            packs.append(dict(profile_version=v, positive_issue_key=chunk[0], comparison_issue_keys=chunk[1:]))
        slices[v] = (start,start+len(keys))
    vx, names = enriched(packs); tx,_ = enriched(train); hx,_ = enriched(hard)
    if not all(np.isfinite(x).all() for x in [vx,tx,hx]): raise ValueError('Nonfinite feature matrix')
    counts = Counter(r['github_numeric_id'] for r in train)
    weights = np.repeat([1/counts[r['github_numeric_id']] for r in train],21)
    def ranking(scores):
        records=[]
        for r in valid:
            v=r['profile_version']; start,end=slices[v]; keys=plans[v]; s=scores[start:end]
            repo=r['positive_issue_key'].split('#')[0]; ids=[i for i,k in enumerate(keys) if k.split('#')[0]==repo]
            records.append(dict(outcome_id=r['outcome_id'],github_numeric_id=r['github_numeric_id'],
                rank=rank_of(keys,s,r['positive_issue_key']), same_rank=rank_of([keys[i] for i in ids],s[ids],r['positive_issue_key']),
                same_count=len(ids), novel=not any(h[0]==repo for h in lin[v]['retained_X'])))
        return records
    results=[]; rankings={}; timings=[]
    for arm in protocol['arms']:
        for seed in protocol['seeds']:
            use_skills='skills' in arm; x=hx if 'hard' in arm else tx
            columns=len(names) if use_skills else 16
            model=lgb.LGBMRanker(objective='lambdarank', n_estimators=120,learning_rate=.03,num_leaves=15,max_depth=4,
                min_child_samples=40,colsample_bytree=.9,reg_lambda=5,n_jobs=1,random_state=seed,verbosity=-1,deterministic=True,force_col_wise=True)
            started=time.perf_counter()
            model.fit(x[:,:columns],np.tile([1]+[0]*20,len(train)),group=[21]*len(train),sample_weight=weights)
            d=output/f'{arm}-{seed}'; d.mkdir(); model.booster_.save_model(str(d/'model.txt'))
            scores=model.booster_.predict(vx[:,:columns]); restored=lgb.Booster(model_file=str(d/'model.txt'))
            if not np.array_equal(scores,restored.predict(vx[:,:columns])): raise ValueError('Reload mismatch')
            ranked=ranking(scores); rankings[arm,seed]=ranked
            write(d/'ranks.json',ranked); write(d/'importance.json',dict(zip(names[:columns],map(float,model.feature_importances_))))
            def met(sub): return metrics([r['rank'] for r in sub],sub) if sub else None
            same=[r for r in ranked if r['same_count']>1]
            record=dict(arm=arm,seed=seed,overall=met(ranked), novel=met([r for r in ranked if r['novel']]),
                        same_repository_mrr=statistics.mean(1/r['same_rank'] for r in same), seconds=time.perf_counter()-started)
            results.append(record); write(output/'results.json',results); print(json.dumps(record),flush=True)
    summary={}
    for arm in protocol['arms']:
        rr=[r for r in results if r['arm']==arm]
        deltas=[]
        for seed in protocol['seeds']:
            for r,b in zip(rankings[arm,seed],rankings['original',seed]):
                deltas.append(dict(github_numeric_id=r['github_numeric_id'],delta=1/r['rank']-1/b['rank']))
        summary[arm]=dict(overall={k:statistics.mean(r['overall'][k] for r in rr) for k in ['developer_macro_mrr','mrr','recall5','ndcg5']},
            novel={k:statistics.mean(r['novel'][k] for r in rr) for k in ['developer_macro_mrr','mrr','recall5']},
            same_repository_mrr=statistics.mean(r['same_repository_mrr'] for r in rr), paired_developer_ci=ci(deltas))
    for p,h in hashes.items():
        if sha(Path(p))!=h: raise ValueError(f'Input changed: {p}')
    write(output/'summary.json',summary); print(json.dumps(summary,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path.cwd()); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); run(a.root,a.output)
