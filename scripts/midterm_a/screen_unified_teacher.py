"""Offline, fail-closed screening of teacher-discovered Issue identities only."""
import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re

from oss_mentor.collector.source_comparison import normalize_github_issue
from oss_mentor.datasets.live_issues import strict_eligibility, fresh
from oss_mentor.datasets.merged_candidates import repo_context
from build_teacher_review import build

BLOCKED = re.compile(r'needs?.?(?:more.?)?info|waiting.for|awaiting|blocked|in.progress|claimed|duplicate|invalid|wontfix|won.t.fix|(?:^|[: /])questions?(?:$|[: /])|support|discussion|needs.repro|unconfirmed', re.I)
CLAIM = re.compile(r"\b(?:i(?:['’]m| am|['’]ll| will| would like to|['’]d like to| want to| can| could)|we(?:['’]re| are| will))\s+(?:\w+\s+){0,4}(?:work(?:ing)? on|take (?:this|it)|fix(?:ing)? (?:this|it)|implement(?:ing)?|submit(?:ting)? (?:a |the )?pr|pick (?:this|it) up)|\b(?:can|could|may) i\s+(?:\w+\s+){0,3}(?:work|take|fix|pick)|\bassign (?:this (?:issue )?to )?me\b|\b(?:working on (?:this|it)|opened (?:a |the )?pr|i have (?:a |an )?(?:fix|pr)|i['’]ve (?:started|submitted|opened))\b|(?:我来|我正在|我想|我可以).{0,12}(?:修复|处理|解决|认领|实现)", re.I)
SCOPE = re.compile(r'\b(?:test plan|endgame|iteration plan|release plan|meeting|agenda|tracking issue|umbrella issue|weekly sync)\b', re.I)
MANUAL_REVIEW = json.loads((Path(__file__).resolve().parents[2]/'config/midterm_datasets/unified_teacher_manual_review_v0.1.json').read_text(encoding='utf-8'))
NON_CLAIM_REVIEW = json.loads((Path(__file__).resolve().parents[2]/'config/midterm_datasets/reviewed_non_claim_text_v0.1.json').read_text(encoding='utf-8'))


def context_fingerprint(detail,timeline):
    return hashlib.sha256(json.dumps(dict(detail=detail,timeline=timeline),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def assess(source, detail, observed, repository, repository_at, timeline, prs, *, now=None, projected_rejection=None, resolution=None):
    now = now or datetime.now(timezone.utc)
    def result(decision, reason, **extra):
        return dict(decision=decision, reason=reason, open_pr_references=[], claim_signals=[], **extra)
    expected_repo=f"https://api.github.com/repos/{source['repo_name']}"
    if (str(detail.get('id')) != str(source['issue_id']) or detail.get('number') != int(source['issue_number'])
            or str(repository.get('id')) != str(source['repo_id'])
            or detail.get('repository_url', '').casefold() != expected_repo.casefold()):
        return result('unverified', 'identity_mismatch')
    if not fresh({'observed_at':observed}, now):
        return result('unverified', 'stale_issue_evidence')
    if detail.get('state') not in {'open','closed'}:
        return result('unverified', 'unknown_state')
    if 'pull_request' in detail:
        return result('excluded', 'is_pr')
    if detail['state'] == 'closed':
        return result('excluded', 'closed')
    if type(detail.get('locked')) is not bool or not isinstance(detail.get('assignees'), list):
        return result('unverified', 'status_incomplete')
    if detail['locked']:
        return result('excluded', 'locked')
    if detail['assignees']:
        return result('excluded', 'assigned')
    _, usable = repo_context(repository, repository_at, now)
    if not usable:
        return result('unverified', 'repository_inactive_or_unverified')
    cleared=False
    if resolution:
        if (str(resolution.get('issue_id'))!=str(source['issue_id'])
                or resolution.get('evidence_sha256')!=context_fingerprint(detail,timeline)
                or not fresh({'observed_at':resolution.get('reviewed_at')},now)):
            return result('unverified','context_review_stale_or_changed')
        if resolution.get('decision')=='excluded':
            if not resolution.get('rationale') or not resolution.get('evidence'):
                raise ValueError('An adjudication needs a rationale and bound evidence')
            return result('excluded',resolution['reason'],agent_context_review=resolution)
        if resolution.get('decision')!='clear_for_availability_checks':
            raise ValueError('Context review cannot directly approve availability')
        cleared=True
    manual=MANUAL_REVIEW.get(f"{source['repo_name']}#{source['issue_number']}".casefold())
    if manual and not cleared and str(detail['id']) == manual['issue_id'] and detail.get('title') == manual['title']:
        return result('review_pending','manual_scope_or_information_review',manual_review_note=manual['note'])
    ignored_labels=set(resolution.get('ignored_labels',[])) if cleared else set()
    if any(BLOCKED.search(label['name']) and label['name'] not in ignored_labels for label in detail.get('labels', [])):
        return result('review_pending', 'blocking_or_information_needed_label')
    if SCOPE.search(detail.get('title','')) and not (cleared and resolution.get('scope_cleared')):
        return result('review_pending', 'planning_or_umbrella_task')
    # A projected negative can only reject, never approve. Its retained proof
    # must independently establish a claim or current referenced open PR.
    if projected_rejection and not cleared:
        reason=projected_rejection.get('reason')
        if reason == 'claim_or_work_in_progress_text':
            signals=projected_rejection.get('claim_signals',[])
            prefix=detail['html_url']
            if not signals or any(not CLAIM.search(s.get('text','')) or not (s.get('url') == prefix or s.get('url','').startswith(prefix+'#issuecomment-')) for s in signals):
                raise ValueError('Unsupported projected claim evidence')
            value=result('review_pending',reason);value['claim_signals']=signals
            return value
        if reason == 'linked_open_pr':
            if not timeline or timeline.get('complete') is not True or not fresh(timeline,now):
                return result('unverified','projected_pr_timeline_unverified')
            refs={((e.get('source') or {}).get('issue') or {}).get('pull_request',{}).get('url')
                  for page in timeline['pages'] for e in page if e.get('event') == 'cross-referenced'}
            opened=[prs[url].get('html_url',url) for url in refs if url in prs and prs[url].get('state') == 'open' and fresh(prs[url],now)]
            if not opened:
                raise ValueError('Unsupported projected open PR evidence')
            value=result('excluded',reason);value['open_pr_references']=sorted(set(opened))
            return value
        raise ValueError('Unknown projected negative review')
    if detail.get('body_omitted'):
        return result('unverified','body_evidence_omitted')
    if not str(detail.get('body') or '').strip():
        return result('review_pending', 'empty_body')
    record=normalize_github_issue(detail)
    record.update(source_system='github_rest',github_verified_at=observed,has_linked_open_pr=False,
                  is_pull_request=False)
    # Only preliminary eligibility here; complete timeline/PR checks remain mandatory below.
    prelim=strict_eligibility(record,timeline_complete=True)
    if prelim.eligibility != 'eligible':
        return result('review_pending', 'base_policy:' + ','.join(prelim.reasons))
    if not timeline or timeline.get('complete') is not True or not fresh(timeline, now):
        return result('unverified', 'timeline_incomplete_or_stale')
    events=[e for page in timeline['pages'] for e in page]
    open_prs=[]
    for event in events:
        issue=(event.get('source') or {}).get('issue') or {}
        if event.get('event') != 'cross-referenced' or 'pull_request' not in issue:
            continue
        pr_url=issue['pull_request'].get('url')
        pr=prs.get(pr_url)
        if not pr or pr.get('state') not in {'open','closed'} or not fresh(pr, now):
            return result('unverified', 'referenced_pr_unverified_or_stale')
        if pr['state'] == 'open':
            open_prs.append(pr.get('html_url',pr_url))
    if cleared:
        source_texts=dict([(detail['html_url'],detail.get('body') or '')]+
                          [(e.get('html_url'),e.get('body') or '') for e in events if e.get('event')=='commented'])
        for ref in resolution.get('additional_pr_references',[]):
            text=source_texts.get(ref.get('source_url'))
            if text is None or ref.get('text_sha256')!=hashlib.sha256(text.encode()).hexdigest() or ref.get('html_url','') not in text:
                return result('unverified','context_pr_reference_not_bound')
            match=re.fullmatch(r'https://github\.com/([^/]+/[^/]+)/pull/(\d+)',ref['html_url'])
            if not match:return result('unverified','context_pr_reference_not_bound')
            pr_url=f'https://api.github.com/repos/{match[1]}/pulls/{match[2]}'
            pr=prs.get(pr_url)
            if not pr or pr.get('state') not in {'open','closed'} or not fresh(pr,now):
                return result('unverified','referenced_pr_unverified_or_stale')
            if pr['state']=='open':open_prs.append(pr.get('html_url',pr_url))
    if open_prs:
        value=result('excluded','linked_open_pr')
        value['open_pr_references']=sorted(set(open_prs))
        return value
    signals=[]
    texts=[(detail.get('body',''),detail['html_url'])]
    texts.extend((e.get('body') or '',e.get('html_url')) for e in events if e.get('event') == 'commented')
    for text,url in texts:
        if cleared and resolution.get('ignored_texts',{}).get(url)==hashlib.sha256(text.encode('utf-8')).hexdigest():
            continue
        reviewed=NON_CLAIM_REVIEW.get(url)
        if reviewed and hashlib.sha256(text.encode('utf-8')).hexdigest() == reviewed['text_sha256']:
            continue
        match=CLAIM.search(text)
        if match:
            signals.append(dict(url=url,text=text[max(0,match.start()-80):match.end()+180]))
    if signals:
        value=result('review_pending','claim_or_work_in_progress_text')
        value['claim_signals']=signals
        return value
    return result('availability_passed','current_unassigned_no_observed_claim_or_open_pr')


def absent_from_latest_inventory(key,detail,envelope,inventory):
    return (inventory.get('complete') is True and detail.get('state') == 'open'
            and not detail.get('assignees')
            and envelope.get('method') != 'github_current_open_unassigned_inventory'
            and key not in inventory.get('source_keys_present',[])
            and envelope['observed_at'] < inventory['observed_at'])


def materialize(directory):
    evidence=json.loads(gzip.decompress((directory/'connector_evidence.json.gz').read_bytes()))
    rows=json.loads((directory/'source_issues.json').read_text(encoding='utf-8'))
    now=datetime.now(timezone.utc)
    details, times, decisions, failures={}, {}, [], []
    for row in rows:
        key=f"{row['repo_name']}#{row['issue_number']}"
        lookup=key.casefold()
        envelope=evidence['details'].get(lookup)
        metadata=evidence['repositories'][row['repo_name']]
        if envelope:
            d=envelope['payload']
            inventory=evidence.get('inventory_coverage',{}).get(row['repo_name'],{})
            missing_from_new_snapshot=absent_from_latest_inventory(lookup,d,envelope,inventory)
            if envelope.get('transferred'):
                if str(d.get('id')) != str(row['issue_id']):
                    raise ValueError('A transfer cannot bypass the global identity check')
                reason=('closed' if d.get('state') == 'closed' else 'assigned' if d.get('assignees') else 'locked' if d.get('locked') else 'transferred_repository_requires_mapping')
                decision=dict(decision='excluded' if reason in {'closed','assigned','locked'} else 'review_pending',
                              reason=reason,open_pr_references=[],claim_signals=[],current_url=d.get('html_url'))
            elif missing_from_new_snapshot:
                decision=dict(decision='unverified',reason='not_in_latest_open_unassigned_snapshot',
                              open_pr_references=[],claim_signals=[],availability_absence_observed_at=inventory['observed_at'])
            else:
                decision=assess(row,d,envelope['observed_at'],metadata['payload'],metadata['observed_at'],
                                evidence['timelines'].get(lookup),evidence['pull_requests'],now=now,
                                projected_rejection=envelope.get('projected_rejection'),
                                resolution=evidence.get('context_resolutions',{}).get(lookup))
            if not envelope.get('transferred'):
                details[key]=d
            times[key]=envelope['observed_at']
        else:
            failure=evidence['failures'].get(lookup,dict(code='NOT_CHECKED',observed_at=evidence['observed_end']))
            failures.append(dict(key=key,**{k:v for k,v in failure.items() if k != 'key'}))
            decision=dict(decision='excluded' if failure['code']=='CONFIRMED_DELETED' else 'unverified',
                          reason=failure['code'],open_pr_references=[],claim_signals=[],read_failure=failure)
        decisions.append(dict(key=key,repository=row['repo_name'],number=int(row['issue_number']),
                              teacher_issue_id=str(row['issue_id']),github_issue_id=str(envelope['payload']['id']) if envelope else None,
                              url=f"https://github.com/{row['repo_name']}/issues/{row['issue_number']}",**decision))
    pack=dict(schema_version='teacher-github-review-v1',source_batch=directory.name,
              observed_start=evidence['observed_start'],observed_end=evidence['observed_end'],source_issues=rows,
              repository_details={k:v['payload'] for k,v in evidence['repositories'].items()},issue_details=details,
              repository_observed_at={k:v['observed_at'] for k,v in evidence['repositories'].items()},
              detail_observed_at=times,decisions=decisions,detail_failures=failures,
              timelines={f"{r['repo_name']}#{r['issue_number']}":evidence['timelines'][f"{r['repo_name']}#{r['issue_number']}".casefold()]
                         for r in rows if f"{r['repo_name']}#{r['issue_number']}".casefold() in evidence['timelines']},
              pull_requests=evidence['pull_requests'],listing_audit=evidence['listing_audit'],frozen=False,
              context_resolutions=evidence.get('context_resolutions',{}),
              discovery_summary=dict(total=len(rows),details_matched=len(details),
                                     detail_reads_without_matched_payload=len(failures),
                                     unverified_details=sum(d['decision']=='unverified' for d in decisions),
                                     all_source_rows_audited=True,
                                     all_specific_github_states_verified=not any(d['decision']=='unverified' for d in decisions),
                                     not_checked=sum(f.get('code')=='NOT_CHECKED' for f in failures),
                                     inventory_compared_not_individually_state_verified=sum(f.get('code','').startswith('NOT_IN_') for f in failures)),
              retained_fields_note='Projected GET evidence: rejected bodies/comments may be omitted; full passing candidate bodies/comments retained. Negative claim evidence retains quoted spans and source URLs.',
              inventory_coverage=evidence.get('inventory_coverage',{}),
              collection_scope=evidence.get('incremental_scope',{}),
              scope='Every teacher identity compared; all specific GitHub states are not claimed verified. Absence from an open/unassigned inventory is not treated as proof of closure or deletion.')
    raw=json.dumps(pack,ensure_ascii=False).encode('utf-8')
    with (directory/'review_pack.base64').open('x',encoding='ascii') as stream:
        stream.write(base64.b64encode(gzip.compress(raw)).decode('ascii'))
    report=build(directory)
    return {k:report[k] for k in ['source_issues','counts','reasons','candidate_repositories','timelines_checked','referenced_prs_checked','sqlite_integrity']}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    print(json.dumps(materialize(p.parse_args().directory),ensure_ascii=False,indent=2))
