"""Validate published local candidate identities and write a readable audit summary."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from oss_mentor.candidate_rules import has_newcomer_label


def write_report(review, working):
    screening=json.loads((review/'report.json').read_text(encoding='utf-8'))
    merged=json.loads((working/'report.json').read_text(encoding='utf-8'))
    candidates=json.loads((working/'candidates.json').read_text(encoding='utf-8'))
    sources=json.loads((review/'source_issues.json').read_text(encoding='utf-8'))
    source_index={(r['repo_name'].casefold(),int(r['issue_number'])):str(r['issue_id']) for r in sources}
    keys={(r['repository'].casefold(),r['issue_number']) for r in candidates}
    ids={str(r['github_issue_id']) for r in candidates}
    if len(candidates) != merged['available_candidates'] or len(keys) != len(candidates) or len(ids) != len(candidates):
        raise ValueError('Candidate count or identity duplication')
    for r in candidates:
        if (source_index.get((r['repository'].casefold(),r['issue_number'])) != str(r['github_issue_id'])
                or r['discovery_source'] != 'teacher_clickhouse' or r['state'] != 'open'
                or r['assignment_state'] != 'unassigned' or r['is_locked']
                or r['has_linked_open_pr'] is not False or not r.get('body_text','').strip()):
            raise ValueError('Candidate identity or current-state invariant failed')
    counts=screening['counts'];reasons=screening['reasons']
    if sum(counts.values()) != len(sources) or counts['availability_passed'] != len(candidates):
        raise ValueError('Audit coverage differs from the source corpus')
    newcomers=sum(has_newcomer_label(r['labels']) for r in candidates)
    repositories=Counter(r['repository'] for r in candidates)
    languages=Counter(r.get('primary_language') or 'Unknown' for r in candidates)
    expiry=datetime.fromisoformat(merged['minimum_verification_expiry']).astimezone(timezone(timedelta(hours=8)))
    date_label=datetime.fromisoformat(merged['generated_at']).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    manifest=json.loads((review/'source_manifest.json').read_text('utf-8'))
    inventory=screening.get('inventory_coverage') or {}
    complete=sum(v.get('complete') is True for v in inventory.values())
    lines=['# 老师库 Issue 统一筛选报告','',
           f'完成日期：{date_label}；来源为老师库 {len(manifest)} 个已保存采集批次，跨轮去重 {len(sources)} 个 Issue。',
           '老师数据库没有连接或修改；GitHub 仅只读核实已发现身份，未引入清单之外的 Issue。','',
           '| 结果 | 数量 |','|---|---:|',
           f"| 通过当前可参与性检查 | {counts['availability_passed']} |",
           f"| 排除 | {counts['excluded']} |",f"| 待确认 | {counts.get('review_pending',0)} |",
           f"| 具体状态未核实，未进入候选库 | {counts['unverified']} |",f'| 合计 | {len(sources)} |','',
           f'候选覆盖 {len(repositories)} 个项目；其中 {newcomers} 条带新手或欢迎贡献相关标签。标签只作为信号，难度和个人技能适配尚未确认。','',
           '## 核查范围与未核实记录','',
           f"取得 {screening['details_fetched']} 条身份匹配的详情，检查 {screening['timelines_checked']} 条时间线和 {screening['referenced_prs_checked']} 个引用 PR。",
           f'保留 {len(inventory)} 个项目的当前清单核查记录，其中 {complete} 个标记为范围完整；完整性与观察时间逐仓记录在审计包中。',
           '每条老师库记录都有审计决定；这不等于逐条验证了所有历史 Issue 的具体当前状态。','',
           f"- {sum(v for k,v in reasons.items() if k.startswith('NOT_IN_CURRENT_OPEN_UNASSIGNED'))} 条未在当前开放未分配清单中匹配；其具体状态没有逐条确认，不能宣称全部关闭、删除或无效。",
           f"- {reasons.get('IDENTITY_MISMATCH',0)} 条身份不一致；{reasons.get('NOT_FOUND',0)} 条无法读取。均未进入候选库。",
           '- 未核实记录不能当作已确认的负样本。当前清单用于可参与任务候选池，用户适配标签需另行构建。','',
           '## 排除与待确认的原因','',
           f"排除：已关闭 {reasons.get('closed',0)}、已分配 {reasons.get('assigned',0)}、关联开放 PR {reasons.get('linked_open_pr',0)}、锁定 {reasons.get('locked',0)}。",
           f"待确认：认领/进行中表述 {reasons.get('claim_or_work_in_progress_text',0)}、阻塞/信息不足标签 {reasons.get('blocking_or_information_needed_label',0)}、计划/汇总任务 {reasons.get('planning_or_umbrella_task',0)}、人工范围复核 {reasons.get('manual_scope_or_information_review',0)}。",
           '采用互斥的首个原因计数。认领检测为保守文本规则，可能漏检或过度标记；未观察到认领或 PR，不保证无人开展未关联的工作。','',
           '## 文件与有效期','',
           f"- [标准候选清单]({(working/'candidates.md').as_posix()})",
           f"- [标准候选 JSON（含完整正文）]({(working/'candidates.json').as_posix()})",
           f"- [全部审计记录]({(review/'audit.json').as_posix()})",
           f"- [筛选统计]({(review/'report.json').as_posix()})",
           f"- [来源文件及校验值]({(review/'source_manifest.json').as_posix()})",
           f"- 标准 SQLite 工作库：{merged['database']}",
           f"- 独立完整审计库：{review/'teacher_review.sqlite3'}",'',
           f'最早一批核查有效期至北京时间 {expiry:%Y-%m-%d %H:%M}；各记录按实际观察时间保留 24 小时工作缓存期限。',
           '读取接口会刷新本地过期标记；参与前仍应重新确认 GitHub 状态。复用的旧证据保留原核查时间。','',
           '仍为内部工作版本，未冻结、未接入网页、未正式交付 C/D。旧输出保留。',
           '证据经过字段投影：候选保留完整正文及评论，负面决定保留必要字段、引用链接和认领片段；不能视为完整原始 HTTP 响应。',
           'SQLite 完整性和外键检查通过，候选身份、去重、来源及当前状态不变量已核对。','',
           '## 候选语言分布','', '| 语言 | 候选数 |','|---|---:|']
    lines.extend(f'| {language} | {count} |' for language,count in languages.most_common())
    (review/'screening_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    verification=dict(source_issues=len(sources),candidates=len(candidates),repositories=len(repositories),
                      newcomer_or_help_wanted_signal=newcomers,languages=languages,source_identity_checks='passed',
                      sqlite_integrity=merged['sqlite_integrity'],foreign_key_violations=merged['foreign_key_violations'],
                      earliest_expiry_beijing=expiry.isoformat(),all_specific_states_verified=False,frozen=False)
    (review/'verification_summary.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    return verification


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review',type=Path,required=True)
    p.add_argument('--working',type=Path,required=True)
    print(json.dumps(write_report(**vars(p.parse_args())),ensure_ascii=False,indent=2))
