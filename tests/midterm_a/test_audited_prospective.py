import importlib.util
from pathlib import Path
from datetime import datetime,timedelta,timezone
import sys
import pytest

sys.path.insert(0,str(Path(__file__).parents[2]/'scripts/midterm_a'))
from build_audited_curve_plan import classify,nested_users
from check_prospective_readiness import maturity_ready
from audit_v5_publication import source_identity


def test_whole_profile_admission_not_partial_rewrite():
    plans=[dict(profile_version='a',split='train',expected_files=2),dict(profile_version='b',split='validation',expected_files=1)]
    rows=[dict(profile_version='a',document_key='x',publication_passed=True),dict(profile_version='a',document_key='y',publication_passed=False),dict(profile_version='b',document_key='z',publication_passed=True)]
    admitted,quarantine=classify(plans,rows)
    assert [r['profile_version'] for r in admitted]==['b']
    assert quarantine[0]['profile_version']=='a'


def test_duplicate_evidence_does_not_fill_missing_file():
    p=[dict(profile_version='a',split='train',expected_files=2)]
    rows=[dict(profile_version='a',document_key='x',publication_passed=True)]*2
    assert not classify(p,rows)[0]


def test_nested_sampling_is_stable_and_developer_grouped():
    order=nested_users([1,2,3,4,5,6,7,8],20261102)
    assert order==nested_users(list(reversed(range(1,9))),20261102)
    assert set(order[:2])<set(order[:4])<set(order)


def test_no_lock_never_matures():
    assert not maturity_ready(None,datetime.now(timezone.utc))


def test_full90days_required():
    cutoff=datetime(2026,11,7,tzinfo=timezone.utc)
    assert not maturity_ready(cutoff.isoformat(),cutoff+timedelta(days=90,seconds=-1))
    assert maturity_ready(cutoff.isoformat(),cutoff+timedelta(days=90))


def test_projected_source_schema_and_exact_sha():
    src=dict(schema_version='b-sanitized-source-projection-v1',status_code=200,fields=dict(sha='a'*40),source_url='https://api.github.com/repos/Org/Repo/commits/'+'a'*40+'?per_page=100')
    assert source_identity(src)==('org/repo','a'*40)
    src['fields']['sha']='b'*40
    with pytest.raises(ValueError):source_identity(src)
