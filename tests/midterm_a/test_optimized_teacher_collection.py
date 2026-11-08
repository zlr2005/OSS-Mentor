import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/midterm_a/optimized_teacher_collection.py'
spec=importlib.util.spec_from_file_location('optimized_teacher_collection',SCRIPT)
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)


class OptimizedCollectionTests(unittest.TestCase):
    def setUp(self):
        self.batch=collector.make_batch(dict(repository='example/demo',repo_id=10,owner_id=20),202601,1767196800,1769875200)

    def payload(self,rows=0,**stats):
        data=[dict(platform='GitHub',type='IssuesEvent',action='opened',repo_id='10',repo_name='example/demo',
                   issue_id=str(100+i),issue_number=i+1,event_epoch=self.batch['start_epoch']) for i in range(rows)]
        return dict(rows=rows,data=data,statistics=dict(rows_read=1000,bytes_read=4096,elapsed=.1,**stats))

    def exercise(self,temporary,answers,batches=None,budget=None,resume=None,extended=False):
        root=Path(temporary)/'collections';root.mkdir(exist_ok=True)
        budget=budget or collector.DEFAULT_BUDGET
        plan=dict(policy=collector.POLICY,budget=budget,batches=batches or [self.batch],baseline_keys=[])
        path=Path(temporary)/'plan.json';path.write_text(json.dumps(plan),'utf-8')
        with patch.object(collector,'ROOT',root),patch.object(collector,'DEFAULT_BUDGET',budget),\
             patch('builtins.input',return_value='test-user'),patch.object(collector.getpass,'getpass',return_value='test-secret'),\
             patch.object(collector.time,'sleep') as sleep,patch.object(collector,'fetch',side_effect=answers) as fetch:
            collector.run(path,resume,extended_run=extended)
            self.sleep_calls=sleep.call_args_list
        directory=next(path.parent for path in root.glob('*/status.json'))
        for file in root.rglob('*.json'):self.assertNotIn('test-secret',file.read_text('utf-8'))
        return directory,collector.read(directory/'status.json'),fetch

    def test_fixed_select_has_original_guards_and_rejects_scope_injection(self):
        query=collector.sql(self.batch)
        for guard in ('SELECT DISTINCT ','LIMIT 500','max_execution_time=15','max_rows_to_read=1000000','max_bytes_to_read=134217728','max_threads=1'):
            self.assertIn(guard,query)
        self.assertNotIn(';',query)
        with self.assertRaises(ValueError):collector.sql(dict(self.batch,repo_id='10; DROP TABLE x'))
        with self.assertRaises(ValueError):collector.sql(dict(self.batch,end_epoch=1792340000))

    def test_newcomer_discovery_accepts_later_label_events_but_rejects_other_labels(self):
        batch=collector.make_batch(dict(self.batch,discovery_filter='newcomer'),202601,
            self.batch['start_epoch'],self.batch['end_epoch'])
        self.assertNotIn("action='opened'",batch['sql'])
        self.assertIn('arrayExists',batch['sql'])
        payload=self.payload(1)
        payload['data'][0].update(action='labeled',**{'issue_labels.name':['Good-First-Issue']})
        self.assertEqual(len(collector.validate(payload,batch)),1)
        payload['data'][0]['issue_labels.name']=['help wanted']
        with self.assertRaisesRegex(ValueError,'Label scope'):collector.validate(payload,batch)
        payload['data'][0]['issue_labels.name']=['not a good first issue']
        with self.assertRaisesRegex(ValueError,'Label scope'):collector.validate(payload,batch)

    def test_opened_inventory_uses_action_prefix_without_label_restriction(self):
        seed=dict(repository='example/demo',repo_id=10,owner_id=20,
                  discovery_filter='all_opened',query_layout='action_prewhere_v2',event_action='opened')
        start=1792339200  # Shanghai midnight, October 19.
        batch=collector.make_batch(seed,202610,start,start+4*86400)
        scope,where=batch['sql'].split(' WHERE ')
        self.assertIn("AND action='opened' AND org_id",scope)
        self.assertNotIn('arrayExists',batch['sql'])
        self.assertIn('max_rows_to_read=1000000',where)
        for child in collector.split_batch(batch):
            self.assertEqual(child['event_action'],'opened')
            self.assertEqual(child['discovery_filter'],'all_opened')
        with self.assertRaises(ValueError):
            collector.make_batch(dict(seed,event_action='closed'),202610,start,start+86400)
        with self.assertRaisesRegex(ValueError,'time range'):
            collector.make_batch(seed,202610,start,start+5*86400)

    def test_newcomer_split_preserves_filter_and_is_distinct_from_legacy(self):
        batch=collector.make_batch(dict(self.batch,discovery_filter='help_wanted'),202601,
            self.batch['start_epoch'],self.batch['end_epoch'])
        self.assertNotEqual(batch['key'],self.batch['key'])
        for child in collector.split_batch(batch):
            self.assertEqual(child['discovery_filter'],'help_wanted')
            self.assertIn("'help wanted'",child['sql'])
            self.assertNotIn("action='opened'",child['sql'])
        with self.assertRaises(ValueError):collector.sql(dict(batch,discovery_filter="'; DROP TABLE x"))

    def test_splitting_exactly_covers_month_week_day_and_stops_at_hour(self):
        batch=self.batch
        for expected in (7*86400,86400,3600):
            children=collector.split_batch(batch)
            self.assertEqual(children[0]['start_epoch'],batch['start_epoch'])
            self.assertEqual(children[-1]['end_epoch'],batch['end_epoch'])
            self.assertTrue(all(a['end_epoch']==b['start_epoch'] for a,b in zip(children,children[1:])))
            self.assertTrue(all(b['end_epoch']-b['start_epoch']<=expected for b in children))
            batch=children[0]
        self.assertEqual(collector.split_batch(batch),[])

    def test_daily_prewhere_keeps_guards_and_label_filter_outside_scope(self):
        seed=dict(self.batch,discovery_filter='newcomer',query_layout='scope_prewhere_v1')
        batch=collector.make_batch(seed,202601,self.batch['start_epoch'],self.batch['start_epoch']+86400)
        scope,labels=batch['sql'].split(' WHERE ')
        self.assertIn('PREWHERE toYYYYMM',scope)
        self.assertIn('repo_id=10',scope)
        self.assertNotIn('arrayExists',scope)
        self.assertIn('arrayExists',labels)
        self.assertIn('max_rows_to_read=1000000',labels)
        self.assertIn('max_bytes_to_read=134217728',labels)
        children=collector.split_batch(batch)
        self.assertEqual(children[-1]['end_epoch'],batch['end_epoch'])
        self.assertTrue(all(c['query_layout']=='scope_prewhere_v1' for c in children))
        with self.assertRaisesRegex(ValueError,'at most one day'):
            collector.make_batch(seed,202601,self.batch['start_epoch'],self.batch['end_epoch'])

    def daily_batch(self):
        return collector.make_batch(dict(self.batch,discovery_filter='newcomer',query_layout='scope_prewhere_v1'),
            202601,self.batch['start_epoch'],self.batch['start_epoch']+86400)

    def test_daily_cheap_row_pressure_continues_and_accounts_for_cost(self):
        batch=self.daily_batch();second=collector.split_batch(batch)[0]
        payload=self.payload();payload['statistics']=dict(rows_read=393197,bytes_read=8762709,elapsed=.025)
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(collector.time,'monotonic',return_value=100) as monotonic:
                _,status,fetch=self.exercise(temporary,[{'data':[{'ok':1}]},payload,self.payload()],batches=[batch,second])
            self.assertEqual(fetch.call_count,3)
            self.assertEqual(status['status'],'finished_unverified')
            self.assertEqual(status['usage_this_invocation']['rows'],394197)
            self.assertEqual(status['pending_batches'],0)
            self.assertTrue(any(call.args==(6,) for call in self.sleep_calls))

    def test_daily_row_pressure_still_respects_cumulative_row_budget(self):
        payload=self.payload();payload['statistics']=dict(rows_read=393197,bytes_read=8762709,elapsed=.025)
        with tempfile.TemporaryDirectory() as temporary:
            budget=dict(collector.DEFAULT_BUDGET,rows_per_run=1_200_000)
            _,status,fetch=self.exercise(temporary,[{'data':[{'ok':1}]},payload],
                batches=[self.daily_batch(),collector.split_batch(self.daily_batch())[0]],budget=budget)
            self.assertEqual(fetch.call_count,2)
            self.assertEqual(status['stop_reason'],'run_read_row_budget')
            self.assertEqual(status['pending_batches'],1)

    def test_daily_byte_or_time_pressure_still_pauses(self):
        for stats in (dict(rows_read=393197,bytes_read=16777216,elapsed=.025),
                      dict(rows_read=1000,bytes_read=4096,elapsed=6)):
            with self.subTest(stats=stats),tempfile.TemporaryDirectory() as temporary:
                payload=self.payload();payload['statistics']=stats
                _,status,fetch=self.exercise(temporary,[{'data':[{'ok':1}]},payload],
                    batches=[self.daily_batch(),collector.split_batch(self.daily_batch())[0]])
                self.assertEqual(fetch.call_count,2)
                self.assertEqual(status['stop_reason'],'cost_near_guard')

    def test_extended_run_continues_past_old_byte_pause_and_keeps_sql_guards(self):
        payload=self.payload();payload['statistics']=dict(rows_read=524170,bytes_read=17600211,elapsed=.046)
        batch=self.daily_batch()
        with tempfile.TemporaryDirectory() as temporary:
            _,status,fetch=self.exercise(temporary,[{'data':[{'ok':1}]},payload,self.payload()],
                batches=[batch,collector.split_batch(batch)[0]],extended=True)
            self.assertEqual(fetch.call_count,3)
            self.assertEqual(status['status'],'finished_unverified')
            self.assertTrue(status['extended_run'])
            self.assertEqual(status['budget']['rows_per_run'],400_000_000)
            self.assertIn('max_rows_to_read=1000000',fetch.call_args_list[1].args[1])
            self.assertIn('max_bytes_to_read=134217728',fetch.call_args_list[1].args[1])

    def test_extended_run_is_opt_in_and_remains_bounded(self):
        base=dict(collector.DEFAULT_BUDGET)
        budget=collector.invocation_budget(base,True)
        self.assertEqual(base,collector.DEFAULT_BUDGET)
        self.assertEqual(collector.invocation_budget(base),base)
        for usage,expected in ((dict(requests=1000,rows=0,bytes=0),'run_request_budget'),
                               (dict(requests=0,rows=399000001,bytes=0),'run_read_row_budget'),
                               (dict(requests=0,rows=0,bytes=budget['bytes_per_run']),'run_read_byte_budget')):
            self.assertEqual(collector.budget_reason(usage,[],budget,10000,True,True),expected)
        with tempfile.TemporaryDirectory() as temporary:
            payload=self.payload();payload['statistics']['bytes_read']=67_108_864
            _,status,fetch=self.exercise(temporary,[{'data':[{'ok':1}]},payload],
                batches=[self.daily_batch(),collector.split_batch(self.daily_batch())[0]],extended=True)
            self.assertEqual(fetch.call_count,2)
            self.assertEqual(status['stop_reason'],'cost_near_guard')

    def test_invalid_rows_and_unmeasured_cost_cannot_be_accepted(self):
        for mutation in ({'rows':501},{'rows':True},{'data':[dict(repo_id=11,type='IssuesEvent',action='opened',event_epoch=self.batch['start_epoch'])],'rows':1}):
            payload=self.payload();payload.update(mutation)
            with self.assertRaises(ValueError):collector.validate(payload,self.batch)
        with self.assertRaises((KeyError,ValueError)):collector.cost({'statistics':{}})
        with self.assertRaises(ValueError):collector.cost({'statistics':dict(rows_read=float('nan'),bytes_read=0,elapsed=0)})

    def test_http_boundary_is_get_only_and_rejects_partial_json_and_redirects(self):
        class FakeOpener:
            def __init__(self,body):self.body=body
            def open(self,request,timeout):
                self.request=request
                return io.BytesIO(self.body)
        opener=FakeOpener(b'{"data":[{"ok":1}]}')
        collector.fetch(opener,'SELECT 1 AS ok FORMAT JSON','user','test-secret')
        self.assertEqual(opener.request.get_method(),'GET')
        self.assertTrue(opener.request.full_url.startswith(collector.ENDPOINT))
        self.assertNotIn('test-secret',opener.request.full_url)
        self.assertIsNone(collector.NoRedirect().redirect_request(None,None,None,None,None,None))
        with self.assertRaises(ValueError):collector.fetch(opener,'DELETE FROM opensource.events','user','test-secret')
        with self.assertRaisesRegex(collector.RemoteFailure,'server_protection'):
            collector.fetch(FakeOpener(b'{"data":[]}\nCode: TOO_MANY_ROWS'),'SELECT 1 AS ok FORMAT JSON','user','test-secret')

    def test_transport_errors_are_classified_without_exposing_secrets(self):
        class BrokenOpener:
            def __init__(self,error):self.error=error
            def open(self,*args,**kwargs):raise self.error
        for error,category in ((collector.urllib.error.URLError(collector.socket.gaierror(11001,'test-secret')),'network_dns_failure'),
                               (TimeoutError('test-secret'),'network_timeout'),
                               (ConnectionResetError('test-secret'),'network_connection_failure'),
                               (ValueError('test-secret'),'invalid_request_encoding_or_headers')):
            with self.subTest(category=category),self.assertRaises(collector.RemoteFailure) as caught:
                collector.fetch(BrokenOpener(error),'SELECT 1 AS ok FORMAT JSON','user','test-secret')
            self.assertEqual(str(caught.exception),category)

    def test_http_diagnostics_keep_only_fixed_identifiers_and_numeric_code(self):
        detail=collector.safe_http_diagnostics(403,'Code: 516. AUTHENTICATION_FAILED user=test-secret https://user:password@example.com')
        self.assertEqual(detail,dict(http_status=403,database_error_code=516,recognized_errors=['AUTHENTICATION_FAILED']))
        self.assertNotIn('test-secret',json.dumps(detail))
        self.assertNotIn('password',json.dumps(detail))

    def test_transient_preflight_retries_without_asking_for_credentials_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(collector.getpass,'getpass',return_value='test-secret'):
                _,status,fetch=self.exercise(temporary,[collector.RemoteFailure('network_timeout'),
                    {'data':[{'ok':1}]},self.payload()])
            self.assertEqual(fetch.call_count,3)
            self.assertEqual(status['status'],'finished_unverified')
            self.assertEqual(status['usage_this_invocation']['requests'],3)

    def test_preflight_auth_failure_does_not_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            _,status,fetch=self.exercise(temporary,[collector.RemoteFailure('HTTP_401')])
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(status['stop_reason'],'HTTP_401')

    def test_empty_password_prompts_again_without_echoing_secret(self):
        output=io.StringIO()
        with patch('builtins.input',return_value='user'),\
             patch.object(collector.getpass,'getpass',side_effect=['','test-secret']) as password,\
             patch('sys.stdout',output):
            self.assertEqual(collector.prompt_credentials(),('user','test-secret'))
        self.assertEqual(password.call_count,2)
        self.assertIn('No password was received',output.getvalue())
        self.assertNotIn('test-secret',output.getvalue())

    def test_repeated_empty_password_stops_with_clear_message(self):
        output=io.StringIO()
        with patch('builtins.input',return_value='user'),\
             patch.object(collector.getpass,'getpass',return_value='') as password,\
             patch('sys.stdout',output):
            self.assertEqual(collector.prompt_credentials(),(None,None))
        self.assertEqual(password.call_count,3)
        self.assertIn('No network requests were sent',output.getvalue())

    def test_preflight_transient_retry_is_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            _,status,fetch=self.exercise(temporary,[collector.RemoteFailure('network_dns_failure')]*3)
            self.assertEqual(fetch.call_count,3)
            self.assertEqual(status['stop_reason'],'network_dns_failure')

    def test_hourly_override_preserves_daily_and_run_guards(self):
        budget=collector.DEFAULT_BUDGET
        usage=dict(requests=0,rows=0,bytes=0)
        prior=[dict(at=9999,rows=0,bytes=budget['bytes_per_hour'])]
        self.assertIsNone(collector.budget_reason(usage,prior,budget,10000,True))
        prior=[dict(at=9999,rows=0,bytes=budget['bytes_per_day'])]
        self.assertEqual(collector.budget_reason(usage,prior,budget,10000,True),'rolling_day_byte_budget')
        self.assertEqual(collector.budget_reason(dict(usage,requests=150),[],budget,10000,True),'run_request_budget')

    def test_budgets_reserve_next_full_query_and_account_for_prior_traffic(self):
        budget=collector.DEFAULT_BUDGET;usage=dict(requests=0,rows=19000001,bytes=0)
        self.assertEqual(collector.budget_reason(usage,[],budget,10000),'run_read_row_budget')
        prior=[dict(at=9999,rows=0,bytes=0)]*200
        self.assertEqual(collector.budget_reason(dict(requests=0,rows=0,bytes=0),prior,budget,10000),'rolling_hour_request_budget')
        prior=[dict(at=9999,rows=0,bytes=budget['bytes_per_hour'])]
        self.assertEqual(collector.budget_reason(dict(requests=0,rows=0,bytes=0),prior,budget,10000),'rolling_hour_byte_budget')

    def test_daily_override_is_explicit_and_preserves_run_limits(self):
        budget=collector.DEFAULT_BUDGET;usage=dict(requests=0,rows=0,bytes=0)
        prior=[dict(at=9999,rows=budget['rows_per_day'],bytes=budget['bytes_per_day'])]
        self.assertIsNotNone(collector.budget_reason(usage,prior,budget,10000,True))
        self.assertIsNone(collector.budget_reason(usage,prior,budget,10000,True,True))
        self.assertIsNotNone(collector.budget_reason(usage,prior,budget,10000,False,True))
        for change,expected in ((dict(requests=150),'run_request_budget'),
                                (dict(rows=19000001),'run_read_row_budget'),
                                (dict(bytes=budget['bytes_per_run']),'run_read_byte_budget')):
            self.assertEqual(collector.budget_reason(dict(usage,**change),prior,budget,10000,True,True),expected)
        requests=[dict(at=9999,rows=0,bytes=0)]*budget['requests_per_day']
        self.assertEqual(collector.budget_reason(usage,requests,budget,10000,True),'rolling_day_request_budget')
        self.assertIsNone(collector.budget_reason(usage,requests,budget,10000,True,True))

    def test_saturated_month_persists_children_and_resume_does_not_repeat_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            budget=dict(collector.DEFAULT_BUDGET,requests_per_run=2)
            directory,status,fetch=self.exercise(temporary,[{'data':[{'ok':1}]},self.payload(500)],budget=budget)
            self.assertEqual(status['net_new_issues'],500)
            self.assertEqual(status['stop_reason'],'run_request_budget')
            checkpoint=collector.read(directory/'checkpoint.json')
            self.assertEqual(len(checkpoint['pending']),5)
            child=checkpoint['pending'][0]
            _,status,fetch=self.exercise(temporary,[{'data':[{'ok':1}]},self.payload()],budget=budget,resume=directory)
            self.assertEqual(fetch.call_args_list[1].args[1],child['sql'])
            self.assertEqual(status['unique_issues'],500)

    def test_quota_error_stops_without_retry_and_reserves_unknown_cost(self):
        with tempfile.TemporaryDirectory() as temporary:
            _,status,fetch=self.exercise(temporary,[{'data':[{'ok':1}]},collector.RemoteFailure('HTTP_429')])
            self.assertEqual(fetch.call_count,2)
            self.assertEqual(status['usage_this_invocation']['rows'],1000000)
            self.assertEqual(status['pending_batches'],1)
            self.assertEqual(status['stop_reason'],'HTTP_429')

    def test_transient_error_is_deferred_not_retried_in_same_invocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            second=collector.make_batch(dict(repository='example/second',repo_id=11,owner_id=20),202601,1767196800,1769875200)
            _,status,fetch=self.exercise(temporary,[{'data':[{'ok':1}]},collector.RemoteFailure('HTTP_502'),self.payload()],batches=[self.batch,second])
            self.assertEqual(fetch.call_count,3)
            self.assertEqual(status['pending_batches'],1)
            self.assertEqual(status['successful_batches'],1)
            self.assertEqual(status['failed_batches'],1)

    def test_soft_stop_accepts_response_but_never_starts_next_query(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload=self.payload();payload['statistics']['rows_read']=200001
            second=collector.make_batch(dict(repository='example/second',repo_id=11,owner_id=20),202601,1767196800,1769875200)
            _,status,fetch=self.exercise(temporary,[{'data':[{'ok':1}]},payload],batches=[self.batch,second])
            self.assertEqual(fetch.call_count,2)
            self.assertEqual(status['status'],'paused_budget_unverified')
            self.assertEqual(status['pending_batches'],1)

    def test_missing_statistics_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload=self.payload();payload.pop('statistics')
            _,status,fetch=self.exercise(temporary,[{'data':[{'ok':1}]},payload])
            self.assertEqual(fetch.call_count,2)
            self.assertEqual(status['unique_issues'],0)
            self.assertEqual(status['stop_reason'],'unmeasured_read_cost')

    def test_other_credential_window_blocks_any_new_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'collections';old=root/'round20_old';old.mkdir(parents=True)
            collector.save(old/'status.json',dict(status='waiting_for_credentials'))
            with self.assertRaisesRegex(RuntimeError,'Another collection is active'):
                self.exercise(temporary,[])

    def test_protected_repository_scopes_are_deferred_without_dropping_or_changing_queries(self):
        safe=dict(self.batch,repo_id=11,key='safe')
        failed=dict(batch=self.batch,error='server_protection',diagnostics=dict(recognized_errors=['TOO_MANY_ROWS']))
        ordered,protected,count=collector.defer_protected_repositories([self.batch,safe],[failed])
        self.assertEqual(ordered,[safe,self.batch])
        self.assertEqual(protected,{10})
        self.assertEqual(count,1)
        self.assertEqual(ordered[1]['sql'],self.batch['sql'])

    def test_quota_or_unclassified_protection_is_not_treated_as_repository_cost(self):
        for errors in (['QUOTA_EXCEEDED'],[]):
            result=dict(batch=self.batch,error='server_protection',diagnostics=dict(recognized_errors=errors))
            ordered,protected,count=collector.defer_protected_repositories([self.batch],[result])
            self.assertEqual(ordered,[self.batch]);self.assertEqual(protected,set());self.assertEqual(count,0)

    def test_action_indexed_query_constrains_sorting_prefix_and_validates_returned_action(self):
        seed=dict(self.batch,discovery_filter='newcomer',query_layout='action_prewhere_v2',event_action='labeled')
        batch=collector.make_batch(seed,202601,self.batch['start_epoch'],self.batch['end_epoch'])
        self.assertIn("type='IssuesEvent' AND action='labeled' AND org_id",batch['sql'])
        self.assertIn('max_rows_to_read=1000000',batch['sql'])
        self.assertIn('max_bytes_to_read=134217728',batch['sql'])
        payload=self.payload(1);payload['data'][0].update(action='labeled',**{'issue_labels.name':['good first issue']})
        self.assertEqual(len(collector.validate(payload,batch)),1)
        payload['data'][0]['action']='closed'
        with self.assertRaisesRegex(ValueError,'Action mismatch'):collector.validate(payload,batch)
        with self.assertRaisesRegex(ValueError,'Invalid Issue action'):
            collector.make_batch(dict(seed,event_action="labeled' OR 1=1"),202601,self.batch['start_epoch'],self.batch['end_epoch'])
        self.assertTrue(all(b['event_action']=='labeled' for b in collector.split_batch(batch)))

    def test_action_mode_uses_distinct_scope_key_and_is_not_deferred_for_old_layout_failure(self):
        batch=collector.make_batch(dict(self.batch,discovery_filter='newcomer',query_layout='action_prewhere_v2',event_action='opened'),
                                   202601,self.batch['start_epoch'],self.batch['end_epoch'])
        labeled=collector.make_batch(dict(batch,event_action='labeled'),202601,batch['start_epoch'],batch['end_epoch'])
        self.assertNotEqual(batch['key'],labeled['key'])
        old_failure=dict(batch=self.batch,error='server_protection',diagnostics=dict(recognized_errors=['TOO_MANY_ROWS']))
        ordered,protected,count=collector.defer_protected_repositories([batch],[old_failure])
        self.assertEqual(ordered,[batch]);self.assertEqual(count,0)


if __name__=='__main__':unittest.main()
