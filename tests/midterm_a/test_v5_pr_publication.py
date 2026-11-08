import copy
import importlib.util
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / 'scripts/midterm_a'))
spec = importlib.util.spec_from_file_location('pr_publication', Path(__file__).parents[2] / 'scripts/midterm_a/v5_pr_publication.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sample():
    check = dict(repository='org/repo', commit='a'*40, cutoff='2025-02-01T00:00:00Z')
    link = dict(pr=dict(number=12, baseRepository=dict(nameWithOwner='Org/Repo'),
                       mergeCommit=dict(oid='a'*40), mergedAt='2025-01-01T00:00:00Z'))
    event = dict(repo_name='org/repo', number=12, action='closed', event_at='2025-01-02 00:00:00')
    return check, link, event


def test_composite_proof():
    assert module.qualifies(*sample())


@pytest.mark.parametrize('field,value', [('repo_name','other/repo'), ('number',13), ('action','opened'),
    ('event_at','2024-12-31 23:59:59'), ('event_at','2025-02-01 00:00:00')])
def test_wrong_event_rejected(field, value):
    c, l, e = sample()
    e[field] = value
    assert not module.qualifies(c, l, e)


def test_wrong_merge_and_missing_time_rejected():
    c, l, e = sample()
    wrong = copy.deepcopy(l)
    wrong['pr']['mergeCommit']['oid'] = 'b'*40
    assert not module.qualifies(c, wrong, e)
    l['pr']['mergedAt'] = None
    assert not module.qualifies(c, l, e)
