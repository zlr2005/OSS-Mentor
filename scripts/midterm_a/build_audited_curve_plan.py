"""整份 X 全部获证才准入；隔离其余画像，生成按开发者嵌套抽样计划。"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path


def classify(plans,checks):
    by=defaultdict(list)
    for c in checks:by[c['profile_version']].append(c)
    admitted=[];quarantine=[]
    for p in plans:
        rows=by[p['profile_version']]
        okay=(p['expected_files']>0 and len(rows)==p['expected_files'] and
              len({r['document_key'] for r in rows})==p['expected_files'] and
              all(r['publication_passed'] for r in rows))
        (admitted if okay else quarantine).append(dict(profile_version=p['profile_version'],split=p['split'],
            expected_files=p['expected_files'],proven_files=sum(r['publication_passed'] for r in rows),
            reason='all_original_X_proven' if okay else 'whole_profile_quarantined_do_not_silently_drop_X'))
    return admitted,quarantine


def nested_users(users,seed):
    return sorted(set(users),key=lambda u:hashlib.sha256(f'{seed}:{u}'.encode()).hexdigest())


def run(root,materials,proofs,output):
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    admitted,quarantine=classify(read(materials/'profiles.json'),read(proofs/'checks.json'))
    okay={p['profile_version'] for p in admitted}
    profiles={}
    with (root/'data/midterm/working/rc1_training_inputs_20261106_v1/profiles.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            p=json.loads(line)
            if p['split'] in ('train','validation'):profiles[p['profile_version']]=p
    actual=read(root/'data/midterm/working/patch_semantics_v6_runs_20261107_v2/outcome_availability.json')
    train=[r for r in actual['train'] if r['profile_version'] in okay]
    valid=[r for r in actual['validation'] if r['profile_version'] in okay]
    users={profiles[r['profile_version']]['github_numeric_id'] for r in train}
    assignments=[]
    for seed in range(20261102,20261107):
        ordered=nested_users(users,seed)
        for fraction in (.25,.5,.75,1.):
            selected=set(ordered[:math.ceil(len(ordered)*fraction)])
            rows=[r for r in train if profiles[r['profile_version']]['github_numeric_id'] in selected]
            assignments.append(dict(seed=seed,fraction=fraction,users=len(selected),outcome_ids=[r['outcome_id'] for r in rows],profile_versions=sorted({r['profile_version'] for r in rows})))
    output.mkdir(parents=True,exist_ok=False)
    for n,v in [('admitted_profiles.json',admitted),('quarantined_profiles.json',quarantine),('curve_assignments.json',assignments),('fixed_validation.json',valid)]:
        (output/n).write_text(json.dumps(v,indent=2),encoding='utf-8')
    plan=dict(status='admission_and_sampling_ready_not_trained',admitted_profiles=len(admitted),quarantined_profiles=len(quarantine),
        admitted_actual_train_outcomes=len(train),admitted_actual_validation_outcomes=len(valid),train_developers=len(users),
        fractions=[.25,.5,.75,1.],seeds=list(range(20261102,20261107)),sampling='nested developer groups; keep all admitted outcomes for selected developers',
        fixed_validation='same admitted validation set across all sizes and seeds; never fit PCA on validation',
        next_training='freshly initialize trainable RC1/v2/v3/content branches for each size; do not freeze old train-derived weights',
        prerequisites=['audit pretrained coder bundle and historical issue candidate visibility separately','encode/rebuild audited profile/candidate version','deduplicate user-Issue supervision under a new version','lock clean model recipe and complete training/evaluation'],
        training_ready=False,learning_curve_metrics_available=False,
        reason='original frozen pretrained/graph/candidate source visibility not fully audited; no false full-model learning curve from unsafe frozen features',
        input_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [materials/'profiles.json',proofs/'checks.json']})
    (output/'plan.json').write_text(json.dumps(plan,indent=2))
    print(json.dumps({k:v for k,v in plan.items() if k!='input_hashes'}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','materials','proofs','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.root,a.materials,a.proofs,a.output)
