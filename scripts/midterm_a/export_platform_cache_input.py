"""Export frozen candidate text for C's offline cache, without changing eligibility."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def export(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Choose a new independent output directory')
    raw = (source / 'manifest.json').read_bytes()
    if sha(raw) != (source / 'manifest.sha256').read_text().split()[0]:
        raise ValueError('Source manifest checksum mismatch')
    manifest = json.loads(raw)
    assets = set()
    for item in manifest['files']:
        path = (source / item['path']).resolve()
        if not path.is_relative_to(source) or item['path'] in assets:
            raise ValueError('Unsafe or duplicate asset path')
        assets.add(item['path'])
        data = path.read_bytes()
        if len(data) != item['size_bytes'] or sha(data) != item['sha256']:
            raise ValueError('Source asset checksum mismatch')

    def table(name):
        rows = []
        for relative in manifest['table_files'][name]:
            if relative not in assets:
                raise ValueError('Unverified table asset')
            rows.extend(json.loads(line) for line in (source / relative).read_text(encoding='utf-8').splitlines())
        if len(rows) != manifest['table_counts'][name]:
            raise ValueError('Source table count mismatch')
        return rows

    repositories = table('repository')
    repos = {row['repository_id']: row for row in repositories}
    if len(repos) != len(repositories):
        raise ValueError('Duplicate repository ID')
    rows = table('task_candidate')
    keys, ids, output_rows = set(), set(), []
    for row in rows:
        repo = repos[row['repository_id']]
        key = repo['full_name'].strip().casefold() + '#' + str(int(row['issue_number']))
        if key in keys or row['task_candidate_id'] in ids:
            raise ValueError('Duplicate Issue key or task ID')
        keys.add(key); ids.add(row['task_candidate_id'])
        title, body = str(row.get('title') or ''), str(row.get('body_text') or '')
        output_rows.append({
            'issue_key': key, 'task_candidate_id': row['task_candidate_id'],
            'github_issue_id': row['github_issue_id'], 'repository': repo['full_name'],
            'github_repository_id': repo['github_repository_id'], 'issue_number': row['issue_number'],
            'title': title, 'body_text': body,
            'text_sha256': sha(canonical({'title': title, 'body_text': body}).encode('utf-8')),
            'candidate_eligibility': row['candidate_eligibility'],
            'candidate_availability': row['candidate_availability'],
            'github_verified_at': row['github_verified_at'],
            'verification_expires_at': row['verification_expires_at'],
            'input_scope': 'offline_frozen_text_only_not_current_recommendation_approval',
        })
    output.mkdir(parents=True)
    data = ''.join(canonical(r) + '\n' for r in output_rows).encode('utf-8')
    (output / 'candidate_texts.jsonl').write_bytes(data)
    result = {
        'schema_version': 'a-platform-cache-input-v1',
        'source_version': manifest['dataset_version'], 'source_manifest_sha256': sha(raw),
        'input_version': 'a-platform-cache-input-' + sha(data)[:16],
        'candidate_count': len(rows), 'repository_count': len(repos),
        'candidate_texts_sha256': sha(data),
        'text_hash_definition': 'SHA256 UTF-8 canonical JSON {body_text,title}; sorted keys, ensure_ascii=false, compact separators',
        'eligibility_policy': 'Copied without renewal; all rows for offline encoding, including excluded/expired rows',
        'embedding_status': 'not_generated', 'model_acceptance': 'pending_C_assets_and_D_integration',
    }
    metadata = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (output / 'manifest.json').write_bytes(metadata)
    (output / 'manifest.sha256').write_text(sha(metadata) + '  manifest.json\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.source, args.output), ensure_ascii=False, indent=2))
