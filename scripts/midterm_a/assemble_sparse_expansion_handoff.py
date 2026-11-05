"""Deduplicate supplementary outcomes and report evidence completeness separately."""
from collections import defaultdict
import argparse
import json
from pathlib import Path

import backfill_adapter_2024_2026 as b


def main(version=1):
    base=b.OUT.parent
    baseline=base/'temporal_xy_2024_2026_v6'
    cohort=b.rows(baseline/'low_history_gaps.jsonl')
    ids={r['github_numeric_id'] for r in cohort}
    official={r['outcome_id']:r for r in b.rows(base/'merged_ab_history_2024_2026_v6/verified_outcomes.jsonl')}
    original=defaultdict(list)
    admitted_ids=set()
    for group in b.rows(baseline/'train.jsonl'):
        admitted_ids.update(y['outcome_id'] for y in group['y'])
        if group['github_numeric_id'] in ids:
            original[group['github_numeric_id']].extend(official[y['outcome_id']] for y in group['y'])
    source_names=['earlier_sparse_outcomes_2022_2023_v1','earlier_sparse_outcomes_2008_2021_v1',
                  'preinteraction_sparse_supplement_20261104_v1']
    if version>=3:
        source_names.append('sparse_closing_recovery_20261104_v3' if version>=4 else 'sparse_closing_recovery_20261104_v2')
    merged,overlap={},0
    for name in source_names:
        for row in b.rows(base/name/'verified_outcomes.jsonl'):
            if row['outcome_id'] in official and not (row.get('recovered_existing_verified_outcome') and row['outcome_id'] not in admitted_ids):
                continue
            overlap+=row['outcome_id'] in merged
            merged[row['outcome_id']]=dict(row,source_root=(base/name).relative_to(b.ROOT).as_posix())
    history_names=['earlier_sparse_history_2022_2023_v1','preinteraction_sparse_history_20261104_v1',
                   'earlier_sparse_history_2008_2021_v1']
    if version>=2:
        history_names.append('sparse_evidence_repair_20261104_v1')
    if version>=3:
        history_names.append('sparse_closing_recovery_history_20261104_v1')
    if version>=4:
        history_names.append('sparse_evidence_repair_20261105_v2')
    if version>=5:
        history_names.append('sparse_evidence_repair_20261105_v3')
    if version>=6:
        history_names.append('sparse_pr_code_20261105_v1')
    ready={}
    for name in history_names:
        for row in b.rows(base/name/'comparison_groups.jsonl'):
            ready[row['outcome_id'],row['as_of']]=dict(row,history_source_root=(base/name).relative_to(b.ROOT).as_posix())
    pairs=[]
    known_positives=defaultdict(set)
    for row in list(official.values())+list(merged.values()):
        known_positives[row['github_numeric_id']].add(row['issue_key'])
    for row in merged.values():
        key=row['outcome_id'],row['profile_as_of']
        if key in ready:
            comparison=ready[key]
            filtered=[k for k in comparison['comparison_issue_keys'] if k not in known_positives[row['github_numeric_id']]]
            if filtered:
                pairs.append(dict(comparison,outcome_source_root=row['source_root'],
                    comparison_issue_keys=filtered,
                    removed_known_positive_issue_keys=[k for k in comparison['comparison_issue_keys'] if k not in filtered]))
    by_account=defaultdict(list)
    for row in merged.values():by_account[row['github_numeric_id']].append(row)
    paired_by_account=defaultdict(set)
    for row in pairs:paired_by_account[row['github_numeric_id']].add(row['outcome_id'])
    report=[]
    for account in cohort:
        identity=account['github_numeric_id']
        added=by_account[identity]
        all_rows=original[identity]+added
        report.append(dict(account,new_verified_outcomes=len(added),
            combined_outcomes=len(all_rows),combined_unique_issues=len({r['issue_key'] for r in all_rows}),
            combined_cutoffs=sorted({r['profile_as_of'] for r in all_rows}),
            new_outcomes_with_history_and_controls=len(paired_by_account[identity]),
            missing_before_three=max(0,3-len({r['issue_key'] for r in all_rows})),training_ready=False))
    b.OUT=base/('sparse_expansion_20261104_handoff' if version==1 else f'sparse_expansion_20261104_handoff_v{version}');b.OUT.mkdir(exist_ok=True)
    b.lines('outcome_index.jsonl',sorted(merged.values(),key=lambda r:r['outcome_id']))
    b.lines('comparison_index.jsonl',pairs)
    b.lines('account_coverage.jsonl',report)
    b.lines('remaining_gaps.jsonl',[r for r in report if r['missing_before_three']])
    source_names+=history_names+['historical_repository_context_20261104_v1',
        'candidate_context_enrichment_20261104_v1','platform_feedback_audit_20261104_v1',
        'advanced_adapter_expanded_20261104_v1','expanded_closing_link_triage_20261104_v1',
        'remaining_five_final_probe_20261104_v1']
    if version>=3:
        source_names+=['teacher_sparse_readonly_20261104_v1','teacher_language_context_20261104_v1']
    if version>=4:
        source_names.append('teacher_sparse_readonly_20261104_v2')
    b.save('source_manifest.json',dict(source_roots={n:(base/n).relative_to(b.ROOT).as_posix() for n in source_names},
        source_manifest_sha256={n:b.digest((base/n/'manifest.json').read_bytes()) for n in source_names}))
    summary=dict(new_unique_outcomes=sum(r['outcome_id'] not in official for r in merged.values()),
        supplementary_outcomes=len(merged),recovered_existing_verified_outcomes=sum(r['outcome_id'] in official for r in merged.values()),
        overlapping_outcome_records_deduplicated=overlap,
        accounts_with_new_outcomes=sum(bool(by_account[a['github_numeric_id']]) for a in cohort),
        accounts_at_least_three_unique_issues_if_admitted=sum(not r['missing_before_three'] for r in report),
        accounts_still_under_three=sum(bool(r['missing_before_three']) for r in report),
        new_outcomes_with_history_and_controls=len(pairs),training_ready=False,
        cutoff_policy='Mixed source policies, explicit per-row as_of; pre-interaction version preferred on overlap. B/C review required.')
    b.save('summary.json',summary)
    b.save('manifest.json',dict(collector_sha256=b.digest(Path(__file__).read_bytes()),
        files_sha256={p.name:b.digest(p.read_bytes()) for p in b.OUT.iterdir() if p.is_file() and p.name!='manifest.json'}))
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',type=int,choices=(1,2,3,4,5,6),default=1)
    main(parser.parse_args().version)
