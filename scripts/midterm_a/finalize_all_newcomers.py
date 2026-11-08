"""Offline, evidence-preserving review of the complete historical newcomer scope."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from optimized_teacher_collection import NEWCOMER_LABEL_GROUPS
from screen_unified_teacher import assess, context_fingerprint

STRONG=set(NEWCOMER_LABEL_GROUPS['newcomer'])
WELCOME=set(NEWCOMER_LABEL_GROUPS['help_wanted'])
WIP=re.compile(r'\bpr in works\b|\bfix\s*pending\b|\bwork in progress\b|\bin progress\b|\bclaimed\b',re.I)
# Supplemental signals only defer a task for context review; never declare a
# historical statement to be proof of a current claim or implementation.
WORK=re.compile(r'\b(?:will work on (?:this|it)|made (?:some )?progress on (?:this|that)|'
                r'(?:i shall|i will|i can|i can also) (?:put up|open|raise|prepare|rebase|submit) (?:a |the |my )?(?:draft )?pr|'
                r'submitted\s+#\d+|claiming this|i (?:have )?opened a draft pr|'
                r'i took a shot at this|please review my (?:new )?pr|'
                r'can you please assign someone to review this pr)\b',re.I)

PRIORITY={
 'freecodecamp/freecodecamp#71012':dict(task='修正课程中的文件搜索术语、复数形式和 macOS 版本说明',
     skills=['英文文档','Markdown','Windows/macOS 基础'],scope='按 Issue 列出的三类文案问题修改对应课程并核对示例。',
     rationale='说明列出具体原文、问题和期望结果，修改边界较清楚，未观察到认领或开放 PR。'),
 'bazelbuild/bazel#31316':dict(task='修正已删除参数与过时警告的文档',
     skills=['英文文档','Markdown','Bazel 基础'],scope='核对指定版本并修改 docs/run/build.mdx 的过时段落，补正确文档链接。',
     rationale='已给出文件、过时参数、版本和复现结果，适合有命令行基础的新手从文档入手。'),
 'apache/pinot#8775':dict(task='补充插件配置文档',skills=['英文文档','Pinot 插件配置基础'],
     scope='先选一个已熟悉插件的配置说明，按维护者指定的位置补文档；不一次覆盖全部插件。',
     rationale='有明确的新手/文档标签，维护者认可文档位置；需先了解所选插件的实际配置。'),
 'supercog-ai/agentic#208':dict(task='为事件接口补充类型和 OpenAPI 描述',skills=['Python','FastAPI','Pydantic/OpenAPI'],
     scope='梳理 getevents 返回的事件类型并验证 /docs 的 schema 展示；适合已有 Python Web 基础的人。',
     rationale='目标和验收入口明确，带 good first issue；优先级低于纯文档任务，需已有后端基础。'),
}


def normalize(label):return re.sub(r'[-_ ]+',' ',label).strip().lower()


def supplementary_review(detail,timeline,prs):
    labels=[label['name'] for label in detail.get('labels',[])]
    blocked=[label for label in labels if WIP.search(normalize(label))]
    if blocked:return dict(decision='review_pending',reason='work_in_progress_label',evidence=blocked)
    texts=[(detail['html_url'],detail.get('body') or '')]
    if timeline:
        texts.extend((e.get('html_url'),e.get('body') or '') for page in timeline['pages'] for e in page if e.get('event')=='commented')
    signals=[]
    for url,text in texts:
        match=WORK.search(text)
        if match:signals.append(dict(url=url,text=text[max(0,match.start()-60):match.end()+140]))
    if signals:return dict(decision='review_pending',reason='additional_work_or_implementation_text',evidence=signals)
    opened=[];unverified=[]
    for url,text in texts:
        for repo,num in re.findall(r'https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)',text):
            ref=f'https://api.github.com/repos/{repo}/pulls/{num}'
            if prs.get(ref,{}).get('state') not in ('open','closed'):unverified.append(dict(source=url,pr=ref))
            if prs.get(ref,{}).get('state')=='open':opened.append(dict(source=url,pr=ref))
    if opened:return dict(decision='review_pending',reason='open_pr_in_text_requires_context',evidence=opened)
    if unverified:return dict(decision='review_pending',reason='pr_in_text_unverified_requires_context',evidence=unverified)
    refs=[e.get('commit_url') for page in (timeline or {}).get('pages',[]) for e in page if e.get('event')=='referenced' and e.get('commit_url')]
    if refs:return dict(decision='review_pending',reason='commit_reference_requires_context',evidence=sorted(set(refs)))
    return None


def finalize(directory,output_name='results'):
    read=lambda name:json.loads((directory/name).read_text(encoding='utf-8-sig'))
    rows=read('source_issues.json');ev=read('complete_evidence.json');now=datetime.now(timezone.utc)
    overrides={r['key']:r for r in read('context_reviews.json')}
    if not re.fullmatch(r'[A-Za-z0-9_-]+',output_name):raise ValueError('Output name must be a simple directory name')
    output=directory/output_name;output.mkdir(exist_ok=False)
    reviews=[];seen=set()
    for source in rows:
        key=f"{source['repo_name']}#{source['issue_number']}".lower()
        if key in seen:raise ValueError('Duplicate issue key')
        seen.add(key)
        envelope=ev['details'].get(key);repo=ev['repositories'].get(source['repo_name'])
        value=dict(key=key,repository=source['repo_name'],issue_number=source['issue_number'],
            github_issue_id=str(source['issue_id']),url=f"https://github.com/{source['repo_name']}/issues/{source['issue_number']}",
            title=source.get('issue_title') or '',source_files=source['source_batches'],
            historical_labels=source['historical_labels'],beginner_difficulty_confirmed=False,frozen=False)
        for flag in ('in_current_collection','in_prior_candidate_pool'):
            if flag in source:value[flag]=source[flag]
        if not envelope or not repo:
            value.update(decision='unverified',reason='issue_or_repository_read_failed',bucket='unverified')
            reviews.append(value);continue
        detail=envelope['payload'];timeline=ev['timelines'].get(key)
        expected=dict(source)
        if not expected.get('repo_id'):
            expected['repo_id']=repo['payload']['id']
            value['repository_identity_bound_from_current_metadata']=True
        result=assess(expected,detail,envelope['observed_at'],repo['payload'],repo['observed_at'],timeline,ev['pull_requests'],now=now)
        if result['reason']=='identity_mismatch':raise ValueError('Identity mismatch: '+key)
        if key in overrides:
            context=overrides[key]
            if context['evidence_sha256']!=context_fingerprint(detail,timeline):raise ValueError('Context evidence changed')
            if context['decision'] not in ('excluded','review_pending'):raise ValueError('Context cannot automatically approve')
            result.update(decision=context['decision'],reason=context['reason'],context_review=context)
        if result['decision']=='availability_passed':
            additional=supplementary_review(detail,timeline,ev['pull_requests'])
            if additional:result.update(additional)
        labels=[l['name'] for l in detail['labels']];normalized={normalize(l) for l in labels}
        group='newcomer' if normalized&STRONG else 'welcome' if normalized&WELCOME else 'historical_signal_only'
        value.update(result,current_labels=labels,current_signal_group=group,title=detail['title'],url=detail['html_url'],
            body=detail.get('body') or '',language=repo['payload'].get('language'),state=detail['state'],
            assignees=[a['login'] for a in detail['assignees']],locked=detail['locked'],
            verified_at=envelope['observed_at'],expires_at=(datetime.fromisoformat(envelope['observed_at'].replace('Z','+00:00'))+timedelta(hours=24)).isoformat(),
            timeline_complete=bool(timeline and timeline.get('complete')))
        if value['decision']=='availability_passed':
            if key in PRIORITY:
                value.update(bucket='newcomer_priority',agent_scope_review=PRIORITY[key],priority=1 if '文档' in PRIORITY[key]['task'] or '课程' in PRIORITY[key]['task'] else 2)
            elif group=='newcomer':value['bucket']='newcomer_label_candidate'
            elif group=='welcome':value['bucket']='welcome_difficulty_review'
            else:value['bucket']='historical_signal_only'
        else:value['bucket']=value['decision']
        reviews.append(value)
    if len(reviews)!=len(rows):raise ValueError('Incomplete review coverage')
    candidates=[r for r in reviews if r['decision']=='availability_passed']
    for r in candidates:
        if r['state']!='open' or r['locked'] or r['assignees'] or not r['timeline_complete']:raise ValueError('Invalid candidate')
        if r.get('open_pr_references') or r.get('claim_signals'):raise ValueError('Unresolved availability signal')
    counts=dict(Counter(r['decision'] for r in reviews));buckets=dict(Counter(r['bucket'] for r in reviews))
    summary=dict(generated_at=now.isoformat(),source_count=len(rows),source_inputs=len(read('source_manifest.json')),
        source_repositories=len({r['repository'].lower() for r in reviews}),counts=counts,buckets=buckets,
        reasons=dict(Counter(r['reason'] for r in reviews)),detail_reads_matched=len(ev['details']),
        timeline_reads=len(ev['timelines']),complete_timelines=sum(t['complete'] for t in ev['timelines'].values()),
        pr_reads=len(ev['pull_requests']),read_failures=ev['failures'],
        observations_start=ev['observed_start'],observations_end=ev['observed_end'],
        earliest_candidate_expiry=min((r['expires_at'] for r in candidates),default=None),
        teacher_database_requests=0,remote_writes=0,frozen=False,merged_into_website=False,
        difficulty_ground_truth=False)
    def save(name,value):(output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save('audit.json',reviews);save('candidates.json',candidates);save('summary.json',summary)
    save('priority_newcomers.json',[r for r in candidates if r['bucket']=='newcomer_priority'])
    save('review_queue.json',[r for r in reviews if r['decision'] in ('review_pending','unverified')])
    def csv_safe(value):
        text=str(value or '')
        return "'"+text if text.startswith(('=','+','-','@','\t','\r')) else text
    for name,items in [('all_reviews.csv',reviews),('candidates.csv',candidates),('priority_newcomers.csv',[r for r in candidates if r['bucket']=='newcomer_priority'])]:
        fields=['repository','issue_number','title','url','language','decision','reason','bucket','verified_at','expires_at']
        with (output/name).open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
            for row in items:writer.writerow({field:csv_safe(row.get(field)) for field in fields})
    with sqlite3.connect(output/'screening.sqlite3') as db:
        db.execute('CREATE TABLE review(issue_key TEXT PRIMARY KEY,decision TEXT NOT NULL,bucket TEXT NOT NULL,record_json TEXT NOT NULL)')
        db.executemany('INSERT INTO review VALUES(?,?,?,?)',[(r['key'],r['decision'],r['bucket'],json.dumps(r,ensure_ascii=False)) for r in reviews])
        db.execute("CREATE VIEW candidate AS SELECT * FROM review WHERE decision='availability_passed'")
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('SELECT count(*) FROM review').fetchone()[0]==len(rows)
    display_date=now.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    lines=[f'# 新手任务统一筛选与复核（{display_date}）','',
        f"范围：{summary['source_inputs']} 个来源，{summary['source_repositories']} 个仓库，去重 {len(rows)} 条 Issue；不是只筛最新 83 条。",'',
        '| 结果 | 数量 |','|---|---:|']
    labels={'excluded':'当前排除','review_pending':'需要语境/范围核实','unverified':'具体状态未核实','availability_passed':'通过当前可参与性检查'}
    lines += [f"| {labels.get(k,k)} | {counts.get(k,0)} |" for k in ('excluded','review_pending','unverified','availability_passed')]
    lines += ['','## 可参与候选的层次','',
        f"优先新手短名单：{buckets.get('newcomer_priority',0)}；其他明确新手标签：{buckets.get('newcomer_label_candidate',0)}；",
        f"仅欢迎贡献信号、难度待评估：{buckets.get('welcome_difficulty_review',0)}；仅历史信号：{buckets.get('historical_signal_only',0)}。",'',
        '短名单是助手基于任务范围和证据给出的优先阅读建议，不是难度人工标注真值；参与前需要重查 GitHub。','',
        '## 优先查看的新手任务','']
    for row in candidates:
        if row['bucket']=='newcomer_priority':
            p=PRIORITY[row['key']]
            lines += [f"- [{row['repository']}#{row['issue_number']} — {p['task']}]({row['url']})",f"  - 基础：{'、'.join(p['skills'])}。范围：{p['scope']} 理由：{p['rationale']}"]
    lines+=['','## 核查与边界','',
        f"读取到身份匹配的 Issue 详情 {summary['detail_reads_matched']} 条；完整时间线 {summary['complete_timelines']} 条；关联 PR 详情 {summary['pr_reads']} 条。",
        '详情未读取到的记录保留未核实，不推断为关闭或删除。旧认领、未确认实现、提交引用和阻塞标签保守进入待核实，不等同于当前必然被占用。',
        f'本次范围内全部 {len(rows)} 条均有审计结果；待核实记录并未被伪称为已完成状态确认。',
        '原始证据 observation 时间保留，未续期；通过标准使用 24 小时有效期。',
        '全量审计见 audit.json / all_reviews.csv；候选见 candidates.json / candidates.csv；短名单见 priority_newcomers.json。',
        '本次老师库访问 0、远端写入 0；旧工作库、网页与原采集进度未修改；结果未冻结。','']
    (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    save('manifest.json',dict(source_sha256=hashlib.sha256((directory/'source_issues.json').read_bytes()).hexdigest(),
        evidence_sha256=hashlib.sha256((directory/'complete_evidence.json').read_bytes()).hexdigest(),
        outputs={file.name:hashlib.sha256(file.read_bytes()).hexdigest() for file in output.iterdir() if file.is_file()}))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    parser.add_argument('--output-name',default='results')
    args=parser.parse_args()
    print(json.dumps(finalize(args.directory,args.output_name),ensure_ascii=False))
