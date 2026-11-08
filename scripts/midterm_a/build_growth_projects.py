"""Build a separate, evidence-backed project pool from read-only GitHub snapshots."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def metadata_reasons(source, envelope, now):
    if not envelope:
        return ['metadata_unverified']
    p = envelope['payload']
    reasons = []
    if source.get('github_repository_id') and str(source['github_repository_id']) != str(p.get('id')):
        reasons.append('repository_identity_mismatch')
    if p.get('full_name', '').casefold() != source['full_name'].casefold():
        reasons.append('repository_mapping_requires_review')
    if now - timestamp(envelope['observed_at']) > timedelta(days=7):
        reasons.append('metadata_stale')
    if p.get('archived') is not False or p.get('disabled') is not False:
        reasons.append('archived_disabled_or_unknown')
    if p.get('fork') is not False:
        reasons.append('fork_or_unknown')
    if not p.get('pushed_at') or now - timestamp(p['pushed_at']) > timedelta(days=180):
        reasons.append('no_recent_push_signal')
    return reasons


def pathway_reasons(config, ev, now):
    repo = config['repository']
    reads = ev['reads']
    reasons = []
    doc = reads.get(repo + '/' + config['contribution_doc'])
    if not doc or not isinstance(doc['payload'], str) or len(doc['payload']) < 100:
        reasons.append('contribution_guide_unverified')
    elif now - timestamp(doc['observed_at']) > timedelta(days=7):
        reasons.append('contribution_guide_stale')
    entries = []
    for key, value in reads.items():
        if key == repo + '/root' or key.startswith(repo + '/directory/'):
            entries.extend(value['payload'])
    paths = {x['path'] for x in entries if isinstance(x, dict)}
    if not set(config['module_paths']) <= paths:
        reasons.append('module_path_unverified')
    pulls = reads.get(repo + '/recent_pulls_extended', reads.get(repo + '/recent_pulls'))
    if not pulls or now - timestamp(pulls['observed_at']) > timedelta(days=7):
        reasons.append('recent_collaboration_unverified')
    elif not any(p.get('merged_at') and p.get('user', {}).get('type') == 'User'
                 and now - timestamp(p['merged_at']) <= timedelta(days=90) for p in pulls['payload']):
        reasons.append('no_recent_human_merge_in_sample')
    metadata = ev['repositories'][repo]['payload']
    spdx = (metadata.get('license') or {}).get('spdx_id')
    if spdx in (None, 'NOASSERTION'):
        license_read = reads.get(repo + '/LICENSE.md')
        if not (repo == 'rollup/rollup' and license_read and
                'Rollup is released under the MIT license' in license_read['payload']):
            reasons.append('license_requires_review')
    return reasons


def build(directory, configuration):
    read = lambda p: json.loads(p.read_text(encoding='utf-8-sig'))
    sources = read(directory / 'source_repositories.json')
    ev = read(directory / 'final_evidence.json')
    configs = read(configuration)
    now = datetime.now(timezone.utc)
    output = directory / 'results'
    output.mkdir(exist_ok=False)
    by_repo = {r['full_name']: r for r in sources}
    assert len(by_repo) == len(sources)
    assert len({c['repository'] for c in configs}) == len(configs)
    audits, cards, reviews = [], [], []
    for source in sources:
        repo = source['full_name']
        envelope = ev['repositories'].get(repo)
        reasons = metadata_reasons(source, envelope, now)
        audits.append(dict(repository=repo,metadata_passed=not reasons,reasons=reasons,
                           observed_at=envelope['observed_at'] if envelope else None,
                           deep_reviewed=repo in {c['repository'] for c in configs}))
    for config in configs:
        repo = config['repository']
        assert repo in by_repo, 'Cannot recommend a project outside the source inventory'
        envelope = ev['repositories'].get(repo)
        reasons = metadata_reasons(by_repo[repo], envelope, now)
        if envelope:
            reasons += pathway_reasons(config, ev, now)
        decision = 'project_candidate' if not reasons else 'review_pending'
        p = envelope['payload'] if envelope else {}
        entries = {k:v for k,v in ev['reads'].items() if k.startswith(repo + '/')}
        doc = entries.get(repo + '/' + config['contribution_doc'])
        source_times = [envelope['observed_at']] if envelope else []
        source_times += [v['observed_at'] for v in entries.values()]
        observed = min(source_times) if source_times else None
        card = dict(config,project_key=repo.casefold(),repository_id=p.get('id'),
            item_type='project',decision=decision,reasons=reasons,url=p.get('html_url'),
            primary_language=p.get('language'),description=p.get('description'),
            license_api=(p.get('license') or {}).get('spdx_id'),
            license_note='核心为 MIT；另查仓库 LICENSE.md，各组件许可证仍以原文为准。' if repo == 'rollup/rollup' else None,
            pushed_at=p.get('pushed_at'),observed_at=observed,
            recheck_after=(timestamp(observed)+timedelta(days=7)).isoformat() if observed else None,
            contribution_url=f"{p.get('html_url')}/blob/{p.get('default_branch')}/{config['contribution_doc']}",
            module_links=[dict(path=path,url=f"{p.get('html_url')}/tree/{p.get('default_branch')}/{path}")
                          for path in config['module_paths']],
            contribution_snapshot_key=repo + '/' + config['contribution_doc'],
            source_inventory='merged_candidates_v0.12',local_build_tested=False,
            role_or_membership_promised=False,maintainer_requested_task=False,
            evidence_sha256={k:hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                             for k,v in entries.items()})
        sample = entries.get(repo + '/recent_pulls_extended', entries.get(repo + '/recent_pulls'))
        card['recent_human_merged_prs'] = [dict(url=x['html_url'],merged_at=x['merged_at'])
            for x in (sample or {}).get('payload',[]) if x.get('merged_at')
            and x.get('user',{}).get('type') == 'User'
            and now-timestamp(x['merged_at']) <= timedelta(days=90)]
        (cards if not reasons else reviews).append(card)
    summary = dict(generated_at=now.isoformat(),source_repositories=len(sources),
        metadata_reads=len(ev['repositories']),metadata_passed=sum(a['metadata_passed'] for a in audits),
        deep_reviewed=len(configs),project_candidates=len(cards),review_pending=len(reviews),
        not_deep_reviewed=len(sources)-len(configs),language_tracks=dict(Counter(c['language_track'] for c in cards)),
        levels=dict(Counter(c['level'] for c in cards)),teacher_database_requests=0,remote_writes=0,
        operational_database_changed=False,website_integrated=False,personalized=False,
        observations_start=ev['observed_start'],observations_end=ev['observed_end'],
        rules=dict(push_signal_days=180,human_merged_sample_days=90,evidence_validity_days=7),
        limitations=['定向首批筛选，不是320个项目的全面优劣排名。','PR样本来自最近更新的已关闭PR，不是完整合并历史。',
                     '推送与合并仅是维护信号，不保证响应速度、接纳贡献或个人适配。',
                     '成果、基础与层次是助手建议；未实际构建这些项目，不是难度人工标注真值。'])
    def save(name, data):
        (output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save('repository_audit.json',audits)
    save('project_candidates.json',cards)
    save('review_pending.json',reviews)
    save('summary.json',summary)
    with sqlite3.connect(output/'growth_projects.sqlite3') as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
        CREATE TABLE repository_audit(repository TEXT PRIMARY KEY,metadata_passed INTEGER NOT NULL,record_json TEXT NOT NULL);
        CREATE TABLE project_candidate(project_key TEXT PRIMARY KEY,repository TEXT UNIQUE NOT NULL,repository_id INTEGER NOT NULL,item_type TEXT NOT NULL CHECK(item_type='project'),language_track TEXT NOT NULL,level TEXT NOT NULL,observed_at TEXT NOT NULL,recheck_after TEXT NOT NULL,record_json TEXT NOT NULL);
        CREATE TABLE project_pathway(project_key TEXT PRIMARY KEY REFERENCES project_candidate(project_key),focus TEXT NOT NULL,prerequisites_json TEXT NOT NULL,outcome TEXT NOT NULL,acceptance_json TEXT NOT NULL);
        CREATE TABLE project_resource(resource_key TEXT PRIMARY KEY,project_key TEXT NOT NULL REFERENCES project_candidate(project_key),kind TEXT NOT NULL,url TEXT NOT NULL,evidence_key TEXT NOT NULL);
        CREATE TABLE project_review_queue(project_key TEXT PRIMARY KEY,record_json TEXT NOT NULL);
        ''')
        db.executemany('INSERT INTO repository_audit VALUES(?,?,?)',[(r['repository'],r['metadata_passed'],json.dumps(r,ensure_ascii=False)) for r in audits])
        for c in cards:
            db.execute('INSERT INTO project_candidate VALUES(?,?,?,?,?,?,?,?,?)',(c['project_key'],c['repository'],c['repository_id'],'project',c['language_track'],c['level'],c['observed_at'],c['recheck_after'],json.dumps(c,ensure_ascii=False)))
            db.execute('INSERT INTO project_pathway VALUES(?,?,?,?,?)',(c['project_key'],c['focus'],json.dumps(c['prerequisites'],ensure_ascii=False),c['proposed_outcome'],json.dumps(c['acceptance'],ensure_ascii=False)))
            db.execute('INSERT INTO project_resource VALUES(?,?,?,?,?)',(c['project_key']+'/contributing',c['project_key'],'contributing',c['contribution_url'],c['contribution_snapshot_key']))
            for n,m in enumerate(c['module_links']):
                db.execute('INSERT INTO project_resource VALUES(?,?,?,?,?)',(c['project_key']+'/module/'+str(n),c['project_key'],'module',m['url'],'final_evidence.json:root_or_directory'))
        db.executemany('INSERT INTO project_review_queue VALUES(?,?)',[(c['project_key'],json.dumps(c,ensure_ascii=False)) for c in reviews])
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    lines=['# 进阶项目推荐首批清单（2026-10-23）','',
        f"从现有{len(sources)}个仓库核查元数据，定向深入整理{len(configs)}个项目；形成{len(cards)}个项目候选、{len(reviews)}个待复核。其余项目尚未深入筛选，不能视作不适合。",'',
        '项目卡片提供持续参与方向，插件、扩展、测试、性能实验和设计讨论都可成为入口。Issue仍可用于确认需求，但不再是唯一推荐单元。',
        '层次与成果均为助手建议。提交代码前先按贡献指南确认需求和流程；不承诺成为协作者或贡献被接收。','',
        '| 项目 | 技术方向 | 建议层次 | 模块方向 |','|---|---|---|---|']
    for c in cards:
        lines.append(f"| [{c['repository']}]({c['url']}) | {c['language_track']} | {c['level']} | {c['focus']} |")
    lines += ['','## 项目卡片','']
    for c in cards:
        links='、'.join(f"[{m['path']}]({m['url']})" for m in c['module_links'])
        lines += [f"### {c['repository']}：{c['focus']}",'',
            f"基础：{'、'.join(c['prerequisites'])}。建议层次：{c['level']}。",'',
            f"建议成果：{c['proposed_outcome']}",f"验收建议：{'；'.join(c['acceptance'])}。",'',
            f"模块入口：{links}。",f"[官方贡献指南]({c['contribution_url']})。{c['setup_notes']}",
            f"协作与范围：{c['coordination']}",
            f"维护信号：最近推送 {c['pushed_at']}；抽样观察到{len(c['recent_human_merged_prs'])}个人类作者近期合并PR，不代表全部历史或当前待领任务。",'']
    lines += ['## 待复核','']
    for c in reviews:
        lines += [f"- {c['repository']}：{', '.join(c['reasons'])}。{c['coordination']}"]
    lines += ['','## 数据与边界','',
        f"320个仓库中{summary['metadata_passed']}个通过元数据初筛，仅代表身份、非归档、非禁用、非fork和180天内推送信号。",
        '项目准入另外要求：已读贡献文档、已观察到代码目录、90天内有人类作者合并PR样本，且证据不超过7天。未达标归入待复核，不推断项目已停止维护。',
        '项目未在本机编译，环境、成本和测试耗时未实测；没有个人画像时不声称完成个性化适配。',
        '独立工作库：growth_projects.sqlite3；结构化卡片：project_candidates.json；完整审计：repository_audit.json；官方原始证据位于上级 final_evidence.json。',
        '原业务库、Issue候选库和网页未变更；本次老师数据库请求0次、远端写入0次。','']
    (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    save('manifest.json',dict(source_sha256=hashlib.sha256((directory/'source_repositories.json').read_bytes()).hexdigest(),
        evidence_sha256=hashlib.sha256((directory/'final_evidence.json').read_bytes()).hexdigest(),
        configuration_sha256=hashlib.sha256(configuration.read_bytes()).hexdigest(),
        outputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--configuration',type=Path,default=Path('config/midterm_datasets/growth_project_pathways_v0.1.json'))
    args=parser.parse_args()
    print(json.dumps(build(args.directory,args.configuration),ensure_ascii=False))
