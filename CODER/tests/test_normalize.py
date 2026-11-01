from __future__ import annotations

from coder_issue.data.normalize import (
    developer_key,
    issue_key,
    normalize_labels,
    repository_key,
)


def test_entity_keys_are_casefolded_and_stable() -> None:
    assert repository_key("OpenMined", "PySyft") == "openmined/pysyft"
    assert issue_key("OPENMINED", "pysyft", "19") == "openmined/pysyft#19"
    assert developer_key(" Alice ") == "alice"


def test_labels_are_deduplicated_deterministically() -> None:
    labels, invalid = normalize_labels(["Bug", "bug", " Help Wanted ", None])
    assert labels == ["Bug", "Help Wanted"]
    assert invalid == 1
