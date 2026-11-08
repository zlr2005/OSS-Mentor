"""Prepare a local source handoff from a fixed main plus an explicit A allowlist.

Does not stage, commit, fetch, push, collect data or modify existing databases.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
MODULES = (
    'b_delivery', 'code_languages', 'connector_cache', 'gfibot', 'git_history',
    'git_objects', 'git_source_validation', 'github_history', 'identity_map',
    'issue_links', 'path_tree', 'source_validation',
)
SCRIPTS = (
    'collect_git_history', 'collect_github_history', 'import_gfibot',
    'enrich_issue_links', 'import_connector_reads', 'inspect_working_dataset',
    'audit_coder_resources', 'collect_b_real_pilot', 'collect_b_candidate_repositories',
    'repair_b_repository_coverage', 'export_b_delivery', 'upgrade_b_delivery',
    'verify_b_delivery', 'verify_b_real_pilot', 'b_delivery_reader',
    'import_b_delivery_011', 'prepare_source_delivery', 'verify_source_delivery',
)
TESTS = ('test_gfibot', 'test_git_history', 'test_github_history',
         'test_issue_links', 'test_b_delivery', 'test_dataset_pipeline')
ALLOWLIST = (
    *(f'src/oss_mentor/datasets/{name}.py' for name in MODULES),
    *(f'scripts/midterm_a/{name}.py' for name in SCRIPTS),
    *(f'tests/midterm_a/{name}.py' for name in TESTS),
    'tests/midterm_a/__init__.py',
    'docs/a_source_delivery_2026-11-01.md',
)
BASE_PATHS = ('src', 'tests', 'fixtures', 'config', 'db', 'scripts', 'docs',
              'web', 'pyproject.toml', 'README.md')


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', *args], cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base', default='18fd830a9cc343d70ef4a5e54204bcf4b58750a3')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT):
        parser.error('Choose a new output directory inside this workspace')
    base = git('rev-parse', '--verify', args.base + '^{commit}').decode().strip()
    selected = {}
    secret = re.compile(rb'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC )?PRIVATE KEY-----')
    for relative in ALLOWLIST:
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError('Missing or unsafe allowlist file: ' + relative)
        data = path.read_bytes()
        if secret.search(data):
            raise ValueError('Possible credential in allowlist file: ' + relative)
        selected[relative] = data
    archive = git('archive', '--format=tar', base, *BASE_PATHS)
    output.mkdir(parents=True)
    checkout = output / 'checkout'
    checkout.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            target = (checkout / member.name).resolve()
            if not target.is_relative_to(checkout):
                raise ValueError('Unsafe archive member')
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
            else:
                raise ValueError('Unsupported archive member')
    changes = []
    patch = []
    for relative, data in selected.items():
        target = checkout / relative
        before = target.read_bytes() if target.exists() else None
        changes.append({'path': relative, 'bytes': len(data),
                        'sha256': hashlib.sha256(data).hexdigest(),
                        'change': 'added' if before is None else 'unchanged' if before == data else 'modified'})
        if before != data:
            patch.extend(difflib.unified_diff(
                (before or b'').decode('utf-8').splitlines(keepends=True),
                data.decode('utf-8').splitlines(keepends=True),
                fromfile='a/' + relative if before is not None else '/dev/null',
                tofile='b/' + relative))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (output / 'a-source-changes.patch').write_bytes(''.join(patch).encode('utf-8'))
    files = []
    for path in sorted(checkout.rglob('*')):
        if path.is_file():
            data = path.read_bytes()
            files.append({'path': path.relative_to(checkout).as_posix(),
                          'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    manifest = {'schema_version': 'a-source-delivery-v1', 'base_commit': base,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'status': 'prepared_not_verified', 'network_collection': False,
                'overlay_files': changes, 'files': files,
                'excluded': ['local API/UI changes', 'candidate lifecycle migrations',
                             'raw data and credentials', 'model weights', 'working SQL plans'],
                'test_modules': list(TESTS)}
    (output / 'source-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'prepared', 'output': str(output), 'base_commit': base,
                      'overlay_count': len(changes), 'file_count': len(files)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
