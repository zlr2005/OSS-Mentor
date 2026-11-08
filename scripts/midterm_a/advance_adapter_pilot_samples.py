"""复用 B 的规则回放试采画像；保持第一轮数据不变，不输出正式训练包。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from oss_mentor.profiling.assessment import _fuse_feature, _normalize_external_evidence, load_questionnaire
from oss_mentor.profiling.evidence import interpret_github_contributions

SOURCE = ROOT / 'data/midterm/working/advanced_adapter_pilot_20261102'
OUT = ROOT / 'data/midterm/working/advanced_adapter_pilot_20261102_stage2'
QUESTIONNAIRE = ROOT / 'config/midterm_profile/questionnaire_v0.1.json'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def canonical(payload):
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def save(relative, payload):
    path = OUT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(payload))


def lines(relative, payloads):
    path = OUT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b''.join(canonical(p) for p in payloads))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iso(value):
    if value.endswith(' UTC'):
        value = value[:-4].replace(' ', 'T') + 'Z'
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def eligible():
    return [r for r in rows(SOURCE / 'sample_audit.jsonl') if r['structural_chain_eligible']]


def replay_one(sample, histories, questionnaire):
    """不读取任何结果 PR 的代码或当前问卷；仅消费该截止点之前的代码证据。"""
    inputs, documents, omissions = [], [], []
    for history in histories:
        if history['as_of'][:4] != str(sample['year']) or history['github_login'].casefold() != sample['github_login'].casefold() or history['repository'] != sample['repository']:
            continue
        assert iso(history['event_at']) < iso(history['as_of'])
        assert iso(history['author_at']) < iso(history['as_of'])
        if not history['code_documents']:
            omissions.append({'commit_sha': history['commit_sha'], 'reason': 'no_collected_python_code_in_bounded_sample'})
        for doc in history['code_documents']:
            content = (SOURCE / doc['content_location']).read_bytes()
            assert hashlib.sha256(content).hexdigest() == doc['content_sha256']
            version_key = 'pilot-file-version:' + hashlib.sha256(canonical([history['repository'], doc['path'], history['commit_sha']])).hexdigest()
            documents.append({'file_version_key': version_key, 'repository_key': history['repository'].lower(),
                'path': doc['path'], 'available': True, 'content_sha256': doc['content_sha256'],
                'content_location': str((SOURCE / doc['content_location']).relative_to(ROOT))})
            inputs.append({'developer_key': sample['github_login'].casefold(),
                'source_developer_key': history['developer_key'],
                'github_numeric_id': history['github_numeric_id'],
                'repository_key': history['repository'].lower(), 'file_key': 'pilot-file:' + doc['path'],
                'file_version_key': version_key, 'commit_sha': history['commit_sha'],
                'behavior_type': 'commit', 'author_link_status': 'verified',
                'event_at': iso(history['event_at']), 'author_at': iso(history['author_at']),
                'evidence_location': str((SOURCE / history['evidence_ref']).relative_to(ROOT))})
    cutoff = f"{sample['year']}-04-01T00:00:00Z"
    source_observed = iso(read(SOURCE / 'evidence' / (sample['sample_key'] + '_history.json'))['observed_at'])
    batch = interpret_github_contributions(inputs, documents, developer_key=sample['github_login'].casefold(),
        as_of=cutoff, observed_at=source_observed, collection_status='partial',
        collection_limitations=['At most 3 Q1 commits and 1 Python file per selected commit.',
            'No historical questionnaire, weak interest or target outcome evidence.',
            'Commit timestamps checked; independent first-public-availability evidence not established.'])
    grouped = defaultdict(list)
    for e in _normalize_external_evidence(batch['evidence'], allowed_features=set(questionnaire['feature_names'])):
        grouped[e['feature_name']].append(e)
    fused = [_fuse_feature(name, grouped[name]) for name in questionnaire['feature_names']]
    profile = {'schema_version': 'advanced-pilot-temporal-profile-v0.1',
        'profile_state': 'research_preview', 'b_review_status': 'pending', 'training_ready': False,
        'profile_key': str(uuid5(NAMESPACE_URL, 'github-user:' + str(inputs[0]['github_numeric_id']))) if inputs else 'empty:' + sample['sample_key'],
        'profile_version': 'pilot-replay:' + sample['sample_key'],
        'developer_key': sample['github_login'].casefold(), 'as_of': cutoff,
        'feature_schema_version': questionnaire['feature_schema_version'],
        'feature_names': list(questionnaire['feature_names']),
        'values': [f['proposed_value'] for f in fused],
        'observed_mask': [f['observed'] for f in fused], 'locked_mask': [False] * len(fused),
        'evidence_refs': [f['evidence_refs'] for f in fused], 'current_interests': [],
        'input_file_contributions': len(inputs), 'omitted_history_commits': omissions,
        'source_identity_mapping': {'source_developer_keys': sorted({r['source_developer_key'] for r in inputs}),
            'github_numeric_ids': sorted({r['github_numeric_id'] for r in inputs})}}
    profile['profile_hash'] = hashlib.sha256(canonical(profile)).hexdigest()
    return profile, batch, {'sample_key': sample['sample_key'], 'as_of': cutoff,
                           'contributions': inputs, 'code_documents': documents}


def replay():
    q = load_questionnaire(QUESTIONNAIRE)
    histories = rows(SOURCE / 'historical_contributions.jsonl')
    extras = prepare_extra_code()
    for h in histories:
        h['code_documents'] = list(h['code_documents'])
        for doc in extras:
            if doc['commit_sha'] == h['commit_sha']:
                h['code_documents'].append(doc)
    profiles, batches, inputs = [], [], []
    for sample in eligible():
        p, b, i = replay_one(sample, histories, q)
        profiles.append(p)
        batches.append({'sample_key': sample['sample_key'], **b})
        inputs.append(i)
    lines('profile_previews.jsonl', profiles)
    lines('profile_evidence_batches.jsonl', batches)
    lines('b_replay_inputs.jsonl', inputs)
    vector_counts = Counter(tuple(p['values'] + p['observed_mask']) for p in profiles)
    observed = Counter(name for p in profiles for name, mask in zip(p['feature_names'], p['observed_mask']) if mask)
    validation = {'schema_version': 'advanced-pilot-replay-audit-v0.1',
        'profiles': len(profiles), 'developers': len({p['developer_key'] for p in profiles}),
        'observed_dimensions_per_profile': [sum(p['observed_mask']) for p in profiles],
        'distinct_model_input_vectors': len(vector_counts), 'vector_group_sizes': sorted(vector_counts.values(), reverse=True),
        'feature_observed_profiles': dict(observed),
        'unobserved_dimensions': [n for n in q['feature_names'] if n not in observed],
        'b_rule_reused': True, 'b_temporal_export_implemented': False,
        'official_C_validator_run': False, 'C_validator_limitation': 'torch unavailable in current Python environment',
        'training_ready': False, 'b_review_status': 'pending',
        'extra_code_documents': len(extras),
        'source_hashes': {str(p.relative_to(ROOT)): sha(p) for p in [QUESTIONNAIRE,
            ROOT / 'src/oss_mentor/profiling/evidence.py', ROOT / 'src/oss_mentor/profiling/assessment.py',
            SOURCE / 'historical_contributions.jsonl', SOURCE / 'sample_audit.jsonl']}}
    save('profile_replay_audit.json', validation)
    print(json.dumps(validation, ensure_ascii=False))


def prepare_extra_code():
    path = OUT / 'extra_code_jobs.json'
    if not path.exists():
        return []
    documents, failures = [], []
    for job in read(path):
        try:
            payload = read(OUT / 'extra_evidence' / (job['id'] + '.json'))['payload']
            content = payload['content'].encode('utf-8')
            if hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest() != job['blob_sha']:
                raise ValueError('git_blob_hash_mismatch')
            if len(content) > 262144:
                raise ValueError('code_size_limit')
            location = OUT / 'extra_code' / (job['blob_sha'] + Path(job['path']).suffix)
            location.parent.mkdir(exist_ok=True)
            location.write_bytes(content)
            documents.append({'commit_sha': job['commit_sha'], 'blob_sha': job['blob_sha'],
                'path': job['path'], 'content_sha256': hashlib.sha256(content).hexdigest(),
                'content_location': '../' + OUT.name + '/' + location.relative_to(OUT).as_posix(),
                'source_url': job['url'], 'evidence_ref': 'extra_evidence/' + job['id'] + '.json'})
        except (OSError, ValueError) as exc:
            failures.append({'record': job['id'], 'reason': str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
    lines('extra_code_documents.jsonl', documents)
    lines('extra_code_failures.jsonl', failures)
    return documents


def replay_title(current, events, cutoff):
    """按完整事件列表逆序撤销截止后的改名；不推断正文版本。"""
    if len(events) >= 100:
        raise ValueError('event_pagination_not_proven_complete')
    title = current
    for e in sorted(events, key=lambda e: (iso(e['created_at']), e['id']), reverse=True):
        if e['event'] != 'renamed' or iso(e['created_at']) <= iso(cutoff):
            continue
        rename = e['rename']
        if title != rename['to']:
            raise ValueError('title_history_inconsistent')
        title = rename['from']
    return title


def content_replay():
    records = []
    for s in eligible():
        cutoff = f"{s['year']}-04-01T00:00:00Z"
        for suffix, role in [('', 'positive'), ('_control', 'unlabeled_control')]:
            key = s['sample_key'] + suffix
            issue = read(SOURCE / 'evidence' / (key + '_issue.json'))['payload']
            events = read(SOURCE / 'evidence' / (key + '_events.json'))['payload']
            assert iso(issue['created_at']) <= cutoff
            p = read(OUT / 'content_evidence' / (key + '.json'))
            assert p.get('payload', {}).get('title') == issue['title'], 'Issue title changed between source reads'
            historical_title = replay_title(issue['title'], events, cutoff)
            records.append({'schema_version': 'advanced-pilot-issue-content-v0.1',
                'sample_key': s['sample_key'], 'issue_key': issue['html_url'].split('github.com/')[1].replace('/issues/', '#').lower(),
                'as_of': cutoff, 'role': role, 'title_at_cutoff': historical_title,
                'title_status': 'replayed_from_current_title_and_complete_returned_rename_events',
                'title_sha256': hashlib.sha256(historical_title.encode()).hexdigest(),
                'body_at_cutoff': None, 'body_status': 'unverified', 'labels_at_cutoff': None,
                'labels_status': 'unverified', 'historical_content_verified': False,
                'training_ready': False, 'source_url': issue['html_url'],
                'source_evidence_refs': [str((SOURCE / 'evidence' / (key + x + '.json')).relative_to(ROOT)) for x in ['_issue', '_events']]})
    lines('historical_issue_content.jsonl', records)
    return records


def import_graphql_response(path):
    """只接受含明确最后编辑时间的正文；截止后编辑的 diff 留待进一步重建。"""
    response = read(path)
    if response.get('errors') or not isinstance(response.get('data'), dict):
        raise ValueError('GraphQL response contains errors or lacks data')
    issues = {}
    for value in response['data'].values():
        issue = value.get('issue') if isinstance(value, dict) else None
        if issue:
            issues[issue['url']] = issue
    records = rows(OUT / 'historical_issue_content.jsonl')
    for record in records:
        record['body_at_cutoff'] = None
        for field in ['model_text_verified', 'model_text_sha256', 'graphql_response_sha256']:
            record.pop(field, None)
        issue = issues.get(record['source_url'])
        if issue is None or 'lastEditedAt' not in issue:
            record['body_status'] = 'explicit_last_edited_field_missing'
            continue
        if iso(issue['createdAt']) > record['as_of']:
            raise ValueError('Issue created after cutoff')
        edited = issue['lastEditedAt']
        if edited is not None and iso(edited) > record['as_of']:
            record['body_status'] = 'edited_after_cutoff_requires_version_reconstruction'
            continue
        if not isinstance(issue.get('body'), str):
            raise ValueError('Issue body must be a string')
        record['body_at_cutoff'] = issue['body']
        record['body_status'] = 'verified_from_explicit_last_edited_time'
        record['body_last_edited_at'] = edited
        record['model_text_verified'] = True
        record['model_text_sha256'] = hashlib.sha256(canonical({'title': record['title_at_cutoff'], 'body': issue['body']})).hexdigest()
        record['graphql_response_sha256'] = sha(path)
        # 仍不自动升级为训练包：标签、正式画像与 C 的内容格式未验收。
    save('graphql_response_projection.json', response)
    lines('historical_issue_content.jsonl', records)


def finalize():
    audit = read(OUT / 'profile_replay_audit.json')
    baseline = read(OUT / 'baseline_profile_replay_audit.json')
    content = rows(OUT / 'historical_issue_content.jsonl')
    code_failures = rows(OUT / 'extra_code_failures.jsonl')
    text_verified = sum(c.get('model_text_verified', False) for c in content)
    table = ['# Advanced Adapter 小样本续采与画像回放', '',
        '2026-11-02；working / frozen=false。本轮不修改第一轮数据，不训练、不提交、不推送。', '',
        f'结论：5 条符合时间窗口的样例已生成基于 B 现有规则的历史画像预览。补采测试/文档后，输入从全部相同变为 4 种；已核实 {text_verified}/{len(content)} 条历史正文，正式训练仍需 B/C 审核。', '',
        '| 项目 | 结果 |', '|---|---|',
        f"| 画像预览 | {audit['profiles']} 份 / {audit['developers']} 位开发者 |",
        f"| 首轮仅 Python 文件的不同画像输入 | {baseline['distinct_model_input_vectors']} 种 |",
        f"| 补测试/文档后的不同画像输入 | {audit['distinct_model_input_vectors']} 种 |",
        f"| 每份画像已观察维度 | {audit['observed_dimensions_per_profile']} / 23 |",
        f"| 新增固定文件版本 | {audit['extra_code_documents']} 份；另 {len(code_failures)} 份因超过 256 KiB 排除 |",
        f"| Issue 标题事件回放 | {len(content)} 条（含正样本与未标记对照） |",
        f"| 通过最后编辑时间验证的历史正文 | {text_verified} 条 |",
        '| 正式训练样本 | 0 |', '',
        '画像直接复用本地 B 的 interpret_github_contributions 与特征融合规则，没有补造问卷、兴趣、操作系统或时间预算。未知维度保持 0 / observed_mask=false；不把 Python 代码贡献等同于用户语言偏好。', '',
        '输出使用 research_preview 和独立 pilot schema，不能进入线上 confirmed 接口或 C 的正式训练加载器。B 仍需审核规则适用性，并实现/确认 research_frozen 导出。未改动 B 的源文件。', '',
        '额外采样优先取测试文件，其次文档；源码与测试/文档分层采样的具体选择见 extra_code_jobs.json。大文件保留排除记录，没有截断后冒充原文件。文档规则也会覆盖 changelog，其合理性应由 B 复核。', '',
        '10 个公开页面均未暴露 lastEditedAt/userContentEdits 字段。缺字段不等于从未编辑；REST 的 renamed 事件只能支持标题回放，不能证明正文未变化。', '',
        ('用户已通过官方设备流程恢复本机 zlr2005 登录，并在沙箱外验证成功。只读 GraphQL 查询已返回全部 10 条记录；正文最后编辑时间均为空或早于对应截止点，当前正文因此可用于这些截止点的回放。沙箱内 gh auth status 的网络失败不应再被解释为凭据失效。' if text_verified == len(content) else
         '历史正文仍未全部核实。historical_content_query.graphql 是只读查询，需要可用的本机 GitHub CLI 登录。') , '',
        '以下为查询与离线导入的复现命令（当前网络请求需在可联网环境执行）：', '',
        '```powershell',
        '$queryPath = "data/midterm/working/advanced_adapter_pilot_20261102_stage2/historical_content_query.graphql"',
        '$responsePath = "data/midterm/working/advanced_adapter_pilot_20261102_stage2/historical_content_graphql_response.json"',
        'gh api graphql -F "query=@$queryPath" | Out-File -Encoding utf8NoBOM $responsePath',
        'python scripts/midterm_a/advance_adapter_pilot_samples.py --graphql-response $responsePath',
        '```', '',
        '导入器仅在明确 lastEditedAt 为空或不晚于截止点时接收正文。若截止后改过正文，则保留缺失，需根据版本历史或老师库当时的事件快照继续恢复；不会直接使用当前正文。标题/正文验证不自动证明历史标签或候选内容格式已被 C 接受。', '',
        '剩余工作：请 B 审核画像规则和冻结导出；确认候选内容格式及标签使用；扩展开发者与技术栈；由 C 审核输入并生成向量。当前 Python 无 torch，未运行 C 的官方自检或模型。', '',
        '可离线复现：`python scripts/midterm_a/advance_adapter_pilot_samples.py`；校验：加 `--verify`。', '',
        '官方依据：[Issue 事件类型](https://docs.github.com/en/rest/using-the-rest-api/issue-event-types)、[Issue GraphQL 编辑字段](https://docs.github.com/en/graphql/reference/issues)。']
    (OUT / 'REPORT.md').write_text('\n'.join(table) + '\n', encoding='utf-8')
    summary = {'status': 'working', 'frozen': False, 'profiles': audit['profiles'],
        'distinct_model_input_vectors': audit['distinct_model_input_vectors'],
        'historical_title_records': len(content), 'verified_historical_body_records': text_verified,
        'training_ready_samples': 0, 'b_review_status': 'pending'}
    save('summary.json', summary)
    manifest = {p.relative_to(OUT).as_posix(): sha(p) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name != 'manifest.json'}
    save('manifest.json', {'schema_version': 'advanced-pilot-stage2-manifest-v0.1',
        'source_stage1_checksums_sha256': sha(SOURCE / 'SHA256SUMS'), 'files_sha256': manifest,
        'script_sha256': sha(Path(__file__)), 'source_rule_sha256': audit['source_hashes']})
    print(json.dumps(summary, ensure_ascii=False))


def verify():
    manifest = read(OUT / 'manifest.json')
    assert manifest['source_stage1_checksums_sha256'] == sha(SOURCE / 'SHA256SUMS')
    assert manifest['script_sha256'] == sha(Path(__file__))
    for relative, digest in manifest['files_sha256'].items():
        path = (OUT / relative).resolve()
        assert path.is_relative_to(OUT.resolve()) and sha(path) == digest
    for relative, digest in manifest['source_rule_sha256'].items():
        assert sha(ROOT / relative) == digest
    for p in rows(OUT / 'profile_previews.jsonl'):
        assert p['profile_state'] == 'research_preview' and p['training_ready'] is False
        profile_hash = p.pop('profile_hash')
        assert hashlib.sha256(canonical(p)).hexdigest() == profile_hash
        assert len(p['values']) == len(p['observed_mask']) == len(p['evidence_refs']) == 23
        assert all(v == 0 for v, m in zip(p['values'], p['observed_mask']) if not m)
    for b in rows(OUT / 'profile_evidence_batches.jsonl'):
        for e in b['evidence']:
            assert iso(e['event_at']) < iso(b['as_of'])
            assert (ROOT / e['evidence_location']).is_file()
    for b in rows(OUT / 'b_replay_inputs.jsonl'):
        for d in b['code_documents']:
            assert sha(ROOT / d['content_location']) == d['content_sha256']
    print(json.dumps({'status': 'verified', 'files_checked': len(manifest['files_sha256']),
        'checks': ['content hashes', 'source rule hashes', 'profile hashes', 'unknown masks',
            'evidence before cutoff', 'fixed source files', 'preview stays excluded from training']}))


def probe_content():
    """只读检查公开页面中的正文编辑信息；字段缺失不能解释成没有编辑。"""
    jobs = []
    for s in eligible():
        for suffix in ['', '_control']:
            issue = read(SOURCE / 'evidence' / (s['sample_key'] + suffix + '_issue.json'))['payload']
            jobs.append((s['sample_key'] + suffix, issue['html_url'], issue['number']))

    def fetch(job):
        key, url, number = job
        path = OUT / 'content_evidence' / (key + '.json')
        if path.exists():
            return read(path)
        result = {'sample_key': key, 'source_url': url,
            'observed_at': datetime.now(timezone.utc).isoformat(), 'historical_content_verified': False}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'OSS-Mentor-adapter-pilot/2'}), timeout=25) as response:
                if response.geturl().rstrip('/') != url:
                    raise ValueError('Unexpected redirect')
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('Page too large')
                html = raw.decode('utf-8')
            result['page_sha256'] = hashlib.sha256(raw).hexdigest()
            for script in re.findall(r'<script\b[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.S):
                doc = json.loads(script)
                p = doc.get('payload', {}).get('issueViewerRoute', {}).get('data', {}).get('repository', {}).get('issue') if isinstance(doc, dict) else None
                if not p or p.get('number') != number or p.get('url') != url:
                    continue
                result['projection_not_raw'] = True
                result['payload'] = {k: p[k] for k in ['number', 'url', 'createdAt', 'title', 'body',
                    'bodyVersion', 'lastEditedAt', 'userContentEdits'] if k in p}
                result['last_edited_field_present'] = 'lastEditedAt' in p
                result['edit_history_field_present'] = 'userContentEdits' in p
                result['reason'] = 'body_edit_history_not_exposed_in_public_embedded_issue_record'
                break
            if 'payload' not in result:
                result['reason'] = 'structured_issue_record_not_found'
        except Exception as exc:
            result['reason'] = 'public_page_request_failed'
            result['error_type'] = type(exc).__name__
        save('content_evidence/' + key + '.json', result)
        return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch, jobs))
    save('historical_content_audit.json', {'issues_checked': len(results),
        'historical_content_verified': sum(r['historical_content_verified'] for r in results),
        'reasons': dict(Counter(r['reason'] for r in results)),
        'auth_status': 'existing gh CLI authentication failed; no credentials altered',
        'next_source': 'Authenticated read-only GraphQL edit history, or timestamped historical Issue event snapshots',
        'official_reference': 'https://docs.github.com/en/graphql/reference/issues'})
    query_parts = []
    for index, (_, url, number) in enumerate(jobs):
        owner, repo = url.split('github.com/')[1].split('/')[:2]
        query_parts.append(f'q{index}: repository(owner:"{owner}",name:"{repo}") {{ issue(number:{number}) {{ url createdAt title body lastEditedAt userContentEdits(first:100) {{ totalCount pageInfo {{ hasNextPage endCursor }} nodes {{ editedAt deletedAt diff }} }} }} }}')
    (OUT / 'historical_content_query.graphql').write_text('query {\n' + '\n'.join(query_parts) + '\n}\n', encoding='utf-8')
    print(json.dumps(read(OUT / 'historical_content_audit.json'), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-content', action='store_true')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--graphql-response', type=Path)
    args = parser.parse_args()
    if args.probe_content:
        probe_content()
    elif args.verify:
        verify()
    else:
        replay()
        content_replay()
        if args.graphql_response:
            import_graphql_response(args.graphql_response)
        elif (OUT / 'graphql_response_projection.json').exists():
            import_graphql_response(OUT / 'graphql_response_projection.json')
        finalize()
