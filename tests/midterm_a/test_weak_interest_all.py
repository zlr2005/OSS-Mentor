"""Offline tests of coverage fairness, pagination, quota stops and identity boundaries."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import gzip
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('all_weak',ROOT/'scripts/midterm_a/collect_b_weak_interest_all.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class AllWeakInterestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.parent=self.root/'parent';(self.parent/'tables').mkdir(parents=True)
        (self.parent/'evidence').mkdir()
        (self.parent/'manifest.json').write_text(json.dumps({'dataset_version':'synthetic-parent'}))
        developers=[]
        for number,kind in ((1,'User'),(2,'User'),(3,'Bot')):
            location=f'evidence/{number}.json'
            (self.parent/location).write_text(json.dumps({'fields':{'author':{'id':number,'login':f'user{number}','type':kind}}}))
            developers.append({'developer_key':f'd{number}','github_numeric_id':number,'identity_status':'verified','evidence_locations':[location]})
        developers.append({'developer_key':'unknown','identity_status':'unconfirmed'})
        (self.parent/'tables/developers.jsonl').write_text(''.join(json.dumps(d)+'\n' for d in developers))
        (self.parent/'tables/repositories.jsonl').write_text('')
        self.calls=[];self.output=self.root/'out';self.numeric_links=False
        outer=self
        class FakeClient:
            def __init__(self,**kwargs): pass
            def get(self,path,params=None,extra_headers=None):
                page=params['page'] if params else None
                outer.calls.append((path,page))
                login=path.split('/')[2];uid=int(login[-1])
                headers={}
                if params is None: payload={'id':uid,'login':login,'type':'User'}
                elif path.endswith('/repos'): payload=[]
                else:
                    repo={'id':uid*100+page,'full_name':f'public/r{uid}_{page}','private':False,'archived':False,
                          'language':'Python','fork':False,'html_url':'https://github.com/public/example','owner':{'id':9}}
                    payload=[{'starred_at':'2026-01-01T00:00:00Z','repo':repo}]
                    if page==1:
                        link_path=path.replace('/users/'+login,'/user/'+str(uid)) if outer.numeric_links else path
                        headers['link']=f'<https://api.github.com{link_path}?page=2>; rel="next"'
                return SimpleNamespace(url='https://api.github.com'+path,status_code=200,headers=headers,payload=payload,
                                       fetched_at=module.datetime(2026,10,28,tzinfo=module.timezone.utc))
        self.client=FakeClient

    def collect(self,budget):
        with patch.object(module,'CompressedGitHubClient',self.client),patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='')),redirect_stdout(io.StringIO()):
            module.collect(self.parent,self.output,self.root/'absent',budget,1)
        return json.loads((self.output/'manifest.json').read_bytes())

    def test_all_verified_humans_selected_without_requiring_contributions(self):
        accounts,excluded=module.select_accounts(self.parent)
        self.assertEqual([a['developer_key'] for a in accounts],['d1','d2'])
        self.assertEqual({a['exclusion_reason'] for a in excluded},{'non_human_account','identity_unconfirmed'})

    def test_first_pages_cover_everyone_before_second_pages(self):
        manifest=self.collect(20)
        first=[i for i,(p,n) in enumerate(self.calls) if n==1]
        second=[i for i,(p,n) in enumerate(self.calls) if n==2]
        self.assertGreater(min(second),max(first))
        self.assertEqual(manifest['coverage_summary']['endpoints'],{'completed':4})
        self.assertEqual(manifest['behavior_counts'],{'star':4})

    def test_budget_leaves_explicit_partial_coverage_and_can_resume_from_cache(self):
        manifest=self.collect(6)
        self.assertEqual(len(self.calls),6)
        self.assertEqual(manifest['coverage_summary']['endpoints'],{'partial':2,'completed':2})
        self.assertEqual(manifest['behavior_counts'],{'star':2})
        # Same immutable plan resumes without resetting the lifetime request budget.
        self.calls.clear();manifest=self.collect(6)
        self.assertEqual(len(self.calls),0)
        self.assertEqual(manifest['coverage_summary']['endpoints'],{'partial':2,'completed':2})
        self.assertEqual(manifest['behavior_counts'],{'star':2})

    def test_numeric_identity_pagination_links_are_accepted(self):
        self.numeric_links=True
        manifest=self.collect(20)
        self.assertEqual(manifest['coverage_summary']['endpoints'],{'completed':4})

    def test_projection_drops_unneeded_payload_fields(self):
        value=module.compact({'id':1,'login':'public','email':'unused','private_field':'unused'})
        self.assertEqual(value,{'id':1,'login':'public'})

    def test_gzip_and_plain_responses_decode_identically(self):
        raw=b'{"id": 42}'
        self.assertEqual(module.CompressedGitHubClient._decode_payload(raw),{'id':42})
        self.assertEqual(module.CompressedGitHubClient._decode_payload(gzip.compress(raw)),{'id':42})

    def test_failed_behavior_endpoint_is_not_reported_as_empty_success(self):
        base=self.client
        class UnavailableStarClient(base):
            def get(self,path,params=None,extra_headers=None):
                if path=='/users/user1/starred': raise RuntimeError('synthetic outage')
                return super().get(path,params,extra_headers)
        self.client=UnavailableStarClient
        manifest=self.collect(20)
        self.assertEqual(manifest['coverage_summary']['endpoints'],{'failed':1,'completed':3})
        states=[json.loads(l) for l in (self.output/'tables/coverage.jsonl').read_text().splitlines()]
        failed=next(s for s in states if s['developer_key']=='d1' and s['endpoint']=='starred')
        self.assertEqual(failed['pages'],0)
        self.assertEqual(failed['error_type'],'RuntimeError')

    def test_identity_failure_is_retried_after_other_accounts(self):
        base=self.client;attempts=[]
        class TransientClient(base):
            def get(self,path,params=None,extra_headers=None):
                if path=='/users/user1' and params is None:
                    attempts.append(path)
                    if len(attempts)==1: raise RuntimeError('synthetic transient')
                return super().get(path,params,extra_headers)
        self.client=TransientClient
        manifest=self.collect(20)
        self.assertEqual(len(attempts),2)
        self.assertEqual(manifest['coverage_summary']['verified'],2)
        self.assertEqual(manifest['coverage_summary']['identity_failures'],0)

if __name__=='__main__': unittest.main()
