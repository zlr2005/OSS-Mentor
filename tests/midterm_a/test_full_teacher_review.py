"""Full-inventory review must account for every source identity and fail closed."""
import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('full_review_expander', ROOT/'scripts/midterm_a/expand_full_review_evidence.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FullReviewTests(unittest.TestCase):
    def fixture(self, root, *, missing=False, wrong_id=False):
        source = root/'source.json'
        source.write_text(json.dumps([dict(repo_name='example/demo',repo_id='42',issue_number=n,issue_id=str(100+n))
                                      for n in (1,2)]), encoding='utf-8')
        columns = ['source_index','id','title','state','locked','assignees','labels','created_at','updated_at',
                   'closed_at','comments','author_association','repository_url','observed_at','method','is_pull_request']
        at = '2026-10-16T12:00:00Z'
        evidence = dict(expected_source_count=2,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                        source_batch='fixture_only', observed_start=at, observed_end=at, detail_columns=columns,
                        detail_rows=[[0,'999' if wrong_id else '101','Closed task','closed',False,[],[],at,at,at,0,'NONE',
                                      'https://api.github.com/repos/example/demo',at,'github_issue_detail',False]],
                        failure_rows=[] if missing else [[1,'NOT_FOUND',at]], bodies={},blocked_indices=[],open_reviews={},
                        repository_details={},timelines={},pull_requests={},mismatches={},listing_audit=[])
        compressor = zlib.compressobj(wbits=-15)
        packed = compressor.compress(json.dumps(evidence).encode()) + compressor.flush()
        (root/'full_evidence.deflate.base64').write_text(base64.b64encode(packed).decode(), encoding='ascii')
        return source

    def test_not_found_is_unverified_and_all_rows_are_accounted_for(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = module.expand(root, self.fixture(root))
            self.assertEqual(result['attempted'],2)
            self.assertEqual(result['not_checked'],0)
            self.assertEqual(result['decisions'],{'excluded':1,'unverified':1})
            pack = json.loads(gzip.decompress(base64.b64decode((root/'review_pack.base64').read_text())))
            self.assertEqual(pack['decisions'][1]['reason'],'NOT_FOUND')
            self.assertIsNone(pack['decisions'][1]['github_issue_id'])
            self.assertIsInstance(pack['issue_details']['example/demo#1']['id'], int)

    def test_missing_status_is_not_reported_as_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError,'not checked'):
                module.expand(root,self.fixture(root,missing=True))
            self.assertFalse((root/'review_pack.base64').exists())

    def test_wrong_identity_and_changed_source_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.fixture(root,wrong_id=True)
            with self.assertRaisesRegex(ValueError,'Identity mismatch'):
                module.expand(root,source)
            source.write_text('[]',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'checksum'):
                module.expand(root,source)


if __name__ == '__main__':
    unittest.main()
