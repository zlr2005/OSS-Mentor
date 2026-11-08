"""按 C 低历史名单跨仓库检索训练期 PR；只读 GitHub，保留分页证据。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

import backfill_adapter_2024_2026 as b


def collect_account(api, account, max_pages):
    identity = account['github_numeric_id']
    query_text = (f'author:{account["developer_key"]} is:pr is:merged linked:issue '
                  'created:2024-04-01..2025-12-31 merged:<2026-01-01 sort:created-asc')
    cursor = None
    records, coverage = [], []
    for page in range(1, max_pages + 1):
        after = ',after:' + json.dumps(cursor) if cursor else ''
        query = ('query { q0: search(query:' + json.dumps(query_text)
                 + ',type:ISSUE,first:100' + after + ') { issueCount ' + b.PAGE
                 + ' nodes { ... on PullRequest { number url createdAt mergedAt '
                 + b.AUTHOR + ' } } } }')
        payload, ref = api.call(query=query)
        result = payload['data']['q0']
        coverage.append({'github_numeric_id': identity, 'login': account['developer_key'],
                         'page': page, 'query': query_text, 'evidence_ref': ref,
                         'returned': len(result['nodes']), 'total_matches': result['issueCount'],
                         **result['pageInfo']})
        for node in result['nodes']:
            if not node or (node.get('author') or {}).get('databaseId') != identity:
                continue
            records.append({'pr_url': node['url'], 'repository': node['url'].split('github.com/')[1].split('/pull/')[0],
                            'pr_number': node['number'], 'created_at': node['createdAt'], 'merged_at': node['mergedAt'],
                            'github_login': node['author']['login'], 'github_numeric_id': identity,
                            'verified_seed_author': True, 'evidence_refs': [ref],
                            'outcome_verified': False, 'training_ready': False})
        if not result['pageInfo']['hasNextPage']:
            break
        cursor = result['pageInfo']['endCursor']
        if not cursor:
            raise ValueError('Missing continuation cursor')
    return records, coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--max-pages', type=int, default=3)
    parser.add_argument('--allow-network', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 10:
        parser.error('max-pages must be between 1 and 10')
    cohort = b.read(args.cohort)
    seeds = b.read(b.SEEDS)
    accounts = [r for r in cohort['accounts'] if 1 <= r['developer_train_outcomes'] <= 2]
    if any(str(r['github_numeric_id']) not in seeds for r in accounts):
        raise ValueError('Account outside verified seed identities')
    api = b.API(args.allow_network)
    indexed, coverage = {}, []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(collect_account, api, account, args.max_pages) for account in accounts]
        for done, future in enumerate(as_completed(futures), 1):
            records, pages = future.result()
            for row in records:
                indexed[row['pr_url'].casefold()] = row
            coverage.extend(pages)
            b.save('cohort_author_search_coverage.json', sorted(coverage, key=lambda r: (r['github_numeric_id'], r['page'])))
            b.lines('cohort_author_pr_index.jsonl', [indexed[k] for k in sorted(indexed)])
            if done % 10 == 0 or done == len(accounts):
                print(json.dumps({'accounts_done': done, 'accounts_total': len(accounts), 'indexed_prs': len(indexed)}), flush=True)
    b.save('cohort_author_search_summary.json', {
        'cohort_sha256': b.digest(args.cohort.read_bytes()), 'accounts': len(accounts),
        'indexed_prs': len(indexed), 'repositories': len({r['repository'].casefold() for r in indexed.values()}),
        'accounts_still_paginated': sum(r['page'] == args.max_pages and r['hasNextPage'] for r in coverage),
        'note': 'Additional author-based discovery index; the original repository search index remains separately counted.',
        'training_ready': False})


if __name__ == '__main__':
    main()
