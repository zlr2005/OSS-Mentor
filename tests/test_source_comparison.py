from __future__ import annotations

import unittest

from oss_mentor.collector.source_comparison import (
    normalize_ecosystems_issue,
    normalize_github_issue,
)


class SourceComparisonTests(unittest.TestCase):
    def test_normalized_common_fields_agree(self) -> None:
        github = normalize_github_issue(
            {
                "id": 123,
                "number": 7,
                "html_url": "https://github.com/example/demo/issues/7",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "author_association": "NONE",
                "title": "Example",
                "labels": [{"name": "good first issue"}],
                "assignees": [],
                "comments": 2,
                "state": "open",
                "body": "Details",
            }
        )
        ecosystems = normalize_ecosystems_issue(
            {
                "uuid": 123,
                "number": 7,
                "html_url": "https://github.com/example/demo/issues/7",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "author_association": "NONE",
                "title": "Example",
                "labels": ["good first issue"],
                "assignees": [],
                "comments_count": 2,
                "state": "open",
            }
        )

        for field in (
            "issue_number",
            "html_url",
            "created_at",
            "last_activity_at",
            "author_association",
            "title",
            "labels",
            "assignment_state",
            "comment_count",
            "state",
        ):
            self.assertEqual(github[field], ecosystems[field])
        self.assertIsNone(ecosystems["github_issue_id"])
        self.assertEqual("Details", github["body_text"])
        self.assertNotIn("body_text", ecosystems)


if __name__ == "__main__":
    unittest.main()
