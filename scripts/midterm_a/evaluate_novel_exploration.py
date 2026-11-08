"""Fixed development diagnostic for hypothetical exploration; no intention labels or tuning."""
import argparse
import json
from pathlib import Path
import statistics
import numpy as np
import torch
from pfirec_inspired_ranker import rows, sha, write, build, lgb
from evaluate_expanded_pool import rank_of


def exploration_pool(keys, history):
    known = {entry[0].casefold() for entry in history}
    return [key for key in keys if key.split('#')[0].casefold() not in known]


def metrics(records):
    covered = [r for r in records if r['exploration_rank'] is not None]
    by = {}
    for r in covered:
        by.setdefault(r['github_numeric_id'], []).append(1 / r['exploration_rank'])
    return dict(outcomes=len(records), eligible_targets=len(covered),
                target_coverage=len(covered) / len(records),
                conditional_mrr=statistics.mean(1/r['exploration_rank'] for r in covered) if covered else None,
                conditional_macro_mrr=statistics.mean(map(statistics.mean, by.values())) if by else None,
                conditional_recall5=statistics.mean(r['exploration_rank'] <= 5 for r in covered) if covered else None,
                same_cohort_unfiltered_mrr=statistics.mean(1/r['original_rank'] for r in covered) if covered else None,
                same_cohort_unfiltered_recall5=statistics.mean(r['original_rank'] <= 5 for r in covered) if covered else None,
                all_outcome_recall5=sum(r['exploration_rank'] is not None and r['exploration_rank'] <= 5 for r in records)/len(records))


def run(expanded, output):
    old = json.loads((expanded/'protocol.json').read_text())
    for p, h in old['input_sha256'].items():
        if sha(Path(p)) != h:
            raise ValueError(f'Frozen input changed: {p}')
    paths = list(map(Path, old['input_sha256']))
    find = lambda name: next(p for p in paths if p.name == name)
    valid = [r for r in rows(find('samples.jsonl')) if r['split'] == 'validation']
    candidates = {r['issue_key']: r for r in rows(find('candidates.jsonl'))}
    lineage = {r['profile_version']: r for r in rows(find('lineage.jsonl'))}
    plans = json.loads((expanded/'candidate_plans.json').read_text())
    filtered = {v: exploration_pool(keys, lineage[v]['retained_X']) for v, keys in plans.items()}
    output.mkdir(parents=True, exist_ok=False)
    hashes = dict(old['input_sha256'])
    for p in [expanded/'candidate_plans.json', Path(__file__)]:
        hashes[str(p)] = sha(p)
    write(output/'protocol.json', dict(input_sha256=hashes, models_frozen=True, retuning=False,
          scenario='hypothetical new project exploration; actual historical intention unknown',
          rule='case-insensitive repository exclusion from cutoff retained_X; no target injection',
          independent_evaluation=False, evaluate_test=False, training_changed=False,
          warning='Conditional metrics on eligible targets cannot be compared to all209 metrics'))
    write(output/'candidate_plans.json', filtered)
    torch.set_num_threads(1)
    with np.load(find('text_features.npz'), allow_pickle=False) as f:
        arrays = {k: f[k].copy() for k in f.files}
    packs, slices = [], {}
    for v, keys in plans.items():
        start = len(packs)*21
        for offset in range(0, len(keys), 21):
            chunk = keys[offset:offset+21]
            chunk += [chunk[-1]]*(21-len(chunk))
            packs.append(dict(profile_version=v, positive_issue_key=chunk[0], comparison_issue_keys=chunk[1:]))
        slices[v] = (start, start+len(keys))
    matrix, cosine, _ = build(packs, arrays, lineage, candidates)
    cosine = cosine.reshape(-1)
    summary = {}
    for arm in old['arms']:
        seeds = [None] if arm == 'cosine' else old['seeds']
        arm_metrics = []
        for seed in seeds:
            if arm == 'cosine':
                scores = cosine
            else:
                model_path = next(p for p in paths if p.parent.name == f'{arm}-{seed}' and p.name == 'model.txt')
                delta = lgb.Booster(model_file=str(model_path)).predict(matrix)
                scores = 10*cosine+np.clip(delta, -1, 1) if arm == 'cosine_residual' else delta
            records = []
            for r in valid:
                v = r['profile_version']; keys = plans[v]
                start, end = slices[v]; values = scores[start:end]
                if not np.isfinite(values).all():
                    raise ValueError('Nonfinite scores')
                allowed = set(filtered[v])
                indices = [i for i, k in enumerate(keys) if k in allowed]
                records.append(dict(outcome_id=r['outcome_id'], github_numeric_id=r['github_numeric_id'],
                    profile_version=v, original_pool_size=len(keys), exploration_pool_size=len(indices),
                    original_rank=rank_of(keys, values, r['positive_issue_key']),
                    exploration_rank=rank_of(filtered[v], values[indices], r['positive_issue_key'])))
            name = arm if seed is None else f'{arm}-{seed}'
            previous = json.loads((expanded/f'{name}.json').read_text())
            if [r['original_rank'] for r in records] != [r['rank'] for r in previous]:
                raise ValueError('Original full-pool ranks not reproduced')
            write(output/f'{name}.json', records)
            arm_metrics.append(metrics(records))
        summary[arm] = {k: statistics.mean(m[k] for m in arm_metrics) if arm_metrics[0][k] is not None else None for k in arm_metrics[0]}
    summary['original_ranks_reproduced'] = True
    summary['query_pool_sizes'] = dict(min=min(map(len, filtered.values())), max=max(map(len, filtered.values())),
                                      mean=statistics.mean(map(len, filtered.values())))
    for p, h in hashes.items():
        if sha(Path(p)) != h:
            raise ValueError(f'Input changed during evaluation: {p}')
    write(output/'summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--expanded', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); run(a.expanded, a.output)
