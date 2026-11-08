"""Merge local IssuePilot candidates and teacher-discovered GitHub review evidence.

Only local files are read. Admission uses existing candidate normalization/rules/storage.
Newer negative observations replace earlier positive ones; provenance stays in sidecars.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import html
import json
from pathlib import Path

from oss_mentor.candidate_rules import EligibilityResult, has_newcomer_label
from oss_mentor.candidate_sync import repository_maintenance_status
from oss_mentor.collector.source_comparison import normalize_github_issue
from oss_mentor.datasets.live_issues import fresh, strict_eligibility
from oss_mentor.storage.candidates import SQLiteCandidateStorage

POLICY = 'merged-candidates-v0.4'


def stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Observation timestamp requires timezone')
    return parsed.astimezone(timezone.utc)


def issue_key(repository, number):
    return f'{repository.casefold()}#{int(number)}'


def repo_context(raw, observed, now):
    active, reason = repository_maintenance_status(raw.get('pushed_at'), checked_at=now)
    context = dict(full_name=raw['full_name'], github_repository_id=raw['id'],
                   html_url=raw['html_url'], ecosystems_last_synced_at=None,
                   primary_language=raw.get('language'), github_verified_at=observed,
                   is_archived=raw.get('archived'), is_disabled=raw.get('disabled'),
                   pushed_at=raw.get('pushed_at'), maintenance_status=active,
                   maintenance_reason=reason, activity_checked_at=observed, mark_synced=True)
    known = (type(raw.get('archived')) is bool and type(raw.get('disabled')) is bool
             and raw.get('has_issues') is True and type(raw.get('id')) is int)
    usable = (known and fresh({'observed_at': observed}, now) and not raw['archived']
              and not raw['disabled'] and active == 'active')
    return context, usable


def choose_latest(old, teacher, teacher_observed):
    """Choose by observation time, never by whether an observation is positive."""
    if old and teacher:
        old_id = old.get('github_issue_id')
        new_id = teacher.get('github_issue_id') or teacher.get('teacher_issue_id')
        if old_id and new_id and str(old_id) != str(new_id):
            return 'identity_conflict'
        if stamp(old['github_verified_at']) > stamp(teacher_observed):
            return 'old'
    return 'teacher' if teacher else 'old'


def admission(record, *, observed, approved, repo_usable, now, reason=None):
    result = strict_eligibility(record, timeline_complete=approved)
    if not fresh({'observed_at': observed}, now) or not repo_usable:
        return EligibilityResult('unknown', ('merge:stale_or_unverified_evidence',), result.warnings,
                                 result.newcomer_label_signal, POLICY)
    if reason:
        category = 'unknown' if reason in {'detail_unavailable', 'identity_conflict'} else 'temporarily_ineligible'
        return EligibilityResult(category, ('merge:' + reason,), result.warnings,
                                 result.newcomer_label_signal, POLICY)
    return EligibilityResult(result.eligibility, result.reasons, result.warnings,
                             result.newcomer_label_signal, POLICY)


def build_merged(*, old_candidates: Path, old_source: Path, teacher_review: Path,
                 database: Path, output: Path, migration: Path, prior_reviews=(), now=None):
    now = now or datetime.now(timezone.utc)
    if database.exists() or output.exists():
        raise ValueError('Use fresh output paths; existing work is preserved')
    old_rows = json.loads(old_candidates.read_text(encoding='utf-8'))
    pack_path = teacher_review / 'review_pack.json.gz'
    pack = json.loads(gzip.decompress(pack_path.read_bytes()))
    observed = pack['observed_start']
    stamp(observed)
    old_index = {issue_key(r['repository'], r['issue_number']): r for r in old_rows}
    teacher_index = {issue_key(r['repository'], r['number']): r for r in pack['decisions']}
    source_index = {issue_key(r['repo_name'], r['issue_number']): r for r in pack['source_issues']}
    if len(old_index) != len(old_rows) or len(teacher_index) != len(pack['decisions']):
        raise ValueError('Duplicate issue keys in input')
    if set(teacher_index) != set(source_index):
        raise ValueError('Teacher review does not cover source identities')
    details = {key.casefold(): value for key, value in pack['issue_details'].items()}
    detail_times = {key.casefold(): value for key, value in pack.get('detail_observed_at', {}).items()}
    failure_times = {value['key'].casefold(): value['observed_at'] for value in pack.get('detail_failures', [])
                     if value.get('key') and value.get('observed_at')}
    metadata = {}
    for path in sorted(teacher_review.glob('metadata_supplement_*.json')):
        metadata.update({k.casefold(): v for k, v in json.loads(path.read_text(encoding='utf-8')).items()})
    contexts = {}
    # Later generations include repositories outside the original paper seed cache.
    # Reuse timestamped repository evidence from prior review packs, never invent metadata.
    for review_directory in prior_reviews:
        previous_pack = json.loads(gzip.decompress((review_directory / 'review_pack.json.gz').read_bytes()))
        previous_observed = previous_pack['observed_start']
        for repository, raw in previous_pack['repository_details'].items():
            previous = contexts.get(repository.casefold())
            if not previous or stamp(previous[2]) <= stamp(previous_observed):
                contexts[repository.casefold()] = (*repo_context(raw, previous_observed, now), previous_observed)
    for repository in sorted({r['repository'] for r in old_rows}):
        envelope_path = old_source / repository.replace('/', '__') / 'repository.json'
        if not envelope_path.exists():
            if repository.casefold() not in contexts:
                raise ValueError(f'Missing repository source evidence: {repository}')
            continue
        envelope = json.loads(envelope_path.read_text(encoding='utf-8'))
        if envelope.get('tool_success') is not True or envelope.get('url') != f'https://api.github.com/repos/{repository}':
            raise ValueError('Invalid legacy repository envelope')
        previous = contexts.get(repository.casefold())
        if not previous or stamp(previous[2]) <= stamp(envelope['observed_at']):
            contexts[repository.casefold()] = (*repo_context(envelope['payload'], envelope['observed_at'], now), envelope['observed_at'])
    for repository, raw in pack['repository_details'].items():
        repository_observed=pack.get('repository_observed_at',{}).get(repository,observed)
        previous = contexts.get(repository.casefold())
        if not previous or stamp(previous[2]) <= stamp(repository_observed):
            contexts[repository.casefold()] = (*repo_context(raw, repository_observed, now), repository_observed)
    store = SQLiteCandidateStorage(database, migration)
    store.initialize()
    audits, available, normalized = [], [], []
    for key in sorted(set(old_index) | set(teacher_index)):
        old, reviewed = old_index.get(key), teacher_index.get(key)
        teacher_observed = detail_times.get(key, failure_times.get(key, observed))
        selected = choose_latest(old, reviewed, teacher_observed)
        repository = (reviewed or old)['repository']
        context, repo_usable, _ = contexts[repository.casefold()]
        detail = details.get(key)
        row = None
        reason = None
        selected_at = teacher_observed
        scope = 'teacher GitHub detail + timeline + linked PR + conservative claim review'
        if selected == 'old':
            row = dict(old)
            selected_at = row['github_verified_at']
            approved = row.get('has_linked_open_pr') is False
            scope = row.get('verification_scope', 'legacy GitHub verification')
        elif detail is not None and reviewed['decision'] == 'excluded' and ('comments' not in detail or 'created_at' not in detail):
            # Compact, identity-bound negative proofs belong in the audit. They
            # must not manufacture normalization fields or candidate rows.
            if (str(detail.get('id')) != str(source_index[key]['issue_id'])
                    or str(context['github_repository_id']) != str(source_index[key]['repo_id'])
                    or detail.get('repository_url', '').casefold() != f'https://api.github.com/repos/{repository}'.casefold()
                    or detail.get('number') != reviewed['number']):
                raise ValueError(f'Identity mismatch: {key}')
            approved = False
            reason = reviewed['reason']
        elif detail is not None:
            expected_url = f'https://api.github.com/repos/{repository}'
            if (str(detail['id']) != str(source_index[key]['issue_id'])
                    or str(context['github_repository_id']) != str(source_index[key]['repo_id'])
                    or detail.get('repository_url', '').casefold() != expected_url.casefold()
                    or detail.get('number') != reviewed['number']):
                raise ValueError(f'Identity mismatch: {key}')
            enriched = {**detail, **metadata.get(key, {})}
            row = normalize_github_issue(enriched)
            # Omitted comment-count metadata is unknown, not a measured zero.
            if row.get('comment_count') is None:
                raise ValueError(f'Missing authoritative comment count: {key}')
            row.update(source_system='github_rest', source_fetched_at=teacher_observed, github_verified_at=teacher_observed,
                       discovery_source='teacher_clickhouse', source_batch=pack.get('source_batch'),
                       is_pull_request='pull_request' in detail,
                       has_linked_open_pr=True if reviewed.get('open_pr_references') else
                       False if reviewed['decision'] == 'availability_passed' else None)
            approved = reviewed['decision'] == 'availability_passed'
            reason = None if approved else reviewed['reason']
            if selected == 'identity_conflict':
                reason = 'identity_conflict'
        elif old:
            row = dict(old)
            approved = False
            reason = 'identity_conflict' if selected == 'identity_conflict' else 'detail_unavailable'
        else:
            approved = False
            reason = reviewed['reason'] if reviewed and reviewed['decision'] == 'excluded' else 'detail_unavailable'
        audit = dict(key=key, repository=repository, issue_number=int(key.rsplit('#', 1)[1]),
                     old_source_present=old is not None, teacher_source_present=reviewed is not None,
                     selected_source=selected, selected_observed_at=selected_at,
                     teacher_decision=reviewed, record=row, record_in_database=row is not None,
                     verification_scope=scope, frozen=False)
        if row is not None:
            eligibility = admission(row, observed=selected_at, approved=approved,
                                    repo_usable=repo_usable, now=now, reason=reason)
            store.save_repository_candidates(repository=context, candidates=[(row, eligibility)])
            # Fail closed for review/freshness policy rejections even if basic state is open.
            if eligibility.eligibility != 'eligible':
                with store.connect() as connection:
                    connection.execute("""UPDATE task_candidate SET candidate_availability='temporarily_unverified',
                        availability_reasons_json=? WHERE issue_number=? AND repository_id=(
                        SELECT repository_id FROM repository WHERE full_name=?) AND candidate_availability='available'""",
                        (json.dumps(eligibility.reasons), row['issue_number'], context['full_name']))
            audit.update(eligibility=eligibility.eligibility, reasons=list(eligibility.reasons))
            normalized.append(row)
            if eligibility.eligibility == 'eligible':
                available.append({**row, 'repository': context['full_name'], 'primary_language': context['primary_language'],
                                  'newcomer_label_signal': has_newcomer_label(row['labels']),
                                  'verification_expires_at': (stamp(row['github_verified_at']) + timedelta(hours=24)).isoformat(),
                                  'verification_scope': scope, 'selected_source': selected,
                                  'retrieval_method': 'github_connector', 'frozen': False})
        else:
            known_negative = reviewed and reviewed['decision'] == 'excluded' and reason != 'detail_unavailable'
            audit.update(eligibility='ineligible' if known_negative else 'unknown', reasons=['merge:' + reason])
        audits.append(audit)
    with store.connect() as connection:
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        fk = connection.execute('PRAGMA foreign_key_check').fetchall()
        count = connection.execute("SELECT count(*) FROM task_candidate WHERE candidate_eligibility='eligible' AND candidate_availability='available'").fetchone()[0]
        duplicate = connection.execute('SELECT 1 FROM task_candidate GROUP BY repository_id,issue_number HAVING count(*)>1').fetchone()
    if integrity != 'ok' or fk or duplicate or count != len(available):
        raise ValueError('Standard candidate database integrity/count check failed')
    output.mkdir(parents=True)
    def write(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    write('candidates.json', available)
    write('audit.json', audits)
    lines = ['# 合并候选工作清单', '', f'生成时间 UTC：{now.isoformat()}；当前有效候选 {len(available)} 条。', '',
             '未冻结。状态有 24 小时有效期，参与前需重新查询 GitHub；难度和个人技能适合度未确认。',
             '沿用旧库的记录未重新采集，其原核查时间和范围均保留。', '']
    for row in available:
        title = html.escape(row['title']).replace('\n', ' ')
        lines.append(f"- [{row['repository']}#{row['issue_number']}: {title}]({row['html_url']})")
    (output / 'candidates.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    report = dict(policy=POLICY, generated_at=now.isoformat(), lifecycle='working', frozen=False,
                  old_candidates=len(old_rows), teacher_discovered=len(teacher_index),
                  overlap=len(set(old_index) & set(teacher_index)), distinct_audited=len(audits),
                  stored_records=len(normalized), available_candidates=count,
                  eligibility_counts=dict(Counter(a['eligibility'] for a in audits)),
                  candidate_repositories=dict(Counter(r['repository'] for r in available)),
                  candidate_source_counts=dict(Counter(r['selected_source'] for r in available)),
                  newer_teacher_removed_old=sum(a['old_source_present'] and a['selected_source'] == 'teacher'
                                                and a['eligibility'] != 'eligible' for a in audits),
                  stale_or_unverified=sum('merge:stale_or_unverified_evidence' in a['reasons'] for a in audits),
                  sqlite_integrity=integrity, foreign_key_violations=len(fk), database=str(database.resolve()),
                  minimum_verification_expiry=min((r['verification_expires_at'] for r in available), default=None),
                  input_hashes={str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in [old_candidates, pack_path,
                                          *(p / 'review_pack.json.gz' for p in prior_reviews),
                                          *sorted(teacher_review.glob('metadata_supplement_*.json'))]},
                  limitations=['Old-only records retain original verification scope; not re-fetched',
                               'Claim detection is conservative and can miss unlinked work',
                               'No automatic web configuration changes or experimental freeze'])
    write('report.json', report)
    return report
