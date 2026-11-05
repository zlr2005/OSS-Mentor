"""从完整 PR 索引追加核验批次；保留父批次，复用只读缓存和原证据校验。"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict, deque
import json
from pathlib import Path
import shutil

import backfill_adapter_2024_2025 as b


def choose(index, selected, seeds, limit, years=(2024, 2025)):
    """按季度/仓库/作者轮转，保留旧选择且不重复计算同一 PR。"""
    seen = {r['url'].casefold() for r in selected}
    groups = defaultdict(list)
    for row in index:
        key = row['pr_url'].casefold()
        identity = str(row['github_numeric_id'])
        if key in seen or identity not in seeds:
            continue
        if not row['verified_seed_author'] or not row['evidence_refs']:
            raise ValueError('Indexed seed membership or evidence missing')
        cutoff = b.quarter(row['created_at'])
        if cutoff.year not in years or (cutoff.year == 2024 and cutoff.month == 1):
            continue
        seen.add(key)
        groups[(cutoff.year, cutoff.month, row['repository'].casefold(), identity)].append(row)
    queues = [deque(sorted(values, key=lambda r: (r['created_at'], r['pr_url'].casefold())))
              for _, values in sorted(groups.items())]
    added = []
    while queues and len(added) < limit:
        next_round = []
        for queue in queues:
            row = queue.popleft()
            added.append({'repository': row['repository'], 'year': b.quarter(row['created_at']).year,
                'url': row['pr_url'], 'number': row['pr_number'],
                'createdAt': row['created_at'], 'mergedAt': row['merged_at'],
                'author': {'login': row['github_login'], 'databaseId': row['github_numeric_id']},
                'search_evidence_ref': row['evidence_refs'][0],
                'seed_identity': seeds[str(row['github_numeric_id'])]})
            if queue:
                next_round.append(queue)
            if len(added) == limit:
                break
        queues = next_round
    return added


def choose_later_2026(index, selected, seeds, limit):
    """在 2026 Q2/Q3/Q4 间分配名额，避免年初排序占满整批。"""
    added = []
    for position, month in enumerate((4, 7, 10)):
        quota = limit // 3 + (position < limit % 3)
        candidates = [r for r in index if b.quarter(r['created_at']).year == 2026
                      and b.quarter(r['created_at']).month == month]
        added.extend(choose(candidates, selected + added, seeds, quota, years=(2026,)))
    return added


def choose_sparse_history(index, selected, seeds, outcomes, limit):
    """优先补未覆盖的稀疏历史窗口，再补已有稀疏窗口中的其他 PR。

    历史数仅为当前采集证据覆盖，不代表完整 GitHub 历史或 C 的训练分组。
    限于 2024/2025，避免此次扩充默认改变 2026 留出范围。
    """
    history = defaultdict(dict)
    covered = set()
    for row in outcomes:
        identity = str(row['github_numeric_id'])
        history[identity][row['outcome_id']] = b.dt(row['outcome_at'])
        covered.add((identity, b.dt(row['profile_as_of'])))
    times = {identity: sorted(values.values()) for identity, values in history.items()}
    buckets = defaultdict(list)
    for row in index:
        cutoff = b.quarter(row['created_at'])
        identity = str(row['github_numeric_id'])
        if cutoff.year not in (2024, 2025):
            continue
        if not cutoff < b.dt(row['created_at']) <= b.dt(row['merged_at']) <= cutoff + b.timedelta(days=90):
            continue
        count = bisect_left(times.get(identity, []), cutoff)
        if count <= 2:
            prefix = 'covered_' if (identity, cutoff) in covered else 'new_'
            buckets[prefix + ('sparse' if count else 'zero')].append(row)
    added = []
    groups = set()
    for name in ('new_sparse', 'new_zero', 'covered_sparse', 'covered_zero'):
        ordered = choose(buckets[name], selected, seeds, len(buckets[name]))
        for row in ordered:
            group = (str(row['author']['databaseId']), b.quarter(row['createdAt']))
            if group in groups:
                continue
            groups.add(group)
            added.append(row)
            if len(added) >= limit:
                return added
    return added


def choose_training_cohort(index, selected, seeds, accounts, limit):
    """按 C 实验训练历史数量选人，仅追加训练时间范围内的结果候选。"""
    identities = {str(row['github_numeric_id']) for row in accounts
                  if 1 <= row['developer_train_outcomes'] <= 2}
    return choose_training_accounts(index, selected, seeds, identities, limit)


def choose_training_accounts(index, selected, seeds, identities, limit):
    """对显式账号白名单应用相同的训练时间窗口，不推断其历史数量。"""
    identities = {str(identity) for identity in identities}
    candidates = []
    for row in index:
        cutoff = b.quarter(row['created_at'])
        merged = b.dt(row['merged_at'])
        if str(row['github_numeric_id']) not in identities or cutoff.year not in (2024, 2025):
            continue
        if cutoff < b.dt(row['created_at']) <= merged <= cutoff + b.timedelta(days=90) and merged < b.dt('2026-01-01T00:00:00Z'):
            candidates.append(row)
    return choose(candidates, selected, seeds, limit)


def main():
    global b
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--additional-prs', type=int, default=1200)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--three-year', action='store_true', help='Continue the independent 2024/2025/2026 dataset')
    parser.add_argument('--focus-2026-later', action='store_true', help='Balance new selections across 2026 Q2/Q3/Q4')
    parser.add_argument('--balance-years', action='store_true', help='Allocate new selections evenly across dataset years')
    parser.add_argument('--new-training-groups', action='store_true', help='Select 2024/2025 candidates from uncovered account-cutoff groups')
    parser.add_argument('--sparse-history', action='store_true', help='Prioritize uncovered 2024/2025 sparse-history windows, then covered sparse-history windows')
    parser.add_argument('--training-cohort', type=Path, help='C experiment cohort JSON containing accounts and developer_train_outcomes')
    parser.add_argument('--additional-index', type=Path, help='Additional evidence-backed PR discovery index for the C cohort')
    args = parser.parse_args()
    if args.additional_index and not args.training_cohort:
        parser.error('additional-index requires training-cohort')
    if sum((args.balance_years, args.focus_2026_later, args.new_training_groups, args.sparse_history, args.training_cohort is not None)) > 1:
        parser.error('Choose one selection policy')
    if args.three_year:
        import backfill_adapter_2024_2026
        b = backfill_adapter_2024_2026
    if args.additional_prs < 1 or not args.run_id.replace('_', '').replace('-', '').isalnum():
        raise ValueError('Positive batch size and simple run ID required')
    if args.focus_2026_later and not args.three_year:
        raise ValueError('Late 2026 selection requires the three-year dataset')
    run_dir = b.ROOT / 'tmp' / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / 'status.json'

    def status(value):
        value['updated_at'] = b.iso(b.datetime.now(b.timezone.utc))
        temporary = state_path.with_suffix('.tmp')
        temporary.write_bytes(b.canonical(value))
        temporary.replace(state_path)
        print(json.dumps(value, ensure_ascii=False), flush=True)

    settings = b.read(b.OUT / 'collection_plan.json')
    if settings['source_seed_sha256'] != b.digest(b.SEEDS.read_bytes()):
        raise ValueError('Verified seed cohort changed')
    cohort = b.read(args.training_cohort) if args.training_cohort else None
    cohort_sha256 = b.digest(args.training_cohort.read_bytes()) if args.training_cohort else None
    additional_index_sha256 = b.digest(args.additional_index.read_bytes()) if args.additional_index else None
    plan_name = 'detail_continuations/' + args.run_id + '.json'
    plan_path = b.OUT / plan_name
    if not plan_path.exists():
        b.verify()
        selected = b.read(b.OUT / 'selected_prs.json')
        index = b.rows(b.OUT / 'verified_seed_pr_index.jsonl')
        if args.additional_index:
            index += b.rows(args.additional_index)
        if cohort is not None:
            added = choose_training_cohort(index, selected, b.read(b.SEEDS), cohort['accounts'], args.additional_prs)
        elif args.sparse_history:
            added = choose_sparse_history(index, selected, b.read(b.SEEDS),
                b.rows(b.OUT / 'verified_outcomes.jsonl'), args.additional_prs)
        elif args.new_training_groups:
            covered = {(r['github_numeric_id'], b.dt(r['profile_as_of']))
                       for r in b.rows(b.OUT / 'verified_outcomes.jsonl')}
            candidates = []
            for row in index:
                cutoff = b.quarter(row['created_at'])
                if (row['github_numeric_id'], cutoff) in covered:
                    continue
                if cutoff < b.dt(row['created_at']) <= b.dt(row['merged_at']) <= cutoff + b.timedelta(days=90):
                    candidates.append(row)
            added = choose(candidates, selected, b.read(b.SEEDS), args.additional_prs)
        elif args.focus_2026_later:
            added = choose_later_2026(index, selected, b.read(b.SEEDS), args.additional_prs)
        elif args.balance_years:
            seeds = b.read(b.SEEDS)
            years = settings['years']
            quota, remainder = divmod(args.additional_prs, len(years))
            added = []
            for position, year in enumerate(years):
                count = quota + (position < remainder)
                added.extend(choose(index, selected + added, seeds, count, years=(year,)))
        else:
            added = choose(index, selected, b.read(b.SEEDS), args.additional_prs, years=settings['years'])
        snapshot = b.OUT / 'batch_snapshots' / args.run_id
        snapshot.mkdir(parents=True, exist_ok=False)
        for path in b.OUT.iterdir():
            if path.is_file():
                shutil.copy2(path, snapshot / path.name)
        shutil.copy2(Path(b.__file__), snapshot / 'collector_source.py')
        shutil.copy2(Path(__file__), snapshot / 'continuation_source.py')
        plan = {'run_id': args.run_id, 'parent_selected_sha256': b.digest(b.canonical(selected)),
            'parent_summary': b.read(b.OUT / 'summary.json'),
            'additional_prs_requested': args.additional_prs, 'additional_prs': len(added),
            'focus_2026_later': args.focus_2026_later,
            'balance_years': args.balance_years,
            'new_training_groups': args.new_training_groups,
            'sparse_history': args.sparse_history,
            'training_cohort_sha256': cohort_sha256,
            'additional_index_sha256': additional_index_sha256,
            'selection': 'Equal quotas for 2026 Q2/Q3/Q4; rotate repository/author.' if args.focus_2026_later else 'Existing verified seed authors only; round robin by quarter/repository/author. History remains bounded.',
            'selected_prs': selected + added, 'frozen': False, 'training_ready': False,
            'continuation_script_sha256': b.digest(Path(__file__).read_bytes())}
        if args.sparse_history:
            plan['selection'] = '2024/2025 only; uncovered sparse/zero-history windows first, then covered sparse/zero-history windows; one PR per account-cutoff. Prior outcome counts reflect collected evidence only, not complete history or C training splits.'
        if cohort is not None:
            plan['selection'] = 'C experiment accounts with 1-2 training outcomes; 2024/2025 quarter-safe PRs merged before 2026; rotate quarter/repository/author.'
            plan['training_cohort'] = cohort
        b.save(plan_name, plan)
    else:
        plan = b.read(plan_path)
        if plan.get('additional_index_sha256') != additional_index_sha256:
            raise ValueError('Resume additional discovery index differs from saved plan')
        if plan.get('training_cohort_sha256') != cohort_sha256:
            raise ValueError('Resume C experiment cohort differs from saved plan')
        if plan.get('sparse_history', False) != args.sparse_history:
            raise ValueError('Resume sparse-history policy differs from saved plan')
        if plan['additional_prs_requested'] != args.additional_prs:
            raise ValueError('Resume batch size differs from saved plan')
        if plan.get('focus_2026_later', False) != args.focus_2026_later:
            raise ValueError('Resume selection policy differs from saved plan')
        if plan.get('balance_years', False) != args.balance_years:
            raise ValueError('Resume year balancing differs from saved plan')
        if plan.get('new_training_groups', False) != args.new_training_groups:
            raise ValueError('Resume training-group selection differs from saved plan')
    b.save('selected_prs.json', plan['selected_prs'])
    api = b.API(args.allow_network)
    stages = [('prefetch-details', lambda: b.prefetch_details(api, settings)),
              ('details', lambda: b.details(api, settings)),
              ('controls', lambda: b.controls(api, settings)),
              ('history', lambda: b.history(api, settings)),
              ('history-repair', lambda: b.history(api, settings, repair=True)),
              ('report', lambda: (b.build_profiles(), b.report(settings))),
              ('verify', b.verify)]
    prior = b.read(state_path) if state_path.exists() else {}
    completed = list(prior.get('completed_stages', []))
    try:
        for name, operation in stages:
            if name in completed:
                continue
            status({'status': 'running', 'stage': name, 'completed_stages': completed,
                    'additional_prs': plan['additional_prs'], 'total_selected_prs': len(plan['selected_prs'])})
            operation()
            if name == 'prefetch-details' and b.rows(b.OUT / 'prefetch_failures.jsonl'):
                raise RuntimeError('PR prefetch incomplete; resume after resolving read failures')
            completed.append(name)
        status({'status': 'complete', 'completed_stages': completed,
                'before': plan['parent_summary'], 'after': b.read(b.OUT / 'summary.json')})
    except Exception as exc:
        status({'status': 'failed', 'stage': name, 'completed_stages': completed,
                'error_type': type(exc).__name__})
        raise


if __name__ == '__main__':
    main()
