"""Offline, immutable input indexes for C; never infer training-user identity."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


class Source:
    def __init__(self, root):
        self.root = Path(root).resolve()
        raw = (self.root / 'manifest.json').read_bytes()
        expected = (self.root / 'manifest.sha256').read_text(encoding='utf-8').split()[0]
        if digest(raw) != expected:
            raise ValueError('Source manifest checksum mismatch')
        self.manifest_hash = expected
        self.manifest = json.loads(raw)
        if self.manifest['schema_version'] != 'b-profile-evidence-v1' or self.manifest['fixture_only'] is not False:
            raise ValueError('Expected real B normalized contribution delivery')
        self.assets = {r['path']: r for r in self.manifest['files']}
        if len(self.assets) != len(self.manifest['files']):
            raise ValueError('Duplicate source asset path')
        self.cache = {}

    def read(self, relative):
        if relative not in self.assets:
            raise ValueError('Unlisted source asset: ' + relative)
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Unsafe source asset path')
        # Cache small metadata, not code bodies; content is rechecked by readers.
        if relative in self.cache:
            return self.cache[relative]
        data = path.read_bytes()
        asset = self.assets[relative]
        if len(data) != asset['size_bytes'] or digest(data) != asset['sha256']:
            raise ValueError('Source asset checksum mismatch: ' + relative)
        if relative.startswith('evidence/'):
            self.cache[relative] = data
        return data

    def table(self, name):
        return [json.loads(line) for line in self.read('tables/' + name + '.jsonl').decode('utf-8').splitlines()]


def identity_row(source, developer):
    observations = []
    numeric = developer.get('github_numeric_id')
    if developer.get('identity_status') == 'verified' and numeric is not None:
        for relative in developer.get('evidence_locations', []):
            projection = json.loads(source.read(relative))
            fields = projection.get('fields', {})
            # Never substitute the committer for the author. A direct user
            # projection is usable only if its numeric ID matches as well.
            for candidate in (fields.get('author'), fields):
                if not isinstance(candidate, dict) or str(candidate.get('id')) != str(numeric):
                    continue
                login = candidate.get('login')
                if isinstance(login, str) and login.strip():
                    observations.append({'login': login.strip().casefold(), 'type': candidate.get('type'),
                                         'source_location': relative, 'source_sha256': digest(source.read(relative)),
                                         'source_url': projection.get('source_url'),
                                         'observed_at': projection.get('observed_at'),
                                         'projection_not_raw': projection.get('projection_not_raw')})
    logins = {r['login'] for r in observations}
    types = {r['type'] for r in observations}
    if developer.get('identity_status') != 'verified' or numeric is None:
        status = 'identity_unconfirmed'
    elif not observations:
        status = 'login_not_in_delivered_evidence'
    elif len(logins) != 1:
        status = 'conflicting_logins'
    elif types != {'User'}:
        status = 'non_user_or_unknown_type'
    else:
        status = 'source_verified_login'
    return {'a_developer_key': developer['developer_key'], 'github_numeric_id': numeric,
            'identity_status': developer.get('identity_status'), 'status': status,
            'candidate_developer_key': next(iter(logins)) if status == 'source_verified_login' else None,
            'training_mapping_verified': False, 'observations': observations}


def build(source_root, output):
    source = Source(source_root)
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(source.root) or source.root.is_relative_to(output):
        raise ValueError('Choose a new output outside the source delivery')
    codes = source.table('code_documents')
    if len({r['file_version_key'] for r in codes}) != len(codes):
        raise ValueError('Duplicate file_version_key')
    counts = Counter()
    index = []
    for code in codes:
        row = dict(code)
        row['input_status'] = 'missing_code'
        row['encoding_failure_reason'] = code.get('exclusion_reason')
        if code['availability'] == 'available':
            data = source.read(code['content_location'])
            if digest(data) != code['content_hash']:
                raise ValueError('Code content_hash mismatch')
            try:
                text = data.decode('utf-8')
                row['input_status'] = 'ready' if text.splitlines() else 'empty_content'
                row['encoding_failure_reason'] = None if text.splitlines() else 'empty_content'
                row['has_crlf'] = b'\r\n' in data
                row['over_default_1920_lines'] = len(text.splitlines()) > 1920
            except UnicodeDecodeError:
                row['input_status'] = 'non_utf8'
                row['encoding_failure_reason'] = 'strict_utf8_decode_failed'
        counts[row['input_status']] += 1
        index.append(row)
    identities = [identity_row(source, r) for r in source.table('developers')]
    owners = {}
    for row in identities:
        key = row['candidate_developer_key']
        if key:
            owners.setdefault(key, set()).add(str(row['github_numeric_id']))
    for row in identities:
        key = row['candidate_developer_key']
        if key and len(owners[key]) != 1:
            row.update(status='login_numeric_id_conflict', candidate_developer_key=None)
    output.mkdir(parents=True)
    for name, records in [('code_input_index.jsonl', index), ('identity_crosswalk.jsonl', identities)]:
        (output / name).write_bytes(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in records).encode('utf-8'))
    shutil.copyfile(__file__, output / 'reader.py')
    manifest = {'schema_version': 'a-c-code-input-v1',
                'input_version': 'a-c-code-input-' + source.manifest_hash[:16],
                'source_version': source.manifest['dataset_version'],
                'source_manifest_sha256': source.manifest_hash,
                'code_counts': dict(counts),
                'identity_counts': dict(Counter(r['status'] for r in identities)),
                'time_policy': '2024-2026 code; NOT input to the old 2021 cutoff experiment',
                'training_identity_policy': 'Candidate logins require C mapping validation; no automatic identity assignment',
                'files': {p.name: digest(p.read_bytes()) for p in sorted(output.iterdir())},
                'acceptance': 'prepared_locally_pending_C_acceptance'}
    raw = encoded(manifest)
    (output / 'manifest.json').write_bytes(raw)
    (output / 'manifest.sha256').write_text(digest(raw) + '  manifest.json\n', encoding='utf-8')
    return manifest


def load_documents(input_root, source_root, document_factory):
    """Yield C CodeDocument objects, preserving UTF-8 bytes and CRLF.

    Includes empty available files so C records empty_content failure; missing
    code stays in code_input_index and is never represented by a fake vector.
    """
    root = Path(input_root).resolve()
    raw = (root / 'manifest.json').read_bytes()
    if digest(raw) != (root / 'manifest.sha256').read_text().split()[0]:
        raise ValueError('Input manifest checksum mismatch')
    manifest = json.loads(raw)
    for name, expected in manifest['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or digest(path.read_bytes()) != expected:
            raise ValueError('Input file checksum mismatch')
    source = Source(source_root)
    if source.manifest_hash != manifest['source_manifest_sha256']:
        raise ValueError('Wrong source delivery version')
    rows = [json.loads(line) for line in (root / 'code_input_index.jsonl').read_text(encoding='utf-8').splitlines()]
    for row in rows:
        if row['input_status'] in {'ready', 'empty_content'}:
            data = source.read(row['content_location'])
            if digest(data) != row['content_hash']:
                raise ValueError('Code content_hash mismatch')
            yield document_factory(file_version_key=row['file_version_key'], content=data.decode('utf-8'))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), ensure_ascii=False, indent=2))
