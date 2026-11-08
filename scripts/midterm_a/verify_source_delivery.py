"""Verify a prepared handoff's bytes and run offline tests in its own checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    checkout = root / 'checkout'
    manifest = json.loads((root / 'source-manifest.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        path = (checkout / item['path']).resolve()
        if not path.is_relative_to(checkout):
            raise ValueError('Unsafe manifest path')
        data = path.read_bytes()
        if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Source checksum mismatch: ' + item['path'])
    env = dict(os.environ)
    env['PYTHONPATH'] = str(checkout / 'src') + os.pathsep + str(checkout)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    temp = root / 'test-temp'
    temp.mkdir(exist_ok=True)
    env['TEMP'] = env['TMP'] = str(temp)
    for name in ('GITHUB_TOKEN', 'GH_TOKEN', 'GITHUB_OAUTH_CLIENT_SECRET', 'OSS_MENTOR_SESSION_SECRET'):
        env.pop(name, None)
    groups = [
        ['-m', 'unittest', *(f'tests.midterm_a.{name}' for name in manifest['test_modules']), '-v'],
        ['-m', 'unittest', 'tests.midterm_a.test_snapshot',
         'tests.midterm_a.test_dataset_store', 'tests.test_github_client', 'tests.test_raw_store', '-v'],
        ['scripts/ci_sqlite_migration_check.py'],
        ['scripts/ci_contract_fixture_check.py'],
    ]
    results = []
    for index, command in enumerate(groups, 1):
        result = subprocess.run([sys.executable, *command], cwd=checkout, env=env,
                                capture_output=True, text=True, errors='replace', timeout=300)
        log = root / f'verification-{index}.log'
        log.write_text(result.stdout + result.stderr, encoding='utf-8')
        results.append({'command': [sys.executable, *command], 'exit_code': result.returncode,
                        'log': log.name})
        print(json.dumps(results[-1]), flush=True)
    report = {'status': 'source_delivery_verified' if all(r['exit_code'] == 0 for r in results) else 'failed',
              'verified_at': datetime.now(timezone.utc).isoformat(),
              'python': sys.version, 'base_commit': manifest['base_commit'],
              'checksummed_files': len(manifest['files']), 'checks': results,
              'scope': 'Offline source handoff only; no real collection, training or end-to-end acceptance.'}
    (root / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'source_delivery_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
