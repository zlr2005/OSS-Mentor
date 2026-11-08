"""Expand compact projected GitHub evidence into an auditable local review pack.

This command is offline. It does not query or modify the teacher database or GitHub.
"""
import argparse
import base64
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import zlib


def expand(directory, source):
    compressed = base64.b64decode((directory / 'full_evidence.deflate.base64').read_text(encoding='ascii'), validate=True)
    evidence = json.loads(zlib.decompress(compressed, -15))
    source_bytes = source.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != evidence['source_sha256']:
        raise ValueError('Discovery source checksum mismatch')
    rows = json.loads(source_bytes.decode('utf-8-sig'))
    keys = [f"{r['repo_name']}#{r['issue_number']}" for r in rows]
    if len(keys) != len(set(keys)) or len(keys) != evidence['expected_source_count']:
        raise ValueError('Duplicate or incomplete source identities')
    details, observed, methods, coverage = {}, {}, {}, set()
    for vector in evidence['detail_rows']:
        if len(vector) != len(evidence['detail_columns']):
            raise ValueError('Malformed detail vector')
        value = dict(zip(evidence['detail_columns'], vector))
        index = value.pop('source_index')
        if type(index) is not int or not 0 <= index < len(rows) or index in coverage:
            raise ValueError('Duplicate or invalid detail index')
        coverage.add(index)
        row, key = rows[index], keys[index]
        if str(value['id']) != str(row['issue_id']):
            raise ValueError('Identity mismatch admitted as verified detail')
        value['id'] = int(value['id'])
        if value['id'] <= 0:
            raise ValueError('Invalid GitHub numeric ID')
        if value['repository_url'].lower() != f"https://api.github.com/repos/{row['repo_name']}".lower():
            raise ValueError('Transferred repository identity not resolved')
        if value['state'] not in {'open', 'closed'} or type(value['locked']) is not bool:
            raise ValueError('Unknown issue state')
        observed[key] = value.pop('observed_at')
        methods[key] = value.pop('method')
        is_pr = value.pop('is_pull_request')
        value.update(number=row['issue_number'], url=f"https://api.github.com/repos/{row['repo_name']}/issues/{row['issue_number']}",
                     html_url=f"https://github.com/{row['repo_name']}/issues/{row['issue_number']}")
        if is_pr:
            value['pull_request'] = {}
        if str(index) in evidence['bodies']:
            value['body'] = evidence['bodies'][str(index)]
        else:
            value['body_omitted'] = True
        details[key] = value
    failures = {}
    for vector in evidence['failure_rows']:
        index, code, at = vector
        if type(index) is not int or not 0 <= index < len(rows) or index in coverage:
            raise ValueError('Duplicate or invalid failure index')
        coverage.add(index)
        failures[keys[index]] = dict(key=keys[index], error_code=code, observed_at=at)
    if coverage != set(range(len(rows))):
        raise ValueError('Some source issues were not checked')
    blocked = set(evidence['blocked_indices'])
    decisions = []
    for index, row in enumerate(rows):
        key = keys[index]
        detail = details.get(key)
        review = evidence['open_reviews'].get(key)
        if key in failures:
            decision, reason = 'unverified', failures[key]['error_code']
        elif detail.get('pull_request') is not None:
            decision, reason = 'excluded', 'is_pr'
        elif detail['state'] != 'open':
            decision, reason = 'excluded', 'closed'
        elif detail['locked']:
            decision, reason = 'excluded', 'locked'
        elif detail['assignees']:
            decision, reason = 'excluded', 'assigned'
        elif index in blocked:
            decision, reason = 'review_pending', 'blocked_label_or_missing_information'
        elif review:
            decision, reason = review['decision'], review['reason']
        else:
            raise ValueError(f'Open task not covered by final review: {key}')
        decisions.append(dict(key=key, repository=row['repo_name'], number=row['issue_number'],
                              teacher_issue_id=str(row['issue_id']), github_issue_id=str(detail['id']) if detail else None,
                              url=f"https://github.com/{row['repo_name']}/issues/{row['issue_number']}",
                              decision=decision, reason=reason,
                              open_pr_references=(review or {}).get('open_pr_references', []),
                              claim_signals=(review or {}).get('claim_signals', [])))
    pack = dict(schema_version='teacher-github-review-v1', source_batch=evidence['source_batch'],
                observed_start=evidence['observed_start'], observed_end=evidence['observed_end'],
                retrieval_method='github_connector', http_headers_available=False, source_issues=rows,
                issue_details=details, repository_details=evidence['repository_details'],
                timelines=evidence['timelines'], pull_requests=evidence['pull_requests'], decisions=decisions,
                detail_failures=list(failures.values()), detail_observed_at=observed, detail_retrieval_methods=methods,
                mismatches=evidence['mismatches'], listing_audit=evidence['listing_audit'], frozen=False,
                discovery_summary=dict(discovered=len(rows), attempted=len(coverage), details_matched=len(details),
                                       unresolved=len(failures), not_checked=0,
                                       failure_counts=dict(Counter(f['error_code'] for f in failures.values())),
                                       method_counts=dict(Counter(methods.values()))),
                retained_fields_note='Projected GitHub evidence. Closed/assigned issue bodies omitted. '
                                     'Full body retained for unassigned open issues; timeline comment bodies '
                                     'retained only for passing candidates; PR bodies omitted. Request URLs, '
                                     'identity, state and observation times are retained; not a full raw response archive.')
    raw = json.dumps(pack, ensure_ascii=False).encode('utf-8')
    output = directory / 'review_pack.base64'
    with output.open('x', encoding='ascii') as stream:
        stream.write(base64.b64encode(gzip.compress(raw)).decode('ascii'))
    (directory / 'coverage.json').write_text(json.dumps(pack['discovery_summary'], indent=2), encoding='utf-8')
    return {**pack['discovery_summary'], 'decisions': dict(Counter(d['decision'] for d in decisions))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(expand(args.directory, args.source), indent=2))
