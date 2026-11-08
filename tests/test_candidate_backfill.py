import importlib.util
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

spec = importlib.util.spec_from_file_location('candidate_backfill', Path(__file__).resolve().parents[1] / 'scripts/candidate_backfill.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from oss_mentor.collector.github_client import GitHubApiError


class ConnectorReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = module.ConnectorReplay(self.root)

    def evidence(self, url, payload, *, age=0, success=True):
        stamp = (datetime.now(timezone.utc) - timedelta(hours=age)).isoformat()
        entry = {'url': url, 'retrieval_method': 'github_connector', 'tool_success': success,
                 'observed_at': stamp, 'payload': payload,
                 'payload_sha256': hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()}
        path = self.root / (hashlib.sha256(url.encode()).hexdigest() + '.json')
        path.write_text(json.dumps(entry), encoding='utf-8')
        return path

    def test_missing_source_becomes_pending_without_network(self):
        with self.assertRaises(module.PendingRequest):
            self.client.get('/repos/owner/repo')
        self.assertEqual(len(self.client.pending), 1)

    def test_full_page_requires_explicit_continuation(self):
        self.evidence('https://api.github.com/repos/o/r/issues/1/timeline?per_page=100&page=1', [{}] * 100)
        pages = self.client.iter_pages('/repos/o/r/issues/1/timeline', params={'per_page': 100})
        next(pages)
        with self.assertRaises(module.PendingRequest):
            next(pages)
        self.assertIn('page=2', next(iter(self.client.pending.values()))['url'])

    def test_failed_and_expired_sources_cannot_refresh(self):
        url = 'https://api.github.com/repos/o/r'
        for options in [{'age': 25}, {'age': -1}, {'success': False}]:
            self.evidence(url, {'id': 1}, **options)
            with self.assertRaises(ValueError):
                self.client.get(url)

    def test_tampered_payload_rejected(self):
        url = 'https://api.github.com/repos/o/r'
        path = self.evidence(url, {'id': 1})
        entry = json.loads(path.read_text()); entry['payload']['id'] = 2
        path.write_text(json.dumps(entry))
        with self.assertRaises(ValueError):
            self.client.get(url)

    def test_token_never_follows_cross_origin_url(self):
        with self.assertRaises(GitHubApiError):
            self.client.get('https://untrusted.invalid/repos/o/r')
        self.assertEqual(self.client.pending, {})

    def test_authenticated_transport_saves_evidence_without_credential(self):
        client = module.AuthenticatedCollector(self.root, 'runtime-secret-example', 2)
        url = 'https://api.github.com/repos/o/r'
        response = module.GitHubResponse(url, 200, {}, {'id': 1}, datetime.now(timezone.utc))
        with patch.object(module.GitHubClient, 'get', return_value=response) as request:
            self.assertEqual(client.get(url).payload, {'id': 1})
            self.assertEqual(client.get(url).payload, {'id': 1})
            self.assertEqual(request.call_count, 1)
        files = list(self.root.glob('*.json'))
        self.assertEqual(len(files), 1)
        self.assertNotIn('runtime-secret-example', files[0].read_text())

    def test_parallel_request_budget_is_reserved_before_requests(self):
        client = module.AuthenticatedCollector(self.root, 'runtime-secret-example', 2)
        def request(_client, path, **kwargs):
            return module.GitHubResponse(path, 200, {}, {'id': 1}, datetime.now(timezone.utc))
        def attempt(index):
            try:
                client.get(f'https://api.github.com/repos/o/r{index}')
                return 'ok'
            except module.RequestBudgetExhausted:
                return 'budget'
        with patch.object(module.GitHubClient, 'get', side_effect=request) as fetch:
            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(attempt, range(8)))
            self.assertEqual(fetch.call_count, 2)
        self.assertEqual(outcomes.count('ok'), 2)
        self.assertEqual(outcomes.count('budget'), 6)
        self.assertEqual(client.reservations, 2)

    def test_failed_authenticated_request_is_not_saved_as_success(self):
        client = module.AuthenticatedCollector(self.root, 'runtime-secret-example', 2)
        with patch.object(module.GitHubClient, 'get', side_effect=GitHubApiError('failure', status_code=401)):
            with self.assertRaises(GitHubApiError):
                client.get('/repos/o/r')
        self.assertEqual(list(self.root.glob('*.json')), [])

    def test_full_queue_runner_bounds_retries_and_preserves_unresolved_status(self):
        (self.root / 'evidence').mkdir()
        module.save(self.root / 'jobs.json', [{'status': 'pending'}])
        module.save(self.root / 'network_progress.json', {'wave_outcomes': {}})
        response = module.GitHubResponse('https://api.github.com/rate_limit', 200, {},
            {'resources': {'core': {'remaining': 1000, 'reset': 2000000000}}}, datetime.now(timezone.utc))
        def failed_batch(*args):
            module.save(self.root / 'jobs.json', [{'status': 'needs_retry'}])
        with patch.object(module.GitHubClient, 'get', return_value=response), \
             patch.object(module, 'collect_waves', side_effect=failed_batch) as collect, \
             patch.object(module.subprocess, 'run'):
            module.collect_until_done(self.root, 'runtime-secret-example', 900, 100, 4)
        self.assertEqual(collect.call_count, 3)
        state = json.loads((self.root / 'runner_state.json').read_text())
        self.assertEqual(state['phase'], 'finished_with_review')
        self.assertEqual(state['retry_passes'], 2)
        self.assertEqual(state['job_status'], {'needs_retry': 1})

    def test_secondary_limit_stops_after_three_blocked_batches(self):
        module.save(self.root / 'jobs.json', [{'status': 'pending'}])
        module.save(self.root / 'network_progress.json', {'wave_outcomes': {'authentication_or_rate_limit': 1}})
        response = module.GitHubResponse('https://api.github.com/rate_limit', 200, {},
            {'resources': {'core': {'remaining': 1000, 'reset': 2000000000}}}, datetime.now(timezone.utc))
        with patch.object(module.GitHubClient, 'get', return_value=response), \
             patch.object(module, 'collect_waves') as collect, \
             patch.object(module.subprocess, 'run'), patch.object(module.time, 'sleep'):
            module.collect_until_done(self.root, 'runtime-secret-example', 900, 100, 4)
        self.assertEqual(collect.call_count, 3)
        state = json.loads((self.root / 'runner_state.json').read_text())
        self.assertEqual(state['phase'], 'source_unavailable')


if __name__ == '__main__':
    unittest.main()
