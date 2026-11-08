"""Evidence-backed current candidates seeded by IssuePilot's repository list.

Only repository names are reused from the paper. Every candidate comes from a
new GitHub REST read. This adapter uses the existing candidate rules and store.
"""

from __future__ import annotations

import hashlib
import html
import json
import sqlite3
import zipfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oss_mentor.candidate_rules import EligibilityResult, evaluate_candidate, has_newcomer_label
from oss_mentor.candidate_sync import CandidateSynchronizer, repository_maintenance_status
from oss_mentor.collector.source_comparison import normalize_github_issue
from oss_mentor.storage.candidates import SQLiteCandidateStorage


POLICY = 'live-issuepilot-v2'
EXTRA_BLOCKING = {'in progress', 'in-progress', 'status: in progress', 'claimed', 'duplicate', 'invalid', 'wontfix', "won't fix",
                  'question', 'questions', 'discussion', 'support', 'type: question', 'type: support', 'support request'}
DISCOVERY_QUERY = '?state=open&sort=updated&direction=desc&per_page=30'


def seed_repositories(archive: Path) -> list[str]:
    if hashlib.md5(archive.read_bytes()).hexdigest() != '6e1bbbe42b2ae66582f00c111902c6a1':
        raise ValueError('IssuePilot archive checksum mismatch')
    with zipfile.ZipFile(archive) as bundle:
        names = [name for name in bundle.namelist() if '/repo_analysis_output/' in name and name.endswith('/issues.json')]
    # Do not extract or execute the paper's programs or copy its cached issues.
    return sorted({'/'.join(name.split('/')[-3:-1]) for name in names}, key=str.casefold)


def load_evidence(path: Path, url: str) -> dict | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('url') != url or value.get('tool_success') is not True or value.get('retrieval_method') not in {'github_connector', 'github_rest'}:
        raise ValueError(f'Invalid source envelope: {path.name}')
    stamp = datetime.fromisoformat(value['observed_at'].replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Source observation must include timezone')
    return value


def fresh(value: dict | None, now: datetime) -> bool:
    if not value:
        return False
    stamp = datetime.fromisoformat(value['observed_at'].replace('Z', '+00:00'))
    return timedelta(minutes=-5) <= now - stamp <= timedelta(hours=24)


def repository_context(source: Path, seed: str, now: datetime) -> tuple[dict | None, str | None]:
    envelope = load_evidence(source / seed.replace('/', '__') / 'repository.json', f'https://api.github.com/repos/{seed}')
    if not fresh(envelope, now):
        return None, 'repository_unverified_or_stale'
    repo = envelope['payload']
    if not isinstance(repo, dict) or type(repo.get('id')) is not int or not repo.get('full_name'):
        return None, 'repository_lookup_unresolved'
    if type(repo.get('archived')) is not bool or type(repo.get('disabled')) is not bool:
        return None, 'repository_status_unknown'
    maintenance, reason = repository_maintenance_status(repo.get('pushed_at'), checked_at=now)
    if repo['archived'] or repo['disabled'] or maintenance != 'active' or repo.get('has_issues') is not True:
        return None, 'repository_archived_disabled_inactive_or_no_issues'
    return {
        'full_name': repo['full_name'], 'github_repository_id': repo['id'], 'html_url': repo['html_url'],
        'ecosystems_last_synced_at': None, 'primary_language': repo.get('language'),
        'github_verified_at': envelope['observed_at'], 'is_archived': repo['archived'], 'is_disabled': repo['disabled'],
        'pushed_at': repo.get('pushed_at'), 'maintenance_status': maintenance, 'maintenance_reason': reason,
        'activity_checked_at': envelope['observed_at'], 'mark_synced': True,
    }, None


def normalize_live(issue: dict, envelope: dict, repository: str) -> dict:
    expected = f'https://api.github.com/repos/{repository}'
    if (issue.get('repository_url', '').casefold() != expected.casefold() or type(issue.get('id')) is not int
            or type(issue.get('number')) is not int or not isinstance(issue.get('assignees'), list)
            or type(issue.get('locked')) is not bool):
        raise ValueError('Missing or transferred issue identity/status')
    record = normalize_github_issue(issue)
    record.update(source_system='github_rest', source_fetched_at=envelope['observed_at'],
                  github_verified_at=envelope['observed_at'], is_pull_request='pull_request' in issue,
                  has_linked_open_pr=None)
    return record


def strict_eligibility(record: dict, *, timeline_complete: bool) -> EligibilityResult:
    base = evaluate_candidate(record)
    if base.eligibility != 'eligible':
        return base
    reasons = []
    if not str(record.get('body_text') or '').strip():
        reasons.append('batch_policy:empty_body')
    reasons.extend('batch_policy:blocking_label:' + label for label in sorted(
        {x.casefold().strip() for x in record.get('labels', [])}.intersection(EXTRA_BLOCKING)))
    if reasons:
        return EligibilityResult('temporarily_ineligible', tuple(reasons), base.warnings, base.newcomer_label_signal, POLICY)
    if not timeline_complete:
        return EligibilityResult('unknown', ('batch_policy:linked_pr_check_incomplete',), base.warnings, base.newcomer_label_signal, POLICY)
    return EligibilityResult('eligible', (), (), base.newcomer_label_signal, POLICY)


def discovery(source: Path, seed: str, repository: str, now: datetime) -> tuple[list, list]:
    records = {}
    failures = []
    for kind in ('recent', 'newcomer'):
        url = f'https://api.github.com/repos/{repository}/issues{DISCOVERY_QUERY}'
        if kind == 'newcomer':
            url += '&labels=good%20first%20issue'
        envelope = load_evidence(source / seed.replace('/', '__') / f'{kind}.json', url)
        if not fresh(envelope, now) or not isinstance(envelope['payload'], list):
            failures.append(kind)
            continue
        for issue in envelope['payload']:
            if 'pull_request' in issue:
                continue
            normalize_live(issue, envelope, repository)  # Reject mismatched objects before planning reads.
            previous = records.get(issue['number'])
            if previous is None or previous[1]['observed_at'] < envelope['observed_at']:
                records[issue['number']] = (issue, envelope)
    return list(records.values()), failures


def plan_verification(source: Path, seeds: list[str], limit: int = 10, *, now=None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    jobs = []
    for seed in seeds:
        repo, reason = repository_context(source, seed, now)
        if reason:
            continue
        rows, _ = discovery(source, seed, repo['full_name'], now)
        choices = []
        for issue, envelope in rows:
            record = normalize_live(issue, envelope, repo['full_name'])
            # Timeline completeness is bypassed only for selecting reads, never for admission.
            if strict_eligibility(record, timeline_complete=True).eligibility == 'eligible':
                choices.append(issue)
        choices.sort(key=lambda row: (has_newcomer_label([x['name'] for x in row.get('labels', [])]), row.get('updated_at', '')), reverse=True)
        for issue in choices[:limit]:
            jobs.append({'seed': seed, 'repository': repo['full_name'], 'number': issue['number']})
    return jobs


def timeline_evidence(source: Path, seed: str, repo: str, number: int, now: datetime):
    events = []
    paths = []
    for page in range(1, 11):
        path = source / seed.replace('/', '__') / f'timeline_{number}_{page}.json'
        url = f'https://api.github.com/repos/{repo}/issues/{number}/timeline?per_page=100&page={page}'
        envelope = load_evidence(path, url)
        if not fresh(envelope, now) or not isinstance(envelope['payload'], list):
            return None, paths
        paths.append(path)
        events.extend(envelope['payload'])
        if len(envelope['payload']) < 100:
            return CandidateSynchronizer._has_linked_open_pr(events), paths
    return None, paths  # Truncation cannot establish absence of an open PR.


def build_pool(source: Path, seeds: list[str], database: Path, output: Path, migration: Path, review: dict | None = None):
    if database.exists() or output.exists():
        raise ValueError('Choose new outputs; previous working data is preserved')
    now = datetime.now(timezone.utc)
    review = review or {}
    store = SQLiteCandidateStorage(database, migration)
    store.initialize()
    repository_results = []
    audits = []
    candidates = []
    for seed in seeds:
        if seed.casefold() in review.get('excluded_repositories', {}):
            repository_results.append({'seed': seed, 'status': 'excluded_by_review',
                                       **review['excluded_repositories'][seed.casefold()]})
            continue
        repo, reason = repository_context(source, seed, now)
        if reason:
            repository_results.append({'seed': seed, 'status': 'excluded_or_unverified', 'reason': reason})
            continue
        rows, failures = discovery(source, seed, repo['full_name'], now)
        normalized = []
        for discovered, observed in rows:
            issue = discovered
            detail_path = source / seed.replace('/', '__') / f"issue_{issue['number']}.json"
            detail_url = f"https://api.github.com/repos/{repo['full_name']}/issues/{issue['number']}"
            detail = load_evidence(detail_path, detail_url)
            detail_ok = (fresh(detail, now) and isinstance(detail['payload'], dict)
                         and detail['payload'].get('id') == issue['id'] and detail['payload'].get('number') == issue['number'])
            if detail_ok:
                issue, observed = detail['payload'], detail
            record = normalize_live(issue, observed, repo['full_name'])
            linked, paths = timeline_evidence(source, seed, repo['full_name'], issue['number'], now) if detail_ok else (None, [])
            record['has_linked_open_pr'] = linked
            eligibility = strict_eligibility(record, timeline_complete=detail_ok and linked is not None)
            decision = review.get('excluded_issues', {}).get(f"{repo['full_name']}#{issue['number']}".casefold())
            if decision:
                eligibility = EligibilityResult('temporarily_ineligible', ('batch_review:' + decision,),
                                                 eligibility.warnings, eligibility.newcomer_label_signal, POLICY)
            normalized.append((record, eligibility))
            audit = {'repository': repo['full_name'], 'record': record, 'eligibility': asdict(eligibility),
                     'detail_verified': bool(detail_ok), 'timeline_pages': [p.relative_to(source).as_posix() for p in paths],
                     'retrieval_method': observed['retrieval_method']}
            audits.append(audit)
            if eligibility.eligibility == 'eligible':
                candidates.append({**record, 'repository': repo['full_name'], 'primary_language': repo['primary_language'],
                                   'verification_expires_at': (datetime.fromisoformat(record['github_verified_at'].replace('Z', '+00:00')) + timedelta(hours=24)).isoformat(),
                                   'verification_scope': 'REST issue detail + complete REST timeline cross-references',
                                   'newcomer_label_signal': eligibility.newcomer_label_signal})
        store.save_repository_candidates(repository=repo, candidates=normalized)
        repository_results.append({'seed': seed, 'repository': repo['full_name'], 'status': 'processed',
                                   'discovered_issues': len(rows), 'discovery_failures': failures,
                                   'available_candidates': sum(e.eligibility == 'eligible' for _, e in normalized)})
    connection = store.connect()
    try:
        with connection:
            # Existing availability rules permit an unknown PR flag; this batch explicitly fails closed.
            connection.execute("""UPDATE task_candidate SET candidate_availability='temporarily_unverified',
                availability_reasons_json='["batch_policy:linked_pr_check_incomplete"]'
                WHERE has_linked_open_pr IS NULL AND candidate_availability='available'""")
            integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
            fk = connection.execute('PRAGMA foreign_key_check').fetchall()
            available = connection.execute("SELECT count(*) FROM task_candidate WHERE candidate_availability='available' AND candidate_eligibility='eligible'").fetchone()[0]
            if integrity != 'ok' or fk or available != len(candidates):
                raise ValueError('Candidate DB integrity/count mismatch')
    finally:
        connection.close()
    output.mkdir(parents=True)
    candidates.sort(key=lambda row: (not row['newcomer_label_signal'], row['repository'].casefold(), row['issue_number']))
    (output / 'candidates.json').write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# 当前 GitHub Issue 候选清单', '', f"核查批次时间（UTC）：{now.isoformat()}。共 {len(candidates)} 条。",
             '', '这是当前状态筛选结果，尚未接入网页；参与前仍需刷新状态。带新手标签不等于难度已人工确认。', '']
    for title, signal in [('带新手或求助相关标签', True), ('其他开发与文档候选', False)]:
        lines.extend([f'## {title}', '', '| Issue | 项目主要语言 | 标题 |', '|---|---|---|'])
        for row in candidates:
            if row['newcomer_label_signal'] != signal:
                continue
            text = html.escape(row['title']).replace('|', '&#124;').replace('\n', ' ').replace('\r', ' ')
            lines.append(f"| [{row['repository']}#{row['issue_number']}]({row['html_url']}) | {row['primary_language'] or '未知'} | {text} |")
        lines.append('')
    (output / 'candidates.md').write_text('\n'.join(lines), encoding='utf-8')
    (output / 'audit.json').write_text(json.dumps(audits, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    source_files = [{'path': p.relative_to(source).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in sorted(source.rglob('*.json'))]
    report = {'policy': POLICY, 'generated_at': now.isoformat(), 'lifecycle': 'working', 'frozen': False,
              'paper_source_url': 'https://zenodo.org/records/18363387', 'seed_repositories': len(seeds),
              'batch_review': review,
              'repositories': repository_results, 'discovered_issues': len(audits), 'available_candidates': len(candidates),
              'candidate_repositories': len({r['repository'] for r in candidates}),
              'newcomer_label_candidates': sum(r['newcomer_label_signal'] for r in candidates),
              'eligibility_counts': dict(Counter(r['eligibility']['eligibility'] for r in audits)),
              'reason_counts': dict(Counter(reason for r in audits for reason in r['eligibility']['reasons'])),
              'language_counts': dict(Counter(r['primary_language'] for r in candidates)),
              'sqlite_integrity': integrity, 'foreign_key_violations': len(fk), 'source_files': source_files,
              'discovery_scope': 'At most 30 recent open API items and 30 good-first-issue items per active seed repository; PRs excluded',
              'limitations': ['Snapshot availability, not a reservation or guaranteed task acceptance',
                              'Complete REST timeline checks cross-referenced PRs; other work in progress may not be linked',
                              'Not every candidate is beginner-friendly or has verified task clarity',
                              'Refresh within 24h and recheck before user participation; not configured for web consumption']}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report
