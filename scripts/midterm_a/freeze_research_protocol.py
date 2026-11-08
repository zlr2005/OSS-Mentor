"""锁定研究权重、前瞻评价协议与固定开发者名册；不读取未来标签。"""
import argparse
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def run(root,assets,output):
    output.mkdir(parents=True,exist_ok=False)
    now=datetime.now(timezone.utc)
    files={}
    seeds=list(range(20261102,20261107))
    for seed in seeds:
        for p in [root/f'data/midterm/working/patch_semantics_v6_runs_20261107_v2/content-{seed}/weights.pt',
                  assets/f'runs/bottleneck-diagnostics-20261106-v1/original-{seed}/weights.pt',
                  assets/f'runs/history-fusion-v2-20261106-v1/learned_behavior-{seed}/weights.pt',
                  assets/f'runs/transfer-cold-v3-20261107-v1/cosine-{seed}/weights.pt']:
            files[str(p.resolve())]=sha(p)
    for p in [root/'scripts/midterm_a/patch_semantics_v6.py',root/'data/midterm/working/patch_semantics_v6_runs_20261107_v2/pca.json',root/'data/midterm/working/patch_semantics_v6_20261107_v1/protocol.json']:
        files[str(p.resolve())]=sha(p)
    training_protocol=root/'data/midterm/working/patch_semantics_v6_runs_20261107_v2/protocol.json'
    source_protocol=json.loads(training_protocol.read_text())
    files[str(training_protocol.resolve())]=sha(training_protocol)
    for path,expected in source_protocol['dependency_hashes'].items():
        if sha(Path(path))!=expected:raise ValueError('Training dependency changed before lock')
        files[str(Path(path).resolve())]=expected
    for name,expected in source_protocol['input_hashes'].items():
        path=root/'data/midterm/working/rc1_training_inputs_20261106_v1'/name
        if sha(path)!=expected:raise ValueError('Original frozen input changed')
        files[str(path.resolve())]=expected
    bundle=assets/'encoding-assets/coder_bundle/data/models/gfibot-m0-v1.1/coder/coder-full-search8-v1'
    for name in ('weights.pt','embeddings.npz','model_manifest.json'):
        path=bundle/name;files[str(path.resolve())]=sha(path)
    profiles={}
    with (root/'data/midterm/working/rc1_training_inputs_20261106_v1/profiles.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            p=json.loads(line)
            if p['split'] not in ('train','validation'):continue
            profiles[p['profile_version']]=p
    availability=json.loads((root/'data/midterm/working/patch_semantics_v6_runs_20261107_v2/outcome_availability.json').read_text())
    train_users={profiles[r['profile_version']]['github_numeric_id'] for r in availability['train']}
    roster={}
    for p in profiles.values():
        uid=p['github_numeric_id']
        if uid in roster and roster[uid]['as_of']>=p['as_of']:continue
        roster[uid]={k:p[k] for k in ('github_numeric_id','developer_key','profile_version','as_of')}
        roster[uid]['no_original_training_outcomes']=uid not in train_users
        roster[uid]['profile_requires_current_refresh']=True
    roster=sorted(roster.values(),key=lambda r:r['github_numeric_id'])
    excluded=set();exclusion_sources={}
    for version in ('v3','v6'):
        dataset=root/('tmp/pr39-review/datasets/temporal_xy_2024_2026_v3' if version=='v3' else 'data/midterm/working/temporal_xy_2024_2026_v6')
        for split in ('train','validation','test'):
            path=dataset/(split+'.jsonl');exclusion_sources[str(path)]=sha(path)
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    g=json.loads(line)
                    for y in g['y']:excluded.add((g['github_numeric_id'],y['issue_key'].casefold()))
    path=root/'data/midterm/working/advanced_adapter_targeted_20261104_handoff/new_repository_test_ready.jsonl'
    exclusion_sources[str(path)]=sha(path)
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            r=json.loads(line);excluded.add((r['github_numeric_id'],r['issue_key'].casefold()))
    (output/'excluded_user_issues.json').write_text(json.dumps([dict(github_numeric_id=u,issue_key=i) for u,i in sorted(excluded)],indent=2))
    (output/'roster.json').write_text(json.dumps(roster,indent=2),encoding='utf-8')
    protocol=dict(version='prospective-research-v1',locked_at=now.isoformat(),
        status='preregistered_preparation_only',prediction_lock_at=None,observation_ends_at=None,
        research_comparison=dict(candidate='v6_content',baseline='v3_reference',seeds=seeds,
            ensemble='within each query, z-normalize candidate scores per seed (std floor 1e-8), average five scores; ties by canonical issue_key',
            retuning_allowed=False,base_publication_audit_complete=False,
            sanitized_retraining_requires_new_artifact_lock_before_enrollment=True),
        admission=dict(users='all roster users without selecting on future success; current profiles need refreshed cutoff-safe evidence',
            candidates='open, non-PR Issues verified public at prediction lock; title/body/labels/state and payload hashes frozen before outcomes',
            universe='fixed candidate pool, report outcome pool coverage; no later candidates inserted retrospectively',
            exclusions='exclude all known user-Issue labels and every previously used evaluation label before outcome scoring',
            independent_of='future outcome labels and rankings; known users are allowed but this is temporal independence, not user-disjoint evaluation'),
        evaluation=dict(horizon_days=90,primary='developer_macro_MRR',secondary=['outcome_MRR','Recall@5','NDCG@5','Recall@10','Recall@20'],
            candidate_ranking='all fixed admitted candidates per user, not newly sampled negatives',
            positives='verified resolver/closing PR identity with interaction after prediction lock and resolution within 90 days',
            comparisons='unresolved is unlabeled, never a true-negative or explicit rejection',
            groups=['no_original_training_outcomes','low_profile_coverage','new_repository'],
            uncertainty='paired developer-cluster bootstrap 4000 resamples; include zero-positive roster coverage separately',
            promotion=dict(final_evaluations=1,primary_paired_CI_lower_bound_gt=0,
                subgroup_MRR_noninferiority_margin=.02,each_subgroup_paired_CI_lower_bound_gte=-.02,
                missing_or_uninformative_group_interval='no promotion; do not infer safety from mean alone'),
            minimum_users=None,power_target='estimate from developer-level variance without examining new outcome labels; do not invent sufficiency threshold'),
        lifecycle=['audit and quarantine base inputs','retrain/freeze audited model artifacts','refresh profiles and candidates','freeze predictions and input hashes; set actual prediction_lock_at','wait full90days','collect verified outcomes and deduplicate','one final evaluation'],
        earliest_possible_maturity_if_locked_now=(now+timedelta(days=90)).isoformat(),
        maturity_is_not_started=True,future_labels_collected=False,old_test_scored=False,
        roster=dict(users=len(roster),no_original_training_outcomes=sum(r['no_original_training_outcomes'] for r in roster),sha256=sha(output/'roster.json')),
        exclusion_manifest=dict(labels=len(excluded),sha256=sha(output/'excluded_user_issues.json'),
            source_sha256=exclusion_sources,read_scope='source label identities for exclusion only; no prior ranking/metrics read or test scoring'),
        artifact_sha256=files)
    previous=[p for p in (root/'data/midterm/working').glob('prospective_protocol_20261107_v*/protocol.json') if p.parent.resolve()!=output.resolve()]
    prior=max(previous,key=str) if previous else None
    if prior:
        protocol['supersedes_before_enrollment']=dict(path=str(prior),sha256=sha(prior),reason='explicit subgroup noninferiority decision rule and known-label exclusion manifest; no future outcomes observed')
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    (output/'LOCK.sha256').write_text(sha(output/'protocol.json')+'\n')
    print(json.dumps(dict(status=protocol['status'],roster=protocol['roster'],locked_artifacts=len(files),maturity_started=False)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','assets','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.root,a.assets,a.output)
