"""Read-only progress report for the resumable inventory backfill."""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from oss_mentor.candidate_freshness import recommendation_is_current
from oss_mentor.task_readiness import task_review_reasons


def database_summary(path, now):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN')
        rows = [dict(r) for r in connection.execute('''SELECT tc.*,r.full_name AS repository,
            r.is_archived,r.is_disabled,r.maintenance_status FROM task_candidate tc
            JOIN repository r USING(repository_id)''')]
        requirements = Counter(r[0] for r in connection.execute('SELECT task_candidate_id FROM task_skill_requirement'))
        counts = {r[0]: r[1] for r in connection.execute('''SELECT name,0 FROM sqlite_master
            WHERE type='table' AND name LIKE 'midterm_dataset_%' ''')}
        for table in counts:
            counts[table] = connection.execute('SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0]
        connection.rollback()
    ready = []
    for row in rows:
        row['requirements'] = requirements[row['task_candidate_id']]
        if (row['candidate_eligibility'] == 'eligible' and recommendation_is_current(row, now=now)
                and not row['is_archived'] and not row['is_disabled'] and row['maintenance_status'] != 'inactive'
                and not task_review_reasons(row)):
            ready.append(row)
    return {'database': path.name, 'candidate_total': len(rows), 'matchable': len(ready),
            'newcomer_label_signal_matchable': sum(bool(r['newcomer_label_signal']) for r in ready),
            'availability_states': dict(Counter(r['candidate_availability'] for r in rows)),
            'model_history_table_counts': counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    jobs = json.loads((args.directory / 'jobs.json').read_text(encoding='utf-8'))
    now = datetime.now(timezone.utc)
    statuses = dict(Counter(j['status'] for j in jobs))
    summary = {'observed_at': now.isoformat(), 'total': len(jobs), 'job_status': statuses,
               'feature_review_reasons': dict(Counter(r for j in jobs for r in j.get('feature_review_reasons', []))),
               'eligibility': dict(Counter(j.get('eligibility') for j in jobs if j['status'] in {'verified', 'needs_task_review'})),
               'databases': [database_summary(ROOT / 'data' / name, now)
                             for name in ['oss_mentor_web.sqlite3', 'oss_mentor.sqlite3']],
               'scope': 'Current Issue verification and rule feature readiness; not CODER model inference or complete file-history coverage.'}
    resolution_path = args.directory / 'screening_resolution.json'
    if resolution_path.exists():
        summary['automated_screening_resolution'] = json.loads(resolution_path.read_text(encoding='utf-8'))
    (args.directory / 'report_snapshot.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 全量候选补采进度', '',
             f"统计时间：{now.astimezone(timezone(timedelta(hours=8))):%Y-%m-%d %H:%M:%S} 北京时间。", '',
             f"原库存共 {len(jobs):,} 条；待处理 {statuses.get('pending', 0):,} 条。", '',
             '| 队列状态 | 数量 | 含义 |', '|---|---:|---|',
             f"| verified | {statuses.get('verified', 0)} | 完成生产状态检查，仍可能因关闭、占用或其他条件不准入 |",
             f"| needs_task_review | {statuses.get('needs_task_review', 0)} | 已读详情；范围、类型或技能门槛未通过，未宣称完成占用检查 |",
             f"| needs_retry | {statuses.get('needs_retry', 0)} | 读取或身份等异常，保留待查 |", '',
             '| 业务库 | 当前可推荐 | 其中新手标签信号 |', '|---|---:|---:|']
    review_summary_path = args.directory / 'review_summary.json'
    if review_summary_path.exists():
        review = json.loads(review_summary_path.read_text(encoding='utf-8'))
        decisions = review['decisions']
        lines[0] = '# 候选内容复核及准入检查进度'
        lines[3:3] = ['本轮先对原 5,201 条待复核记录做规则辅助重评，并抽查样例；并非逐条人工验收或构建验证。', '',
            '| 初次内容重评结论 | 数量 |', '|---|---:|',
            f"| 满足内容门槛，需重新核验当前状态 | {decisions.get('passes_content_gate', 0)} |",
            f"| 范围仍需补证据 | {decisions.get('scope_needs_evidence', 0)} |",
            f"| 类型仍需判定 | {decisions.get('type_needs_disambiguation', 0)} |",
            f"| 设计仍需确认 | {decisions.get('design_needs_confirmation', 0)} |",
            f"| 汇总或项目背景，不是独立任务 | {decisions.get('not_a_single_task', 0)} |", '',
            '以下是后续处理进度和两库的全局准入数量。内容初审通过不等于新增可推荐数；源文本变化时重新计算。', '']
    for db in summary['databases']:
        lines.append(f"| {db['database']} | {db['matchable']} | {db['newcomer_label_signal_matchable']} |")
    manifest_path = args.directory / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('admission_policy') == 'expanded-content-v1':
            lines[0] = '# 放宽内容准入后的当前状态核验'
            lines = [line.replace('原库存共 ', '本轮扩大准入对象共 ') if line.startswith('原库存共 ') else line for line in lines]
            lines[3:3] = ['任务类型未知、范围说明较少和方案讨论改为提示；不降低实际状态、有效期、身份、技能和用户难度限制。', '',
                          f"最低内容门槛或非独立任务拦截：{statuses.get('not_admitted_content',0)} 条。该状态不分派给用户人工处理。", '']
            summary['admission_policy'] = manifest['admission_policy']
            (args.directory / 'report_snapshot.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    if 'automated_screening_resolution' in summary:
        resolution = summary['automated_screening_resolution']
        lines[0] = '# 历史内容复核阶段（遗留名单已自动终筛结案）'
        target = Path(resolution['result_directory']).as_posix() + '/report.md'
        lines[3:3] = [f"本轮遗留记录已按保守机器政策处理，不再要求用户逐条核实。当前处置见[自动终筛报告]({target})。", '',
                      '下方 needs_task_review 为前一阶段的历史原因快照，不代表仍分派给用户的人工待办。', '']
    lines += ['', '数量随核验到期、后台刷新和本轮写入变化。任务类型和难度仍是规则推断。', '',
              '本轮执行 Issue 当前状态及特征补采；完整的代码、贡献图、事前文件关联和 CODER 模型包仍需单独补齐和验证。',
              '已关闭、占用、锁定或未满足最低准入条件的原记录保留真实原因。', '',
              '断点 jobs.json；来源 evidence/；最新网络批次 network_progress.json；原库备份 *.sqlite3.before。', '',
              '恢复命令（需要正常本机权限读取已有 gh 登录）：', '', '```powershell',
              'python -u scripts/candidate_backfill.py collect `',
              f'  --directory "{args.directory.as_posix()}" `',
              f'  --limit {len(jobs)} --request-budget 4500 --workers 4 --allow-network --until-done', '```', '',
              '确认没有同一目录的采集进程后再恢复；每批保留证据并检查限额。',
              'runner_state.json 显示 collecting、waiting_for_quota、finished_with_review 等运行阶段；读取失败最多追加两轮重试。']
    (args.directory / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'statuses': statuses, 'matchable': {d['database']: d['matchable'] for d in summary['databases']}}))


if __name__ == '__main__':
    main()
