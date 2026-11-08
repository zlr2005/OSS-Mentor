import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/midterm_a'))
import collect_historical_repository_context as module


class ContextTests(unittest.TestCase):
    def api(self, *, corrupt=False, future=False):
        text = json.dumps({'dependencies': {'typescript': '^5'}})
        data = text.encode()
        oid = hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()
        result = {'commit': {'oid': 'commit', 'committedDate': '2025-03-01T00:00:00Z',
                             'authoredDate': '2026-01-01T00:00:00Z' if future else '2025-03-01T00:00:00Z'},
                  'f0': {'oid': 'wrong' if corrupt else oid, 'byteSize': len(data), 'isBinary': False, 'text': text}}
        class API:
            def call(self, *, query):
                return {'data': {'repository': result}}, 'api_cache/test.json'
        return API()

    def test_pinned_manifest_preserves_scope_and_unknown_difficulty(self):
        with tempfile.TemporaryDirectory(dir=module.b.ROOT / 'tmp') as temp, patch.object(module.b, 'OUT', Path(temp)):
            record = module.collect(self.api(), ('owner/repo', '2025-04-01T00:00:00Z', 'commit'))
            self.assertEqual(record['manifest_ecosystems'], ['Node.js'])
            self.assertEqual(record['package_dependencies'][0]['name'], 'typescript')
            self.assertIsNone(record['primary_language'])
            self.assertIsNone(record['code_difficulty'])
            self.assertTrue((Path(temp)/record['documents'][0]['content_location']).exists())

    def test_rejects_corrupt_blob_and_future_author_date(self):
        with tempfile.TemporaryDirectory(dir=module.b.ROOT / 'tmp') as temp, patch.object(module.b, 'OUT', Path(temp)):
            for api in (self.api(corrupt=True), self.api(future=True)):
                with self.assertRaises(ValueError):
                    module.collect(api, ('owner/repo', '2025-04-01T00:00:00Z', 'commit'))


if __name__ == '__main__':
    unittest.main()
