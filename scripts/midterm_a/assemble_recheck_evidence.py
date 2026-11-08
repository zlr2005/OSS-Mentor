"""Offline merge of resumable, teacher-ID-bound read-only verification batches."""
import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path


def read_records(directory):
    records = []
    for path in sorted(directory.glob('*.json.gz')):
        if path.name.startswith(('direct_', 'compressed_')):
            records.extend(json.loads(gzip.decompress(path.read_bytes())))
    groups = defaultdict(list)
    for path in directory.glob('large_*.part'):
        groups[path.name.rsplit('.', 2)[0]].append(path)
    incomplete = []
    # Legacy text fragments lack a final checksum/part-count manifest. Even a
    # syntactically valid JSON join cannot prove a middle segment is present.
    # Keep them for diagnostics; only finalized gzip batches are authoritative.
    incomplete.extend(groups)
    incomplete.extend(sorted({p.name.rsplit('.',2)[0] for p in directory.glob('compressed_*.b64part')
                              if not (directory/(p.name.rsplit('.',2)[0]+'.json.gz')).exists()}))
    return records, incomplete


def latest_details(records, sources):
    latest = {}
    for record in records:
        if record['kind'] == 'detail':
            key = record['key'].casefold()
            if key not in sources or record['expected_id'] != str(sources[key]['issue_id']):
                raise ValueError('Unexpected Issue identity in direct evidence')
            previous = latest.get(key)
            direct = record.get('method') != 'github_search_exact_identity_closed_verification'
            previous_direct = previous and previous.get('method') != 'github_search_exact_identity_closed_verification'
            if not previous or (direct and not previous_direct) or (direct == previous_direct and record['observed_at'] > previous['observed_at']):
                latest[key] = record
        elif record['kind'] == 'closed_page':
            repo = record['repository']
            for number, issue_id, closed_at, updated_at, locked in record['rows']:
                key = f'{repo}#{number}'.casefold()
                if key not in sources or str(sources[key]['issue_id']) != issue_id:
                    raise ValueError('Unexpected Issue identity in collection evidence')
                detail = dict(kind='detail', key=key, expected_id=issue_id, identity_match=True,
                              observed_at=record['observed_at'], url=record['url'], method=record['method'],
                              payload=dict(id=int(issue_id), number=number, state='closed', closed_at=closed_at,
                                           updated_at=updated_at, locked=locked, assignees=[], labels=[], body_omitted=True,
                                           html_url=f'https://github.com/{repo}/issues/{number}',
                                           url=f'https://api.github.com/repos/{repo}/issues/{number}',
                                           repository_url=f'https://api.github.com/repos/{repo}'))
                previous = latest.get(key)
                if not previous or (previous.get('method') == detail['method'] and previous['observed_at'] < detail['observed_at']):
                    latest[key] = detail
    return latest


def assemble(previous, directory):
    rows = json.loads((previous/'source_issues.json').read_text('utf-8'))
    sources = {f"{r['repo_name']}#{r['issue_number']}".casefold(): r for r in rows}
    old_audit = json.loads((previous/'audit.json').read_text('utf-8'))
    targets = {r['key'].casefold(): r for r in old_audit if r['decision'] in ('unverified', 'review_pending')}
    evidence = json.loads(gzip.decompress((previous/'connector_evidence.json.gz').read_bytes()))
    records, incomplete = read_records(directory)
    latest = latest_details(records, sources)
    if set(latest) - set(targets):
        raise ValueError('Verification must remain within requested teacher-discovered targets')
    if set(targets) - set(latest):
        raise ValueError(f'Recheck has {len(set(targets)-set(latest))} identities not yet queried')
    for key, record in latest.items():
        evidence['failures'].pop(key, None)
        if record.get('payload') and (record.get('identity_match') or record.get('transferred')):
            payload = record['payload']
            if str(payload.get('id')) != str(sources[key]['issue_id']):
                raise ValueError('Matched envelope has a different global Issue ID')
            envelope = {k: record[k] for k in ('payload', 'observed_at', 'url', 'method')}
            if record.get('transferred'):
                envelope['transferred'] = True
            evidence['details'][key] = envelope
        else:
            evidence['details'].pop(key, None)
            error = record.get('error') or {}
            status = str((error.get('error_data') or {}).get('status', '')) if isinstance(error, dict) else ''
            code = ('CONFIRMED_DELETED' if status == '410' else error.get('error_code', 'READ_ERROR')) if isinstance(error, dict) else 'READ_ERROR'
            if isinstance(error,dict) and (error.get('response') or {}).get('message')=='Moved Permanently':
                code='REDIRECT_TARGET_UNAVAILABLE'
            if record.get('identity_match') is False:
                code = 'IDENTITY_MISMATCH'
            evidence['failures'][key] = dict(key=key, code=code, observed_at=record['observed_at'],
                                            url=record['url'], error=error,
                                            observed_payload=record.get('payload'), redirects=record.get('redirects', []))
    for record in sorted(records, key=lambda r: r.get('observed_at', '')):
        if record['kind'] == 'timeline':
            key = record['key'].casefold()
            if key not in targets:
                raise ValueError('Unexpected timeline target')
            evidence['timelines'][key] = {k: record[k] for k in ('pages', 'complete', 'observed_at')}
        elif record['kind'] == 'pull_request' and record.get('payload'):
            evidence['pull_requests'][record['url']] = dict(record['payload'], observed_at=record['observed_at'])
    evidence['observed_end'] = max(r.get('observed_at', '') for r in records)
    evidence['recheck_summary'] = dict(targets=len(targets), attempted=len(latest), not_attempted=len(set(targets)-set(latest)),
                                      previous_decisions=dict(Counter(r['decision'] for r in targets.values())),
                                      ignored_unfinalized_transport_groups=incomplete)
    for name in ('source_issues.json', 'source_manifest.json', 'source_identity_conflicts.json', 'corpus_summary.json', 'old_candidates_empty.json'):
        with (directory/name).open('xb') as out:
            out.write((previous/name).read_bytes())
    with (directory/'connector_evidence.json.gz').open('xb') as out:
        out.write(gzip.compress(json.dumps(evidence, ensure_ascii=False).encode()))
    return evidence['recheck_summary']


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('previous', type=Path)
    p.add_argument('directory', type=Path)
    args = p.parse_args()
    print(json.dumps(assemble(args.previous, args.directory), ensure_ascii=False))
