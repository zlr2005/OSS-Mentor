import hashlib
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).parents[2]/'scripts/midterm_a'))
from audit_historical_candidate_evidence import check_snapshot,canonical
from assemble_public_text_dataset import choose_controls,label_verified


def sample():
    issue=dict(createdAt='2024-01-01T00:00:00Z',lastEditedAt=None,title='old',body='body',state='OPEN',
        timelineItems=dict(pageInfo=dict(hasNextPage=False),nodes=[]))
    candidate=dict(available_from='2024-02-01T00:00:00Z',title='old',body_text='body',
        content_sha256=hashlib.sha256(canonical(dict(title='old',body='body'))).hexdigest())
    return candidate,issue


def test_exact_text_and_open_state():check_snapshot(*sample())


def test_title_replay_after_cutoff():
    c,i=sample();i['title']='new'
    i['timelineItems']['nodes']=[dict(__typename='RenamedTitleEvent',createdAt='2024-03-01T00:00:00Z',previousTitle='old',currentTitle='new')]
    check_snapshot(c,i)


def test_body_edit_after_cutoff_rejected():
    c,i=sample();i['lastEditedAt']='2024-03-01T00:00:00Z'
    with pytest.raises(ValueError,match='body_edited'):check_snapshot(c,i)


def test_closed_at_exact_cutoff_rejected():
    c,i=sample();i['state']='CLOSED';i['timelineItems']['nodes']=[dict(__typename='ClosedEvent',createdAt=c['available_from'])]
    with pytest.raises(ValueError,match='closed_at_cutoff'):check_snapshot(c,i)


def test_incomplete_timeline_rejected():
    c,i=sample();i['timelineItems']['pageInfo']['hasNextPage']=True
    with pytest.raises(ValueError,match='incomplete'):check_snapshot(c,i)


def test_controls_unique_exclude_known_positive_and_deterministic():
    pool=['o/r#'+str(i) for i in range(1,41)]
    a=choose_controls(pool,'o/r#1',{'o/r#2'},'outcome')
    assert len(a)==len(set(a))==20
    assert not {'o/r#1','o/r#2'}&set(a)
    assert a==choose_controls(list(reversed(pool)),'o/r#1',{'o/r#2'},'outcome')


def test_label_requires_exact_author_and_closing_relation(tmp_path):
    label=dict(evidence_ref='api_cache/x.json',source_url='https://github.com/o/r/pull/2',issue_key='o/r#1',interaction_at='2024-02-02T00:00:00Z',outcome_at='2024-02-03T00:00:01Z')
    pr=dict(url=label['source_url'],merged=True,author=dict(databaseId=1),createdAt=label['interaction_at'],mergedAt='2024-02-03T00:00:00Z',
        closingIssuesReferences=dict(nodes=[dict(repository=dict(nameWithOwner='o/r'),number=1,timelineItems=dict(nodes=[dict(__typename='ClosedEvent',createdAt=label['outcome_at'],closer=dict(url=label['source_url']))]))]))
    p=tmp_path/'data/midterm/working/advanced_adapter_backfill_2024_2026_v1/api_cache/x.json';p.parent.mkdir(parents=True)
    p.write_text(json.dumps(dict(payload=pr,payload_sha256=hashlib.sha256(canonical(pr)).hexdigest())))
    assert label_verified(label,1,tmp_path,{})
    assert not label_verified(label,2,tmp_path,{})
