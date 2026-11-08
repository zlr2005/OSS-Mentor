"""Conservative final screening: archive uncertainty without deleting source data."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from oss_mentor.candidate_freshness import recommendation_is_current
from oss_mentor.task_readiness import task_review_reasons

POLICY = 'automated-conservative-final-screen-v1'
MUTABLE = {'candidate_eligibility', 'ineligibility_reasons_json', 'feature_evidence_json'}


def _binary_fingerprint(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'binary_sha256':hashlib.sha256(value).hexdigest(),'binary_size':len(value)}
    raise TypeError('Unsupported fingerprint value type')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                                   default=_binary_fingerprint).encode()).hexdigest()


def identity(row):
    return f"{row['repository'].casefold()}#{int(row['issue_number'])}"


def content_hash(row):
    return hashlib.sha256(json.dumps([identity(row), str(row['github_issue_id']), row['title'],
        row['body_text'], sorted(json.loads(row['labels_json']))], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def is_ready(row, now):
    return (row['candidate_eligibility'] == 'eligible' and not row.get('is_archived')
            and not row.get('is_disabled') and row.get('maintenance_status') != 'inactive'
            and row.get('state') == 'open' and row.get('assignment_state') == 'unassigned'
            and row.get('is_locked') == 0 and row.get('has_linked_open_pr') == 0
            and recommendation_is_current(row, now=now) and not task_review_reasons(row))


def decide(row, reference, now):
    if is_ready(row, now):
        return 'keep', ['current_recommendation_gates_passed']
    if content_hash(row) != reference['content_sha256']:
        return 'quarantine', ['source_changed_since_content_review']
    quality = json.loads(row['feature_evidence_json']).get('difficulty_assessment', {}).get('information_quality', {})
    if quality.get('actionability') == 'non_actionable':
        return 'exclude', ['not_a_single_implementation_task']
    reasons = list(task_review_reasons(row))
    if quality.get('actionability') == 'design_pending':
        reasons.append('design_decision_not_demonstrated')
    if not recommendation_is_current(row, now=now):
        reasons.append('current_availability_not_confirmed')
    return 'quarantine', sorted(set(reasons or ['recommendation_readiness_not_demonstrated']))


def connect(path, mode='ro'):
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=' + mode, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def load_rows(conn):
    requirements = defaultdict(list)
    for r in conn.execute('SELECT * FROM task_skill_requirement'):
        requirements[r['task_candidate_id']].append(dict(r))
    return [{**dict(r), 'requirements': requirements[r['task_candidate_id']]} for r in conn.execute('''
        SELECT tc.*,r.full_name AS repository,r.is_archived,r.is_disabled,r.maintenance_status
        FROM task_candidate tc JOIN repository r USING(repository_id)''')]


def fingerprints(conn, selected):
    result = {}
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if r[0] != 'task_candidate' and not r[0].startswith('sqlite_')]
    for table in tables:
        sql = 'SELECT * FROM "' + table.replace('"', '""') + '"'
        result[table] = digest(sorted(digest(tuple(r)) for r in conn.execute(sql)))
    rows = load_rows(conn)
    result['unselected_candidates'] = digest(sorted(digest(r) for r in rows if identity(r) not in selected))
    result['selected_immutable_fields'] = digest(sorted(digest({k: v for k, v in r.items() if k not in MUTABLE})
                                                       for r in rows if identity(r) in selected))
    return result


def apply_database(path, references, now, screening_id):
    conn = connect(path, 'rw')
    try:
        conn.execute('BEGIN IMMEDIATE')
        before = fingerprints(conn, references)
        rows = load_rows(conn)
        ready_before = {identity(r) for r in rows if is_ready(r, now)}
        decisions = []
        found = set()
        for row in rows:
            key = identity(row)
            if key not in references:
                continue
            reference = references[key]
            if str(row['github_issue_id']) != reference['github_issue_id']:
                raise ValueError('Global Issue identity mismatch')
            found.add(key)
            decision, reasons = decide(row, reference, now)
            entry = {'identity': key, 'github_issue_id': str(row['github_issue_id']), 'title': row['title'],
                'task_candidate_id': row['task_candidate_id'], 'decision': decision, 'reason_codes': reasons,
                'policy_version': POLICY, 'screening_id': screening_id, 'screened_at': now.isoformat(),
                'content_sha256': content_hash(row), 'source_review_decision': reference['decision'],
                'source_verified_at': row['github_verified_at'], 'source_expires_at': row['verification_expires_at'],
                'manual_review_required': False, 'reconsider_when': 'new_source_evidence_or_review_policy'}
            decisions.append(entry)
            if decision == 'keep':
                continue
            evidence = json.loads(row['feature_evidence_json'])
            evidence['automated_screening'] = {k: entry[k] for k in ['decision','reason_codes','policy_version',
                'screening_id','screened_at','content_sha256','manual_review_required','reconsider_when']}
            ineligible = json.loads(row['ineligibility_reasons_json'])
            marker = 'auto_screening_context_only' if decision == 'exclude' else 'auto_screening_evidence_insufficient'
            if marker not in ineligible:
                ineligible.append(marker)
            eligibility = 'excluded' if decision == 'exclude' else row['candidate_eligibility']
            # Never turn a formerly eligible but newly unverifiable item into a keep.
            if decision == 'quarantine' and eligibility == 'eligible':
                eligibility = 'temporarily_ineligible'
            conn.execute('''UPDATE task_candidate SET candidate_eligibility=?,ineligibility_reasons_json=?,
                feature_evidence_json=? WHERE task_candidate_id=?''',
                (eligibility, json.dumps(ineligible,ensure_ascii=False), json.dumps(evidence,ensure_ascii=False,sort_keys=True),
                 row['task_candidate_id']))
        if found != references.keys():
            raise ValueError('Screening source identities missing from destination')
        after = fingerprints(conn, references)
        if before != after:
            raise ValueError('Protected fields changed during screening')
        ready_after = {identity(r) for r in load_rows(conn) if is_ready(r, now)}
        if ready_before != ready_after:
            raise ValueError('This archive-only operation must preserve the existing ready pool')
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('SQLite validation failed')
        conn.commit()
        return {'decisions': sorted(decisions, key=lambda r:r['identity']), 'ready_identities': sorted(ready_after),
                'protected_fingerprints': before, 'ready_count': len(ready_after), 'integrity': 'ok'}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def snapshot(source, target):
    if target.exists():
        raise ValueError('Refusing to overwrite a backup or staging database')
    src = connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close(); dst.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output must be a new directory')
    source = [json.loads(l) for l in (args.source/'review_results.jsonl').read_text(encoding='utf-8').splitlines()]
    references = {r['identity']:r for r in source if r['decision']!='passes_content_gate'}
    if not references:
        parser.error('No unresolved input records')
    now = datetime.now(timezone.utc)
    screening_id = digest([POLICY, sorted(references), now.isoformat()])
    args.output.mkdir(parents=True)
    names = ['oss_mentor.sqlite3','oss_mentor_web.sqlite3']
    staged = {}
    for name in names:
        path = ROOT/'data'/name
        backup = args.output/(name+'.before')
        snapshot(path, backup)
        staging = args.output/(name+'.staging')
        snapshot(backup, staging)
        staged[name] = apply_database(staging, references, now, screening_id)
    if {r['identity']:r['decision'] for r in staged[names[0]]['decisions']} != {r['identity']:r['decision'] for r in staged[names[1]]['decisions']}:
        raise ValueError('Database screening decisions disagree')
    actual = {}
    for name in names:
        actual[name] = apply_database(ROOT/'data'/name, references, now, screening_id)
        if actual[name] != staged[name]:
            raise ValueError('Production result differs from staged rehearsal')
    canonical = actual['oss_mentor_web.sqlite3']
    summary = {'screening_id': screening_id, 'policy_version': POLICY, 'screened_at':now.isoformat(),
        'screened_count':len(references), 'decision_counts':dict(Counter(r['decision'] for r in canonical['decisions'])),
        'human_queue_remaining':0, 'source_records_deleted':0, 'github_requests':0,
        'databases':{name:{k:v for k,v in result.items() if k not in {'decisions','ready_identities'}} for name,result in actual.items()},
        'method':'automated_conservative_filter; not individual human or maintainer validation',
        'source_review_results_sha256':hashlib.sha256((args.source/'review_results.jsonl').read_bytes()).hexdigest()}
    (args.output/'decisions.jsonl').write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in canonical['decisions'])+'\n',encoding='utf-8')
    (args.output/'retained_ready_identities.json').write_text(json.dumps(canonical['ready_identities'],ensure_ascii=False,indent=2),encoding='utf-8')
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    resolution = {'screening_id':screening_id,'screened_at':now.isoformat(),'result_directory':str(args.output.resolve()),
        'decision_counts':summary['decision_counts'],'human_queue_remaining':0,
        'note':'Historical needs_task_review entries are resolved by an automated keep/quarantine/exclude disposition; original review files remain evidence snapshots.'}
    (args.source/'screening_resolution.json').write_text(json.dumps(resolution,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='databases'},ensure_ascii=False))


if __name__=='__main__':
    main()
