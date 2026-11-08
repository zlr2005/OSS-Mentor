"""Classify eligible tasks in a new working DB and report real inventory gaps."""
import argparse
import csv
from datetime import datetime, timezone, timedelta
from itertools import combinations
import json
from pathlib import Path

from oss_mentor.candidate_report import build_candidate_report
from oss_mentor.developer_profiles import ALLOWED_LANGUAGES, LANGUAGE_DISPLAY_NAMES
from oss_mentor.storage.candidates import SQLiteCandidateStorage
from oss_mentor.task_features import extract_task_features, infer_skill_requirements

KINDS=('bug_fix','feature','documentation','testing','refactor','build_tooling')
NAMES=('修复缺陷','功能','文档','测试','重构','构建工具')


def write(database, output, review):
    if not database.is_file(): raise FileNotFoundError(database)
    store=SQLiteCandidateStorage(database,Path('db/sqlite/001_mvp.sql'))
    store.initialize(); pool=store.candidate_pool_status()
    records=[r for r in store.feature_records() if r['candidate_eligibility']=='eligible']
    with store.connect() as connection:
        for r in records:
            features=extract_task_features(r)
            store.update_features(connection,task_candidate_id=r['task_candidate_id'],features=features)
            store.replace_skill_requirements(connection,task_candidate_id=r['task_candidate_id'],
                requirements=infer_skill_requirements(r,features),feature_version=features.task_feature_version)
    report=build_candidate_report(store)
    coverage=report['recommendation_coverage']
    audit=json.loads((review/'report.json').read_text('utf-8'))
    baseline=json.loads((review/'baseline_coverage.json').read_text('utf-8'))
    with store.connect() as connection:
        rows=[dict(r) for r in connection.execute('''SELECT tc.task_candidate_id,tc.issue_number,tc.github_issue_id,
        tc.html_url,tc.task_types_json,tc.newcomer_label_signal,r.primary_language,r.full_name AS repository
        FROM task_candidate tc JOIN repository r USING(repository_id)
        WHERE tc.candidate_eligibility='eligible' AND tc.candidate_availability='available'
        AND coalesce(r.is_archived,0)=0 AND coalesce(r.is_disabled,0)=0
        AND coalesce(r.maintenance_status,'active')!='inactive' ''')]
        integrity=connection.execute('PRAGMA integrity_check').fetchone()[0]
        assert integrity=='ok' and not connection.execute('PRAGMA foreign_key_check').fetchall()
    assert len(rows)==pool['recommendable_count']==coverage['growth']['total_count']
    assert len({(r['repository'].casefold(),r['issue_number']) for r in rows})==len(rows)
    output.mkdir(parents=True,exist_ok=True)
    generated=datetime.now(timezone.utc)
    manifest=json.loads((review/'source_manifest.json').read_text('utf-8'))
    rounds=[int(m['batch'].split('_')[0][5:]) for m in manifest if m['batch'].startswith('round')]
    cutoff=max(rounds) if rounds else None
    result=dict(generated_at=generated.isoformat(),source_round_cutoff=cutoff,
        source_issues=audit['source_issues'],screening_counts=audit['counts'],coverage=coverage,
        baseline=baseline,sqlite_integrity=integrity,database=str(database),
        note='Inventory counts, not personalized ranking results; task types are inferred rules and may overlap.')
    (output/'inventory_coverage.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (output/'language_task_counts.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['track','language','task_type','count','meets_minimum_5','example_urls'])
        for track in ('newcomer','growth'):
            selected=[r for r in rows if track=='growth' or r['newcomer_label_signal']]
            for language in sorted(ALLOWED_LANGUAGES):
                for kind in KINDS:
                    matching=[r for r in selected if (r['primary_language'] or '').casefold()==language and kind in json.loads(r['task_types_json'])]
                    count=coverage[track]['language_task_type_counts'][language][kind]
                    assert len(matching)==count
                    writer.writerow([track,LANGUAGE_DISPLAY_NAMES[language],kind,count,count>=5,' | '.join(r['html_url'] for r in matching[:3])])
    # Multi-select languages are an OR filter, as in matching.profile_task_suitability.
    # All 63 nonempty language subsets use distinct task IDs and are exported separately.
    with (output/'language_subsets.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['track','languages','distinct_candidates',*KINDS])
        languages=sorted(ALLOWED_LANGUAGES)
        for n in range(1,len(languages)+1):
            for subset in combinations(languages,n):
                for track in ('newcomer','growth'):
                    selected=[r for r in rows if (r['primary_language'] or '').casefold() in subset and (track=='growth' or r['newcomer_label_signal'])]
                    writer.writerow([track,' + '.join(LANGUAGE_DISPLAY_NAMES[l] for l in subset),len(selected),
                        *[sum(kind in json.loads(r['task_types_json']) for r in selected) for kind in KINDS]])
    lines=['# 已保存采集数据的筛选及推荐库存覆盖','',
        f'统计时间：{generated.astimezone(timezone(timedelta(hours=8))):%Y-%m-%d %H:%M}（北京时间）。',
        f"来源为截至第 {cutoff} 轮已保存的成功采集部分，跨轮去重 {audit['source_issues']} 个老师库 Issue；未完成及失败范围仍保留缺口。",'',
        '这是按现有可参与性、24 小时有效期、新人标签与任务类型规则计算的库存。个人画像、难度、系统环境和排序还会影响实际推荐结果。',
        '语言使用仓库主语言，不代表每条 Issue 涉及的全部技术；任务类型为规则推断，同一 Issue 可属于多类，格子不能直接相加。','',
        '| 筛选结果 | 数量 |','|---|---:|']
    for decision,count in audit['counts'].items():lines.append(f'| {decision} | {count} |')
    lines.extend(['','旧版证据保留原始观察时间，不因本轮合并续期；未核实或待人工确认的记录均未计入以下库存。'])
    for track,name in (('newcomer','新人'),('growth','进阶')):
        c=coverage[track];matrix=c['language_task_type_counts'];nonzero=36-len(c['zero_combinations']);qualified=36-len(c['combinations_below_minimum'])
        lines.extend(['',f'## {name}库存','',
            f"通过可参与性检查 {c['total_count']} 条；支持的六种语言中有 {sum(c['language_counts'].values())} 条。",
            f'非零覆盖 {nonzero}/36；每格至少 5 条达标 {qualified}/36；'+('已非零全覆盖。' if nonzero==36 else '尚未全覆盖。'),'',
            '| 语言 | '+' | '.join(NAMES)+' | 语言候选数 |','|---|'+'---:|'*7])
        for lang in ('python','javascript','typescript','java','go','rust'):
            lines.append('| '+LANGUAGE_DISPLAY_NAMES[lang]+' | '+' | '.join(str(matrix[lang][kind]) for kind in KINDS)+f" | {c['language_counts'][lang]} |")
        lines.extend(['',f"旧版非零覆盖：{36-len(baseline[track]['zero'])}/36。",
            '零库存组合：'+('、'.join(c['zero_combinations']) or '无')+'。',
            '低于 5 条组合：'+('、'.join(c['combinations_below_minimum']) or '无')+'。'])
    lines.extend(['','## 文件','',
        '- `language_task_counts.csv`：72 个轨道×语言×类型格子，含实例链接。',
        '- `language_subsets.csv`：63 个非空多选语言组合，各含新人和进阶的去重数量；多选语言按“任一语言匹配”计算。',
        '- `inventory_coverage.json`：完整机器可读统计与旧版对照。',
        f'- 当前独立工作库：`{database}`。','',
        '尚未接入网页或更改网页数据库。未冻结。未核实记录不能用作已确认负样本。'])
    (output/'inventory_coverage.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {t:dict(total=c['total_count'],covered=36-len(c['zero_combinations']),minimum5=36-len(c['combinations_below_minimum']),matrix=c['language_task_type_counts']) for t,c in coverage.items()}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--review',type=Path,required=True)
    print(json.dumps(write(**vars(p.parse_args())),ensure_ascii=False))
