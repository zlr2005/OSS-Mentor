"""校验预注册产物完整性，拒绝未锁预测或未完成90天观察的评估。"""
import argparse
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path


def maturity_ready(prediction_lock_at,now,horizon_days=90):
    if not prediction_lock_at:return False
    cutoff=datetime.fromisoformat(prediction_lock_at.replace('Z','+00:00'))
    if cutoff.tzinfo is None or now.tzinfo is None:raise ValueError('Timezone required')
    return now>=cutoff+timedelta(days=horizon_days)


def run(protocol_dir,candidates_dir):
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    p=read(protocol_dir/'protocol.json')
    if sha(protocol_dir/'protocol.json')!=(protocol_dir/'LOCK.sha256').read_text().strip():raise ValueError('Protocol lock changed')
    if sha(protocol_dir/'roster.json')!=p['roster']['sha256']:raise ValueError('Roster changed')
    if sha(protocol_dir/'excluded_user_issues.json')!=p['exclusion_manifest']['sha256']:raise ValueError('Exclusions changed')
    for name,h in p['artifact_sha256'].items():
        if sha(Path(name))!=h:raise ValueError('Locked model artifact changed')
    candidates=read(candidates_dir/'candidates.json')
    for c in candidates:
        if c['snapshot']['state']!='OPEN':raise ValueError('Non-open snapshot')
        if sha(Path(c['source_file']))!=c['source_sha256']:raise ValueError('Candidate evidence changed')
    reasons=[]
    if not p['research_comparison']['base_publication_audit_complete']:reasons.append('base_input_audit_and_clean_retraining_pending')
    if not p['prediction_lock_at']:reasons.append('current_profiles_and_predictions_not_locked')
    if not maturity_ready(p['prediction_lock_at'],datetime.now(timezone.utc)):reasons.append('90_day_observation_not_complete')
    result=dict(protocol_integrity=True,locked_artifacts=len(p['artifact_sha256']),roster=p['roster']['users'],candidate_snapshots=len(candidates),
                evaluation_ready=not reasons,reasons=reasons,future_outcomes_scored=False)
    (candidates_dir/'readiness.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--protocol',type=Path,required=True);p.add_argument('--candidates',type=Path,required=True)
    a=p.parse_args();run(a.protocol,a.candidates)
