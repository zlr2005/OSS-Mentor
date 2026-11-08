"""Idempotent conversion of screened inventories into the platform schema."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from oss_mentor.candidate_freshness import parse_timestamp, verification_is_current
from oss_mentor.candidate_rules import EligibilityResult, evaluate_candidate, has_beginner_label
from oss_mentor.candidate_sync import repository_maintenance_status
from oss_mentor.storage.candidates import SQLiteCandidateStorage
from oss_mentor.task_features import TASK_FEATURE_VERSION


def import_inventory(snapshot: Path, store: SQLiteCandidateStorage, *,
                     evidence_paths: list[Path] = ()) -> dict[str, Any]:
    snapshot = snapshot.resolve()
    if snapshot == store.database_path:
        raise ValueError('source inventory and destination database must differ')
    observed_now = datetime.now(timezone.utc)
    with closing(sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True)) as source:
        source.row_factory = sqlite3.Row
        rows = [dict(r) for r in source.execute('SELECT * FROM issue_inventory ORDER BY issue_key')]
    if len({str(r['github_issue_id']) for r in rows}) != len(rows):
        raise ValueError('duplicate global GitHub issue identity in inventory')
    keys = {r['issue_key'].casefold() for r in rows}
    repositories = {r['repository'].casefold() for r in rows}
    details, metadata = {}, {}
    for path in evidence_paths:
        evidence = json.loads(path.read_text(encoding='utf-8-sig'))
        for key, value in evidence.get('details', {}).items():
            if key.casefold() in keys:
                details[key.casefold()] = value.get('payload', value)
        for key, value in evidence.get('repositories', {}).items():
            if key.casefold() in repositories:
                old = metadata.get(key.casefold())
                if not old or str(value.get('observed_at') or '') > str(old.get('observed_at') or ''):
                    metadata[key.casefold()] = value
    store.initialize()
    counts = Counter()
    languages = Counter()
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        for row in rows:
            raw = json.loads(row['record_json'])
            repo = row['repository']
            if str(raw.get('repository')).casefold() != repo.casefold():
                raise ValueError('repository identity mismatch: ' + row['issue_key'])
            number, issue_id = int(row['issue_number']), int(row['github_issue_id'])
            if str(raw.get('github_issue_id')) != str(issue_id) or int(raw['issue_number']) != number:
                raise ValueError('source identity mismatch: ' + row['issue_key'])
            existing = connection.execute(
                'SELECT t.*, r.full_name AS repository FROM task_candidate t '
                'JOIN repository r USING(repository_id) WHERE t.github_issue_id = ? '
                'OR (r.full_name = ? COLLATE NOCASE AND t.issue_number = ?)',
                (issue_id, repo, number)).fetchall()
            if any((x['github_issue_id'] is not None and str(x['github_issue_id']) != str(issue_id)) or
                   x['repository'].casefold() != repo.casefold() or x['issue_number'] != number for x in existing):
                raise ValueError('destination identity conflict: ' + row['issue_key'])
            source_time = parse_timestamp(row['observed_at'])
            if existing:
                previous_time = parse_timestamp(existing[0]['github_verified_at'])
                if previous_time and (not source_time or previous_time >= source_time):
                    counts['skipped_equal_or_newer'] += 1
                    continue
            detail = details.get(row['issue_key'].casefold(), {})
            if detail and (str(detail.get('id')) != str(issue_id) or detail.get('number') != number):
                raise ValueError('detail evidence identity mismatch: ' + row['issue_key'])
            repo_envelope = metadata.get(repo.casefold(), {})
            meta = repo_envelope.get('payload', repo_envelope)
            previous_repo = connection.execute('SELECT * FROM repository WHERE full_name=? COLLATE NOCASE', (repo,)).fetchone()
            language = raw.get('language') or raw.get('primary_language') or meta.get('language') or (previous_repo['primary_language'] if previous_repo else None)
            repo_values = dict(full_name=repo, github_repository_id=meta.get('id'),
                               html_url=f'https://github.com/{repo}', ecosystems_last_synced_at=None,
                               primary_language=language)
            if previous_repo:
                repo_values.update(is_archived=bool(previous_repo['is_archived']), is_disabled=bool(previous_repo['is_disabled']))
                if (parse_timestamp(previous_repo['github_verified_at']) or datetime.min.replace(tzinfo=timezone.utc)) > (parse_timestamp(repo_envelope.get('observed_at')) or datetime.min.replace(tzinfo=timezone.utc)):
                    meta = {}
            if meta:
                status, reason = repository_maintenance_status(meta.get('pushed_at'), checked_at=observed_now)
                repo_values.update(is_archived=bool(meta.get('archived')), is_disabled=bool(meta.get('disabled')),
                                   pushed_at=meta.get('pushed_at'), maintenance_status=status, maintenance_reason=reason,
                                   github_verified_at=repo_envelope.get('observed_at'),
                                   activity_checked_at=repo_envelope.get('observed_at'))
            repo_id = store.upsert_repository(connection, **repo_values)
            audited = row['review_status'] == 'availability_passed'
            labels = raw.get('current_labels', json.loads(raw.get('labels_json', '[]')))
            record = dict(
                github_issue_id=issue_id, issue_number=number, html_url=raw.get('url') or raw.get('html_url') or f'https://github.com/{repo}/issues/{number}',
                title=raw.get('title') or '', body_text=raw.get('body', raw.get('body_text', detail.get('body'))),
                created_at=raw.get('created_at') or detail.get('created_at') or '',
                author_association=raw.get('author_association') or detail.get('author_association'),
                labels=labels, state=raw.get('state', 'open'), is_pull_request='pull_request' in detail,
                assignment_state=('assigned' if raw.get('assignees') else 'unassigned') if audited else raw.get('assignment_state', 'unknown'),
                is_locked=raw.get('locked', raw.get('is_locked', False)),
                has_linked_open_pr=False if audited and raw.get('timeline_complete') is True and not raw.get('open_pr_references') else raw.get('has_linked_open_pr'),
                comment_count=raw.get('comment_count', detail.get('comments', 0)),
                last_activity_at=raw.get('last_activity_at') or detail.get('updated_at'),
                source_system='github_rest', source_fetched_at=raw.get('source_fetched_at') or row['observed_at'] or '',
                github_verified_at=row['observed_at'], verification_expires_at=row['expires_at'],
            )
            eligibility = evaluate_candidate(record)
            eligibility = EligibilityResult(eligibility.eligibility, eligibility.reasons,
                (*eligibility.warnings, '难度与技能要求为规则推断，尚未经实际构建验证'), eligibility.newcomer_label_signal)
            if not audited or raw.get('claim_signals') or raw.get('frozen') or raw.get('timeline_complete') is not True:
                eligibility = EligibilityResult('unknown', ('inventory_requires_current_review',),
                                                eligibility.warnings, eligibility.newcomer_label_signal)
            store.upsert_candidate(connection, repository_id=repo_id, record=record, eligibility=eligibility)
            connection.execute(
                'UPDATE task_candidate SET inventory_review_status=?, inventory_source=?, inventory_record_json=? '
                'WHERE repository_id=? AND issue_number=?',
                (row['review_status'], str(snapshot), row['record_json'], repo_id, number))
            counts['imported'] += 1
            counts['fresh_at_import' if audited and verification_is_current(record, now=observed_now) else 'held_for_refresh'] += 1
            languages[language or 'unknown'] += 1
        result = dict(snapshot_path=str(snapshot), snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                      database_path=str(store.database_path), imported_at=observed_now.isoformat(),
                      source_count=len(rows), counts=dict(counts), languages=dict(languages),
                      inference='existing explainable rules; estimated, not human difficulty labels')
        connection.execute('INSERT INTO candidate_inventory_import(snapshot_path,snapshot_sha256,imported_at,summary_json) VALUES(?,?,?,?)',
                           (str(snapshot), result['snapshot_sha256'], result['imported_at'], json.dumps(result, ensure_ascii=False)))
    result['currently_matchable'] = len(store.matchable_candidates())
    return result


def import_prepared_inventory(snapshot: Path, prepared: Path, store: SQLiteCandidateStorage) -> dict[str, Any]:
    """Apply an already validated conversion without repeating expensive inference.

    Candidate IDs are remapped; user/profile/feedback tables are never copied.
    The source digest and current rule version must match the prepared database.
    """
    prepared = prepared.resolve()
    if prepared == store.database_path or snapshot.resolve() == store.database_path:
        raise ValueError('prepared source and destination must differ')
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    with closing(sqlite3.connect(prepared.as_uri() + '?mode=ro', uri=True)) as source:
        source.row_factory = sqlite3.Row
        proof = source.execute('SELECT * FROM candidate_inventory_import ORDER BY import_id DESC LIMIT 1').fetchone()
        if not proof or proof['snapshot_sha256'] != digest:
            raise ValueError('prepared database does not match source snapshot')
        tasks = [dict(r) for r in source.execute('SELECT * FROM task_candidate ORDER BY task_candidate_id')]
        repos = [dict(r) for r in source.execute('SELECT * FROM repository')]
        skills = {}
        for row in source.execute('SELECT * FROM task_skill_requirement'):
            skills.setdefault(row['task_candidate_id'], []).append(dict(row))
        expected = json.loads(proof['summary_json'])['source_count']
        if len(tasks) != expected or any(t['task_feature_version'] != TASK_FEATURE_VERSION for t in tasks):
            raise ValueError('prepared candidate coverage or rule version does not match')
    store.initialize()
    counts = Counter()
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        repo_ids = {}
        for repo in repos:
            previous = connection.execute('SELECT * FROM repository WHERE full_name=? COLLATE NOCASE', (repo['full_name'],)).fetchone()
            values = {k: repo[k] for k in ('full_name','github_repository_id','html_url','ecosystems_last_synced_at','ecosystem','primary_language',
                'github_verified_at','is_archived','is_disabled','pushed_at','maintenance_status','maintenance_reason','activity_checked_at','has_contributing_guide')}
            if previous and (parse_timestamp(previous['github_verified_at']) or datetime.min.replace(tzinfo=timezone.utc)) > (parse_timestamp(repo['github_verified_at']) or datetime.min.replace(tzinfo=timezone.utc)):
                values = {k: previous[k] for k in values}
            repo_ids[repo['repository_id']] = store.upsert_repository(connection, **values)
        names = {r['repository_id']: r['full_name'] for r in repos}
        for task in tasks:
            existing = connection.execute(
                'SELECT t.*, r.full_name AS repository FROM task_candidate t JOIN repository r USING(repository_id) '
                'WHERE t.github_issue_id=? OR (t.repository_id=? AND t.issue_number=?)',
                (task['github_issue_id'], repo_ids[task['repository_id']], task['issue_number'])).fetchall()
            if any((r['github_issue_id'] is not None and str(r['github_issue_id']) != str(task['github_issue_id'])) or r['repository'].casefold() != names[task['repository_id']].casefold() or r['issue_number'] != task['issue_number'] for r in existing):
                raise ValueError('destination identity conflict in prepared import')
            if existing and (parse_timestamp(existing[0]['github_verified_at']) or datetime.min.replace(tzinfo=timezone.utc)) >= (parse_timestamp(task['github_verified_at']) or datetime.min.replace(tzinfo=timezone.utc)):
                counts['skipped_equal_or_newer'] += 1
                continue
            values = {k: v for k, v in task.items() if k != 'task_candidate_id'}
            values['repository_id'] = repo_ids[task['repository_id']]
            values['newcomer_label_signal'] = int(has_beginner_label(json.loads(task['labels_json'])))
            columns = list(values)
            assignments = ','.join(f'{c}=excluded.{c}' for c in columns if c not in {'repository_id','issue_number'})
            connection.execute(f'INSERT INTO task_candidate({",".join(columns)}) VALUES({",".join("?" for _ in columns)}) '
                               f'ON CONFLICT(repository_id,issue_number) DO UPDATE SET {assignments}', tuple(values.values()))
            new_id = connection.execute('SELECT task_candidate_id FROM task_candidate WHERE repository_id=? AND issue_number=?',
                                        (values['repository_id'], values['issue_number'])).fetchone()[0]
            connection.execute('DELETE FROM task_skill_requirement WHERE task_candidate_id=?', (new_id,))
            for skill in skills.get(task['task_candidate_id'], []):
                skill['task_candidate_id'] = new_id
                columns = list(skill)
                connection.execute(f'INSERT INTO task_skill_requirement({",".join(columns)}) VALUES({",".join("?" for _ in columns)})', tuple(skill.values()))
            counts['imported'] += 1
        result = dict(snapshot_path=str(snapshot.resolve()), snapshot_sha256=digest, prepared_database=str(prepared),
                      database_path=str(store.database_path), imported_at=datetime.now(timezone.utc).isoformat(),
                      source_count=expected, counts=dict(counts))
        connection.execute('INSERT INTO candidate_inventory_import(snapshot_path,snapshot_sha256,imported_at,summary_json) VALUES(?,?,?,?)',
                           (result['snapshot_path'], digest, result['imported_at'], json.dumps(result, ensure_ascii=False)))
    store.mark_stale_candidates()
    result['currently_matchable'] = len(store.matchable_candidates())
    return result
