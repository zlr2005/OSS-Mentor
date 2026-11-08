"""Merge saved teacher-ID-bound verification increments without remote access."""
import argparse
import gzip
import json
from pathlib import Path
from datetime import datetime, timezone
from screen_unified_teacher import CLAIM


def assemble(directory):
    evidence = json.loads(gzip.decompress((directory/'previous_evidence.json.gz').read_bytes()))
    rows = json.loads((directory/'source_issues.json').read_text('utf-8'))
    sources = {f"{r['repo_name']}#{r['issue_number']}".casefold(): r for r in rows}
    assert len(sources) == len(rows)
    assert not json.loads((directory/'source_identity_conflicts.json').read_text('utf-8'))
    targets = {f'{r[0]}#{r[1]}'.casefold(): r for r in json.loads((directory/'new_targets.json').read_text('utf-8'))}
    covered = set()
    for path in sorted(directory.glob('new_details_*.json')):
        update = json.loads(path.read_text('utf-8'))
        evidence['listing_audit'].extend(update['listing_audit'])
        for key, env in update['details'].items():
            assert key in targets and str(env['payload']['id']) == targets[key][2]
            evidence['details'][key] = env
            evidence['failures'].pop(key, None)
            covered.add(key)
        for key, failure in update['failures'].items():
            assert key in targets
            evidence['failures'][key] = failure
            evidence['details'].pop(key, None)
            covered.add(key)
    assert covered == set(targets), 'Missing repository-level verification batch'
    direct_keys = set()
    for path in sorted(directory.glob('direct_batch_*.json')):
        for record in json.loads(path.read_text('utf-8')):
            key = record['key']; assert key in targets and key not in direct_keys
            direct_keys.add(key)
            if 'envelope' in record:
                assert str(record['envelope']['payload']['id']) == targets[key][2]
                evidence['details'][key] = record['envelope']
                evidence['failures'].pop(key, None)
            else:
                evidence['details'].pop(key, None)
                evidence['failures'][key] = record['failure']
    assert direct_keys == {q['key'] for q in json.loads((directory/'direct_queue.json').read_text('utf-8'))}, 'Direct verification unfinished'
    for pattern in ('timeline_batch_*.json', 'timeline_late_*.json'):
        for path in sorted(directory.glob(pattern)):
            for record in json.loads(path.read_text('utf-8')):
                assert record['key'] in sources
                evidence['timelines'][record['key']] = record['timeline']
    for pattern in ('pr_early_*.json', 'pr_late_*.json'):
        for path in sorted(directory.glob(pattern)):
            for record in json.loads(path.read_text('utf-8')):
                if 'payload' in record:
                    evidence['pull_requests'][record['url']] = record['payload']
    # A complete body can establish a conservative negative without a timeline.
    # Persist only source-bound spans; this never approves a candidate.
    for key in targets:
        env = evidence['details'].get(key)
        if not env: continue
        body = env['payload'].get('body') or ''
        matches = list(CLAIM.finditer(body))
        if matches:
            env['projected_rejection'] = dict(reason='claim_or_work_in_progress_text',claim_signals=[
                dict(url=env['payload']['html_url'],text=body[max(0,m.start()-80):m.end()+180]) for m in matches])
    evidence['observed_end'] = datetime.now(timezone.utc).isoformat()
    evidence['incremental_scope'] = dict(source_round_cutoff=14,new_teacher_identities=len(targets),
        direct_followups=len(direct_keys),active_round15_excluded=True,old_observation_times_preserved=True)
    output = directory/'connector_evidence.json.gz'
    with output.open('xb') as stream:
        stream.write(gzip.compress(json.dumps(evidence,ensure_ascii=False).encode()))
    return evidence['incremental_scope']


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    print(json.dumps(assemble(parser.parse_args().directory)))
