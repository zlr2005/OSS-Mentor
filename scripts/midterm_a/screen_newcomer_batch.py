"""Offline newcomer screening report from retained, current GitHub GET evidence."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from screen_unified_teacher import assess, context_fingerprint


def screen(directory):
    def read(name):return json.loads((directory/name).read_text(encoding='utf-8-sig'))
    def write(name,value):(directory/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    rows=read('source_issues.json');ev=read('complete_evidence.json')
    overrides={r['key']:r for r in read('context_reviews.json')}
    now=datetime.now(timezone.utc);reviews=[]
    seen=set()
    for row in rows:
        key=f"{row['repo_name']}#{row['issue_number']}".lower()
        if key in seen:raise ValueError('Duplicate source')
        seen.add(key)
        envelope=ev['details'][key];detail=envelope['payload'];repo=ev['repositories'][row['repo_name']]
        timeline=ev['timelines'].get(key)
        result=assess(row,detail,envelope['observed_at'],repo['payload'],repo['observed_at'],timeline,ev['pull_requests'],now=now)
        if result['reason']=='identity_mismatch':raise ValueError('Unresolved identity')
        if key in overrides:
            review=overrides[key]
            if review['decision'] not in ('excluded','review_pending'):raise ValueError('Context review cannot approve a task')
            if not review['evidence'] or not review['rationale']:raise ValueError('Missing review evidence')
            if review['reason']=='linked_open_pr':
                urls={p['html_url'] for p in ev['pull_requests'].values() if p.get('state')=='open'}
                if not urls.intersection(review['evidence']):raise ValueError('Missing open PR proof')
            result.update(decision=review['decision'],reason=review['reason'],context_review=dict(review,
                evidence_sha256=context_fingerprint(detail,timeline),reviewed_at=now.isoformat()))
        reviews.append(dict(key=key,repository=row['repo_name'],issue_number=int(row['issue_number']),
            github_issue_id=str(detail['id']),title=detail['title'],url=detail['html_url'],
            current_labels=[l['name'] for l in detail['labels']],language=repo['payload'].get('language'),
            verified_at=envelope['observed_at'],beginner_difficulty_confirmed=False,**result))
    counts=dict(Counter(r['decision'] for r in reviews));reasons=dict(Counter(r['reason'] for r in reviews))
    summary=dict(source_count=len(rows),reviewed=len(reviews),counts=counts,reasons=reasons,
        source_repositories=len(ev['repositories']),timelines_checked=len(ev['timelines']),
        prs_checked=len(ev['pull_requests']),observed_start=ev['observed_start'],observed_end=ev['observed_end'],
        generated_at=now.isoformat(),teacher_database_requests=0,remote_writes=0,frozen=False,
        directly_recommendable=sum(r['decision']=='availability_passed' for r in reviews),
        evidence_sha256=hashlib.sha256((directory/'complete_evidence.json').read_bytes()).hexdigest())
    write('audit.json',reviews);write('summary.json',summary)
    write('candidates.json',[r for r in reviews if r['decision']=='availability_passed'])
    for filename,decision in (('review_queue.csv','review_pending'),('excluded.csv','excluded')):
        with (directory/filename).open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['repository','issue_number','title','url','language','reason','rationale'])
            writer.writeheader()
            for r in reviews:
                if r['decision']==decision:writer.writerow({k:r.get(k,r.get('context_review',{}).get(k,'')) for k in writer.fieldnames})
    database=directory/'screening.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE review(issue_key TEXT PRIMARY KEY, decision TEXT NOT NULL, reason TEXT NOT NULL, record_json TEXT NOT NULL)')
        db.executemany('INSERT INTO review VALUES(?,?,?,?)',[(r['key'],r['decision'],r['reason'],json.dumps(r,ensure_ascii=False)) for r in reviews])
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('SELECT count(*) FROM review').fetchone()[0]==len(rows)
    lines=['# 新手任务筛选结果（2026-10-21）','',
        f"已核验 {len(rows)} 条来源身份；直接推荐 {summary['directly_recommendable']} 条；当前排除 {counts.get('excluded',0)} 条；待核实 {counts.get('review_pending',0)} 条。",'',
        '当前排除原因：'+ '；'.join(f'{k}: {v}' for k,v in reasons.items() if any(r['decision']=='excluded' and r['reason']==k for r in reviews)),'',
        '## 待核实任务','',
        '以下任务尚不能直接推荐；旧认领不自动认定仍然有效，也不能自动认定已经失效。','']
    for r in reviews:
        if r['decision']=='review_pending':lines.append(f"- [{r['repository']}#{r['issue_number']}: {r['title']}]({r['url']}) — {r.get('context_review',{}).get('rationale',r['reason'])}")
    lines+=['','## 证据与范围','',
        f"当前 GitHub 详情 {len(ev['details'])} 条、仓库元数据 {len(ev['repositories'])} 份、完整时间线 {len(ev['timelines'])} 条、PR 状态 {len(ev['pull_requests'])} 条。",
        '原始公开 GET 证据保存在 complete_evidence.json；逐条结论和语境复核指纹见 audit.json。',
        '老师库采集已停止，本次不连接或修改老师数据库；未修改网页、未合并既有工作库、未冻结数据。',
        '新手标签仅是发现线索，难度尚非人工标注真值。未来实际参与前仍需重查状态。','']
    (directory/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    print(json.dumps(screen(parser.parse_args().directory),ensure_ascii=False))
