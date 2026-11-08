"""Prepare backed-up production rechecks from a completed, versioned content review."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--source-directory', type=Path, required=True)
    args = parser.parse_args()
    target, source = args.directory, args.source_directory
    if (target / 'jobs.json').exists():
        parser.error('Follow-up queue already exists')
    summary = json.loads((target / 'review_summary.json').read_text(encoding='utf-8'))
    actual_hash = hashlib.sha256((ROOT / 'src/oss_mentor/task_features.py').read_bytes()).hexdigest()
    if summary['feature_code_sha256'] != actual_hash:
        parser.error('Rule source changed after review; regenerate the review before applying')
    decisions = {r['identity']: r for r in [json.loads(line) for line in
        (target / 'review_results.jsonl').read_text(encoding='utf-8').splitlines()]}
    originals = [j for j in json.loads((source / 'jobs.json').read_text(encoding='utf-8'))
                 if j['status'] == 'needs_task_review']
    identities = {f"{j['repository'].casefold()}#{j['issue_number']}" for j in originals}
    if identities != decisions.keys() or len(originals) != summary['reviewed_count']:
        parser.error('Review does not exactly cover the original review queue')
    passed = {key for key, r in decisions.items() if r['decision'] == 'passes_content_gate'}
    passed_repos = {key.rsplit('#', 1)[0] for key in passed}
    jobs = []
    for original in originals:
        identity = f"{original['repository'].casefold()}#{original['issue_number']}"
        jobs.append({'repository': original['repository'], 'issue_number': original['issue_number'],
            'github_issue_id': original['github_issue_id'], 'status': 'pending',
            'original_review_reasons': original.get('feature_review_reasons', []),
            'offline_review_decision': decisions[identity]['decision'],
            'review_input_content_sha256': decisions[identity]['content_sha256']})
    jobs.sort(key=lambda j: (j['original_review_reasons'] != ['task_type_requires_review'],
                            j['offline_review_decision'] != 'passes_content_gate',
                            j['repository'].casefold(), j['issue_number']))
    evidence = target / 'evidence'
    evidence.mkdir(exist_ok=False)
    inherited = 0
    for path in (source / 'evidence').glob('*.json'):
        entry = json.loads(path.read_text(encoding='utf-8'))
        parts = urlsplit(entry.get('url', '')).path.strip('/').split('/')
        if len(parts) < 3 or parts[0] != 'repos':
            continue
        repo = '/'.join(parts[1:3]).casefold()
        keep = len(parts) == 3 and repo not in passed_repos
        if len(parts) == 5 and parts[3] == 'issues' and parts[4].isdigit():
            identity = repo + '#' + str(int(parts[4]))
            keep = identity in identities and identity not in passed
        if not keep:
            continue
        try:
            os.link(path, evidence / path.name)
        except OSError:
            shutil.copyfile(path, evidence / path.name)
        inherited += 1
    # No timeline or PR cache is inherited. Every proposed newly admissible task
    # gets a fresh detail, relevant repository health, complete timeline and PR reads.
    for name in ['oss_mentor_web.sqlite3', 'oss_mentor.sqlite3']:
        connection = sqlite3.connect((ROOT / 'data' / name).resolve().as_uri() + '?mode=ro', uri=True)
        backup = sqlite3.connect(target / (name + '.before'))
        try:
            connection.backup(backup)
        finally:
            connection.close()
            backup.close()
    (target / 'jobs.json').write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'job_count': len(jobs),
        'source_review_directory': str(source.resolve()), 'content_review_summary': summary,
        'scope': 'Content rule reassessment and fresh availability checks for newly admissible tasks',
        'inherited_evidence_count': inherited, 'proposed_content_pass_count': len(passed),
        'cache_policy': 'Reuse unresolved frozen details only; newly admissible details, health, timelines and PRs are fetched anew.',
        'model_inference': False}
    (target / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'prepared': len(jobs), 'proposed_content_pass_count': len(passed), 'inherited_evidence_count': inherited}))


if __name__ == '__main__':
    main()
