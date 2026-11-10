import json
from pathlib import Path
import tempfile
import unittest

from oss_mentor.gated_scoring import content_hash

import importlib.util


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_request_inventory_graphql",
    ROOT / "scripts/midterm_d/verify_request_inventory_graphql.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class VerifyRequestInventoryGraphQLTests(unittest.TestCase):
    def test_request_bindings_are_unique_and_content_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "request.json"
            candidate = {
                "task_candidate_id": 7,
                "repository": "example/demo",
                "issue_number": 42,
                "title": "Fix crash",
                "body_text": "Reproduce the crash and return an empty list.",
            }
            request = {
                "feature_schema": "coder-gated-128+16-v1",
                "candidates": [candidate],
                "profile": {},
            }
            path.write_text(json.dumps(request), encoding="utf-8")
            _, bindings = MODULE._request_bindings(path)
        self.assertEqual(
            {
                "example/demo#42": {
                    "repository": "example/demo",
                    "issue_number": 42,
                    "content_hash": content_hash(candidate),
                    "task_candidate_id": 7,
                }
            },
            bindings,
        )

    def test_duplicate_request_candidate_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "request.json"
            candidate = {
                "task_candidate_id": 7,
                "repository": "example/demo",
                "issue_number": 42,
                "title": "Fix crash",
                "body_text": "Reproduce the crash and return an empty list.",
            }
            path.write_text(
                json.dumps({
                    "feature_schema": "coder-gated-128+16-v1",
                    "candidates": [candidate, candidate],
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate request candidate"):
                MODULE._request_bindings(path)

    def test_graphql_batch_contains_required_availability_fields(self):
        query = MODULE._graphql_query([
            {
                "repository": "example/demo",
                "issue_number": 42,
            }
        ])
        self.assertIn('q0: repository(owner:"example",name:"demo")', query)
        self.assertIn("issue(number:42)", query)
        self.assertIn("timelineItems(first:100", query)
        self.assertIn("CrossReferencedEvent", query)
        self.assertIn("assignees(first:1)", query)

    def test_availability_rejects_assigned_linked_changed_and_inactive(self):
        title = "Fix crash"
        body = "Reproduce the crash and return an empty list."
        expected = content_hash({"title": title, "body_text": body})
        issue = {
            "state": "OPEN",
            "locked": False,
            "assignees": {"totalCount": 0},
            "labels": {"pageInfo": {"hasNextPage": False}, "nodes": [{"name": "bug"}]},
            "timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            "title": title,
            "body": body,
        }
        available, reasons, eligibility = MODULE._availability(
            expected_content_hash=expected,
            repository={"isArchived": False, "isDisabled": False},
            issue=issue,
            timeline_errors=False,
        )
        self.assertEqual(("available", (), "eligible"), (available, reasons, eligibility))

        assigned = {**issue, "assignees": {"totalCount": 1}}
        self.assertEqual(
            ("assigned", ("issue_assigned",), "temporarily_ineligible"),
            MODULE._availability(
                expected_content_hash=expected,
                repository={"isArchived": False, "isDisabled": False},
                issue=assigned,
                timeline_errors=False,
            ),
        )

        linked = {
            **issue,
            "timelineItems": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [{"source": {"state": "OPEN", "url": "https://github.com/example/demo/pull/1"}}],
            },
        }
        self.assertEqual(
            ("linked_open_pr", ("linked_open_pr",), "temporarily_ineligible"),
            MODULE._availability(
                expected_content_hash=expected,
                repository={"isArchived": False, "isDisabled": False},
                issue=linked,
                timeline_errors=False,
            ),
        )

        changed = {**issue, "body": body + " Changed after handoff."}
        self.assertEqual(
            (
                "temporarily_unverified",
                ("content_changed_since_handoff",),
                "unknown",
            ),
            MODULE._availability(
                expected_content_hash=expected,
                repository={"isArchived": False, "isDisabled": False},
                issue=changed,
                timeline_errors=False,
            ),
        )

        self.assertEqual(
            ("repository_inactive", ("repository_inactive",), "excluded"),
            MODULE._availability(
                expected_content_hash=expected,
                repository={"isArchived": True, "isDisabled": False},
                issue=issue,
                timeline_errors=False,
            ),
        )


if __name__ == "__main__":
    unittest.main()
