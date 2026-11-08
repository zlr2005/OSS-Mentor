import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('publication', Path(__file__).parents[2] / 'scripts/midterm_a/audit_v5_publication.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_merge_requires_flag_and_exact_sha():
    sha, other = 'a' * 40, 'b' * 40
    assert module.public_commits(dict(head_sha=sha, merge_commit_sha=other, merged=0)) == [sha]
    assert module.public_commits(dict(head_sha=sha, merge_commit_sha=other, merged=1)) == [sha, other]
    assert module.public_commits(dict(head_sha='bad', merge_commit_sha=other, merged=0)) == []


def test_cutoff_strict_and_timezone_equivalent():
    assert module.time('2025-01-01 00:00:00') == module.time('2025-01-01T08:00:00+08:00')
    cutoff = module.time('2025-01-01T00:00:00Z')
    assert not cutoff < cutoff
    assert module.time('2024-12-31T23:59:59Z') < cutoff
