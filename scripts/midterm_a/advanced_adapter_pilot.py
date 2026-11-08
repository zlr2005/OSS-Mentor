"""Offline planning and inspection of a bounded historical adapter pilot.

Network reads are performed separately through the GitHub connector. Existing
datasets are read only; outputs stay in an isolated, unfrozen working directory.
"""
import json
from pathlib import Path
import argparse
import base64
import hashlib
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/midterm/working/advanced_adapter_pilot_20261102'
SOURCE = ROOT / 'datasets/b_real_contributions_v0.2'
REPOS = ['pytest-dev/pytest', 'pydantic/pydantic', 'python-attrs/attrs',
         'pallets/flask', 'Textualize/textual']


def rows(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]


def save(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def plan():
    repos = {r['full_name'].casefold() for r in rows(SOURCE / 'tables/repositories.jsonl')}
    assert all(r.casefold() in repos for r in REPOS)
    identities = {}
    for d in rows(SOURCE / 'tables/developers.jsonl'):
        if d['identity_status'] != 'verified':
            continue
        for loc in d['evidence_locations']:
            e = json.loads((SOURCE / loc).read_text(encoding='utf-8'))
            a = e.get('fields', {}).get('author') or {}
            if str(a.get('id')) == str(d['github_numeric_id']) and a.get('type') == 'User':
                identities[str(a['id'])] = {'developer_key': d['developer_key'],
                    'github_numeric_id': a['id'], 'login': a['login'],
                    'identity_evidence': str((SOURCE / loc).relative_to(ROOT))}
                break
    save('seed_identities.json', identities)
    save('collection_plan.json', {'status': 'working', 'frozen': False,
        'repositories': REPOS, 'years': [2024, 2025, 2026],
        'as_of_template': 'YYYY-04-01T00:00:00Z', 'observation_days': 90,
        'search_period': 'April 1 to June 29 (90 days)',
        'search_limit_per_repository_year': 10, 'selected_pr_limit_per_repository_year': 1,
        'selection': 'merged PRs created after cutoff, linked:issue; ascending creation; existing verified numeric IDs only',
        'history_limit_per_selected_author': 3, 'code_files_per_history_commit': 1,
        'candidate_controls_per_repository_year': 1,
        'scope_note': 'Feasibility sample, not random coverage or training-ready data; no teacher database access in this run.'})
    print(json.dumps({'seed_verified_human_identities': len(identities), 'output': str(OUT)}))


def collect_pages():
    jobs = []
    selected = json.loads((OUT / 'selected_prs.json').read_text(encoding='utf-8'))
    controls = {s['id']: s for s in json.loads((OUT / 'control_selections.json').read_text())}
    for s in selected:
        issue = evidence(s['id'] + '_issue')['payload']
        jobs.append((s['id'] + '_page', issue['html_url'], issue['number']))
        c = controls[s['id']]
        jobs.append((s['id'] + '_control_page', f"https://github.com/{c['repository']}/issues/{c['number']}", c['number']))

    def fetch(job):
        key, url, number = job
        target = OUT / 'evidence' / (key + '.json')
        if target.exists():
            return {'id': key, 'status': 'cached'}
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'OSS-Mentor-adapter-pilot/1'})
            with urllib.request.urlopen(req, timeout=25) as response:
                if response.geturl().rstrip('/') != url:
                    raise ValueError('Unexpected redirect')
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('Page too large')
                html = raw.decode('utf-8')
            record = None
            for script in re.findall(r'<script\b[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.S):
                doc = json.loads(script)
                p = doc.get('payload', {}).get('issueViewerRoute', {}).get('data', {}).get('repository', {}).get('issue') if isinstance(doc, dict) else None
                if p and p.get('url') == url and p.get('number') == number:
                    record = {k: p.get(k) for k in ['number', 'url', 'state', 'createdAt',
                        'bodyVersion', 'closedByPullRequestsReferences', 'linkedPullRequests']}
                    break
            if record is None:
                raise ValueError('Issue structured record unavailable')
            save('evidence/' + key + '.json', {'source_url': url,
                'observed_at': datetime.now(timezone.utc).isoformat(),
                'source_system': 'github_issue_html', 'projection_not_raw': True,
                'page_sha256': hashlib.sha256(raw).hexdigest(), 'payload': record})
            return {'id': key, 'status': 'saved'}
        except Exception as exc:
            return {'id': key, 'status': 'failed', 'error_type': type(exc).__name__}
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(fetch, jobs))
    save('page_collection_results.json', result)
    print(json.dumps(result))


def evidence(key):
    return json.loads((OUT / 'evidence' / (key + '.json')).read_text(encoding='utf-8'))


def timestamp(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def open_at(issue, events, cutoff):
    if 'pull_request' in issue or timestamp(issue['created_at']) > cutoff or len(events) >= 100:
        return False
    state = 'open'
    for event in sorted(events, key=lambda e: e['created_at']):
        if timestamp(event['created_at']) > cutoff:
            break
        if event['event'] == 'closed':
            state = 'closed'
        elif event['event'] == 'reopened':
            state = 'open'
    return state == 'open'


def write_lines(name, records):
    (OUT / name).write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in records), encoding='utf-8')


def report():
    selected = json.loads((OUT / 'selected_prs.json').read_text(encoding='utf-8'))
    mappings = json.loads((OUT / 'code_mappings.json').read_text())
    coverage = json.loads((OUT / 'search_coverage.json').read_text())
    histories, codes, outcomes, candidates, chains, failures = [], [], [], [], [], []
    for m in mappings:
        b = evidence(m['blob_id'])['payload']
        content = base64.b64decode(b['content']) if b.get('encoding') == 'base64' else b['content'].encode('utf-8')
        actual = hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()
        if actual != m['blob_sha'] or len(content) > 262144:
            failures.append({'record': m['blob_id'], 'reason': 'blob_hash_mismatch_or_size_limit'})
            continue
        location = 'code/' + m['blob_sha'] + '.py'
        (OUT / 'code').mkdir(exist_ok=True)
        (OUT / location).write_bytes(content)
        codes.append({**m, 'language': 'Python', 'content_sha256': hashlib.sha256(content).hexdigest(),
                      'content_location': location, 'git_blob_sha_verified': True})
    for s in selected:
        key = s['id']
        cutoff = timestamp(f"{s['year']}-04-01T00:00:00Z")
        end = cutoff + timedelta(days=90)
        pr = evidence(key + '_pr')['payload']
        issue = evidence(key + '_issue')['payload']
        page = evidence(key + '_page')['payload']
        events = evidence(key + '_events')['payload']
        assert pr['user']['id'] == s['author']['github_numeric_id']
        assert pr['base']['repo']['full_name'].casefold() == s['repository'].casefold()
        assert pr['number'] == s['number'] and 'pull_request' not in issue
        refs = page.get('closedByPullRequestsReferences', {}).get('nodes', [])
        explicit = pr['merged'] and any(n['url'] == pr['html_url'] and n['state'] == 'MERGED' for n in refs)
        outcome_at = max(timestamp(pr['merged_at']), timestamp(issue['closed_at']))
        temporal = cutoff < timestamp(pr['created_at']) <= outcome_at <= end
        available = open_at(issue, events, cutoff)
        valid_history = []
        for i, _ in enumerate(evidence(key + '_history')['payload']):
            ck = key + '_commit_' + str(i)
            commit = evidence(ck)['payload']
            author = commit.get('author') or {}
            author_at = timestamp(commit['commit']['author']['date'])
            event_at = timestamp(commit['commit']['committer']['date'])
            if author.get('id') != s['author']['github_numeric_id'] or not (author_at < cutoff and event_at < cutoff):
                failures.append({'record': ck, 'reason': 'identity_or_history_time_mismatch'})
                continue
            record = {'schema_version': 'advanced-pilot-contribution-v0.1',
                'developer_key': s['author']['developer_key'], 'github_numeric_id': author['id'],
                'github_login': author['login'], 'repository': s['repository'],
                'as_of': cutoff.isoformat(), 'commit_sha': commit['sha'],
                'author_at': author_at.isoformat(), 'event_at': event_at.isoformat(),
                'changed_files': [f['filename'] for f in commit.get('files', [])],
                'file_list_may_be_truncated': len(commit.get('files', [])) >= 100,
                'code_documents': [c for c in codes if c['commit_id'] == ck],
                'source_url': commit['html_url'], 'evidence_ref': 'evidence/' + ck + '.json',
                'identity_evidence': s['author']['identity_evidence'],
                'historical_visibility_note': 'Commit timestamps checked; independent first-public-availability evidence not established.'}
            histories.append(record)
            valid_history.append(record)
        outcomes.append({'schema_version': 'advanced-pilot-outcome-v0.1',
            'outcome_id': f"{s['repository']}#{pr['number']}->{issue['number']}",
            'developer_key': s['author']['developer_key'], 'github_login': pr['user']['login'],
            'issue_key': f"{s['repository'].lower()}#{issue['number']}", 'pr_number': pr['number'],
            'interaction_at': pr['created_at'], 'outcome_at': outcome_at.isoformat(),
            'verification_status': 'verified' if explicit else 'unverified',
            'proof_kind': 'explicit_github_closed_by_pr' if explicit else None,
            'source_url': pr['html_url'], 'evidence_refs': ['evidence/' + key + suffix + '.json' for suffix in ['_pr', '_issue', '_events', '_page']],
            'quarter_time_eligible': temporal and available})
        control_valid = False
        for suffix, role in [('', 'positive'), ('_control', 'unlabeled_control')]:
            item = evidence(key + suffix + '_issue')['payload']
            ev = evidence(key + suffix + '_events')['payload']
            valid = open_at(item, ev, cutoff)
            if role == 'unlabeled_control':
                control_valid = valid
            candidates.append({'schema_version': 'advanced-pilot-candidate-v0.1',
                'sample_key': key, 'as_of': cutoff.isoformat(),
                'issue_key': f"{s['repository'].lower()}#{item['number']}", 'role': role,
                'created_at': item['created_at'], 'closed_at_current': item['closed_at'],
                'open_at_cutoff': valid, 'historical_content_verified': False,
                'current_content_observed_at': evidence(key + suffix + '_issue')['observed_at'],
                'source_url': item['html_url'], 'evidence_refs': ['evidence/' + key + suffix + x + '.json' for x in ['_issue', '_events', '_page']],
                'exclusion_reason': 'historical_title_body_and_labels_not_reconstructed'})
        eligible = bool(explicit and temporal and available and valid_history and control_valid)
        chains.append({'sample_key': key, 'repository': s['repository'], 'year': s['year'],
            'github_login': pr['user']['login'], 'pr_url': pr['html_url'], 'issue_url': issue['html_url'],
            'explicit_closing_relation': bool(explicit), 'outcome_within_90_days_after_pr_started': temporal,
            'positive_open_at_cutoff': available, 'history_commits': len(valid_history),
            'verified_code_documents': sum(len(h['code_documents']) for h in valid_history),
            'control_open_at_cutoff': control_valid,
            'structural_chain_eligible': eligible, 'historical_content_verified': False,
            'profile_generated_by_B': False, 'training_ready': False})
    write_lines('historical_contributions.jsonl', histories)
    write_lines('code_documents.jsonl', codes)
    write_lines('issue_outcomes.jsonl', outcomes)
    write_lines('historical_candidates.jsonl', candidates)
    write_lines('sample_audit.jsonl', chains)
    write_lines('failures.jsonl', failures)
    summary = {'status': 'working', 'frozen': False, 'training_ready': False,
        'repositories_probed': len(REPOS), 'repository_year_searches': len(coverage),
        'search_prs_returned': sum(c['returned'] for c in coverage),
        'returned_prs_with_existing_verified_authors': sum(c['existing_verified_authors'] for c in coverage),
        'selected_prs': len(selected), 'distinct_selected_developers': len({s['author']['github_numeric_id'] for s in selected}),
        'verified_closing_prs': sum(c['explicit_closing_relation'] for c in chains),
        'historical_commits': len(histories), 'verified_code_documents': len(codes),
        'candidate_rows': len(candidates),
        'structural_chains': sum(c['structural_chain_eligible'] for c in chains),
        'training_ready_samples': 0, 'failures': len(failures),
        'by_year': {str(y): {'selected_prs': sum(c['year'] == y for c in chains),
            'structural_chains': sum(c['year'] == y and c['structural_chain_eligible'] for c in chains),
            'historical_commits': sum(timestamp(h['as_of']).year == y for h in histories)} for y in [2024, 2025, 2026]},
        'limitations': ['Outcome-enriched bounded sample, not random; zero search hits do not prove absence.',
            'Only three distinct authors selected; cannot infer training sufficiency or effectiveness.',
            'Historical Issue title/body/labels unverified; current observations excluded from historical training.',
            'B historical 23-dimensional profiles, C embeddings and audits not produced.',
            'Quarter snapshot may predate Issue creation; those pairs are excluded.',
            'Only 3 commits per author/repository/quarter and 1 Python file per selected code commit.',
            'Teacher database year coverage not queried or established.']}
    save('summary.json', summary)
    report_lines = ['# Advanced Adapter 历史数据试采（2026-11-02）', '',
        '状态：working / frozen=false。仅本地试采，未提交、未推送、未接入平台。', '',
        '结论：GitHub 能回溯补到 2024、2025、2026 年数据；已验证结果关联与部分历史贡献链条。当前仍不是可训练数据包。', '',
        f"5 个仓库 × 3 年，共返回 {summary['search_prs_returned']} 条候选 PR；其中 {summary['returned_prs_with_existing_verified_authors']} 条作者属于已有核验身份。按每仓库每年最多 1 条，选中 {len(selected)} 条 PR、{summary['distinct_selected_developers']} 位开发者。", '',
        f"结构化关闭关系确认 {summary['verified_closing_prs']} 条；历史 commit {len(histories)} 条；固定 Python 代码 {len(codes)} 份，通过 Git blob SHA-1 和内容 SHA-256 核验。", '',
        '| 年份 | 选中 PR | 历史 commit | 结构链条满足 |', '|---|---:|---:|---:|']
    for year, v in summary['by_year'].items():
        report_lines.append(f"| {year} | {v['selected_prs']} | {v['historical_commits']} | {v['structural_chains']} |")
    report_lines += ['', '“结构链条满足”要求：已有核验身份、明确关闭关系、截止前贡献、截止时开放的正样本和对照、截止后创建 PR 且 90 天内完成。它不表示训练样本已就绪。', '',
        '| PR | 截止时 Issue 开放 | 历史 commit | 结构链条满足 |', '|---|---|---:|---|']
    for c in chains:
        report_lines.append(f"| [{c['repository']} PR]({c['pr_url']}) | {c['positive_open_at_cutoff']} | {c['history_commits']} | {c['structural_chain_eligible']} |")
    report_lines += ['', '当前训练就绪样本为 0：历史 Issue 正文/标题/标签版本仍未恢复，B 也尚未生成历史 23 维画像；不得把当前内容回填到历史截止时间。', '',
        '建议下一步：先为满足结构条件的记录恢复可验证历史 Issue 内容，并请 B 验证历史画像回放；再增加开发者多样性和其他季度。对截止后才创建的 Issue，后续可研究更密的画像快照，必须重新检查截止时间早于 PR 创建，不能直接挪动现有样本。', '',
        '老师数据库本次未连接；此结果不能说明其 2024/2025 年覆盖。搜索为 linked:issue 的有偏样本，零结果不代表仓库没有历史关联。', '',
        '原数据包只读。试采 schema 使用 advanced-pilot-*，防止被当成正式训练输入。API/网页证据保存为明确标注的投影，移除邮箱字段；不伪装完整原始响应。源代码保留在本地工作目录，公开分发前须另行检查。', '',
        '离线重算：`python scripts/midterm_a/advanced_adapter_pilot.py --report`；文件校验清单：`SHA256SUMS`。']
    (OUT / 'REPORT.md').write_text('\n'.join(report_lines) + '\n', encoding='utf-8')
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.relative_to(OUT).as_posix()
              for p in sorted(OUT.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS']
    (OUT / 'SHA256SUMS').write_text('\n'.join(hashes) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


def verify():
    checked = 0
    for line in (OUT / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        digest, relative = line.split('  ', 1)
        path = (OUT / relative).resolve()
        assert path.is_relative_to(OUT.resolve()) and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, relative
        checked += 1
    for row in rows(OUT / 'code_documents.jsonl'):
        content = (OUT / row['content_location']).read_bytes()
        assert hashlib.sha256(content).hexdigest() == row['content_sha256']
        assert hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest() == row['blob_sha']
    commits = rows(OUT / 'historical_contributions.jsonl')
    for row in commits:
        assert timestamp(row['author_at']) < timestamp(row['as_of'])
        assert timestamp(row['event_at']) < timestamp(row['as_of'])
        assert (OUT / row['evidence_ref']).is_file()
        assert (ROOT / row['identity_evidence']).is_file()
    outcomes = rows(OUT / 'issue_outcomes.jsonl')
    assert len({r['outcome_id'] for r in outcomes}) == len(outcomes)
    for row in outcomes:
        for ref in row['evidence_refs']:
            assert (OUT / ref).is_file()
        assert timestamp(row['interaction_at']) <= timestamp(row['outcome_at'])
    audit = rows(OUT / 'sample_audit.jsonl')
    summary = json.loads((OUT / 'summary.json').read_text())
    assert len(commits) == summary['historical_commits']
    assert sum(r['structural_chain_eligible'] for r in audit) == summary['structural_chains']
    assert not any(r['training_ready'] for r in audit)
    for c in rows(OUT / 'historical_candidates.jsonl'):
        assert c['historical_content_verified'] is False
        for ref in c['evidence_refs']:
            assert (OUT / ref).is_file()
    print(json.dumps({'status': 'verified', 'hashed_files': checked,
        'checks': ['all file hashes', 'Git blob identity and content hashes',
                   'history before cutoff', 'unique outcomes', 'source references',
                   'summary reconciliation', 'unverified historical content excluded']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pages', action='store_true', help='Read public Issue HTML for closing-PR evidence')
    parser.add_argument('--report', action='store_true', help='Rebuild normalized pilot tables and report offline')
    parser.add_argument('--verify', action='store_true', help='Verify pilot checksums and temporal/provenance invariants offline')
    args = parser.parse_args()
    verify() if args.verify else report() if args.report else collect_pages() if args.pages else plan()
