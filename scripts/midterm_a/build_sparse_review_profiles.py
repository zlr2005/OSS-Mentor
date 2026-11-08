"""Rebuild B-rule previews for paired supplementary outcomes; never freeze them."""
from collections import defaultdict
from pathlib import Path
from uuid import uuid5,NAMESPACE_URL
import json
import argparse
import backfill_adapter_2024_2026 as b
from oss_mentor.profiling.assessment import ENHANCED_FEATURE_EXPORT_SCHEMA_VERSION
from oss_mentor.profiling.delivery import validate_confirmed_feature_snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True, help='New, empty output directory')
    parser.add_argument('--observed-at', required=True, help='Fixed UTC observation timestamp')
    args = parser.parse_args()
    observed_at = b.iso(b.dt(args.observed_at))
    output = args.output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Output directory must be empty')
    base=b.OUT.parent;source=base/'sparse_expansion_20261104_handoff_v6'
    source_manifest=b.read(source/'source_manifest.json')
    roots=[base/'merged_ab_history_2024_2026_v6']
    roots += [b.ROOT/rel for rel in source_manifest['source_roots'].values()
              if (b.ROOT/rel/'historical_contributions.jsonl').exists()]
    history=defaultdict(dict)
    for root in roots:
        merged=(root/'file_contributions.jsonl').exists()
        mapping=b.read(root/'manifest.json').get('source_roots',{})
        for c in b.rows(root/('file_contributions.jsonl' if merged else 'historical_contributions.jsonl')):
            if not c.get('event_at') or not c.get('content_available',True):continue
            origin=root;record=c
            if merged:
                eligible=[s for s in c['sources'] if s.get('evidence_ref') and s.get('content_location')]
                if not eligible:continue
                record={**c,**eligible[0]};origin=b.ROOT/mapping[eligible[0]['source']]
            content=origin/record['content_location']
            if not content.exists() or b.digest(content.read_bytes())!=c['content_sha256']:continue
            key=(c['repository'].casefold(),c['commit_sha'],c['path'])
            history[c['github_numeric_id']].setdefault(key,dict(c,origin=origin.relative_to(b.ROOT).as_posix(),
                evidence_location=(origin/record['evidence_ref']).relative_to(b.ROOT).as_posix()))
    outcomes={r['outcome_id']:r for r in b.rows(source/'outcome_index.jsonl')}
    pairs=b.rows(source/'comparison_index.jsonl');groups={}
    for pair in pairs:
        row=outcomes[pair['outcome_id']];groups[row['github_numeric_id'],row['profile_as_of']]=row['developer_key']
    config=b.load_questionnaire(b.ROOT/'config/midterm_profile/questionnaire_v0.1.json')
    version_files=[b.ROOT/'src/oss_mentor/profiling/evidence.py',b.ROOT/'src/oss_mentor/profiling/assessment.py',
                   b.ROOT/'config/midterm_profile/questionnaire_v0.1.json',Path(__file__),
                   b.ROOT/'scripts/midterm_a/backfill_adapter_2024_2026.py',
                   b.ROOT/'src/oss_mentor/profiling/delivery.py',
                   b.ROOT/'src/oss_mentor/datasets/code_languages.py']
    version_dependencies={p.relative_to(b.ROOT).as_posix():b.digest(p.read_bytes()) for p in version_files}
    source_hash=b.digest((source/'manifest.json').read_bytes())
    input_hashes={r.relative_to(b.ROOT).as_posix():b.digest((r/'manifest.json').read_bytes()) for r in roots}
    profiles=[];batches=[];inputs=[]
    for (identity,as_of),login in sorted(groups.items()):
        cutoff=b.dt(as_of);records=[];documents=[]
        eligible=[c for c in history[identity].values() if b.dt(c['event_at'])<cutoff and
                  (not c.get('author_at') or b.dt(c['author_at'])<cutoff)]
        if not eligible:raise ValueError('Paired outcome has no attributable historical code')
        for c in sorted(eligible,key=lambda c:(c['repository'],c['commit_sha'],c['path'])):
            version='history-file:'+b.digest(b.canonical([c['repository'].casefold(),c['commit_sha'],c['path']]))
            records.append(dict(developer_key=login,repository_key=c['repository'].lower(),file_key=c['path'],
                file_version_key=version,commit_sha=c['commit_sha'],behavior_type='commit',author_link_status='verified',
                event_at=c['event_at'],evidence_location=c['evidence_location'],content_sha256=c['content_sha256']))
            documents.append(dict(file_version_key=version,repository_key=c['repository'].lower(),path=c['path'],available=True))
        batch=b.interpret_github_contributions(records,documents,developer_key=login,observed_at=observed_at,
            as_of=as_of,collection_status='partial',collection_limitations=[
                'Sampled historical code; independent first-public availability is not established.',
                'Current questionnaire and post-cutoff records excluded. Pending B/C review.'])
        grouped=defaultdict(list)
        for e in b._normalize_external_evidence(batch['evidence'],allowed_features=set(config['feature_names'])):
            grouped[e['feature_name']].append(e)
        fused=[b._fuse_feature(name,grouped[name]) for name in config['feature_names']]
        profile=dict(schema_version=ENHANCED_FEATURE_EXPORT_SCHEMA_VERSION,profile_state='research_preview',b_review_status='pending',training_ready=False,
            profile_key=str(uuid5(NAMESPACE_URL,f'github:{identity}')),github_numeric_id=identity,developer_key=login,
            as_of=as_of,profile_version=b.digest(b.canonical([identity,as_of,records,observed_at,source_hash,input_hashes,version_dependencies])),
            feature_schema_version=config['feature_schema_version'],feature_names=config['feature_names'],
            values=[r['proposed_value'] for r in fused],observed_mask=[r['observed'] for r in fused],
            locked_mask=[False]*len(fused),evidence_refs=[r['evidence_refs'] for r in fused],current_interests=[])
        assert all(v==0 for v,m in zip(profile['values'],profile['observed_mask']) if not m)
        profile['profile_hash']=b.digest(b.canonical(profile));profiles.append(profile)
        batches.append(dict(profile_key=profile['profile_key'],profile_version=profile['profile_version'],**batch))
        inputs.append(dict(github_numeric_id=identity,as_of=as_of,records=records))
    lookup={(p['github_numeric_id'],p['as_of']):p for p in profiles};joined=[]
    for pair in pairs:
        p=lookup[pair['github_numeric_id'],pair['as_of']]
        joined.append(dict(pair,profile_key=p['profile_key'],profile_version=p['profile_version'],
                           profile_hash=p['profile_hash'],profile_state='research_preview',training_ready=False))
    b.OUT=output;b.OUT.mkdir(parents=True,exist_ok=True)
    for name,data in [('profile_previews',profiles),('profile_evidence_batches',batches),('profile_input_index',inputs),('joined_samples',joined)]:
        b.lines(name+'.jsonl',data)
    summary=dict(profiles=len(profiles),joined_outcomes=len(joined),developers=len({p['github_numeric_id'] for p in profiles}),
        mean_observed_dimensions=sum(sum(p['observed_mask']) for p in profiles)/len(profiles),
        unique_model_inputs=len({b.canonical([p['values'],p['observed_mask']]) for p in profiles}),training_ready=False)
    b.save('summary.json',summary)
    for p in profiles:
        # Check the export's shape while retaining the real preview state.
        validate_confirmed_feature_snapshot({**p, 'profile_state': 'confirmed'})
        if p['feature_names'] != config['feature_names'] or any(len(p[k]) != len(config['feature_names']) for k in ('values','observed_mask','locked_mask','evidence_refs')):
            raise ValueError('Invalid feature alignment')
        if any(v != 0 for v,m in zip(p['values'],p['observed_mask']) if not m):
            raise ValueError('Unknown feature must be zero')
        if p['profile_state'] != 'research_preview' or p['training_ready'] is not False or p['b_review_status'] != 'pending':
            raise ValueError('Invalid preview state')
        if b.digest(b.canonical({k:v for k,v in p.items() if k != 'profile_hash'})) != p['profile_hash']:
            raise ValueError('Invalid profile hash')
    for row in joined:
        outcome=outcomes[row['outcome_id']]
        p=lookup[outcome['github_numeric_id'],outcome['profile_as_of']]
        if any(row[k] != p[k] for k in ('profile_key','profile_version','profile_hash')):
            raise ValueError('Invalid profile join')
    b.save('validation.json',dict(status='passed',checks=['export_shape_with_preview_state','feature_order_and_unknowns','profile_hash','profile_join','preview_state'],profiles=len(profiles),joined_outcomes=len(joined),training_ready=False))
    names=['profile_previews.jsonl','profile_evidence_batches.jsonl','profile_input_index.jsonl','joined_samples.jsonl','summary.json','validation.json']
    b.save('manifest.json',dict(source_manifest_sha256=source_hash,input_manifest_sha256=input_hashes,
        b_rule_sha256=version_dependencies,observed_at=observed_at,
        files_sha256={name:b.digest((b.OUT/name).read_bytes()) for name in names}))
    print(json.dumps(summary))


if __name__=='__main__':main()
