import importlib.util
import json
import hashlib
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('patch_supply', Path(__file__).parents[2] / 'scripts/midterm_a/audit_v5_patch_supply.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(tmp_path, files):
    (tmp_path / 'content').write_text('original', encoding='utf-8')
    for f in files:
        f['sha'] = hashlib.sha1(b'blob 8\0original').hexdigest()
    (tmp_path / 'evidence').write_text(json.dumps({'payload': {'sha': 'abc', 'files': files}}))
    return dict(evidence_path='evidence', content_path='content', path='a.py',
                content_sha256=module.digest(tmp_path / 'content'))


def test_git_blob_mismatch(tmp_path):
    doc = fixture(tmp_path, [{'filename': 'a.py', 'patch': '+correct'}])
    e = json.loads((tmp_path / 'evidence').read_text())
    e['payload']['files'][0]['sha'] = '0' * 40
    (tmp_path / 'evidence').write_text(json.dumps(e))
    with pytest.raises(ValueError, match='git_blob_mismatch'):
        module.extract(doc, tmp_path)


def test_zero_change_is_not_a_collection_failure(tmp_path):
    doc = fixture(tmp_path, [{'filename': 'a.py', 'changes': 0}])
    with pytest.raises(ValueError, match='zero_change_without_patch'):
        module.extract(doc, tmp_path)


def test_exact_file_and_unverified_publication(tmp_path):
    doc = fixture(tmp_path, [{'filename': 'other.py', 'patch': 'wrong'}, {'filename': 'a.py', 'patch': '+correct'}])
    result = module.extract(doc, tmp_path)
    assert result['patch'] == '+correct'
    assert result['historical_publication_status'] == 'unverified'


def test_hash_mismatch_rejected(tmp_path):
    doc = fixture(tmp_path, [{'filename': 'a.py', 'patch': '+correct'}])
    (tmp_path / 'content').write_text('changed')
    with pytest.raises(ValueError, match='hash_mismatch'):
        module.extract(doc, tmp_path)


@pytest.mark.parametrize('files,reason', [([], 'ambiguous_file'),
    ([{'filename': 'a.py'}], 'missing_patch'),
    ([{'filename': 'a.py', 'patch': '+one'}, {'filename': 'a.py', 'patch': '+two'}], 'ambiguous_file')])
def test_missing_and_ambiguous_evidence(tmp_path, files, reason):
    with pytest.raises(ValueError, match=reason):
        module.extract(fixture(tmp_path, files), tmp_path)
