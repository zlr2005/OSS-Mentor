"""Checkpointed current-state verification of the full imported candidate inventory.

Network transport is external: only actual GitHub connector JSON envelopes are
accepted. Replays use original observation times and the production checks.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import sys
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from oss_mentor.candidate_sync import CandidateSynchronizer
from oss_mentor.candidate_rules import evaluate_candidate
from oss_mentor.candidate_freshness import parse_timestamp
from oss_mentor.collector.source_comparison import normalize_github_issue
from oss_mentor.collector.github_client import GitHubApiError, GitHubClient, GitHubResponse, RateLimitExceeded
from oss_mentor.storage.candidates import SQLiteCandidateStorage
from oss_mentor.task_features import extract_task_features, infer_skill_requirements
from oss_mentor.task_readiness import task_review_reasons


class PendingRequest(Exception):
    pass


class RequestBudgetExhausted(Exception):
    pass


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class ConnectorReplay(GitHubClient):
    def __init__(self, directory):
        super().__init__(api_base='https://api.github.com', api_version='2026-03-10',
                         user_agent='OSS-Mentor-candidate-backfill/1', token=None, max_retries=0)
        self.directory = directory
        self.pending = {}

    def get(self, path_or_url, *, params=None, extra_headers=None):
        url = self._build_url(path_or_url, params)
        name = hashlib.sha256(url.encode()).hexdigest()
        path = self.directory / (name + '.json')
        if not path.exists():
            self.pending[name] = {'key': name, 'url': url}
            raise PendingRequest()
        entry = json.loads(path.read_text(encoding='utf-8'))
        if entry.get('url') != url or entry.get('retrieval_method') not in {'github_connector', 'github_rest'}:
            raise ValueError('Invalid connector evidence identity')
        if entry.get('tool_success') is not True:
            raise ValueError('Source request failed; preserve unverified state')
        observed = parse_timestamp(entry.get('observed_at'))
        now = datetime.now(timezone.utc)
        if not observed or not observed <= now < observed + timedelta(hours=24):
            raise ValueError('Cached source is stale; use a new collection directory')
        payload = entry['payload']
        if hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest() != entry['payload_sha256']:
            raise ValueError('Connector payload checksum mismatch')
        # 200 is an internal successful-response adapter; no upstream HTTP status
        # or headers are invented in the saved connector evidence.
        return GitHubResponse(url, 200, {}, payload, observed)

    def iter_pages(self, path_or_url, *, params=None, **kwargs):
        # Connector fetch provides no response headers: explicitly request pages
        # until a short page. Full pages alone never establish completeness.
        for page in range(1, 22):
            response = self.get(path_or_url, params={**(params or {}), 'page': page})
            if not isinstance(response.payload, list):
                raise ValueError('Expected paginated list')
            yield response
            if len(response.payload) < (params or {}).get('per_page', 30):
                return
        raise ValueError('Incomplete timeline beyond page budget')


class AuthenticatedCollector(ConnectorReplay):
    """Use runtime credentials for bounded GETs and preserve each successful read."""
    def __init__(self, directory, token, budget):
        super().__init__(directory)
        self.token, self.budget = token, budget
        self.reservations = 0
        self.budget_lock = threading.Lock()

    def get(self, path_or_url, *, params=None, extra_headers=None):
        url = self._build_url(path_or_url, params)
        path = self.directory / (hashlib.sha256(url.encode()).hexdigest() + '.json')
        if not path.exists():
            with self.budget_lock:
                if self.reservations >= self.budget or (self.rate_limit_remaining is not None and self.rate_limit_remaining < 50):
                    raise RequestBudgetExhausted()
                self.reservations += 1
            try:
                response = GitHubClient.get(self, path_or_url, params=params, extra_headers=extra_headers)
            except RateLimitExceeded as exc:
                raise RequestBudgetExhausted() from exc
            payload = response.payload
            entry = {'url': url, 'retrieval_method': 'github_rest', 'tool_success': True,
                     'observed_at': response.fetched_at.isoformat(), 'http_status': response.status_code,
                     'headers_available': True, 'payload': payload,
                     'payload_sha256': hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()}
            save(path, entry)
        return super().get(path_or_url, params=params, extra_headers=extra_headers)


def collect_waves(directory, token, budget, limit, workers):
    """Parallelize distinct read requests; apply all business writes serially."""
    client = AuthenticatedCollector(directory / 'evidence', token, budget)
    while True:
        process(directory, min(limit, 300))
        pending = json.loads((directory / 'requests.json').read_text(encoding='utf-8'))
        if not pending or client.reservations >= budget:
            break
        blocked = threading.Event()

        def fetch(request):
            if blocked.is_set():
                return 'budget_exhausted'
            try:
                client.get(request['url'])
                return 'fetched'
            except RequestBudgetExhausted:
                return 'budget_exhausted'
            except GitHubApiError as exc:
                if exc.status_code in {401, 403, 429}:
                    blocked.set()
                    return 'authentication_or_rate_limit'
                # An unreadable/deleted identity is not a verified closed Issue.
                save(directory / 'evidence' / (request['key'] + '.json'), {
                    'url': request['url'], 'retrieval_method': 'github_rest', 'tool_success': False,
                    'observed_at': datetime.now(timezone.utc).isoformat(),
                    'http_status': exc.status_code, 'error_type': type(exc).__name__})
                return 'source_failed'
            except (OSError, ValueError) as exc:
                save(directory / 'evidence' / (request['key'] + '.json'), {
                    'url': request['url'], 'retrieval_method': 'github_rest', 'tool_success': False,
                    'observed_at': datetime.now(timezone.utc).isoformat(), 'error_type': type(exc).__name__})
                return 'source_failed'

        with ThreadPoolExecutor(max_workers=workers) as executor:
            outcomes = Counter(executor.map(fetch, pending))
        progress = {'requests_this_run': client.reservations, 'request_budget': budget,
                    'rate_limit_remaining': client.rate_limit_remaining,
                    'rate_limit_reset_at': client.rate_limit_reset_at.isoformat() if client.rate_limit_reset_at else None,
                    'wave_outcomes': dict(outcomes), 'observed_at': datetime.now(timezone.utc).isoformat()}
        save(directory / 'network_progress.json', progress)
        print(json.dumps(progress), flush=True)
        if outcomes['authentication_or_rate_limit'] or (not outcomes['fetched'] and not outcomes['source_failed']):
            # Apply the successful reads before leaving the remaining work pending.
            process(directory, min(limit, 300))
            break


def collect_until_done(directory, token, budget, limit, workers):
    """Finite full-queue job, with bounded retries and quota-aware pauses."""
    quota_client = GitHubClient(api_base='https://api.github.com', api_version='2026-03-10',
                               user_agent='OSS-Mentor-backfill-quota/1', token=token,
                               max_retries=0, timeout_seconds=20)
    retry_passes = 0
    source_failures = 0
    blocked_batches = 0
    while True:
        jobs = json.loads((directory / 'jobs.json').read_text(encoding='utf-8'))
        counts = dict(Counter(j['status'] for j in jobs))
        state = {'pid': os.getpid(), 'observed_at': datetime.now(timezone.utc).isoformat(),
                 'job_status': counts, 'retry_passes': retry_passes}
        if not counts.get('pending'):
            if counts.get('needs_retry') and retry_passes < 2:
                archive = directory / 'source_error_archive'
                archive.mkdir(exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                for path in (directory / 'evidence').glob('*.json'):
                    entry = json.loads(path.read_text(encoding='utf-8'))
                    if entry.get('tool_success') is False:
                        path.rename(archive / (path.stem + '.' + stamp + '.json'))
                for job in jobs:
                    if job['status'] == 'needs_retry':
                        job['status'] = 'pending'
                save(directory / 'jobs.json', jobs)
                retry_passes += 1
                continue
            state['phase'] = 'finished_with_review' if counts.get('needs_task_review') or counts.get('needs_retry') else 'finished'
            save(directory / 'runner_state.json', state)
            subprocess.run([sys.executable, str(ROOT / 'scripts/report_candidate_backfill.py'),
                            '--directory', str(directory)], check=True, timeout=60)
            return
        try:
            resource = quota_client.get('/rate_limit').payload['resources']['core']
            source_failures = 0
        except (GitHubApiError, OSError, ValueError) as exc:
            source_failures += 1
            state.update(phase='source_unavailable', error_type=type(exc).__name__,
                         status_code=getattr(exc, 'status_code', None))
            save(directory / 'runner_state.json', state)
            if getattr(exc, 'status_code', None) == 401 or source_failures >= 3:
                return
            time.sleep(30)
            continue
        available = int(resource['remaining']) - 75
        if available <= 0:
            reset = datetime.fromtimestamp(resource['reset'], timezone.utc) + timedelta(seconds=3)
            state.update(phase='waiting_for_quota', resume_after=reset.isoformat())
            save(directory / 'runner_state.json', state)
            while datetime.now(timezone.utc) < reset:
                time.sleep(min(60, max(1, (reset - datetime.now(timezone.utc)).total_seconds())))
            continue
        state.update(phase='collecting', batch_request_budget=min(budget, available),
                     rate_limit_remaining=resource['remaining'])
        save(directory / 'runner_state.json', state)
        collect_waves(directory, token, min(budget, available), limit, workers)
        subprocess.run([sys.executable, str(ROOT / 'scripts/report_candidate_backfill.py'),
                        '--directory', str(directory)], check=True, timeout=60)
        network = json.loads((directory / 'network_progress.json').read_text(encoding='utf-8'))
        if network.get('wave_outcomes', {}).get('authentication_or_rate_limit'):
            # Secondary limits can occur with remaining primary quota. Back off;
            # require attention after three consecutive blocked batches.
            blocked_batches += 1
            if blocked_batches >= 3:
                state['phase'] = 'source_unavailable'
                save(directory / 'runner_state.json', state)
                return
            time.sleep(60)
        else:
            blocked_batches = 0


def initialize(directory):
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'evidence').mkdir()
    inventory = ROOT / 'data/candidate_inventory_20261024_v1/issue_inventory.sqlite3'
    with sqlite3.connect(inventory.resolve().as_uri() + '?mode=ro', uri=True) as c:
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute('SELECT * FROM issue_inventory')]
    # Prioritize explicit welcoming labels; every original identity stays queued.
    rows.sort(key=lambda r: (not any(word in r['record_json'].casefold()
                                    for word in ['good first issue', 'good-first-issue', 'first-contribution']),
                             r['review_status'] != 'availability_passed', r['repository'], r['issue_number']))
    jobs = [{'repository': r['repository'], 'issue_number': r['issue_number'],
             'github_issue_id': str(r['github_issue_id']), 'status': 'pending'} for r in rows]
    save(directory / 'jobs.json', jobs)
    save(directory / 'manifest.json', {'created_at': datetime.now(timezone.utc).isoformat(),
        'source': str(inventory), 'source_sha256': hashlib.sha256(inventory.read_bytes()).hexdigest(),
        'scope': 'All original inventory identities; current Issue verification and feature refresh',
        'model_inference': False, 'job_count': len(jobs)})
    for name in ['oss_mentor_web.sqlite3', 'oss_mentor.sqlite3']:
        source = sqlite3.connect((ROOT / 'data' / name).resolve().as_uri() + '?mode=ro', uri=True)
        destination = sqlite3.connect(directory / (name + '.before'))
        try:
            source.backup(destination)
        finally:
            source.close()
            destination.close()


def process(directory, limit, *, client=None):
    jobs = json.loads((directory / 'jobs.json').read_text(encoding='utf-8'))
    client = client or ConnectorReplay(directory / 'evidence')
    stores = [SQLiteCandidateStorage(ROOT / 'data' / name, ROOT / 'db/sqlite/001_mvp.sql')
              for name in ['oss_mentor_web.sqlite3', 'oss_mentor.sqlite3']]
    synchronizer = CandidateSynchronizer(None, client, stores[0])
    active, errors, healthy = 0, Counter(), {}
    for job in jobs:
        if job['status'] != 'pending':
            continue
        active += 1
        if active > limit:
            break
        repo = job['repository']
        try:
            if repo not in healthy:
                health = synchronizer.fetch_repository_health(repo)
                payload = client.get('/repos/' + repo).payload
                if payload.get('full_name', '').casefold() != repo.casefold() or payload.get('private') is not False:
                    raise ValueError('Repository identity/public status changed')
                if (type(payload.get('id')) is not int or type(payload.get('archived')) is not bool
                        or type(payload.get('disabled')) is not bool):
                    raise ValueError('Incomplete repository status evidence')
                healthy[repo] = health
                for store in stores:
                    with store.connect() as connection:
                        existing = connection.execute('SELECT github_repository_id FROM repository WHERE full_name=?', (repo,)).fetchone()
                    if existing and existing[0] is not None and str(existing[0]) != str(health['github_repository_id']):
                        raise ValueError('Repository global identity changed')
                    store.save_repository_health(repository={'full_name': repo, 'ecosystems_last_synced_at': None, **health})
            detail = client.get(f"/repos/{repo}/issues/{job['issue_number']}")
            payload = detail.payload
            if (not isinstance(payload, dict) or payload.get('number') != job['issue_number']
                    or str(payload.get('id')) != job['github_issue_id']
                    or payload.get('repository_url', '').casefold() != f'https://api.github.com/repos/{repo}'.casefold()
                    or payload.get('state') not in {'open', 'closed'}
                    or type(payload.get('locked')) is not bool or not isinstance(payload.get('assignees'), list)):
                raise ValueError('Incomplete or mismatched Issue evidence')
            preliminary = synchronizer._with_source(normalize_github_issue(payload), source_system='github_rest',
                                                   fetched_at=detail.fetched_at.isoformat())
            preliminary['is_pull_request'] = 'pull_request' in payload
            preliminary['primary_language'] = client.get('/repos/' + repo).payload.get('language')
            if job.get('admission_policy'):
                preliminary['admission_policy'] = job['admission_policy']
            features = extract_task_features(preliminary)
            review = task_review_reasons({**preliminary, **features.to_dict(),
                                         'requirements': infer_skill_requirements(preliminary, features)})
            deferred = bool(review and payload['state'] == 'open' and not payload['assignees'] and not payload['locked'])
            if deferred:
                # Scope/skill gates already prevent recommending this task. Save
                # the current detail and explicit review reasons without claiming
                # ownership or linked-PR verification; defer those expensive reads.
                record = {**preliminary, 'context_review_reasons': list(review), 'has_linked_open_pr': None}
            else:
                record = synchronizer.fetch_current_issue(repo, job['issue_number'], admission_policy=job['admission_policy']) if job.get('admission_policy') else synchronizer.fetch_current_issue(repo, job['issue_number'])
            if str(record.get('github_issue_id')) != job['github_issue_id']:
                raise ValueError('Issue global identity changed')
            eligibility = evaluate_candidate(record)
            # Verify both target identities before updating either database.
            target_ids = []
            for store in stores:
                with store.connect() as connection:
                    row = connection.execute('''SELECT tc.repository_id,tc.github_issue_id FROM task_candidate tc
                        JOIN repository r USING(repository_id) WHERE r.full_name=? AND tc.issue_number=?''',
                        (repo, job['issue_number'])).fetchone()
                if not row or str(row['github_issue_id']) != job['github_issue_id']:
                    raise ValueError('Destination identity mismatch')
                target_ids.append(row['repository_id'])
            for store, repository_id in zip(stores, target_ids):
                store.save_candidate(repository_id=repository_id, record=record, eligibility=eligibility)
            deferred_status = 'not_admitted_content' if job.get('admission_policy') else 'needs_task_review'
            job.update(status=deferred_status if deferred else 'verified', verified_at=record['github_verified_at'],
                       eligibility=eligibility.eligibility, reasons=list(eligibility.reasons),
                       feature_review_reasons=list(review),
                       verification_scope='detail_only' if deferred else 'production_checks')
        except RequestBudgetExhausted:
            break
        except PendingRequest:
            pass
        except GitHubApiError as exc:
            job.update(status='needs_retry', error_type=type(exc).__name__, http_status=exc.status_code)
            errors[type(exc).__name__] += 1
            if exc.status_code in {401, 403, 429}:
                break
        except Exception as exc:
            job.update(status='needs_retry', error_type=type(exc).__name__)
            errors[type(exc).__name__] += 1
        if active % 50 == 0:
            save(directory / 'jobs.json', jobs)
    save(directory / 'jobs.json', jobs)
    save(directory / 'requests.json', list(client.pending.values()))
    counts = dict(Counter(j['status'] for j in jobs))
    summary = {'observed_at': datetime.now(timezone.utc).isoformat(), 'total': len(jobs),
               'job_status': counts, 'pending_network_requests': len(client.pending),
               'verified_eligibility': dict(Counter(j.get('eligibility') for j in jobs if j['status'] in {'verified', 'needs_task_review', 'not_admitted_content'})),
               'github_request_count': client.request_count,
               'rate_limit_remaining': client.rate_limit_remaining,
               'errors': dict(errors),
               'database_matchable': {store.database_path.name: len(store.matchable_candidates()) for store in stores}}
    save(directory / 'progress.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['initialize', 'process', 'seal', 'collect'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--request-budget', type=int, default=3000)
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--workers', type=int, default=4, choices=range(1, 9))
    parser.add_argument('--until-done', action='store_true', help='Process the finite queue, waiting for quota and retrying source failures at most twice.')
    args = parser.parse_args()
    if args.command == 'initialize':
        initialize(args.directory)
    elif args.command == 'seal':
        count = 0
        for path in (args.directory / 'evidence').glob('*.incoming.json'):
            entry = json.loads(path.read_text(encoding='utf-8'))
            if 'payload' in entry:
                entry['payload_sha256'] = hashlib.sha256(json.dumps(entry['payload'], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            target = path.with_name(path.name.replace('.incoming.json', '.json'))
            if target.exists():
                raise ValueError('Refusing to overwrite source evidence')
            save(target, entry)
            path.unlink()
            count += 1
        print(json.dumps({'sealed': count}))
    elif args.command == 'collect':
        if not args.allow_network or not 1 <= args.request_budget <= 5000 or args.limit < 1:
            parser.error('Use --allow-network with a request budget of 1..5000 and a positive limit')
        token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
        if not token:
            credential = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True, timeout=15)
            token = credential.stdout.strip() if credential.returncode == 0 else None
        if not token:
            print(json.dumps({'status': 'authentication_required',
                              'next_step': 'Run gh auth login locally, then rerun this command; do not send a token in chat.'}))
            raise SystemExit(2)
        runner = collect_until_done if args.until_done else collect_waves
        try:
            runner(args.directory, token, args.request_budget, args.limit, args.workers)
        except Exception as exc:
            save(args.directory / 'runner_state.json', {
                'pid': os.getpid(), 'phase': 'failed', 'error_type': type(exc).__name__,
                'observed_at': datetime.now(timezone.utc).isoformat()})
            raise
    else:
        process(args.directory, args.limit)
