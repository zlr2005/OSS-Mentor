import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from oss_mentor.datasets.c_encoder_input import Source, build, identity_row, load_documents


class EncoderInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.files = {}
        self.put('content/a.txt', b'a\r\nb\r\n')
        self.put('content/empty.txt', b'')
        self.projection('evidence/a.json', 'Alice', 12, committer={'id': 13, 'login': 'Other', 'type': 'User'})
        self.codes = [self.code('a', 'content/a.txt'), self.code('empty', 'content/empty.txt'),
                      {'file_version_key': 'missing', 'availability': 'missing', 'exclusion_reason': 'deleted'}]
        self.developers = [{'developer_key': 'opaque', 'github_numeric_id': 12,
                            'identity_status': 'verified', 'evidence_locations': ['evidence/a.json']}]
        self.freeze()

    def put(self, name, data):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.files[name] = {'path': name, 'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def projection(self, name, login, numeric, **fields):
        self.put(name, json.dumps({'fields': {'author': {'id': numeric, 'login': login, 'type': 'User'}, **fields},
                                  'observed_at': '2026-10-01T00:00:00Z', 'projection_not_raw': True}).encode())

    def code(self, key, path):
        return {'file_version_key': key, 'availability': 'available', 'content_location': path,
                'content_hash': self.files[path]['sha256']}

    def freeze(self):
        for name, rows in [('code_documents', self.codes), ('developers', self.developers)]:
            self.put('tables/' + name + '.jsonl', ''.join(json.dumps(r) + '\n' for r in rows).encode())
        data = json.dumps({'schema_version': 'b-profile-evidence-v1', 'fixture_only': False,
                           'dataset_version': 'real-test', 'files': list(self.files.values())}).encode()
        (self.source / 'manifest.json').write_bytes(data)
        (self.source / 'manifest.sha256').write_text(hashlib.sha256(data).hexdigest())

    def test_crlf_empty_and_missing_are_preserved(self):
        out = self.root / 'out'
        report = build(self.source, out)
        docs = list(load_documents(out, self.source, lambda **row: row))
        self.assertEqual(docs, [{'file_version_key': 'a', 'content': 'a\r\nb\r\n'},
                                {'file_version_key': 'empty', 'content': ''}])
        self.assertEqual(report['code_counts'], {'ready': 1, 'empty_content': 1, 'missing_code': 1})

    def test_id_match_does_not_substitute_committer(self):
        row = identity_row(Source(self.source), self.developers[0])
        self.assertEqual(row['candidate_developer_key'], 'alice')
        self.assertFalse(row['training_mapping_verified'])
        other = {**self.developers[0], 'github_numeric_id': 13}
        self.assertIsNone(identity_row(Source(self.source), other)['candidate_developer_key'])

    def test_conflicting_logins_are_unresolved(self):
        self.projection('evidence/b.json', 'Renamed', 12)
        self.developers[0]['evidence_locations'].append('evidence/b.json')
        self.freeze()
        row = identity_row(Source(self.source), self.developers[0])
        self.assertEqual(row['status'], 'conflicting_logins')
        self.assertIsNone(row['candidate_developer_key'])

    def test_tampered_code_and_index_fail_closed(self):
        out = self.root / 'out'
        build(self.source, out)
        (out / 'code_input_index.jsonl').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            list(load_documents(out, self.source, dict))
        (self.source / 'content/a.txt').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            build(self.source, self.root / 'other')

    def test_duplicate_keys_and_overwrite_are_rejected(self):
        self.codes.append(self.codes[0])
        self.freeze()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            build(self.source, self.root / 'out')
        with self.assertRaisesRegex(ValueError, 'new output'):
            build(self.source, self.source)

    def test_path_escape_is_rejected_even_when_listed(self):
        source = Source(self.source)
        source.assets['../outside'] = {'size_bytes': 1, 'sha256': 'x'}
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            source.read('../outside')

    def test_non_utf8_and_unconfirmed_identity_remain_unknown(self):
        self.put('content/a.txt', b'\xff')
        self.codes[0] = self.code('a', 'content/a.txt')
        self.developers[0]['identity_status'] = 'unconfirmed'
        self.freeze()
        out = self.root / 'out'
        report = build(self.source, out)
        self.assertEqual(report['code_counts']['non_utf8'], 1)
        rows = [json.loads(s) for s in (out / 'identity_crosswalk.jsonl').read_text().splitlines()]
        self.assertIsNone(rows[0]['candidate_developer_key'])
        self.assertEqual(len(list(load_documents(out, self.source, lambda **x: x))), 1)

    def test_same_login_for_different_numeric_ids_is_not_joined(self):
        self.projection('evidence/b.json', 'Alice', 99)
        self.developers.append({'developer_key': 'other', 'github_numeric_id': 99,
                                'identity_status': 'verified', 'evidence_locations': ['evidence/b.json']})
        self.freeze()
        report = build(self.source, self.root / 'out')
        self.assertEqual(report['identity_counts'], {'login_numeric_id_conflict': 2})


if __name__ == '__main__':
    unittest.main()
