"""Remove reported credential content from public B/C snapshots; no Git mutations."""
import hashlib
import json
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from oss_mentor.datasets.publication_secrets import contains_secret, redact


def write_json(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8'))


def write_rows(path, rows):
    path.write_bytes(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows).encode('utf-8'))


def run(root, kind):
    manifest_path = root / 'manifest.json'
    original = manifest_path.read_bytes()
    if hashlib.sha256(original).hexdigest() != (root / 'manifest.sha256').read_text().split()[0]:
        raise ValueError('Manifest checksum mismatch')
    manifest = json.loads(original)
    if manifest.get('publication_redaction'):
        raise ValueError('Already remediated; do not overwrite a published version again')
    changes = []
    if kind == 'B':
        table = root / 'tables/code_documents.jsonl'
        codes = [json.loads(s) for s in table.read_text(encoding='utf-8').splitlines()]
        removed = set()
        for row in codes:
            if row['availability'] != 'available':
                continue
            relative = row['content_location']
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError('Unsafe content path')
            if contains_secret(path.read_bytes()):
                removed.add(path)
                row.update(availability='missing', exclusion_reason='secret_redacted_publication',
                           content_location=None, publication_redacted=True)
                changes.append({'file_version_key': row['file_version_key'], 'action': 'content_withheld',
                                'source_content_sha256': row['content_hash']})
        write_rows(table, codes)
        for path in removed:
            path.unlink()
        index_path = root / 'tables/developer_contribution_index.jsonl'
        indices = [json.loads(s) for s in index_path.read_text(encoding='utf-8').splitlines()]
        contributions = [json.loads(s) for s in (root / 'tables/contributions.jsonl').read_text(encoding='utf-8').splitlines()]
        by_version = {r['file_version_key']: r for r in codes}
        by_developer = Counter(r['developer_key'] for r in contributions if by_version[r['file_version_key']]['availability'] == 'available')
        for row in indices:
            row['available_code_count'] = by_developer[row['developer_key']]
            row['missing_code_count'] = row['contribution_count'] - row['available_code_count']
        write_rows(index_path, indices)
        quality = json.loads((root / 'quality.json').read_text(encoding='utf-8'))
        quality['code_availability'] = dict(Counter(r['availability'] for r in codes))
        quality['missing_code_reasons'] = dict(Counter(r['exclusion_reason'] for r in codes if r['availability'] == 'missing'))
        quality['missing_code_rate'] = sum(r['availability'] == 'missing' for r in codes) / len(codes)
        manifest['quality'] = quality
        write_json(root / 'quality.json', quality)
    else:
        for relative in manifest['table_files']['task_candidate']:
            path = root / relative
            raw = path.read_bytes()
            if contains_secret(raw):
                rows = [json.loads(s) for s in raw.decode('utf-8').splitlines()]
                affected = [r['task_candidate_id'] for r in rows if contains_secret(json.dumps(r).encode())]
                path.write_bytes(redact(raw))
                changes.append({'file': relative, 'task_candidate_ids': affected, 'action': 'credential_substrings_redacted'})
    if not changes:
        raise ValueError('No reported pattern found')
    metadata = {'policy': 'withhold credential-bearing code; redact credential substrings in candidate text',
                'parent_dataset_version': manifest['dataset_version'],
                'parent_manifest_sha256': hashlib.sha256(original).hexdigest(),
                'changes': changes, 'credentials_validated_or_used': False,
                'historical_git_content_removed': False, 'provider_revocation_confirmed': False,
                'redacted_at': datetime.now(timezone.utc).isoformat()}
    write_json(root / 'publication_redaction.json', metadata)
    readme = root / 'README.md'
    readme.write_bytes(readme.read_bytes() + ('\n\n## Publication security update\n\n'
        'This security-redacted revision supersedes the original manifest. Credential-bearing code is withheld '
        'with secret_redacted_publication; candidate credential substrings are redacted. '
        'Read current counts and version from manifest.json. Source code hash/commit remain provenance, '
        'not proof that removed content is available. No credential was used or confirmed revoked. '
        'Git history and old databases/caches are not cleaned by this update.\n').encode())
    manifest['dataset_version'] += '-security-redacted-v1'
    manifest['publication_redaction'] = metadata
    paths = {item['path'] for item in manifest['files']}
    paths.add('publication_redaction.json')
    manifest['files'] = [{'path': p, 'size_bytes': (root / p).stat().st_size,
                          'sha256': hashlib.sha256((root / p).read_bytes()).hexdigest()}
                         for p in sorted(paths) if (root / p).is_file()]
    write_json(manifest_path, manifest)
    (root / 'manifest.sha256').write_bytes((hashlib.sha256(manifest_path.read_bytes()).hexdigest() + '  manifest.json\n').encode('utf-8'))
    print(json.dumps({'dataset': str(root), 'version': manifest['dataset_version'], 'affected_groups': len(changes)}))


if __name__ == '__main__':
    run(Path('datasets/b_real_contributions_v0.2'), 'B')
    run(Path('datasets/c_issue_candidates_v1'), 'C')
