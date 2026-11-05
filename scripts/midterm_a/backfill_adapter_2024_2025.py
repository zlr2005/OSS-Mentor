"""有界、可恢复地补采 2024/2025 画像 adapter 历史证据；只读 GitHub。"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from oss_mentor.profiling.assessment import _fuse_feature, _normalize_external_evidence, load_questionnaire
from oss_mentor.profiling.evidence import interpret_github_contributions
from oss_mentor.datasets.code_languages import code_language

SOURCE = ROOT / 'datasets/b_real_contributions_v0.2'
OUT = ROOT / 'data/midterm/working/advanced_adapter_backfill_2024_2025_v1'
SEEDS = OUT / 'verified_seed_identities.json'
if not SEEDS.exists():
    SEEDS = ROOT / 'data/midterm/working/advanced_adapter_pilot_20261102/seed_identities.json'
LOCK = threading.Lock()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def save(name, payload):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f'.{os.getpid()}.{threading.get_ident()}.tmp')
    temporary.write_bytes(canonical(payload))
    temporary.replace(path)


def lines(name, payload):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b''.join(canonical(r) for r in payload))


def dt(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone required')
    return parsed.astimezone(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def quarter(value):
    value = dt(value)
    return value.replace(month=((value.month - 1) // 3) * 3 + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k.lower() not in {'email', 'token', 'access_token'}}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


class API:
    """用已登录的 gh 执行只读请求；凭据从不进入 Python 参数或文件。"""
    def __init__(self, network=False):
        self.network = network
        self.count = 0

    def call(self, *, query=None, endpoint=None):
        if (query is None) == (endpoint is None):
            raise ValueError('Exactly one read request required')
        if query and (not query.lstrip().startswith('query {') or re.search(r'\bmutation\b', query)):
            raise ValueError('Only explicit GraphQL read queries allowed')
        if endpoint and not re.match(r'^/repos/[\w.-]+/[\w.-]+/(commits|git/blobs|pulls)(/|\?)', endpoint):
            raise ValueError('REST endpoint outside read allowlist')
        identity = digest(canonical({'query': query, 'endpoint': endpoint}))
        path = OUT / 'api_cache' / (identity + '.json')
        if path.exists():
            envelope = read(path)
            if envelope['request'] != {'query': query, 'endpoint': endpoint}:
                raise ValueError('Cache request mismatch')
            if digest(canonical(envelope['payload'])) != envelope['payload_sha256']:
                raise ValueError('Cache checksum mismatch')
            return envelope['payload'], path.relative_to(OUT).as_posix()
        if not self.network:
            raise ValueError('Uncached request requires --allow-network')
        request_file = OUT / 'requests' / (identity + '.json')
        request_file.parent.mkdir(parents=True, exist_ok=True)
        if query:
            request_file.write_bytes(canonical({'query': query}))
            command = ['gh', 'api', 'graphql', '--input', str(request_file)]
        else:
            command = ['gh', 'api', '--method', 'GET', endpoint]
        for attempt in range(3):
            try:
                result = subprocess.run(command, capture_output=True, timeout=65, cwd=ROOT)
                if result.returncode:
                    raise ValueError('GitHub read failed; credentials and stderr not logged')
                payload = json.loads(result.stdout)
                if isinstance(payload, dict) and payload.get('errors'):
                    raise ValueError('GraphQL errors: ' + ','.join(str(e.get('type', e.get('message', 'error')))[:150] for e in payload['errors']))
                payload = scrub(payload)
                envelope = {'source_system': 'authenticated_github_cli',
                    'source_url': 'https://api.github.com/graphql' if query else 'https://api.github.com' + endpoint,
                    'observed_at': iso(datetime.now(timezone.utc)), 'request': {'query': query, 'endpoint': endpoint},
                    'projection_not_raw': True, 'projection_note': 'Credential and email keys removed; HTTP metadata not invented.',
                    'payload_sha256': digest(canonical(payload)), 'payload': payload}
                with LOCK:
                    save('api_cache/' + identity + '.json', envelope)
                    self.count += 1
                return payload, path.relative_to(OUT).as_posix()
            except (ValueError, OSError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))


AUTHOR = 'author { login ... on User { databaseId } }'
PAGE = 'pageInfo { hasNextPage endCursor }'
ISSUE_FIELDS = '''url number repository { nameWithOwner } createdAt closedAt state title body lastEditedAt
timelineItems(first:100,itemTypes:[CLOSED_EVENT,REOPENED_EVENT,RENAMED_TITLE_EVENT]) {
pageInfo { hasNextPage endCursor }
nodes { __typename ... on ClosedEvent { createdAt closer { __typename ... on PullRequest { url mergedAt } } }
... on ReopenedEvent { createdAt } ... on RenamedTitleEvent { createdAt previousTitle currentTitle } } }'''


def plan(repo_count, extend=False):
    path = OUT / 'collection_plan.json'
    previous = read(path) if path.exists() else None
    if previous and (not extend or repo_count <= len(previous['repositories'])):
        return previous
    repos = rows(SOURCE / 'tables/repositories.jsonl')
    people = defaultdict(set)
    for c in rows(SOURCE / 'tables/contributions.jsonl'):
        if c['author_association_status'] == 'verified':
            people[c['repository_key']].add(c['developer_key'])
    selected = []
    languages = ['Python', 'TypeScript', 'JavaScript', 'Rust', 'Go', 'Java']
    for language in languages:
        candidates = sorted([r for r in repos if r['primary_language'] == language],
                            key=lambda r: (-len(people[r['repository_key']]), r['full_name'].casefold()))
        selected.extend(candidates[:max(1, repo_count // len(languages))])
    chosen = {r['repository_key'] for r in selected}
    # 语言分布不均，并有 C/C#/C++ 等少量仓库；扩大到全范围时也要覆盖它们。
    selected.extend(sorted([r for r in repos if r['repository_key'] not in chosen],
                           key=lambda r: (-len(people[r['repository_key']]), r['full_name'].casefold())))
    selected = selected[:repo_count]
    result = {'status': 'working', 'frozen': False, 'years': [2024, 2025],
        'repositories': [{'name': r['full_name'], 'seed_primary_language': r['primary_language']} for r in selected],
        'selection': 'Within original 309 repos; top verified seed-author coverage per seed primary language. Current language only stratifies selection, never historical features.',
        'search_return_limit_per_repo_year': 100, 'detail_limit_per_repo_year': 12,
        'detail_limit_per_author_year': 4, 'observation_days': 90,
        'profile_cutoffs': 'Calendar quarter starts; strictly before PR creation; omit 2024 Q1 to keep contributions in requested 2024/2025 scope.',
        'history_commits_per_profile': 3, 'files_per_commit': 3, 'control_candidates_per_repo_cutoff': 3,
        'source_seed_sha256': digest(SEEDS.read_bytes()),
        'limitations': ['Bounded search is not complete history.', 'No 2026 contributions or outcomes.',
            'Profiles are research previews pending B review, not formal frozen profiles.',
            'No model training or teacher database writes.']}
    if previous:
        prior_hash = digest(canonical(previous))
        save('prior_plans/' + prior_hash + '.json', previous)
        result['parent_plan_sha256'] = prior_hash
        if not {r['name'] for r in previous['repositories']} <= {r['name'] for r in result['repositories']}:
            raise ValueError('Scope extension must preserve previous repositories')
    save('collection_plan.json', result)
    return result


def discover(api, settings):
    seeds = read(SEEDS)
    jobs = [(r['name'], year) for r in settings['repositories'] for year in settings['years']]
    searches, selected = [], []
    for offset in range(0, len(jobs), 4):
        chunk = jobs[offset:offset + 4]
        fields = []
        for index, (repo, year) in enumerate(chunk):
            lower = f'{year}-04-01' if year == 2024 else f'{year}-01-01'
            query = f'repo:{repo} is:pr is:merged linked:issue created:{lower}..{year}-12-31 merged:{lower}..{year}-12-31 sort:created-asc'
            fields.append(f'q{index}: search(query:{json.dumps(query)},type:ISSUE,first:100) {{ issueCount {PAGE} nodes {{ ... on PullRequest {{ number url createdAt mergedAt {AUTHOR} }} }} }}')
        payload, ref = api.call(query='query {\n' + '\n'.join(fields) + '\n}')
        for index, (repo, year) in enumerate(chunk):
            data = payload['data'][f'q{index}']
            nodes = [n for n in data['nodes'] if n and str((n.get('author') or {}).get('databaseId')) in seeds]
            searches.append({'repository': repo, 'year': year, 'total_matches': data['issueCount'],
                'returned': len(data['nodes']), 'verified_seed_authors_prs': len(nodes),
                'search_truncated': data['pageInfo']['hasNextPage'],
                'continuation_cursor': data['pageInfo']['endCursor'], 'evidence_ref': ref})
            by_author = defaultdict(list)
            for node in nodes:
                by_author[node['author']['login'].casefold()].append(node)
            # 每个作者轮流取样，避免一个维护者占满整个仓库年份。
            picked = []
            for rank in range(settings['detail_limit_per_author_year']):
                for author in sorted(by_author):
                    if len(by_author[author]) > rank:
                        picked.append(by_author[author][rank])
            for node in picked[:settings['detail_limit_per_repo_year']]:
                selected.append({'repository': repo, 'year': year, **node, 'search_evidence_ref': ref,
                    'seed_identity': seeds[str(node['author']['databaseId'])]})
        save('search_coverage.json', searches)
        save('remaining_search_jobs.json', [{'repository': r['repository'], 'year': r['year'],
            'after_cursor': r['continuation_cursor'], 'known_search_matches': r['total_matches'],
            'returned_so_far': r['returned'], 'reason': 'bounded_first_page_not_complete_history'}
            for r in searches if r['search_truncated']])
        save('selected_prs.json', selected)
        print(json.dumps({'phase': 'discover', 'repo_years_done': len(searches), 'selected_prs': len(selected)}, ensure_ascii=False), flush=True)
    save('base_selected_prs.json', selected)


def issue_at(issue, cutoff):
    """从完整关闭/重开/改名事件回放可用性和标题，单独验证正文时间。"""
    timeline = issue['timelineItems']
    if timeline['pageInfo']['hasNextPage']:
        return None, 'issue_timeline_truncated'
    if dt(issue['createdAt']) > cutoff:
        return None, 'issue_created_after_quarter_cutoff'
    state = 'OPEN'
    nodes = sorted(timeline['nodes'], key=lambda n: n['createdAt'])
    for event in nodes:
        if dt(event['createdAt']) > cutoff:
            break
        if event['__typename'] == 'ClosedEvent':
            state = 'CLOSED'
        elif event['__typename'] == 'ReopenedEvent':
            state = 'OPEN'
    if state != 'OPEN':
        return None, 'issue_closed_at_cutoff'
    title = issue['title']
    for event in reversed(nodes):
        if event['__typename'] != 'RenamedTitleEvent' or dt(event['createdAt']) <= cutoff:
            continue
        if title != event['currentTitle']:
            return None, 'inconsistent_title_history'
        title = event['previousTitle']
    if 'lastEditedAt' not in issue or (issue['lastEditedAt'] is not None and dt(issue['lastEditedAt']) > cutoff):
        return None, 'body_edited_after_cutoff_or_unknown'
    text = {'title': title, 'body': issue['body']}
    return {'issue_key': issue['url'].split('github.com/')[1].replace('/issues/', '#').casefold(),
        'as_of': iso(cutoff), 'title_at_cutoff': title, 'body_at_cutoff': issue['body'],
        'body_last_edited_at': issue['lastEditedAt'], 'content_sha256': digest(canonical(text)),
        'source_url': issue['url'], 'available_from': issue['createdAt'],
        'availability_note': 'Open at this cutoff by full returned close/reopen event replay; intervals not flattened.',
        'historical_labels_status': 'not_reconstructed', 'historical_text_verified': True}, None


def discover_pages(api, settings):
    """对饱和年度搜索继续第 2/3 页，保留旧 PR 并优先增加不同作者。"""
    seeds = read(SEEDS)
    coverage = read(OUT / 'search_coverage.json')
    baseline = read(OUT / 'selected_prs.json')
    baseline_path = OUT / 'pagination_baseline_prs.json'
    if not baseline_path.exists():
        save('pagination_baseline_prs.json', baseline)
    else:
        baseline = read(baseline_path)
    selected = list(baseline)
    seen = {r['url'].casefold() for r in selected}
    author_counts = Counter((r['repository'], r['year'], r['author']['databaseId']) for r in selected)
    added_counts = Counter()
    results = []
    active = [dict(r) for r in coverage if r['search_truncated']]
    save('pagination_plan.json', {'page_numbers': [2, 3], 'records_per_page': 100,
        'additional_detail_prs_per_repository_year': 4,
        'selection': 'Preserve previous PRs; verified numeric IDs only; prefer authors absent from earlier selection.',
        'source_coverage_sha256': digest(canonical(coverage))})
    for page in [2, 3]:
        following = []
        for offset in range(0, len(active), 4):
            chunk = active[offset:offset + 4]
            fields = []
            for index, item in enumerate(chunk):
                repo, year = item['repository'], item['year']
                lower = f'{year}-04-01' if year == 2024 else f'{year}-01-01'
                query = f'repo:{repo} is:pr is:merged linked:issue created:{lower}..{year}-12-31 merged:{lower}..{year}-12-31 sort:created-asc'
                fields.append(f'q{index}: search(query:{json.dumps(query)},type:ISSUE,first:100,after:{json.dumps(item["continuation_cursor"])}) {{ issueCount {PAGE} nodes {{ ... on PullRequest {{ number url createdAt mergedAt {AUTHOR} }} }} }}')
            payload, ref = api.call(query='query {\n' + '\n'.join(fields) + '\n}')
            for index, item in enumerate(chunk):
                data = payload['data'][f'q{index}']
                nodes = [n for n in data['nodes'] if n and str((n.get('author') or {}).get('databaseId')) in seeds and n['url'].casefold() not in seen]
                repo, year = item['repository'], item['year']
                nodes.sort(key=lambda n: (author_counts[(repo, year, n['author']['databaseId'])], n['createdAt'], n['url']))
                for node in nodes:
                    if added_counts[(repo, year)] >= 4:
                        break
                    author_key = (repo, year, node['author']['databaseId'])
                    if author_counts[author_key] >= settings['detail_limit_per_author_year']:
                        continue
                    selected.append({'repository': repo, 'year': year, **node, 'search_evidence_ref': ref,
                        'seed_identity': seeds[str(node['author']['databaseId'])]})
                    seen.add(node['url'].casefold())
                    added_counts[(repo, year)] += 1
                    author_counts[author_key] += 1
                result = {'repository': repo, 'year': year, 'page': page, 'returned': len(data['nodes']),
                    'total_matches': data['issueCount'], 'verified_seed_authors_prs': len(nodes),
                    'search_truncated': data['pageInfo']['hasNextPage'],
                    'continuation_cursor': data['pageInfo']['endCursor'], 'evidence_ref': ref}
                results.append(result)
                if result['search_truncated']:
                    following.append(result)
            save('pagination_search_coverage.json', results)
            save('selected_prs.json', selected)
            print(json.dumps({'phase': 'pagination_discover', 'page': page, 'repo_year_pages_done': len(results),
                'selected_prs': len(selected), 'added_prs': len(selected) - len(baseline)}), flush=True)
        active = following
    save('pagination_remaining_jobs.json', active)


def complete_searches(api, settings):
    """穷尽剩余年度索引；超过 1000 条时按创建日期二分，不升级未核实样本。"""
    jobs = read(OUT / 'pagination_remaining_jobs.json')
    initial = read(OUT / 'search_coverage.json') + read(OUT / 'pagination_search_coverage.json')

    def collect(job):
        repo, year = job['repository'], job['year']
        lower = f'{year}-04-01' if year == 2024 else f'{year}-01-01'
        upper = f'{year}-12-31'
        urls = set()
        prefix_refs = sorted({r['evidence_ref'] for r in initial if r['repository'] == repo and r['year'] == year})
        for ref in prefix_refs:
            for value in read(OUT / ref)['payload']['data'].values():
                if not isinstance(value, dict):
                    continue
                for node in value.get('nodes', []):
                    if node and node.get('url', '').split('github.com/')[-1].split('/pull/')[0].casefold() == repo.casefold() and node.get('createdAt', '')[:4] == str(year):
                        urls.add(node['url'])
        start_cursor = job['continuation_cursor'] if job['total_matches'] <= 1000 else None
        pending = [(lower, upper, start_cursor)]
        leaves, unresolved = [], []
        while pending:
            begin, end, cursor = pending.pop()
            refs, returned = [], 0
            while True:
                q = f'repo:{repo} is:pr is:merged linked:issue created:{begin}..{end} merged:{lower}..{upper} sort:created-asc'
                after = ',after:' + json.dumps(cursor) if cursor else ''
                query = 'query { q0: search(query:' + json.dumps(q) + ',type:ISSUE,first:100' + after + ') { issueCount ' + PAGE + ' nodes { ... on PullRequest { number url createdAt mergedAt ' + AUTHOR + ' } } } }'
                payload, ref = api.call(query=query)
                data = payload['data']['q0']
                refs.append(ref)
                if data['issueCount'] > 1000:
                    if begin == end:
                        unresolved.append({'repository': repo, 'year': year, 'start': begin, 'end': end,
                            'reason': 'single_day_search_exceeds_1000', 'evidence_ref': ref})
                    else:
                        lo, hi = datetime.fromisoformat(begin), datetime.fromisoformat(end)
                        middle = lo + timedelta(days=(hi - lo).days // 2)
                        pending.append(((middle + timedelta(days=1)).date().isoformat(), end, None))
                        pending.append((begin, middle.date().isoformat(), None))
                    break
                for node in data['nodes']:
                    if node:
                        urls.add(node['url'])
                returned += len(data['nodes'])
                if not data['pageInfo']['hasNextPage']:
                    leaves.append({'start': begin, 'end': end, 'query_matches': data['issueCount'],
                        'returned_in_this_stage': returned, 'evidence_refs': refs, 'continued_after_page_3': bool(start_cursor and begin == lower and end == upper)})
                    break
                cursor = data['pageInfo']['endCursor']
        return {'repository': repo, 'year': year, 'unique_indexed_pr_urls': len(urls),
            'complete': not unresolved, 'prefix_evidence_refs': prefix_refs, 'leaf_ranges': leaves,
            'unresolved_ranges': unresolved}

    results, failures = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(collect, job): job for job in jobs}
        for done, future in enumerate(as_completed(futures), 1):
            try:
                results.append(future.result())
            except Exception as exc:
                job = futures[future]
                failures.append({'repository': job['repository'], 'year': job['year'], 'reason': type(exc).__name__})
            results.sort(key=lambda r: (r['repository'], r['year']))
            save('complete_search_coverage.json', results)
            save('complete_search_remaining.json', failures + [x for r in results for x in r['unresolved_ranges']])
            save('search_completion.json', {'root_ranges': len(jobs), 'root_ranges_processed': done,
                'root_ranges_complete': sum(r['complete'] for r in results),
                'unique_pr_urls_in_completed_roots': sum(r['unique_indexed_pr_urls'] for r in results if r['complete']),
                'failures': len(failures), 'status': 'complete' if done == len(jobs) and not failures and all(r['complete'] for r in results) else 'working',
                'scope_note': 'Exhausts accessible search results for defined 2024/2025 queries, not every public contribution or verified training outcome.'})
            if done % 5 == 0 or done == len(jobs):
                print(json.dumps({'phase': 'complete_discovery', 'root_ranges_done': done,
                    'root_ranges_total': len(jobs), 'complete': sum(r['complete'] for r in results), 'failures': len(failures)}), flush=True)


def details(api, settings):
    selected = read(OUT / 'selected_prs.json')
    outcomes, failures = [], []
    known = {}
    for path in sorted((OUT / 'api_cache').glob('*.json')):
        cached = read(path)
        if digest(canonical(cached['payload'])) != cached['payload_sha256']:
            raise ValueError('Cache checksum mismatch')
        if not isinstance(cached['payload'], dict):
            continue
        for value in cached['payload'].get('data', {}).values():
            if isinstance(value, dict) and value.get('pullRequest'):
                pr = value['pullRequest']
                known[pr['url'].casefold()] = (pr, str(path.relative_to(OUT)))
    for offset in range(0, len(selected), 6):
        chunk = selected[offset:offset + 6]
        missing = [s for s in chunk if s['url'].casefold() not in known]
        fields = []
        for index, s in enumerate(missing):
            owner, repo = s['repository'].split('/')
            fields.append(f'q{index}: repository(owner:{json.dumps(owner)},name:{json.dumps(repo)}) {{ pullRequest(number:{s["number"]}) {{ number url createdAt mergedAt merged {AUTHOR} closingIssuesReferences(first:10) {{ {PAGE} nodes {{ {ISSUE_FIELDS} }} }} }} }}')
        if missing:
            data, ref = api.call(query='query {\n' + '\n'.join(fields) + '\n}')
            for index, s in enumerate(missing):
                known[s['url'].casefold()] = (data['data'][f'q{index}']['pullRequest'], ref)
        for seed in chunk:
            pr, ref = known[seed['url'].casefold()]
            if pr is None or not pr['merged'] or (pr.get('author') or {}).get('databaseId') != seed['author']['databaseId']:
                failures.append({'pr_url': seed['url'], 'reason': 'PR_identity_or_merge_mismatch'})
                continue
            cutoff = quarter(pr['createdAt'])
            if cutoff.year == 2024 and cutoff.month == 1:
                failures.append({'pr_url': pr['url'], 'reason': '2024_Q1_has_no_2024_pre_cutoff_history'})
                continue
            if pr['closingIssuesReferences']['pageInfo']['hasNextPage']:
                failures.append({'pr_url': pr['url'], 'reason': 'closing_issue_list_truncated'})
            found = 0
            for issue in pr['closingIssuesReferences']['nodes']:
                if issue['repository']['nameWithOwner'].casefold() != seed['repository'].casefold():
                    failures.append({'pr_url': pr['url'], 'reason': 'cross_repository_issue_outside_current_batch'})
                    continue
                proofs = [n for n in issue['timelineItems']['nodes'] if n['__typename'] == 'ClosedEvent'
                    and n.get('closer') and n['closer'].get('url') == pr['url']]
                if not proofs:
                    failures.append({'pr_url': pr['url'], 'issue_url': issue['url'], 'reason': 'no_explicit_actual_closing_PR_event'})
                    continue
                outcome_at = max(dt(pr['mergedAt']), dt(proofs[0]['createdAt']))
                if not cutoff < dt(pr['createdAt']) <= outcome_at <= cutoff + timedelta(days=90):
                    failures.append({'pr_url': pr['url'], 'issue_url': issue['url'], 'reason': 'outside_90_day_quarter_window'})
                    continue
                candidate, reason = issue_at(issue, cutoff)
                if reason:
                    failures.append({'pr_url': pr['url'], 'issue_url': issue['url'], 'reason': reason})
                    continue
                outcomes.append({'outcome_id': pr['url'] + '->' + candidate['issue_key'],
                    'year': cutoff.year, 'repository': seed['repository'],
                    'developer_key': pr['author']['login'].casefold(), 'github_numeric_id': pr['author']['databaseId'],
                    'seed_identity': seed['seed_identity'], 'profile_as_of': iso(cutoff),
                    'interaction_at': pr['createdAt'], 'outcome_at': iso(outcome_at),
                    'outcome_type': 'verified_closing_pr', 'verification_status': 'verified',
                    'pr_url': pr['url'], 'issue_key': candidate['issue_key'],
                    'issue_content': candidate, 'evidence_ref': ref})
                found += 1
            if not pr['closingIssuesReferences']['nodes']:
                failures.append({'pr_url': pr['url'], 'reason': 'no_current_closing_issue_reference'})
        lines('verified_outcomes.jsonl', outcomes)
        lines('outcome_exclusions.jsonl', failures)
        if (offset + 6) % 60 == 0 or offset + 6 >= len(selected):
            print(json.dumps({'phase': 'details', 'PRs_done': min(offset + 6, len(selected)), 'verified_temporal_outcomes': len(outcomes),
                              'developers': len({r['github_numeric_id'] for r in outcomes})}), flush=True)


def prefetch_details(api, settings):
    """有界并发预取 PR 证据，只写请求缓存，不改样本或排除表。"""
    selected = read(OUT / 'selected_prs.json')
    known = set()
    for path in (OUT / 'api_cache').glob('*.json'):
        envelope = read(path)
        payload = envelope['payload']
        if not isinstance(payload, dict):
            continue
        for value in payload.get('data', {}).values():
            if isinstance(value, dict) and value.get('pullRequest'):
                known.add(value['pullRequest']['url'].casefold())
    requests = []
    for offset in range(0, len(selected), 6):
        missing = [s for s in selected[offset:offset + 6] if s['url'].casefold() not in known]
        if not missing:
            continue
        fields = []
        for index, s in enumerate(missing):
            owner, repo = s['repository'].split('/')
            fields.append(f'q{index}: repository(owner:{json.dumps(owner)},name:{json.dumps(repo)}) {{ pullRequest(number:{s["number"]}) {{ number url createdAt mergedAt merged {AUTHOR} closingIssuesReferences(first:10) {{ {PAGE} nodes {{ {ISSUE_FIELDS} }} }} }} }}')
        requests.append('query {\n' + '\n'.join(fields) + '\n}')
    failures = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(api.call, query=query): digest(query.encode()) for query in requests}
        for index, future in enumerate(as_completed(futures), 1):
            try:
                future.result()
            except Exception as exc:
                failures.append({'request_sha256': futures[future], 'reason': type(exc).__name__})
            if index % 10 == 0 or index == len(futures):
                print(json.dumps({'phase': 'prefetch_details', 'batches_done': index,
                    'batches_total': len(futures), 'failures': len(failures)}), flush=True)
    lines('prefetch_failures.jsonl', sorted(failures, key=canonical))


def discover_late_2025(api, settings):
    """额外覆盖年中/年末，避免年度首 100 条搜索只覆盖 2025 年初。"""
    seeds = read(SEEDS)
    outcomes = rows(OUT / 'verified_outcomes.jsonl')
    counts = Counter(r['repository'] for r in outcomes if r['year'] == 2025)
    languages = sorted({r['seed_primary_language'] for r in settings['repositories']})
    repos = []
    for language in languages:
        group = [r['name'] for r in settings['repositories'] if r['seed_primary_language'] == language]
        repos.extend(sorted(group, key=lambda repo: (-counts[repo], repo.casefold()))[:2])
    if (OUT / 'late_2025_plan.json').exists():
        repos = read(OUT / 'late_2025_plan.json')['repositories']
    jobs = [(repo, start, end) for repo in repos for start, end in [
        ('2025-04-01', '2025-06-30'), ('2025-07-01', '2025-09-30'), ('2025-10-01', '2025-12-31')]]
    if (OUT / 'base_selected_prs.json').exists():
        selected = read(OUT / 'base_selected_prs.json')
    else:
        base_refs = {r['evidence_ref'] for r in read(OUT / 'search_coverage.json')}
        selected = [r for r in read(OUT / 'selected_prs.json') if r['search_evidence_ref'] in base_refs]
        save('base_selected_prs.json', selected)
    seen = {r['url'].casefold() for r in selected}
    coverage = []
    save('late_2025_plan.json', {'reason': 'Annual searches favor early-year outcomes; add later 2025 quarter cutoffs.',
        'repositories': repos, 'quarters': ['2025-Q2', '2025-Q3', '2025-Q4'],
        'search_limit_per_repo_quarter': 50, 'detail_limit_per_repo_quarter': 4,
        'seed_identity_only': True, 'no_2026_events': True})
    for offset in range(0, len(jobs), 4):
        chunk = jobs[offset:offset + 4]
        fields = []
        for index, (repo, start, end) in enumerate(chunk):
            q = f'repo:{repo} is:pr is:merged linked:issue created:{start}..{end} merged:2025-01-01..2025-12-31 sort:created-asc'
            fields.append(f'q{index}: search(query:{json.dumps(q)},type:ISSUE,first:50) {{ issueCount {PAGE} nodes {{ ... on PullRequest {{ number url createdAt mergedAt {AUTHOR} }} }} }}')
        payload, ref = api.call(query='query {\n' + '\n'.join(fields) + '\n}')
        for index, (repo, start, end) in enumerate(chunk):
            data = payload['data'][f'q{index}']
            nodes = [n for n in data['nodes'] if n and str((n.get('author') or {}).get('databaseId')) in seeds]
            coverage.append({'repository': repo, 'year': 2025, 'start': start, 'end': end,
                'returned': len(data['nodes']), 'total_matches': data['issueCount'],
                'verified_seed_authors_prs': len(nodes), 'search_truncated': data['pageInfo']['hasNextPage'],
                'continuation_cursor': data['pageInfo']['endCursor'], 'evidence_ref': ref})
            by_author = defaultdict(list)
            for node in nodes:
                if node['url'].casefold() not in seen:
                    by_author[node['author']['login'].casefold()].append(node)
            picked = []
            for rank in range(4):
                for author in sorted(by_author):
                    if len(by_author[author]) > rank:
                        picked.append(by_author[author][rank])
            for node in picked[:4]:
                selected.append({'repository': repo, 'year': 2025, **node,
                    'search_evidence_ref': ref, 'seed_identity': seeds[str(node['author']['databaseId'])]})
                seen.add(node['url'].casefold())
        save('late_2025_search_coverage.json', coverage)
        save('selected_prs.json', selected)
        print(json.dumps({'phase': 'late_2025_discover', 'repo_quarters_done': len(coverage), 'selected_prs': len(selected)}), flush=True)


def controls(api, settings):
    outcomes = rows(OUT / 'verified_outcomes.jsonl')
    keys = sorted({(r['repository'], r['profile_as_of']) for r in outcomes})
    records, failures, prepared = [], [], []
    for offset in range(0, len(keys), 4):
        chunk = keys[offset:offset + 4]
        fields = []
        for index, (repo, as_of) in enumerate(chunk):
            day = as_of[:10]
            q = f'repo:{repo} is:issue created:<{day} closed:>={day} sort:created-desc'
            fields.append(f'q{index}: search(query:{json.dumps(q)},type:ISSUE,first:8) {{ {PAGE} nodes {{ ... on Issue {{ {ISSUE_FIELDS} }} }} }}')
        prepared.append((offset, chunk, 'query {\n' + '\n'.join(fields) + '\n}'))
    responses = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(api.call, query=query): offset for offset, _, query in prepared}
        for done, future in enumerate(as_completed(futures), 1):
            responses[futures[future]] = future.result()
            if done % 10 == 0 or done == len(futures):
                print(json.dumps({'phase': 'control_reads', 'batches_done': done, 'batches_total': len(futures)}), flush=True)
    for offset, chunk, _ in prepared:
        data, ref = responses[offset]
        for index, (repo, as_of) in enumerate(chunk):
            kept = []
            positive = {r['issue_key'] for r in outcomes if r['repository'] == repo and r['profile_as_of'] == as_of}
            for issue in data['data'][f'q{index}']['nodes']:
                candidate, reason = issue_at(issue, dt(as_of))
                if candidate and candidate['issue_key'] not in positive:
                    kept.append({'repository': repo, 'as_of': as_of, 'role': 'unlabeled_control', **candidate, 'evidence_ref': ref})
                if len(kept) >= settings['control_candidates_per_repo_cutoff']:
                    break
            if not kept:
                day = as_of[:10]
                q = f'repo:{repo} is:issue created:<{day} closed:>={day} sort:created-desc'
                query = 'query { q0: search(query:' + json.dumps(q) + ',type:ISSUE,first:24) { ' + PAGE + ' nodes { ... on Issue { ' + ISSUE_FIELDS + ' } } } }'
                fallback, fallback_ref = api.call(query=query)
                for issue in fallback['data']['q0']['nodes']:
                    candidate, reason = issue_at(issue, dt(as_of))
                    if candidate and candidate['issue_key'] not in positive:
                        kept.append({'repository': repo, 'as_of': as_of, 'role': 'unlabeled_control', **candidate, 'evidence_ref': fallback_ref})
                    if len(kept) >= settings['control_candidates_per_repo_cutoff']:
                        break
            records.extend(kept)
            if not kept:
                failures.append({'repository': repo, 'as_of': as_of, 'reason': 'no_time_and_text_valid_control_in_bounded_search'})
        lines('historical_controls.jsonl', records)
        lines('control_exclusions.jsonl', failures)
        if (offset + 4) % 40 == 0 or offset + 4 >= len(keys):
            print(json.dumps({'phase': 'controls', 'repo_cutoffs_done': min(offset + 4, len(keys)), 'controls': len(records)}), flush=True)


def choose_files(files):
    groups = {'source': [], 'test': [], 'documentation': []}
    for file in files:
        if file.get('status') == 'removed':
            continue
        path = file['filename'].lower()
        parts = path.split('/')
        if any(p in {'tests', 'test', 'testing', 'spec', 'specs'} for p in parts) or parts[-1].startswith('test_'):
            groups['test'].append(file)
        elif any(p in {'docs', 'doc', 'documentation'} for p in parts) or path.endswith(('.md', '.rst', '.mdx', '.adoc')):
            groups['documentation'].append(file)
        elif code_language(file['filename']):
            groups['source'].append(file)
    return [values[0] for values in groups.values() if values]


def history(api, settings, repair=False):
    outcomes = rows(OUT / 'verified_outcomes.jsonl')
    controls_ = rows(OUT / 'historical_controls.jsonl')
    control_keys = {(c['repository'], c['as_of']) for c in controls_}
    groups = {}
    for r in outcomes:
        if (r['repository'], r['profile_as_of']) in control_keys:
            groups[(r['github_numeric_id'], r['repository'], r['profile_as_of'])] = r
    records, failures = [], []
    if repair:
        records = rows(OUT / 'historical_contributions.jsonl')
        failures = rows(OUT / 'history_exclusions.jsonl')
        observed_user_cutoffs = {(r['github_numeric_id'], r['as_of']) for r in records}
        tried = set(groups)
        repositories = {r['repository_key']: r['full_name'] for r in rows(SOURCE / 'tables/repositories.jsonl')}
        seed_repositories = defaultdict(set)
        for c in rows(SOURCE / 'tables/contributions.jsonl'):
            if c['author_association_status'] == 'verified':
                seed_repositories[c['developer_key']].add(repositories[c['repository_key']])
        missing = {}
        for key, r in groups.items():
            if (r['github_numeric_id'], r['profile_as_of']) not in observed_user_cutoffs:
                missing[(r['github_numeric_id'], r['profile_as_of'])] = r
        groups = {}
        for (numeric_id, as_of), r in sorted(missing.items()):
            alternatives = [repo for repo in sorted(seed_repositories[r['seed_identity']['developer_key']])
                if (numeric_id, repo, as_of) not in tried]
            for repo in alternatives[:2]:
                groups[(numeric_id, repo, as_of)] = {**r, 'repository': repo}
        save('history_repair_plan.json', {'missing_user_cutoffs_before_repair': len(missing),
            'additional_repo_cutoffs': len(groups), 'max_extra_seed_repositories_per_user_cutoff': 2,
            'selection_note': 'Current A seed membership selects where to query; only verified pre-cutoff commits enter features.',
            'no_new_repositories_outside_original_309': True})

    def collect(item):
        (_, repo, as_of), r = item
        until = iso(dt(as_of) - timedelta(seconds=1))
        endpoint = f'/repos/{repo}/commits?author={r["developer_key"]}&since=2024-01-01T00%3A00%3A00Z&until={until}&per_page=3'
        listed, listing_ref = api.call(endpoint=endpoint)
        result, errors = [], []
        if not listed:
            errors.append({'repository': repo, 'developer_key': r['developer_key'], 'as_of': as_of,
                'reason': 'no_pre_cutoff_commit_in_seed_repository'})
        for commit in listed:
            detail, ref = api.call(endpoint=f'/repos/{repo}/commits/{commit["sha"]}?per_page=100')
            author = detail.get('author') or {}
            if author.get('id') != r['github_numeric_id']:
                errors.append({'repository': repo, 'sha': detail['sha'], 'reason': 'historical_commit_author_identity_mismatch'})
                continue
            event_at, author_at = detail['commit']['committer']['date'], detail['commit']['author']['date']
            if not dt('2024-01-01T00:00:00Z') <= dt(event_at) < dt(as_of) or dt(author_at) >= dt(as_of):
                errors.append({'repository': repo, 'sha': detail['sha'], 'reason': 'historical_commit_time_invalid'})
                continue
            for file in choose_files(detail.get('files', [])):
                try:
                    blob, blob_ref = api.call(endpoint=f'/repos/{repo}/git/blobs/{file["sha"]}')
                    if blob.get('size', 0) > 262144:
                        errors.append({'repository': repo, 'sha': detail['sha'], 'path': file['filename'], 'reason': 'code_size_limit'})
                        continue
                    if blob.get('encoding') != 'base64':
                        raise ValueError('Unsupported blob encoding')
                    content = base64.b64decode(blob['content'])
                    if hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest() != file['sha']:
                        raise ValueError('Git blob hash mismatch')
                    content.decode('utf-8')
                    location = 'code/' + file['sha'] + '.txt'
                    path = OUT / location
                    path.parent.mkdir(exist_ok=True)
                    with LOCK:
                        if path.exists() and path.read_bytes() != content:
                            raise ValueError('Blob content conflict')
                        path.write_bytes(content)
                    key = digest(canonical([repo, file['filename'], detail['sha']]))
                    result.append({'schema_version': 'advanced-pilot-file-contribution-v0.1',
                        'developer_key': r['developer_key'], 'github_numeric_id': r['github_numeric_id'],
                        'source_developer_key': r['seed_identity']['developer_key'],
                        'identity_evidence': r['seed_identity']['identity_evidence'],
                        'repository': repo, 'as_of': as_of, 'commit_sha': detail['sha'],
                        'event_at': event_at, 'author_at': author_at,
                        'path': file['filename'], 'language': code_language(file['filename']),
                        'file_version_key': 'history-file:' + key, 'blob_sha': file['sha'],
                        'content_sha256': digest(content), 'content_location': location,
                        'source_url': detail['html_url'], 'evidence_ref': ref,
                        'blob_evidence_ref': blob_ref, 'listing_evidence_ref': listing_ref,
                        'file_list_may_be_truncated': len(detail.get('files', [])) >= 100,
                        'historical_visibility_note': 'Commit dates checked; independent first-public-availability not established.'})
                except (ValueError, OSError) as exc:
                    errors.append({'repository': repo, 'sha': detail['sha'], 'path': file['filename'], 'reason': type(exc).__name__})
        return result, errors

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(collect, item): item[0] for item in sorted(groups.items())}
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                result, errors = future.result()
                records.extend(result)
                failures.extend(errors)
            except Exception as exc:
                failures.append({'profile': list(futures[future]), 'reason': type(exc).__name__})
            records.sort(key=lambda r: (r['developer_key'], r['repository'], r['as_of'], r['file_version_key']))
            failures = sorted({canonical(f): f for f in failures}.values(), key=canonical)
            lines('historical_contributions.jsonl', records)
            lines('history_exclusions.jsonl', failures)
            if done % 10 == 0 or done == len(groups):
                print(json.dumps({'phase': 'history_repair' if repair else 'history', 'profiles_done': done, 'profiles_total': len(groups),
                    'file_contributions': len(records), 'failed_or_excluded': len(failures), 'new_network_calls': api.count}), flush=True)


def portable_evidence_paths(value, field=None):
    """只转换证据定位字段，保留 Git 路径、正文和原始缓存字节。"""
    path_fields = {'evidence_ref', 'blob_evidence_ref', 'listing_evidence_ref', 'search_evidence_ref',
                   'identity_evidence', 'evidence_location', 'content_location'}
    list_fields = {'evidence_refs', 'prefix_evidence_refs'}
    if isinstance(value, dict):
        return {k: portable_evidence_paths(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [portable_evidence_paths(v, 'evidence_ref' if field in list_fields else None) for v in value]
    if isinstance(value, str) and field in path_fields:
        return value.replace('\\', '/')
    return value


def normalize_source_paths():
    json_names = ['search_coverage', 'pagination_search_coverage', 'late_2025_search_coverage',
        'complete_search_coverage', 'pagination_remaining_jobs', 'complete_search_remaining',
        'selected_prs', 'base_selected_prs', 'pagination_baseline_prs']
    for name in json_names:
        path = OUT / (name + '.json')
        if path.exists():
            before = read(path)
            after = portable_evidence_paths(before)
            if before != after:
                save(name + '.json', after)
    for name in ['verified_outcomes', 'historical_controls', 'historical_contributions']:
        path = OUT / (name + '.jsonl')
        if path.exists():
            before = rows(path)
            after = portable_evidence_paths(before)
            if before != after:
                lines(name + '.jsonl', after)


def build_profiles():
    normalize_source_paths()
    config = load_questionnaire(ROOT / 'config/midterm_profile/questionnaire_v0.1.json')
    groups = defaultdict(list)
    for c in rows(OUT / 'historical_contributions.jsonl'):
        groups[(c['github_numeric_id'], c['as_of'])].append(c)
    profiles, batches = [], []
    for (numeric_id, as_of), contributions in sorted(groups.items()):
        contributions = list({c['file_version_key']: c for c in contributions}.values())
        contributions.sort(key=lambda c: c['file_version_key'])
        repository_scopes = sorted({c['repository'] for c in contributions})
        records, documents = [], []
        for c in contributions:
            records.append({'developer_key': c['developer_key'], 'repository_key': c['repository'].lower(),
                'file_key': c['path'], 'file_version_key': c['file_version_key'],
                'commit_sha': c['commit_sha'], 'behavior_type': 'commit', 'author_link_status': 'verified',
                'event_at': c['event_at'], 'evidence_location': (OUT / c['evidence_ref']).relative_to(ROOT).as_posix()})
            documents.append({'file_version_key': c['file_version_key'], 'repository_key': c['repository'].lower(), 'path': c['path'], 'available': True})
        observed_at = read(OUT / contributions[0]['evidence_ref'])['observed_at']
        batch = interpret_github_contributions(records, documents, developer_key=contributions[0]['developer_key'],
            observed_at=observed_at, as_of=as_of, collection_status='partial',
            collection_limitations=['3 commits per repository-author-cutoff; up to source/test/documentation file per commit.',
                'Only 2024/2025 history; no current questionnaire or target outcome code used.'])
        grouped = defaultdict(list)
        for e in _normalize_external_evidence(batch['evidence'], allowed_features=set(config['feature_names'])):
            grouped[e['feature_name']].append(e)
        fused = [_fuse_feature(name, grouped[name]) for name in config['feature_names']]
        profile = {'schema_version': 'advanced-pilot-temporal-profile-v0.1', 'profile_state': 'research_preview',
            'b_review_status': 'pending', 'training_ready': False,
            'profile_key': str(uuid5(NAMESPACE_URL, f'github:{numeric_id}')),
            'profile_version': digest(canonical([numeric_id, as_of, records])),
            'developer_key': contributions[0]['developer_key'], 'github_numeric_id': numeric_id,
            'repository_scopes': repository_scopes, 'as_of': as_of,
            'feature_schema_version': config['feature_schema_version'], 'feature_names': config['feature_names'],
            'values': [r['proposed_value'] for r in fused], 'observed_mask': [r['observed'] for r in fused],
            'locked_mask': [False] * 23, 'evidence_refs': [r['evidence_refs'] for r in fused], 'current_interests': []}
        profile['profile_hash'] = digest(canonical(profile))
        profiles.append(profile)
        batches.append({'profile_key': profile['profile_key'], 'profile_version': profile['profile_version'], **batch})
    lines('profile_previews.jsonl', profiles)
    lines('profile_evidence_batches.jsonl', batches)


def join_samples(outcomes, controls_, profiles):
    # 画像属于开发者和截止点，不要求开发者事先贡献过目标仓库。
    profile_keys = {(p['github_numeric_id'], p['as_of']): p for p in profiles}
    controls_by_cutoff = defaultdict(list)
    for c in controls_:
        controls_by_cutoff[(c['repository'], c['as_of'])].append(c)
    joined, exclusions = [], []
    for outcome in outcomes:
        profile = profile_keys.get((outcome['github_numeric_id'], outcome['profile_as_of']))
        candidates = controls_by_cutoff[(outcome['repository'], outcome['profile_as_of'])]
        # 对该开发者后来完成的 Issue 不作为其对照。
        positive_keys = {r['issue_key'] for r in outcomes if r['github_numeric_id'] == outcome['github_numeric_id']}
        candidates = [c for c in candidates if c['issue_key'] not in positive_keys]
        if not profile or not candidates:
            exclusions.append({'outcome_id': outcome['outcome_id'], 'reason': 'profile_or_control_missing'})
            continue
        joined.append({'outcome_id': outcome['outcome_id'], 'developer_key': outcome['developer_key'],
            'github_numeric_id': outcome['github_numeric_id'], 'repository': outcome['repository'],
            'as_of': outcome['profile_as_of'], 'outcome_at': outcome['outcome_at'],
            'profile_key': profile['profile_key'], 'profile_version': profile['profile_version'],
            'positive_issue_key': outcome['issue_key'], 'comparison_issue_keys': [c['issue_key'] for c in candidates],
            'target_repository_in_profile_history': outcome['repository'] in profile['repository_scopes'],
            'training_ready': False, 'status': 'evidence_joined_pending_B_C_acceptance'})
    return joined, exclusions


def build_pr_index():
    """把查询缓存整理为可复用的去重索引；索引记录不等于已核实结果。"""
    refs = {r['evidence_ref'] for r in read(OUT / 'search_coverage.json')}
    for name in ['pagination_search_coverage.json', 'late_2025_search_coverage.json']:
        if (OUT / name).exists():
            refs.update(r['evidence_ref'] for r in read(OUT / name))
    if (OUT / 'complete_search_coverage.json').exists():
        for root in read(OUT / 'complete_search_coverage.json'):
            refs.update(root['prefix_evidence_refs'])
            for leaf in root['leaf_ranges']:
                refs.update(leaf['evidence_refs'])
    seeds = read(SEEDS)
    indexed = {}
    for ref in sorted(refs):
        envelope = read(OUT / ref)
        for value in envelope['payload']['data'].values():
            if not isinstance(value, dict):
                continue
            for node in value.get('nodes', []):
                if not node or '/pull/' not in node.get('url', ''):
                    continue
                key = node['url'].casefold()
                if key not in indexed:
                    indexed[key] = {'node': node, 'observed_at': envelope['observed_at'], 'refs': set()}
                row = indexed[key]
                row['refs'].add(ref)
                if envelope['observed_at'] > row['observed_at']:
                    row.update(node=node, observed_at=envelope['observed_at'])
    records = []
    for key in sorted(indexed):
        item = indexed[key]
        n = item['node']
        author = n.get('author') or {}
        records.append({'schema_version': 'advanced-pilot-pr-index-v0.1',
            'pr_url': n['url'], 'repository': n['url'].split('github.com/')[1].split('/pull/')[0],
            'pr_number': n['number'], 'created_at': n['createdAt'], 'merged_at': n['mergedAt'],
            'github_login': author.get('login'), 'github_numeric_id': author.get('databaseId'),
            'verified_seed_author': str(author.get('databaseId')) in seeds,
            'outcome_verified': False, 'training_ready': False,
            'observed_at': item['observed_at'], 'evidence_refs': sorted(item['refs'])})
    lines('historical_pr_index.jsonl', records)
    lines('verified_seed_pr_index.jsonl', [r for r in records if r['verified_seed_author']])
    return {'unique_indexed_prs': len(records), 'indexed_prs_by_verified_seed_authors': sum(r['verified_seed_author'] for r in records)}


def report(settings):
    outcomes = rows(OUT / 'verified_outcomes.jsonl')
    controls_ = rows(OUT / 'historical_controls.jsonl') if (OUT / 'historical_controls.jsonl').exists() else []
    profiles = rows(OUT / 'profile_previews.jsonl') if (OUT / 'profile_previews.jsonl').exists() else []
    history_ = rows(OUT / 'historical_contributions.jsonl') if (OUT / 'historical_contributions.jsonl').exists() else []
    joined, exclusions = join_samples(outcomes, controls_, profiles)
    lines('joined_samples.jsonl', joined)
    lines('join_exclusions.jsonl', exclusions)
    lines('advanced_issue_outcomes.jsonl', [{'schema_version': 'advanced-issue-outcome-v0.1',
        'outcome_id': r['outcome_id'], 'developer_key': r['developer_key'], 'issue_key': r['issue_key'],
        'outcome_type': r['outcome_type'], 'interaction_at': r['interaction_at'], 'outcome_at': r['outcome_at'],
        'verification_status': r['verification_status'], 'source_url': r['pr_url'],
        'evidence_ref': r['evidence_ref']} for r in outcomes])
    candidate_snapshots = {(r['issue_content']['issue_key'], r['profile_as_of']): {'role': 'positive', **r['issue_content'],
        'evidence_ref': r['evidence_ref']} for r in outcomes}
    for c in controls_:
        candidate_snapshots.setdefault((c['issue_key'], c['as_of']), c)
    lines('historical_candidate_snapshots.jsonl', [candidate_snapshots[k] for k in sorted(candidate_snapshots)])
    coverage = read(OUT / 'search_coverage.json')
    supplementary = read(OUT / 'late_2025_search_coverage.json') if (OUT / 'late_2025_search_coverage.json').exists() else []
    paged = read(OUT / 'pagination_search_coverage.json') if (OUT / 'pagination_search_coverage.json').exists() else []
    completion = read(OUT / 'search_completion.json') if (OUT / 'search_completion.json').exists() else None
    unique_files = {(h['repository'], h['commit_sha'], h['path']): h for h in history_}
    index_summary = build_pr_index()
    summary = {'status': 'working', 'frozen': False, 'training_ready': False,
        'years': [2024, 2025], 'repositories_probed': len(settings['repositories']), **index_summary,
        'search_prs_returned': sum(r['returned'] for r in coverage),
        'late_2025_search_prs_returned': sum(r['returned'] for r in supplementary),
        'paginated_search_prs_returned': sum(r['returned'] for r in paged),
        'annual_ranges_still_truncated_after_paging': len(read(OUT / 'complete_search_remaining.json')) if completion and completion['status'] == 'complete' else len(read(OUT / 'pagination_remaining_jobs.json')) if (OUT / 'pagination_remaining_jobs.json').exists() else sum(r['search_truncated'] for r in coverage),
        'defined_search_coverage_complete': bool(completion and completion['status'] == 'complete'),
        'searches_truncated': sum(r['search_truncated'] for r in coverage),
        'late_2025_searches_truncated': sum(r['search_truncated'] for r in supplementary),
        'selected_prs': len(read(OUT / 'selected_prs.json')), 'verified_temporal_outcomes': len(outcomes),
        'verified_outcome_developers': len({r['github_numeric_id'] for r in outcomes}),
        'historical_file_contributions': len(history_), 'unique_history_commits': len({(h['repository'], h['commit_sha']) for h in history_}),
        'unique_file_contributions': len(unique_files),
        'profile_previews': len(profiles), 'distinct_profile_input_vectors': len({tuple(p['values'] + p['observed_mask']) for p in profiles}),
        'joined_samples': len(joined), 'joined_developers': len({r['github_numeric_id'] for r in joined}),
        'joined_repositories': len({r['repository'] for r in joined}),
        'joined_new_repository_samples': sum(not r['target_repository_in_profile_history'] for r in joined),
        'new_repository_flag_definition': 'Target repository absent from the collected pre-cutoff profile evidence; not a claim about complete GitHub history.',
        'sampled_comparison_pairs': sum(len(r['comparison_issue_keys']) for r in joined),
        'training_ready_samples': 0,
        'by_year': {str(y): {'verified_outcomes': sum(r['year'] == y for r in outcomes),
            'joined_samples': sum(dt(r['as_of']).year == y for r in joined),
            'file_contributions': sum(dt(h['event_at']).year == y for h in history_),
            'unique_file_contributions': sum(dt(h['event_at']).year == y for h in unique_files.values()),
            'unique_commits': len({(h['repository'], h['commit_sha']) for h in history_ if dt(h['event_at']).year == y})} for y in [2024, 2025]},
        'limitations': ['Bounded and outcome-enriched sample, not full 309-repo history.',
            'Profiles aggregate collected repositories per verified account and cutoff; coverage remains bounded and requires B review.',
            'Current body accepted only when explicit lastEditedAt is null or before cutoff; labels not reconstructed.',
            'Unselected candidates are not proven negatives.', 'No learned semantic profile extractor, C audit or training run.']}
    save('summary.json', summary)
    save('remaining_backfill_jobs.json', {
        'not_yet_probed_seed_repositories': sorted({r['full_name'] for r in rows(SOURCE / 'tables/repositories.jsonl')} - {r['name'] for r in settings['repositories']}),
        'saturated_annual_searches': [r for r in coverage if r['search_truncated']],
        'saturated_late_2025_searches': [r for r in supplementary if r['search_truncated']],
        'annual_continuation_after_page_3': read(OUT / 'complete_search_remaining.json') if completion and completion['status'] == 'complete' else read(OUT / 'pagination_remaining_jobs.json') if (OUT / 'pagination_remaining_jobs.json').exists() else [],
        'history_note': 'At most 3 commits per author-repository-cutoff; broadening histories remains a separate bounded stage.'})
    failure_counts = {}
    for name in ['outcome_exclusions', 'control_exclusions', 'history_exclusions', 'join_exclusions']:
        path = OUT / (name + '.jsonl')
        failure_counts[name] = dict(Counter(r['reason'] for r in rows(path))) if path.exists() else {}
    save('failure_summary.json', failure_counts)
    text = ['# 2024/2025 Advanced Adapter 历史补采', '',
        '状态：working / frozen=false；只读 GitHub，本地保存，未训练、未提交、未推送。', '',
        f"检查 {summary['repositories_probed']} 个既有仓库、两年；年度首屏返回 {summary['search_prs_returned']} 条记录，后续分页返回 {summary['paginated_search_prs_returned']} 条，另补查 2025 年中/年末 {summary['late_2025_search_prs_returned']} 条记录，按 PR URL 去重后选择 {summary['selected_prs']} 条已有核验身份的 PR 详细核查。",
        f"追加日期分片补读后，去重 PR 索引共 {summary['unique_indexed_prs']} 条，其中 {summary['indexed_prs_by_verified_seed_authors']} 条作者属于核验种子。索引仅用于发现候选，不作为已经解决 Issue 的标签。",
        f"确认 {len(outcomes)} 条满足季度时间、90 天结果窗口、实际关闭 PR 事件和历史标题/正文校验的结果。",
        f"补采 {len(history_)} 条按画像截止点关联的文件贡献记录，去重为 {len(unique_files)} 条文件贡献、{summary['unique_history_commits']} 个独立 commit；生成 {len(profiles)} 份画像预览。",
        f"最终串联 {len(joined)} 条样本，涉及 {summary['joined_developers']} 位开发者、{summary['joined_repositories']} 个仓库，形成 {summary['sampled_comparison_pairs']} 组未标记候选对照。", '',
        '| 年份 | 核实结果 | 已串联样本 | 去重文件贡献（按发生年） |', '|---|---:|---:|---:|']
    for year, counts in summary['by_year'].items():
        text.append(f"| {year} | {counts['verified_outcomes']} | {counts['joined_samples']} | {counts['unique_file_contributions']} |")
    text += ['', '上述数量不是正式训练集或效果指标；训练就绪数仍为 0。B 需审核画像语义、跨仓库聚合及 research_frozen 导出；C 需确认候选文本编码格式、输入审计、切分及向量。', '',
        '只收集 2024/2025 事件与结果，不使用 2026 最终画像回填。当前仓库语言只用于选择语言多样的采集范围，不进入历史特征。第一轮 5 条样例保持独立，未叠加冒充新人数。', '',
        '候选使用关闭/重开/改名事件完整回放；正文采用明确的最后编辑时间校验；目标 PR 的代码仅作结果，不进入截止前画像。大文件、无法核验作者、时间错误或正文截止后修改的记录保留排除原因。', '',
        f"首屏有 {summary['searches_truncated']} 个年度搜索饱和；继续分页和日期拆分后还有 {summary['annual_ranges_still_truncated_after_paging']} 个年度范围未取完。搜索覆盖完成只针对所定义查询的可访问结果；PR 证据核查和每个截止点的贡献历史仍是有界采样。补充季度首屏作为额外选样入口，另有 {summary['late_2025_searches_truncated']} 个饱和记录，不与年度索引重复累计。后续记录见 remaining_backfill_jobs.json。", '',
        '离线重算：`python scripts/midterm_a/backfill_adapter_2024_2025.py --stage report`；网络阶段可断点恢复已有请求缓存。', '',
        '详细统计：summary.json、failure_summary.json；逐条记录：joined_samples.jsonl、verified_outcomes.jsonl、historical_contributions.jsonl、profile_previews.jsonl。']
    (OUT / 'REPORT.md').write_text('\n'.join(text) + '\n', encoding='utf-8')
    save('manifest.json', {'schema_version': 'advanced-history-backfill-manifest-v0.1',
        'files_sha256': {p.relative_to(OUT).as_posix(): digest(p.read_bytes()) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name != 'manifest.json'},
        'script_sha256': digest(Path(__file__).read_bytes()),
        'B_rule_sha256': {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in [ROOT / 'src/oss_mentor/profiling/evidence.py', ROOT / 'src/oss_mentor/profiling/assessment.py']}})
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def verify():
    manifest = read(OUT / 'manifest.json')
    if manifest['script_sha256'] != digest(Path(__file__).read_bytes()):
        raise ValueError('Collector source changed after manifest')
    for relative, expected in manifest['files_sha256'].items():
        path = (OUT / relative).resolve()
        if not path.is_relative_to(OUT.resolve()) or digest(path.read_bytes()) != expected:
            raise ValueError('Manifest file mismatch: ' + relative)
    for relative, expected in manifest['B_rule_sha256'].items():
        if digest((ROOT / relative).read_bytes()) != expected:
            raise ValueError('B source rules changed')
    settings = read(OUT / 'collection_plan.json')
    if settings['source_seed_sha256'] != digest(SEEDS.read_bytes()):
        raise ValueError('Verified seed cohort changed')
    completion_path = OUT / 'search_completion.json'
    if completion_path.exists() and read(completion_path)['status'] == 'complete':
        expected_roots = {(r['repository'], r['year']) for r in read(OUT / 'pagination_remaining_jobs.json')}
        roots = read(OUT / 'complete_search_coverage.json')
        if {(r['repository'], r['year']) for r in roots} != expected_roots or len(roots) != len(expected_roots):
            raise ValueError('Completed search roots differ from requested gaps')
        for root in roots:
            if not root['complete'] or root['unresolved_ranges']:
                raise ValueError('Unresolved search range marked complete')
            lower = f"{root['year']}-04-01" if root['year'] == 2024 else f"{root['year']}-01-01"
            previous = datetime.fromisoformat(lower).date() - timedelta(days=1)
            for leaf in sorted(root['leaf_ranges'], key=lambda r: r['start']):
                if datetime.fromisoformat(leaf['start']).date() != previous + timedelta(days=1):
                    raise ValueError('Search date partitions have a gap or overlap')
                previous = datetime.fromisoformat(leaf['end']).date()
                last = read(OUT / leaf['evidence_refs'][-1])['payload']['data']['q0']
                if last['pageInfo']['hasNextPage'] or last['issueCount'] > 1000:
                    raise ValueError('Search leaf not exhausted')
            if previous.isoformat() != f"{root['year']}-12-31":
                raise ValueError('Search partitions miss year end')
    outcomes = rows(OUT / 'verified_outcomes.jsonl')
    if len({r['outcome_id'] for r in outcomes}) != len(outcomes):
        raise ValueError('Duplicate outcomes')
    for r in outcomes:
        cutoff = dt(r['profile_as_of'])
        if cutoff.year not in {2024, 2025} or not cutoff < dt(r['interaction_at']) <= dt(r['outcome_at']) <= cutoff + timedelta(days=90):
            raise ValueError('Outcome window invalid')
        ref = read(OUT / r['evidence_ref'])
        prs = [v['pullRequest'] for v in ref['payload']['data'].values() if isinstance(v, dict) and v.get('pullRequest')]
        pr = next(p for p in prs if p['url'] == r['pr_url'])
        issue = next(i for i in pr['closingIssuesReferences']['nodes'] if i['url'] == r['issue_content']['source_url'])
        candidate, reason = issue_at(issue, cutoff)
        if reason or candidate != r['issue_content']:
            raise ValueError('Historical Issue replay differs')
        if not any(e['__typename'] == 'ClosedEvent' and (e.get('closer') or {}).get('url') == pr['url'] for e in issue['timelineItems']['nodes']):
            raise ValueError('Explicit closing proof missing')
    contributions = rows(OUT / 'historical_contributions.jsonl')
    for c in contributions:
        content = (OUT / c['content_location']).read_bytes()
        if digest(content) != c['content_sha256'] or hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest() != c['blob_sha']:
            raise ValueError('Source blob mismatch')
        if dt(c['event_at']).year not in {2024, 2025} or not dt(c['event_at']) < dt(c['as_of']) or not dt(c['author_at']) < dt(c['as_of']):
            raise ValueError('History time invalid')
        detail = read(OUT / c['evidence_ref'])['payload']
        if (detail.get('author') or {}).get('id') != c['github_numeric_id']:
            raise ValueError('Commit author differs from seed account')
        for name in ['evidence_ref', 'blob_evidence_ref', 'listing_evidence_ref']:
            if not (OUT / c[name]).is_file():
                raise ValueError('Source reference missing')
    profiles = rows(OUT / 'profile_previews.jsonl')
    profile_versions = {(p['profile_key'], p['profile_version']): p for p in profiles}
    for p in profiles:
        expected = p.pop('profile_hash')
        if digest(canonical(p)) != expected or p['profile_state'] != 'research_preview' or p['training_ready']:
            raise ValueError('Profile preview hash/state invalid')
        if len(p['values']) != 23 or any(v != 0 for v, m in zip(p['values'], p['observed_mask']) if not m):
            raise ValueError('Unknown feature convention violated')
    summary = read(OUT / 'summary.json')
    samples = rows(OUT / 'joined_samples.jsonl')
    if len(samples) != summary['joined_samples'] or len(contributions) != summary['historical_file_contributions']:
        raise ValueError('Summary counts disagree')
    if any(s['training_ready'] for s in samples):
        raise ValueError('Unreviewed sample promoted to training')
    controls_ = rows(OUT / 'historical_controls.jsonl')
    valid_controls = {(c['issue_key'], c['as_of']) for c in controls_}
    for c in controls_:
        source = read(OUT / c['evidence_ref'])['payload']['data']
        issue = next(n for value in source.values() for n in value['nodes'] if n.get('url') == c['source_url'])
        candidate, reason = issue_at(issue, dt(c['as_of']))
        if reason or candidate['content_sha256'] != c['content_sha256']:
            raise ValueError('Control availability or historical text differs')
    outcome_by_id = {o['outcome_id']: o for o in outcomes}
    for sample in samples:
        profile = profile_versions[(sample['profile_key'], sample['profile_version'])]
        if profile['github_numeric_id'] != sample['github_numeric_id'] or profile['as_of'] != sample['as_of']:
            raise ValueError('Joined profile identity or cutoff differs')
        if sample['positive_issue_key'] != outcome_by_id[sample['outcome_id']]['issue_key']:
            raise ValueError('Joined positive differs')
        if not sample['comparison_issue_keys'] or any((key, sample['as_of']) not in valid_controls for key in sample['comparison_issue_keys']):
            raise ValueError('Joined control missing')
    index_path = OUT / 'historical_pr_index.jsonl'
    indexed_urls = set()
    seeded = 0
    seed_identities = read(SEEDS)
    if index_path.exists():
        with index_path.open(encoding='utf-8') as handle:
            for line in handle:
                row = json.loads(line)
                key = row['pr_url'].casefold()
                if key in indexed_urls or row['outcome_verified'] or row['training_ready']:
                    raise ValueError('PR index duplicated or promoted without verification')
                indexed_urls.add(key)
                if row['verified_seed_author'] != (str(row['github_numeric_id']) in seed_identities):
                    raise ValueError('Indexed seed membership differs')
                seeded += row['verified_seed_author']
                if dt(row['created_at']).year not in {2024, 2025} or dt(row['merged_at']).year not in {2024, 2025}:
                    raise ValueError('PR index outside requested years')
                if any(ref.replace('\\', '/') not in manifest['files_sha256'] for ref in row['evidence_refs']):
                    raise ValueError('Indexed PR source reference missing')
        if len(indexed_urls) != summary['unique_indexed_prs'] or seeded != summary['indexed_prs_by_verified_seed_authors']:
            raise ValueError('PR index counts disagree')
    print(json.dumps({'status': 'verified', 'files_checked': len(manifest['files_sha256']),
        'outcomes_checked': len(outcomes), 'file_contributions_checked': len(contributions),
        'profiles_checked': len(profiles), 'joined_samples_checked': len(samples),
        'pr_index_rows_checked': len(indexed_urls),
        'checks': ['file hashes', 'Git blob hashes', 'verified account IDs', 'actual closing PR events',
            'quarter/90-day windows', 'historical title/body replay', 'profile hashes', 'unknown masks', 'no 2026 events',
            'completed search partitions and last-page cursors']}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['discover', 'late-discover', 'page-discover', 'complete-discover', 'prefetch-details', 'details', 'controls', 'history', 'history-repair', 'report', 'verify', 'all'], default='all')
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--repositories', type=int, default=24)
    parser.add_argument('--extend-scope', action='store_true', help='Extend repository scope while preserving parent plan')
    args = parser.parse_args()
    if args.stage == 'verify':
        verify()
        return
    settings = plan(args.repositories, args.extend_scope)
    if settings['source_seed_sha256'] != digest(SEEDS.read_bytes()):
        raise ValueError('Verified seed cohort changed; use a new dataset version')
    seed_copy = OUT / 'verified_seed_identities.json'
    if not seed_copy.exists():
        seed_copy.write_bytes(SEEDS.read_bytes())
    elif digest(seed_copy.read_bytes()) != settings['source_seed_sha256']:
        raise ValueError('Bundled verified seed cohort differs')
    api = API(args.allow_network)
    if args.stage == 'late-discover':
        discover_late_2025(api, settings)
        return
    if args.stage == 'page-discover':
        discover_pages(api, settings)
        return
    if args.stage == 'complete-discover':
        complete_searches(api, settings)
        return
    if args.stage == 'prefetch-details':
        prefetch_details(api, settings)
        return
    if args.stage == 'history-repair':
        history(api, settings, repair=True)
        return
    for name, operation in [('discover', discover), ('details', details), ('controls', controls), ('history', history)]:
        if args.stage in {name, 'all'}:
            operation(api, settings)
            if name == 'discover' and args.stage == 'all' and (OUT / 'late_2025_plan.json').exists():
                discover_late_2025(api, settings)
            if name == 'discover' and args.stage == 'all' and (OUT / 'pagination_plan.json').exists():
                discover_pages(api, settings)
            if name == 'history' and (OUT / 'history_repair_plan.json').exists():
                history(api, settings, repair=True)
    if args.stage in {'report', 'all'}:
        build_profiles()
        report(settings)


if __name__ == '__main__':
    main()
