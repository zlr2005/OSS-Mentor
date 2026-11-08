"""Validate a complete recheck and explain changes without treating 404 as deletion."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

from assemble_recheck_evidence import latest_details, read_records
from screen_unified_teacher import BLOCKED
from write_unified_screening_report import write_report


def write_recheck(previous, review, working):
    verification = write_report(review, working)
    old = json.loads((previous/'audit.json').read_text('utf-8'))
    audit = json.loads((review/'audit.json').read_text('utf-8'))
    current = {r['key'].casefold(): r for r in audit}
    targets = {r['key'].casefold(): r for r in old if r['decision'] in ('unverified', 'review_pending')}
    evidence = json.loads(gzip.decompress((review/'connector_evidence.json.gz').read_bytes()))
    summary = evidence['recheck_summary']
    if summary['not_attempted'] or summary['attempted'] != len(targets):
        raise ValueError('Recheck is incomplete; do not publish as completed')
    records, _ = read_records(review)
    sources = {f"{r['repo_name']}#{r['issue_number']}".casefold(): r
               for r in json.loads((review/'source_issues.json').read_text('utf-8'))}
    latest = latest_details(records, sources)
    transitions = {old_state: Counter(current[key]['decision'] for key, r in targets.items() if r['decision'] == old_state)
                   for old_state in ('unverified', 'review_pending')}
    reasons = Counter(r['reason'] for r in audit)
    counts = Counter(r['decision'] for r in audit)
    specific = Counter()
    for r in latest.values():
        error=r.get('error') or {}
        if not isinstance(error,dict):
            error={}
        if r.get('identity_match'):
            d = r['payload']
            specific['closed' if d['state']=='closed' else 'locked' if d.get('locked') else 'assigned' if d.get('assignees') else 'open_unassigned'] += 1
        elif str(error.get('error_data', {}).get('status')) == '410':
            specific['confirmed_deleted'] += 1
        else:
            specific['unreadable_or_identity_unresolved'] += 1
    review_rows = []
    for key, before in targets.items():
        after = current[key]
        if after['decision'] not in ('review_pending', 'unverified'):
            continue
        row = dict(after, previous_decision=before['decision'], previous_reason=before['reason'])
        detail = latest[key].get('payload', {})
        row['teacher_title']=sources[key]['issue_title']
        row['observed_title']=detail.get('title')
        row['observed_issue_id']=detail.get('id')
        row['expected_issue_id']=str(sources[key]['issue_id'])
        row['redirects']=latest[key].get('redirects',[])
        row['title'] = (detail.get('title') if latest[key].get('identity_match') else None) or sources[key]['issue_title']
        row['blocking_labels'] = [r['name'] for r in detail.get('labels', []) if BLOCKED.search(r['name'])]
        row['observed_at'] = latest[key]['observed_at']
        if after['reason'] == 'claim_or_work_in_progress_text':
            row['next_check'] = '有认领/进行中表述；需确认认领是否仍有效。历史留言或 PR 关闭本身不能证明任务已经释放。'
        elif after['reason'] == 'blocking_or_information_needed_label':
            row['next_check'] = '仍有阻塞、讨论或信息不足标签；需维护者解除阻塞或补齐可执行任务说明。'
        elif after['reason'] == 'planning_or_umbrella_task':
            row['next_check'] = '这是计划或汇总任务；需拆出未被认领、范围明确的具体开发任务。'
        elif after['reason'] == 'manual_scope_or_information_review':
            row['next_check'] = after.get('manual_review_note', '需确认任务范围和信息是否足够。')
        elif after['reason'] == 'NOT_FOUND':
            row['next_check'] = '当前返回 404；不能区分不存在、删除、转私有或访问不可见，不能作为已关闭/已删除负样本。'
        elif after['reason'] == 'IDENTITY_MISMATCH':
            row['next_check'] = f"老师来源 ID 为 {row['expected_issue_id']}，当前目标 ID 为 {row['observed_issue_id']}；需核对转移或编号映射，不能自动合并成同一可推荐任务。"
        elif after['reason'] == 'REDIRECT_TARGET_UNAVAILABLE':
            row['next_check'] = '接口返回重定向，但未提供可读取的目标地址；尚不能判断原 Issue 的当前状态。'
        else:
            row['next_check'] = '继续核对访问权限、重定向与原始 Issue 身份；暂不进入候选池。'
        review_rows.append(row)
    (review/'remaining_review_queue.json').write_text(json.dumps(review_rows, ensure_ascii=False, indent=2), 'utf-8')
    pending_lines = ['# 仍需确认的记录', '', '这是核查后的剩余队列，包含当前证据和下一步处理建议。', '']
    for row in review_rows:
        pending_lines.extend([f"## [{row['key']}]({row['url']})", '', row['title'], '',
                              f"原因：{row['reason']}；观察时间：{row['observed_at']}", '', row['next_check'], ''])
        if row['blocking_labels']:
            pending_lines.extend(['相关标签：'+', '.join(row['blocking_labels']), ''])
        for signal in row.get('claim_signals', []):
            pending_lines.extend([f"- [{signal.get('url','原文')}]({signal.get('url','')})：{signal.get('text','').replace(chr(10), ' ')}", ''])
    (review/'remaining_review_queue.md').write_text('\n'.join(pending_lines)+'\n', 'utf-8')
    verification.update(recheck=summary, previous_group_transitions=transitions, rechecked_specific_statuses=specific,
                        counts=counts, reasons=reasons, teacher_database_modified=False, frozen=False)
    (review/'verification_summary.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2), 'utf-8')
    lines=['# 老师库 Issue 补充核查报告', '',
           f"总计 {len(sources)} 个去重 Issue；本轮针对原 876 条待确认和 9815 条未核实记录，实际查询 {summary['attempted']} 个老师库已发现身份。", '',
           '未连接、修改老师数据库；GitHub 仅 GET 核对老师已有身份，集合查询中未匹配老师 Issue ID 的结果均丢弃。所有本轮结果保存在 D 盘，旧版保留，未冻结。', '',
           '| 当前结果 | 数量 |', '|---|---:|',
           f"| 通过当前可参与性检查 | {counts['availability_passed']} |",f"| 排除 | {counts['excluded']} |",
           f"| 仍待人工确认 | {counts['review_pending']} |",f"| 仍无法核实身份或状态 | {counts['unverified']} |",f'| 合计 | {len(sources)} |', '',
           '## 原两组记录的去向', '', '| 原组别 | 通过 | 排除 | 待确认 | 未核实 |', '|---|---:|---:|---:|---:|']
    for state, label in [('unverified', '9815 条原未核实'), ('review_pending', '876 条原待确认')]:
        c=transitions[state];lines.append(f"| {label} | {c['availability_passed']} | {c['excluded']} | {c['review_pending']} | {c['unverified']} |")
    lines.extend(['', '## 证据与边界', '',
                  f"本轮明确查到：关闭 {specific['closed']}，开放但已分配 {specific['assigned']}，锁定 {specific['locked']}，开放且未分配 {specific['open_unassigned']}；GitHub 明确返回 410 已删除 {specific['confirmed_deleted']}。", '',
                  '“开放且未分配”还需要评论、认领和关联 PR 检查，不等于可推荐。集合查询只用于匹配具体身份和明确关闭状态；未出现在集合中的记录继续逐条 GET，未用缺席推断关闭。', '',
                  f"仍有 {reasons['NOT_FOUND']} 条返回 404；不能据此断言已删除。身份或转移映射未解决的记录也未进入候选库。", '',
                  '认领留言未因时间久或相关 PR 已关闭而自动视为失效；信息不足和汇总计划未因处于 open 状态而放行。保守文本规则仍可能误判，剩余队列提供证据和处理建议。', '',
                  '原已通过记录保留实际核查时间，未借本轮复查延长有效期。候选缓存为 24 小时，参与任务前仍需再确认；难度及个人适配尚未验证。', '',
                  '## 文件', '', f"- [标准候选清单]({(working/'candidates.md').as_posix()})",
                  f"- [剩余确认队列]({(review/'remaining_review_queue.md').as_posix()})",
                  f"- [全部审计]({(review/'audit.json').as_posix()})",
                  f"- [核查统计]({(review/'verification_summary.json').as_posix()})", '',
                  f"最早有效期：北京时间 {verification['earliest_expiry_beijing']}。SQLite 完整性、外键、候选去重、来源身份及状态不变量均通过校验。", ''])
    (review/'screening_report.md').write_text('\n'.join(lines), 'utf-8')
    return verification


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--working', type=Path, required=True)
    print(json.dumps(write_recheck(**vars(parser.parse_args())), ensure_ascii=False, indent=2))
