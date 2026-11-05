import base64
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/midterm_a'))
import deepen_adapter_evidence as module


class DepthEvidenceTests(unittest.TestCase):
    def test_skip_known_future_and_wrong_author_commits(self):
        window = {'repository': 'o/r', 'login': 'user', 'github_numeric_id': 42,
                  'as_of': '2025-04-01T00:00:00Z', 'known_commits': ['known']}
        calls = []

        class API:
            def call(self, *, endpoint=None, query=None):
                calls.append(endpoint)
                if query:
                    return {'data': {'q0': {'nodes': []}}}, 'query.json'
                if '/commits?' in endpoint:
                    return ([{'sha': name} for name in ('known', 'future', 'wrong')]
                            if 'page=1' in endpoint else []), 'listing.json'
                name = endpoint.split('/commits/')[1].split('?')[0]
                return {'sha': name, 'author': {'id': 99 if name == 'wrong' else 42},
                        'commit': {'committer': {'date': '2025-04-02T00:00:00Z'},
                                   'author': {'date': '2025-03-01T00:00:00Z'}}}, 'detail.json'

        result = module.collect(API(), window)
        self.assertEqual(result['contributions'], [])
        self.assertEqual({r['reason'] for r in result['exclusions']},
                         {'author_identity_mismatch', 'outside_history_window'})
        self.assertFalse(any('/commits/known?' in (call or '') for call in calls))

    def test_reject_corrupted_blob(self):
        window = {'repository': 'o/r', 'login': 'user', 'github_numeric_id': 42,
                  'as_of': '2025-04-01T00:00:00Z', 'known_commits': []}

        class API:
            def call(self, *, endpoint=None, query=None):
                if '/commits?' in endpoint:
                    return [{'sha': 'commit'}], 'listing.json'
                if '/commits/' in endpoint:
                    return {'sha': 'commit', 'author': {'id': 42},
                            'commit': {'committer': {'date': '2025-03-01T00:00:00Z'},
                                       'author': {'date': '2025-03-01T00:00:00Z'}},
                            'files': [{'filename': 'a.py', 'sha': 'bad', 'status': 'modified'}]}, 'detail.json'
                return {'encoding': 'base64', 'size': 1,
                        'content': base64.b64encode(b'x').decode()}, 'blob.json'

        with self.assertRaisesRegex(ValueError, 'checksum'):
            module.collect(API(), window)


if __name__ == '__main__':
    unittest.main()
