"""Rechecks must bind teacher identities and prefer direct states over search."""
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts/midterm_a'))
from assemble_recheck_evidence import latest_details, read_records


class RecheckEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.source={'example/demo#1':dict(issue_id='101')}
        self.direct=dict(kind='detail', key='example/demo#1', expected_id='101', identity_match=True,
                         observed_at='2026-10-18T11:00:00Z', method='github_direct_identity_recheck',
                         payload=dict(id=101, state='open'))
        self.search=dict(kind='closed_page',repository='example/demo',
                         rows=[[1,'101','2026-10-17T00:00:00Z','2026-10-17T00:00:00Z',False]],
                         observed_at='2026-10-18T11:01:00Z',url='https://api.github.com/search/issues?q=example',
                         method='github_search_exact_identity_closed_verification')

    def test_indexed_closed_record_does_not_override_direct_open_state(self):
        for records in ([self.direct,self.search],[self.search,self.direct]):
            self.assertEqual(latest_details(records,self.source)['example/demo#1']['payload']['state'],'open')

    def test_collection_proof_with_different_global_id_is_rejected(self):
        with self.assertRaises(ValueError):
            latest_details([{**self.search,'rows':[[1,'102',None,None,False]]}],self.source)

    def test_unfinalized_fragments_cannot_supply_candidate_bodies(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            (folder/'large_issue.0000.part').write_text(json.dumps([self.direct]),'utf-8')
            rows,fragments=read_records(folder)
            self.assertEqual(rows,[])
            self.assertEqual(fragments,['large_issue'])
            (folder/'compressed_complete.json.gz').write_bytes(gzip.compress(json.dumps([self.direct]).encode()))
            rows,_=read_records(folder)
            self.assertEqual(len(rows),1)


if __name__=='__main__':
    unittest.main()
