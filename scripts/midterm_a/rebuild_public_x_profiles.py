"""显式新版本重建部分已获证 X；保留父版本和删减清单，不改原画像。"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tmp/pr39-review/src'),str(ROOT/'tmp/pr39-review/scripts/midterm_a')]
from oss_mentor.profiling.temporal_training import build_temporal_training_profile,load_questionnaire_contract,validate_temporal_training_profile
from audit_v5_publication import time


def run(proofs,output):
    read=lambda p:[json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]
    dataset=ROOT/'tmp/pr39-review/datasets/temporal_xy_2024_2026_v3'
    index={(r['repository'],r['commit_sha'],r['path']):r for r in read(dataset/'file_contributions.jsonl')}
    groups={(g['github_numeric_id'],time(g['cutoff_at'])):g for s in ('train','validation') for g in read(dataset/(s+'.jsonl'))}
    roots={k:ROOT/v for k,v in json.loads((dataset/'source_manifest.json').read_text())['source_roots'].items()}
    original=[p for p in read(ROOT/'data/midterm/working/rc1_training_inputs_20261106_v1/profiles.jsonl') if p['split'] in ('train','validation')]
    allowed={(c['profile_version'],c['document_key']) for c in json.loads((proofs/'checks.json').read_text()) if c['publication_passed']}
    questionnaire=load_questionnaire_contract(ROOT/'config/midterm_profile/questionnaire_v0.1.json')
    output.mkdir(parents=True,exist_ok=False)
    profiles=[];lineage=[];failures=[]
    for old in original:
        g=groups[old['github_numeric_id'],time(old['as_of'])]
        selected=[];excluded=[]
        for key in g['x_contribution_keys']:
            dkey=hashlib.sha256(json.dumps(key).encode()).hexdigest()
            (selected if (old['profile_version'],dkey) in allowed else excluded).append(key)
        if not selected:
            failures.append(dict(parent_profile_version=old['profile_version'],reason='no_public_X'));continue
        fresh={k:g[k] for k in ('github_numeric_id','cutoff_at','split')};fresh['x_contribution_keys']=selected
        p,audit=build_temporal_training_profile(fresh,contributions_by_key=index,source_roots=roots,questionnaire=questionnaire,
            delivery_status='audited_X_research_not_training_ready',dataset_version='temporal-public-X-subset-20261107-v1')
        validate_temporal_training_profile(p)
        assert p['profile_version']!=old['profile_version']
        profiles.append(p)
        lineage.append(dict(parent_profile_version=old['profile_version'],profile_version=p['profile_version'],
                            split=p['split'],retained_X=selected,excluded_X=excluded,features_recomputed=True))
    for n,v in [('profiles.jsonl',profiles),('lineage.jsonl',lineage),('exclusions.jsonl',failures)]:
        (output/n).write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in v),encoding='utf-8')
    summary=dict(profiles=len(profiles),splits=dict(Counter(p['split'] for p in profiles)),excluded_profiles=len(failures),
        retained_references=sum(len(r['retained_X']) for r in lineage),excluded_references=sum(len(r['excluded_X']) for r in lineage),
        provenance='public evidence subset; every profile version and feature hash rebuilt; partial history explicitly represented',
        pretrained_graph_reused=False,training_ready=False,old_profiles_modified=False,
        proof_input_sha256=hashlib.sha256((proofs/'checks.json').read_bytes()).hexdigest(),source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--proofs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.proofs,a.output)
