"""Combine historical inventory and newer reviews without renewing old evidence."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def build():
    base=Path('D:/OSS-Mentor/data/working/merged_candidates_v0.12.sqlite3')
    recent=Path('data/newcomer_refresh_20261023_v1/results/audit.json')
    batch=Path('data/inventory_screening_20261023_v1/results_v2')
    output=Path('data/candidate_inventory_20261023_v1')
    output.mkdir(exist_ok=False)
    read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    now=datetime.now(timezone.utc)
    pool={};replaced=[]
    with sqlite3.connect(base.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        for r in db.execute("SELECT r.full_name AS repository,t.* FROM task_candidate t JOIN repository r USING(repository_id) WHERE t.candidate_eligibility='eligible'"):
            r=dict(r);key=f"{r['repository']}#{r['issue_number']}".lower()
            pool[key]=dict(key=key,repository=r['repository'],number=r['issue_number'],issue_id=str(r['github_issue_id']),
                observed_at=r['github_verified_at'],expires_at=None,review_status='historical_requires_refresh',
                source=str(base),record=r)
    base_count=len(pool)
    for file in (recent,batch/'audit.json'):
        for r in read(file):
            key=r['key'];previous=pool.get(key)
            if previous and str(r['github_issue_id'])!=previous['issue_id']:
                raise ValueError('Identity conflict: '+key)
            if previous and previous['observed_at'] and r.get('verified_at') and (
                datetime.fromisoformat(previous['observed_at'].replace('Z','+00:00'))>
                datetime.fromisoformat(r['verified_at'].replace('Z','+00:00'))):
                continue
            if r['decision']!='availability_passed':
                if previous:
                    del pool[key]
                    replaced.append(dict(key=key,new_decision=r['decision'],reason=r['reason'],source=str(file)))
                continue
            pool[key]=dict(key=key,repository=r['repository'],number=r['issue_number'],issue_id=str(r['github_issue_id']),
                observed_at=r['verified_at'],expires_at=r['expires_at'],review_status='availability_passed',
                source=str(file),record=r)
    assert len({r['issue_id'] for r in pool.values()})==len(pool),'Duplicate global Issue identity'
    current=[r for r in pool.values() if r['review_status']=='availability_passed'
             and datetime.fromisoformat(r['expires_at'])>now]
    new=read(batch/'candidates.json');summary=read(batch/'summary.json')
    assert len(new)==1137 and len(pool)==9477 and len(current)==1253
    assert all(r['state']=='open' and not r['assignees'] and not r['locked'] and r['timeline_complete'] for r in new)
    value=dict(generated_at=now.isoformat(),historical_base=base_count,total_inventory=len(pool),
        recent_valid_candidates=len(current),historical_requires_refresh=len(pool)-len(current),
        latest_batch_candidates=len(new),latest_batch_counts=summary['counts'],
        target=10000,remaining_to_historical_target=max(0,10000-len(pool)),
        removed_by_newer_review=replaced,source_files=[str(base),str(recent),str(batch/'audit.json')],
        not_a_current_10000_recommendation_claim=True,operational_database_changed=False,remote_writes=0)
    def save(name,data):(output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save('summary.json',value)
    save('recent_candidates.json',[r['record'] for r in current])
    with sqlite3.connect(output/'issue_inventory.sqlite3') as db:
        db.executescript('''
        CREATE TABLE issue_inventory(issue_key TEXT PRIMARY KEY,github_issue_id TEXT UNIQUE NOT NULL,repository TEXT NOT NULL,issue_number INTEGER NOT NULL CHECK(issue_number>0),review_status TEXT NOT NULL CHECK(review_status IN ('availability_passed','historical_requires_refresh')),observed_at TEXT,expires_at TEXT,source TEXT NOT NULL,record_json TEXT NOT NULL);
        CREATE TABLE superseded_candidate(issue_key TEXT PRIMARY KEY,new_decision TEXT NOT NULL,reason TEXT NOT NULL,source TEXT NOT NULL);
        CREATE VIEW fresh_candidate AS SELECT * FROM issue_inventory WHERE review_status='availability_passed' AND julianday(expires_at)>julianday('now');
        CREATE TABLE snapshot_metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
        ''')
        db.executemany('INSERT INTO issue_inventory VALUES(?,?,?,?,?,?,?,?,?)',[(r['key'],r['issue_id'],r['repository'],r['number'],r['review_status'],r['observed_at'],r['expires_at'],r['source'],json.dumps(r['record'],ensure_ascii=False)) for r in pool.values()])
        db.executemany('INSERT INTO superseded_candidate VALUES(?,?,?,?)',[(r['key'],r['new_decision'],r['reason'],r['source']) for r in replaced])
        db.execute('INSERT INTO snapshot_metadata VALUES(?,?)',('summary',json.dumps(value,ensure_ascii=False)))
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('SELECT count(*) FROM fresh_candidate').fetchone()[0]==len(current)
    lines=['# Issue候选库存汇总（2026-10-23）','',
        f"当前累计库存 **{len(pool)}条**，其中 **{len(current)}条有近期有效筛选证据**，另外 **{len(pool)-len(current)}条是需刷新状态的历史候选**。不能把累计库存等同于实时可推荐数。",'',
        '## 本轮3512条线索的筛选结果','',
        '| 结果 | 数量 |','|---|---:|',
        f"| 通过可参与性检查 | {len(new)} |",'| 已关闭、已分配、锁定或已有开放PR而排除 | 1874 |',
        '| 认领语境、已有实现、阻塞标签或范围待核实 | 427 |','| 具体状态未核实 | 74 |','',
        '通过候选中8条有新手标签信号、10条只有欢迎贡献信号，其余1119条进入普通候选；不是直接认定适合所有进阶开发者，也不是难度人工标注。',
        '细查时发现1条正文PR引用无法核实，已从初步1138条通过结果转入待核实；最终以1137条为准。','',
        '## 累计数量如何计算','',
        '旧v0.12的8270条 + 前几轮已筛选但尚未并入旧库的72条 + 本轮1137条 - 最新复查转入待核实的2条 = **9477条**。',
        '撤下的是 apache/gravitino#13803 和 sympy/sympy#30671：存在提交引用需核实，不是宣称任务已关闭或被占用。',
        '**按累计库存距离一万条还差523条。** 要声称一万条当前可推荐，还必须刷新旧库存；合并没有更改旧记录的观察时间。','',
        '## 证据和产物','',
        '本轮成功读取3439条身份匹配详情、1668条完整时间线和460个关联PR详情。73条Issue详情未读取到，另1条时间线不完整；均保留为未核实，不推断为删除。',
        '已有241个仓库元数据沿用本次前序核查，保留原时间戳。PR与讨论按最新读取证据判断；426条初步待核实经补充检查后为427条。',
        '[本轮全部审计](../inventory_screening_20261023_v1/results_v2/audit.json)；[本轮通过候选](../inventory_screening_20261023_v1/results_v2/candidates.json)。',
        '汇总工作库：issue_inventory.sqlite3，issue_inventory表保存累计库存，fresh_candidate视图按24小时有效期动态过滤；summary.json保存来源和口径。',
        '这是独立统计工作库，未接入网页推荐接口；原业务库和旧候选库保持原状。本次筛选老师数据库请求0次、远端写入0次。','']
    (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    manifest=dict(sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [base,recent,batch/'audit.json']},
        outputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()})
    save('manifest.json',manifest)
    # Give the reused generic screening outputs a report relevant to this batch.
    (batch/'report.md').write_text('# 本轮全类型Issue筛选\n\n最终：1137条通过、1874条排除、427条待核实、74条未核实。\n\n累计口径与边界详见 [库存汇总报告](../../candidate_inventory_20261023_v1/report.md)。\n\n原始来源全部为老师库，筛选仅GitHub只读GET。\n',encoding='utf-8')
    manifest=read(batch/'manifest.json')
    manifest['outputs']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in batch.iterdir() if p.is_file() and p.name!='manifest.json'}
    (batch/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return value


if __name__=='__main__':
    print(json.dumps(build(),ensure_ascii=False))
