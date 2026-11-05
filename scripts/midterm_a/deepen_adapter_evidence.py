"""为低历史开发者补充截止前贡献和同仓库候选，生成独立证据包。"""
import argparse
import base64
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import shutil

import backfill_adapter_2024_2026 as b

PARENT = b.OUT
OUT = PARENT.parent / 'advanced_adapter_depth_20261104_v1'
COHORT = b.ROOT / 'tmp/adapter_sparse_20261104_v1/c_experiment_cohort.json'
PAIRED = COHORT.with_name('paired_outcomes_source.jsonl')


class CachedAPI(b.API):
    def call(self, *, query=None, endpoint=None):
        key = b.digest(b.canonical({'query': query, 'endpoint': endpoint})) + '.json'
        source = PARENT / 'api_cache' / key
        target = OUT / 'api_cache' / key
        if not target.exists() and source.exists():
            with b.LOCK:
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        return super().call(query=query, endpoint=endpoint)


def prepare(limit, excluded_accounts=None):
    cohort = b.read(COHORT)
    names = {r['developer_key']: r['github_numeric_id'] for r in cohort['accounts']}
    labels, cutoffs = defaultdict(set), defaultdict(set)
    for row in b.rows(PAIRED):
        if row['split'] == 'train' and row['developer_key'] in names:
            identity = names[row['developer_key']]
            labels[identity].add(row['outcome_id'])
            cutoffs[identity].add(row['profile_as_of'][:10])
    outcomes = b.rows(PARENT / 'verified_outcomes.jsonl')
    for row in b.rows(PARENT / 'joined_samples.jsonl'):
        if row['github_numeric_id'] in names.values() and row['outcome_at'] < '2026-01-01T00:00:00Z':
            labels[row['github_numeric_id']].add(row['outcome_id'])
            cutoffs[row['github_numeric_id']].add(row['as_of'][:10])
    audit = [{**row, 'available_training_outcomes': len(labels[row['github_numeric_id']]),
              'available_training_cutoffs': sorted(cutoffs[row['github_numeric_id']])}
             for row in cohort['accounts']]
    sparse = {r['github_numeric_id'] for r in audit if r['available_training_outcomes'] < 3}
    contributions = b.rows(PARENT / 'historical_contributions.jsonl')
    evidence = defaultdict(set)
    for row in contributions:
        evidence[(row['github_numeric_id'], row['repository'].casefold(), row['as_of'])].add(row['commit_sha'])
    groups = {}
    for row in outcomes:
        if row['github_numeric_id'] in sparse - (excluded_accounts or set()) and row['outcome_at'] < '2026-01-01T00:00:00Z':
            key = (row['github_numeric_id'], row['repository'].casefold(), row['profile_as_of'])
            groups.setdefault(key, row)
    by_account = defaultdict(list)
    for key, row in groups.items():
        by_account[key[0]].append({'github_numeric_id': key[0], 'login': row['developer_key'],
            'repository': row['repository'], 'as_of': key[2], 'known_commits': sorted(evidence[key]),
            'identity_evidence': row['seed_identity']['identity_evidence']})
    # 每个账号先取一个窗口，然后轮转补第二个，优先有历史证据的较新窗口。
    for windows in by_account.values():
        windows.sort(key=lambda r: (not bool(r['known_commits']), -b.dt(r['as_of']).timestamp(), r['repository'].casefold()))
    selected = []
    for position in range(2):
        for identity in sorted(by_account):
            if position < len(by_account[identity]):
                selected.append(by_account[identity][position])
    return {'parent_manifest_sha256': b.digest((PARENT / 'manifest.json').read_bytes()),
            'cohort_sha256': b.digest(COHORT.read_bytes()), 'accounts': audit,
            'sparse_accounts': len(sparse), 'windows': selected[:limit],
            'commits_per_window': 10, 'files_per_commit': 3, 'controls_per_window': 5,
            'training_ready': False}


def collect(api, window, history_pages=2, include_controls=True,
            history_start='2024-01-01T00:00:00Z', commit_page_size=5):
    repo, cutoff = window['repository'], b.dt(window['as_of'])
    if not 1 <= commit_page_size <= 5 or b.dt(history_start) >= cutoff:
        raise ValueError('Invalid history sampling window')
    until = b.iso(cutoff - b.timedelta(seconds=1))
    contributions, pages, exclusions = [], [], []
    seen = set(window['known_commits'])
    for page in range(1, history_pages + 1):
        since = history_start.replace(':', '%3A')
        endpoint = (f'/repos/{repo}/commits?author={window["login"]}&since={since}'
                    f'&until={until}&per_page={commit_page_size}&page={page}')
        listing, listing_ref = api.call(endpoint=endpoint)
        pages.append({'page': page, 'returned': len(listing), 'evidence_ref': listing_ref,
                      'may_have_more': page == history_pages and len(listing) == commit_page_size})
        for item in listing:
            if item['sha'] in seen:
                continue
            seen.add(item['sha'])
            detail, ref = api.call(endpoint=f'/repos/{repo}/commits/{item["sha"]}?per_page=100')
            if (detail.get('author') or {}).get('id') != window['github_numeric_id']:
                exclusions.append({'commit_sha': item['sha'], 'reason': 'author_identity_mismatch'})
                continue
            event = detail['commit']['committer']['date']
            author_at = detail['commit']['author']['date']
            if not b.dt(history_start) <= b.dt(event) < cutoff or b.dt(author_at) >= cutoff:
                exclusions.append({'commit_sha': item['sha'], 'reason': 'outside_history_window'})
                continue
            for file in b.choose_files(detail.get('files', [])):
                blob, blob_ref = api.call(endpoint=f'/repos/{repo}/git/blobs/{file["sha"]}')
                if blob.get('encoding') != 'base64' or blob.get('size', 0) > 262144:
                    exclusions.append({'commit_sha': item['sha'], 'path': file['filename'], 'reason': 'unsupported_or_large_blob'})
                    continue
                content = base64.b64decode(blob['content'])
                if hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest() != file['sha']:
                    raise ValueError('Git blob checksum mismatch')
                try:
                    content.decode('utf-8')
                except UnicodeDecodeError:
                    exclusions.append({'commit_sha': item['sha'], 'path': file['filename'], 'reason': 'non_utf8'})
                    continue
                location = 'code/' + file['sha'] + '.txt'
                with b.LOCK:
                    (OUT / 'code').mkdir(exist_ok=True)
                    (OUT / location).write_bytes(content)
                contributions.append({**window, 'commit_sha': item['sha'], 'event_at': event,
                    'author_at': author_at, 'path': file['filename'], 'language': b.code_language(file['filename']),
                    'blob_sha': file['sha'], 'content_sha256': b.digest(content), 'content_location': location,
                    'evidence_ref': ref, 'blob_evidence_ref': blob_ref, 'listing_evidence_ref': listing_ref,
                    'source_url': detail['html_url'], 'file_list_may_be_truncated': len(detail.get('files', [])) >= 100,
                    'historical_visibility_note': 'Commit timestamps checked; first public availability not independently established.'})
    if not include_controls:
        return {'window': window, 'contributions': contributions, 'controls': [],
                'listing_pages': pages, 'exclusions': exclusions}
    query_text = f'repo:{repo} is:issue created:<{window["as_of"][:10]} closed:>={window["as_of"][:10]} sort:created-desc'
    query = 'query { q0: search(query:' + json.dumps(query_text) + ',type:ISSUE,first:30) { ' + b.PAGE + ' nodes { ... on Issue { ' + b.ISSUE_FIELDS + ' } } } }'
    response, ref = api.call(query=query)
    controls = []
    for issue in response['data']['q0']['nodes']:
        candidate, reason = b.issue_at(issue, cutoff)
        if candidate:
            controls.append({**candidate, 'repository': repo, 'evidence_ref': ref, 'role': 'unlabeled_control'})
        if len(controls) == 5:
            break
    return {'window': window, 'contributions': contributions, 'controls': controls,
            'listing_pages': pages, 'exclusions': exclusions}


def verify():
    manifest = b.read(OUT / 'manifest.json')
    for name, expected in manifest['files_sha256'].items():
        path = (OUT / name).resolve()
        if not path.is_relative_to(OUT.resolve()) or b.digest(path.read_bytes()) != expected:
            raise ValueError('Supplemental manifest mismatch')
    for row in b.rows(OUT / 'historical_contributions.jsonl'):
        content = (OUT / row['content_location']).read_bytes()
        if b.digest(content) != row['content_sha256'] or hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest() != row['blob_sha']:
            raise ValueError('Supplemental blob mismatch')
        if b.dt(row['event_at']) >= b.dt(row['as_of']) or b.dt(row['author_at']) >= b.dt(row['as_of']):
            raise ValueError('Future history evidence')
        detail = b.read(OUT / row['evidence_ref'])['payload']
        if (detail.get('author') or {}).get('id') != row['github_numeric_id']:
            raise ValueError('Supplemental author mismatch')
    for row in b.rows(OUT / 'historical_controls.jsonl'):
        data = b.read(OUT / row['evidence_ref'])['payload']['data']['q0']['nodes']
        issue = next(n for n in data if n['url'] == row['source_url'])
        candidate, reason = b.issue_at(issue, b.dt(row['as_of']))
        if reason or candidate['content_sha256'] != row['content_sha256']:
            raise ValueError('Supplemental candidate replay mismatch')


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--windows', type=int, default=60)
    parser.add_argument('--run-id', default='advanced_adapter_depth_20261104_v1')
    parser.add_argument('--exclude-plan', type=Path, help='Exclude accounts covered by an earlier depth batch')
    args = parser.parse_args()
    if not 1 <= args.windows <= 200:
        parser.error('windows must be between 1 and 200')
    if not args.run_id.replace('_', '').replace('-', '').isalnum():
        parser.error('Simple run ID required')
    OUT = PARENT.parent / args.run_id
    excluded_plan = b.read(args.exclude_plan) if args.exclude_plan else None
    excluded_sha256 = b.digest(args.exclude_plan.read_bytes()) if args.exclude_plan else None
    OUT.mkdir(parents=True, exist_ok=True)
    b.OUT = OUT
    plan_path = OUT / 'collection_plan.json'
    if plan_path.exists():
        plan = b.read(plan_path)
        if plan['parent_manifest_sha256'] != b.digest((PARENT / 'manifest.json').read_bytes()):
            raise ValueError('Parent evidence changed during supplement')
        if plan.get('excluded_plan_sha256') != excluded_sha256 or plan.get('requested_windows', 60) != args.windows:
            raise ValueError('Resume depth selection differs from saved plan')
    else:
        excluded = {w['github_numeric_id'] for w in excluded_plan['windows']} if excluded_plan else set()
        plan = prepare(args.windows, excluded)
        plan.update(excluded_plan_sha256=excluded_sha256, requested_windows=args.windows)
        b.save('collection_plan.json', plan)
    api = CachedAPI(args.allow_network)
    results, failures = [], []
    def run(window):
        key = b.digest(b.canonical(window))
        path = OUT / 'windows' / (key + '.json')
        if path.exists():
            return b.read(path)
        result = collect(api, window)
        b.save('windows/' + key + '.json', result)
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(run, w): w for w in plan['windows']}
        for done, future in enumerate(as_completed(futures), 1):
            try:
                results.append(future.result())
            except Exception as exc:
                failures.append({'window': futures[future], 'error_type': type(exc).__name__})
            b.save('status.json', {'stage': 'collection', 'done': done, 'total': len(futures), 'failures': failures})
            print(json.dumps({'windows_done': done, 'windows_total': len(futures), 'failures': len(failures)}), flush=True)
    if failures:
        raise RuntimeError('Unfinished evidence windows; resume using request cache')
    contributions = [r for result in results for r in result['contributions']]
    seeds = b.read(b.SEEDS)
    for row in contributions:
        row.update(schema_version='advanced-pilot-file-contribution-v0.1',
                   developer_key=row['login'],
                   source_developer_key=seeds[str(row['github_numeric_id'])]['developer_key'],
                   file_version_key='history-file:' + b.digest(b.canonical(
                       [row['repository'], row['path'], row['commit_sha']])))
    parent_files = {(r['repository'].casefold(), r['commit_sha'], r['path'])
                    for r in b.rows(PARENT / 'historical_contributions.jsonl')}
    collected_files = {(r['repository'].casefold(), r['commit_sha'], r['path']) for r in contributions}
    controls = {(r['issue_key'], r['as_of']): r for result in results for r in result['controls']}
    # 正例身份仅用于剔除比较项，不进入候选特征。
    positives = {(r['github_numeric_id'], r['issue_key']) for r in b.rows(PARENT / 'verified_outcomes.jsonl')}
    groups = []
    for result in results:
        w = result['window']
        groups.append({**w, 'comparison_issue_keys': [r['issue_key'] for r in result['controls']
            if (w['github_numeric_id'], r['issue_key']) not in positives], 'training_ready': False})
    b.lines('historical_contributions.jsonl', sorted(contributions, key=lambda r: (r['github_numeric_id'], r['as_of'], r['repository'], r['commit_sha'], r['path'])))
    b.lines('historical_controls.jsonl', [controls[k] for k in sorted(controls)])
    b.lines('comparison_groups.jsonl', groups)
    summary = {'windows': len(results), 'accounts': len({r['window']['github_numeric_id'] for r in results}),
        'new_window_file_contributions': len(contributions),
        'unique_collected_file_contributions': len(collected_files),
        'unique_new_file_contributions': len(collected_files - parent_files),
        'candidate_snapshots': len(controls), 'usable_comparison_items': sum(len(r['comparison_issue_keys']) for r in groups),
        'windows_with_five_controls': sum(len(r['comparison_issue_keys']) == 5 for r in groups),
        'sparse_accounts': plan['sparse_accounts'], 'training_ready': False,
        'note': 'Independent supplemental evidence; pending B merge/freeze and C hard-candidate scoring.'}
    b.save('summary.json', summary)
    shutil.copy2(Path(__file__), OUT / 'collector_source.py')
    b.save('manifest.json', {'files_sha256': {p.relative_to(OUT).as_posix(): b.digest(p.read_bytes())
        for p in OUT.rglob('*') if p.is_file() and p.name not in ('manifest.json', 'status.json')}})
    verify()
    b.save('status.json', {'status': 'complete', 'stage': 'verified', 'summary': summary})
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
