"""Create a new local inventory snapshot from a newer batch review."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3


def append(previous,review,output,pending_batches=None):
    output.mkdir(exist_ok=False)
    read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    rows=read(review/'audit.json');candidates=[r for r in rows if r['decision']=='availability_passed']
    before=read(previous/'summary.json');review_summary=read(review/'summary.json')
    with sqlite3.connect((previous/'issue_inventory.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as source:
        with sqlite3.connect(output/'issue_inventory.sqlite3') as db:
            source.backup(db)
            added=removed=0
            for r in rows:
                existing=db.execute('SELECT github_issue_id FROM issue_inventory WHERE issue_key=?',(r['key'],)).fetchone()
                if existing and existing[0]!=r['github_issue_id']:
                    raise ValueError('Identity conflict: '+r['key'])
                if r['decision']!='availability_passed':
                    if existing:
                        db.execute('DELETE FROM issue_inventory WHERE issue_key=?',(r['key'],));removed+=1
                        db.execute('INSERT OR REPLACE INTO superseded_candidate VALUES(?,?,?,?)',(r['key'],r['decision'],r['reason'],str(review/'audit.json')))
                    continue
                added+=not bool(existing)
                db.execute('INSERT OR REPLACE INTO issue_inventory VALUES(?,?,?,?,?,?,?,?,?)',(
                    r['key'],r['github_issue_id'],r['repository'],r['issue_number'],'availability_passed',
                    r['verified_at'],r['expires_at'],str(review/'audit.json'),json.dumps(r,ensure_ascii=False)))
            total=db.execute('SELECT count(*) FROM issue_inventory').fetchone()[0]
            fresh=db.execute('SELECT count(*) FROM fresh_candidate').fetchone()[0]
            stale=db.execute("SELECT count(*) FROM issue_inventory WHERE review_status='historical_requires_refresh'").fetchone()[0]
            expired=total-fresh-stale
            assert expired>=0
            summary=dict(generated_at=datetime.now(timezone.utc).isoformat(),previous_inventory=before['total_inventory'],
                total_inventory=total,recent_valid_candidates=fresh,historical_requires_refresh=stale,
                expired_reviewed_candidates=expired,total_requires_refresh=total-fresh,
                latest_batch_candidates=len(candidates),latest_batch_counts=review_summary['counts'],
                latest_batch_source_count=review_summary['source_count'],pending_collection_batches=pending_batches,
                added=added,removed=removed,target=10000,remaining_to_historical_target=max(0,10000-total),
                source_files=[str(previous/'issue_inventory.sqlite3'),str(review/'audit.json')],
                not_a_current_10000_recommendation_claim=True,operational_database_changed=False,remote_writes=0)
            db.execute('INSERT OR REPLACE INTO snapshot_metadata VALUES(?,?)',('summary',json.dumps(summary,ensure_ascii=False)))
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert total==before['total_inventory']+added-removed
            current=[json.loads(r[0]) for r in db.execute('SELECT record_json FROM fresh_candidate')]
    def save(name,data):(output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save('summary.json',summary);save('recent_candidates.json',current)
    counts=review_summary['counts']
    result_text=f"{len(candidates)}条通过、{counts.get('excluded',0)}条排除、{counts.get('review_pending',0)}条待核实、{counts.get('unverified',0)}条未核实"
    display_date=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    lines=[f'# 补采筛选与累计库存（{display_date}）','',
        f"本轮{review_summary['source_count']}条采集线索：**{result_text}**。",'',
        f"累计库存 **{total}条**，其中 **{fresh}条有近期有效筛选证据**；{stale}条历史候选和{expired}条已过期复核记录需刷新。距累计一万条还差{max(0,10000-total)}条，当前有效数量以fresh_candidate视图为准。",'',
        f"采集断点仍剩{pending_batches}个待处理范围（含失败），本次只筛已成功取得的身份，不宣称整轮采完。" if pending_batches is not None else '采集完成情况以原始断点为准。',
        '语境复核和理由见本轮context_reviews.json；依赖未完成、提案未定案或已有实现线索保留待核实，不冒充已关闭或已被认领。',
        '候选仍需按技能与范围进一步匹配，不把可参与性或标签当作难度保证。','',
        f"## 本轮通过的{len(candidates)}条候选",'']
    for r in candidates:lines.append(f"- [{r['repository']}#{r['issue_number']} — {r['title']}]({r['url']})")
    lines+=['','## 证据与产物','',
        f"成功读取{review_summary['detail_reads_matched']}条身份匹配详情、{review_summary['complete_timelines']}条完整时间线、{review_summary['pr_reads']}个PR详情。仓库元数据保留原观测时间；失败读取保留未知，不推断为删除。",
        f"本轮最终审计与SQLite位于 {review}；同级results如存在则为初步版本，以当前目录为准。",
        '本目录 issue_inventory.sqlite3 是新的独立汇总工作库；fresh_candidate视图使用记录有效期过滤。旧证据未续期，原库与网页未修改。',
        '本次筛选老师数据库请求0次、远端写入0次。','']
    (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    (review/'report.md').write_text(f'# 本轮补采筛选\n\n最终{result_text}。\n\n详见 [补采筛选与累计库存](../../{output.name}/report.md)。\n',encoding='utf-8')
    manifest=read(review/'manifest.json');manifest['outputs']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in review.iterdir() if p.is_file() and p.name!='manifest.json'}
    (review/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save('manifest.json',dict(sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [previous/'issue_inventory.sqlite3',review/'audit.json']},
        outputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous',type=Path,required=True);parser.add_argument('--review',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pending-batches',type=int)
    args=parser.parse_args();print(json.dumps(append(args.previous,args.review,args.output,args.pending_batches),ensure_ascii=False))
