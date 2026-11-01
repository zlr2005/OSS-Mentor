"""Collect a bounded, resumable real contribution/code pilot for B; no Git writes."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from oss_mentor.collector.github_client import GitHubClient
from oss_mentor.datasets.github_history import CachedGitHubSource, collect_history, write_snapshot
from oss_mentor.datasets.snapshot import load_snapshot, validate_snapshot

REPOSITORIES = ('pytest-dev/pytest', 'pydantic/pydantic', 'Textualize/textual')


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())
    temporary.replace(path)


def metric(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


def describe(path):
    validation = validate_snapshot(path)
    manifest, records, _ = load_snapshot(path)
    developers = records['developers']
    contributions = records['contributions']
    documents = records['code_documents']
    return {
        'validation': validation['status'], 'fixture_only': manifest['fixture_only'],
        'dataset_version': manifest['dataset_version'],
        'time_coverage': manifest['time_coverage'], 'entity_counts': manifest['entity_counts'],
        'selection': manifest['source_provenance']['selection'],
        'quality': {
            'github_account_association': metric(sum(d['identity_status'] == 'verified' for d in developers), len(developers)),
            'contribution_author_association': metric(sum(c['author_association_status'] == 'verified' for c in contributions), len(contributions)),
            'missing_code': metric(sum(d['availability'] != 'available' for d in documents), len(documents)),
            'missing_code_reasons': dict(Counter(d['exclusion_reason'] for d in documents if d['availability'] != 'available')),
        },
        'manifest_sha256': hashlib.sha256((path / 'manifest.json').read_bytes()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--until', required=True)
    parser.add_argument('--allow-network', action='store_true')
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    plan = {'repositories': list(REPOSITORIES), 'until': args.until, 'commits_per_repository': 10,
            'python_files_per_commit': 2, 'fixture_only': False,
            'scope': 'Real contribution/code pilot; no weak-interest data, graph or model inference'}
    plan_path = root / 'collection_plan.json'
    if plan_path.exists() and json.loads(plan_path.read_text(encoding='utf-8')) != plan:
        raise ValueError('Existing collection plan differs; use a new directory')
    save(plan_path, plan)
    token = None
    if args.allow_network:
        credentials = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True, check=False)
        if credentials.returncode or not credentials.stdout.strip():
            raise RuntimeError('Existing gh credentials unavailable')
        token = credentials.stdout.strip()
    results = {}
    for repository in REPOSITORIES:
        name = repository.replace('/', '__')
        snapshot = root / 'snapshots' / name
        client = GitHubClient(api_base='https://api.github.com', api_version='2026-03-10',
                              user_agent='OSS-Mentor-B-real-pilot/1', token=token,
                              timeout_seconds=20, max_retries=2)
        save(root / 'runner_state.json', {'phase': 'collecting', 'repository': repository,
                                          'observed_at': datetime.now(timezone.utc).isoformat()})
        try:
            if not snapshot.exists():
                source = CachedGitHubSource(client, root / 'source_cache' / name, repository,
                                            allow_network=args.allow_network)
                manifest, records, assets = collect_history(source, until=args.until,
                                                            commit_limit=10, files_per_commit=2)
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                write_snapshot(snapshot, manifest, records, assets)
            results[repository] = {'status': 'completed', 'snapshot': str(snapshot.relative_to(root)),
                                   **describe(snapshot)}
        except Exception as error:
            results[repository] = {'status': 'failed', 'error_type': type(error).__name__,
                                   'http_status': getattr(error, 'status_code', None),
                                   'detail': str(error) if isinstance(error, ValueError) else None}
        save(root / 'progress.json', results)
        print(json.dumps({'repository': repository, **results[repository]}, ensure_ascii=False), flush=True)
    complete = [result for result in results.values() if result['status'] == 'completed']
    counts = Counter()
    for result in complete:
        counts.update(result['entity_counts'])
    summary = {'plan': plan, 'repositories_completed': len(complete), 'repositories_failed': len(results)-len(complete),
               'entity_counts_sum_across_snapshots': dict(counts),
               'count_note': 'Developer/file counts are summed per repository, not cross-repository unique counts.',
               'repositories': results}
    save(root / 'summary.json', summary)
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.relative_to(root).as_posix()
              for path in sorted((root / 'snapshots').rglob('*')) if path.is_file()]
    (root / 'SHA256SUMS').write_bytes(('\n'.join(hashes) + '\n').encode())
    save(root / 'runner_state.json', {'phase': 'finished' if len(complete) == len(REPOSITORIES) else 'finished_with_failures',
                                     'observed_at': datetime.now(timezone.utc).isoformat()})
    return 0 if len(complete) == len(REPOSITORIES) else 1


if __name__ == '__main__':
    raise SystemExit(main())
