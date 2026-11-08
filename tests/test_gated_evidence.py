import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from collect_gated_user_evidence import collect


class EvidenceTests(unittest.TestCase):
    def responses(self, author=7, blob_sha=None):
        content = b'print(1)\n'
        digest = hashlib.sha1(b'blob 9\0' + content).hexdigest()
        detail = dict(author={'id': author}, sha='a'*40,
                      commit={k: {'date': '2026-01-01T00:00:00Z'} for k in ['author','committer']},
                      files=[dict(filename='a.py',sha=blob_sha or digest,status='modified')])
        return [{'id':7,'login':'sample'},
                {'items':[{'repository':{'full_name':'sample/demo','private':False},'sha':'a'*40}]},
                detail, {'encoding':'base64','size':len(content),'content':base64.b64encode(content).decode()}]

    def run_collect(self, directory, responses):
        with patch('collect_gated_user_evidence.subprocess.run', side_effect=[
            SimpleNamespace(returncode=0,stdout=json.dumps(r).encode()) for r in responses]):
            collect('sample',directory)

    def test_verified_authored_file_is_bound_to_numeric_identity_and_blob(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'evidence'
            self.run_collect(output,self.responses())
            manifest=json.loads((output/'evidence_manifest.json').read_text())
            self.assertEqual(len(manifest['documents']),1)
            self.assertEqual(manifest['github_numeric_id'],7)
            self.assertFalse(manifest['features_ready'])
            self.assertFalse(manifest['history_complete'])

    def test_other_author_is_not_attributed_to_user(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'evidence'
            self.run_collect(output,self.responses(author=8)[:3])
            manifest=json.loads((output/'evidence_manifest.json').read_text())
            self.assertEqual(manifest['retained_X'],[])
            self.assertEqual(manifest['documents'],[])

    def test_bad_blob_does_not_publish_complete_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'evidence'
            with self.assertRaises(ValueError):
                self.run_collect(output,self.responses(blob_sha='0'*40))
            self.assertFalse((output/'evidence_manifest.json').exists())
