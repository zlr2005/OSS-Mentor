from __future__ import annotations

import json
import unittest

from oss_mentor.developer_profiles import ALLOWED_TASK_TYPES
from oss_mentor.task_features import (
    TASK_FEATURE_VERSION,
    extract_task_features,
    infer_skill_requirements,
)


class TaskFeatureTests(unittest.TestCase):
    @staticmethod
    def _record(
        *,
        title: str,
        body_text: str = "",
        labels: list[str] | None = None,
        candidate_eligibility: str = "eligible",
        comment_count: int = 0,
        primary_language: str = "Python",
    ) -> dict[str, object]:
        return {
            "title": title,
            "body_text": body_text,
            "labels": labels or [],
            "comment_count": comment_count,
            "candidate_eligibility": candidate_eligibility,
            "primary_language": primary_language,
        }

    def test_clear_first_contribution_bug_scores_for_newcomer(self) -> None:
        features = extract_task_features(
            self._record(
                title="Bug: wrong color returned by parser",
                body_text=(
                    "## Steps to reproduce\n```python\nparse('red')\n```\n"
                    "## Expected behavior\nThe parser should return red.\n"
                    "## Acceptance criteria\n- [ ] Add a regression test in `tests/test_color.py`."
                ),
                labels=["bug", "first-contribution"],
                comment_count=2,
            )
        )

        self.assertTrue(features.has_reproduction_steps)
        self.assertTrue(features.has_expected_behavior)
        self.assertTrue(features.has_acceptance_criteria)
        self.assertTrue(features.has_affected_module_hint)
        self.assertIn("bug_fix", features.task_types)
        self.assertIn("testing", features.task_types)
        self.assertGreaterEqual(features.text_clarity_score, 80)
        self.assertGreater(features.newcomer_score, 70)
        self.assertEqual(TASK_FEATURE_VERSION, features.task_feature_version)

    def test_real_world_feature_and_bug_phrases_are_classified_with_evidence(
        self,
    ) -> None:
        feature = extract_task_features(
            {
                "title": "Allow batch based metrics calculation",
                "body_text": "",
                "labels": ["kind/feature", "help wanted"],
                "comment_count": 0,
                "candidate_eligibility": "eligible",
            }
        )
        bug = extract_task_features(
            {
                "title": "Export dialog doesn't respect iOS safe area",
                "body_text": "",
                "labels": ["good first issue"],
                "comment_count": 0,
                "candidate_eligibility": "eligible",
            }
        )

        self.assertIn("feature", feature.task_types)
        self.assertIn("bug_fix", bug.task_types)
        self.assertEqual(
            "label",
            feature.feature_evidence["task_type_evidence"]["feature"][0]["source"],
        )
        self.assertEqual(
            "title",
            bug.feature_evidence["task_type_evidence"]["bug_fix"][0]["source"],
        )

    def test_ineligible_candidate_gets_zero_track_scores(self) -> None:
        features = extract_task_features(
            self._record(
                title="Core architecture refactor",
                body_text="Refactor the core architecture and optimize performance.",
                labels=["core", "refactor"],
                comment_count=20,
                candidate_eligibility="temporarily_ineligible",
            )
        )
        self.assertEqual(0.0, features.newcomer_score)
        self.assertEqual(0.0, features.growth_value_score)
        self.assertEqual(3, features.estimated_code_difficulty)
        self.assertEqual(("refactor",), features.task_types)
        self.assertTrue(features.feature_evidence["auxiliary_signals"]["performance"])

    def test_title_platform_takes_priority_over_body_comparison(self) -> None:
        record = self._record(
            title="Bug in the macOS backend",
            body_text="The behavior differs from Linux.",
            labels=["GUI: MacOSX", "first-contribution"],
        )
        features = extract_task_features(record)
        requirements = infer_skill_requirements(record, features)
        platforms = {
            item.skill_name
            for item in requirements
            if item.skill_name.startswith("platform:")
        }
        self.assertEqual({"platform:macos"}, platforms)

    def test_exact_label_aliases_cover_all_public_types(self) -> None:
        cases = {
            "type/bug": "bug_fix",
            "module:test-suite": "testing",
            "type/documentation": "documentation",
            "kind/feature": "feature",
            "kind/cleanup": "refactor",
            "dependencies": "build_tooling",
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                features = extract_task_features(
                    self._record(title="Neutral task title", labels=[label])
                )
                self.assertEqual((expected,), features.task_types)
                evidence = features.feature_evidence["task_type_evidence"][expected]
                self.assertEqual("label", evidence[0]["source"])

    def test_label_evidence_preserves_raw_and_normalized_values(self) -> None:
        features = extract_task_features(
            self._record(title="Neutral task", labels=[" Type/Enhancement "])
        )
        self.assertEqual(("feature",), features.task_types)
        item = features.feature_evidence["task_type_evidence"]["feature"][0]
        self.assertEqual("Type/Enhancement", item["matched_value"])
        self.assertEqual("type enhancement", item["normalized_value"])

    def test_docker_does_not_match_documentation(self) -> None:
        features = extract_task_features(
            self._record(
                title="Docker support for local development",
                labels=["docker"],
            )
        )
        self.assertNotIn("documentation", features.task_types)

    def test_cli_does_not_match_ci_build_tooling(self) -> None:
        features = extract_task_features(
            self._record(title="Generate CLI for the REST API")
        )
        self.assertEqual(("feature",), features.task_types)
        self.assertNotIn("build_tooling", features.task_types)

    def test_ci_workflow_is_build_tooling(self) -> None:
        features = extract_task_features(
            self._record(title="Migrate CI workflow to GitHub Actions")
        )
        self.assertEqual(("build_tooling",), features.task_types)

    def test_generic_add_feature_is_suppressed_for_specific_test_work(self) -> None:
        features = extract_task_features(
            self._record(title="Add regression tests for the parser")
        )
        self.assertEqual(("testing",), features.task_types)
        rejected = features.feature_evidence["rejected_task_type_evidence"]
        self.assertTrue(
            any(
                item["rule_id"] == "feature.title.capability_action"
                and item["reason"] == "suppressed_by_specific_title_target"
                for item in rejected
            )
        )

    def test_generic_add_feature_is_suppressed_for_documentation(self) -> None:
        features = extract_task_features(
            self._record(title="Add documentation for custom parsers")
        )
        self.assertEqual(("documentation",), features.task_types)

    def test_generic_add_feature_is_suppressed_for_dependency_update(self) -> None:
        features = extract_task_features(
            self._record(title="Upgrade UI libraries to address vulnerabilities")
        )
        self.assertEqual(("build_tooling",), features.task_types)

    def test_dependency_vulnerability_stays_build_tooling(self) -> None:
        features = extract_task_features(
            self._record(
                title="Upgrade UI libraries to address vulnerabilities",
                labels=["dependencies", "vulnerability"],
            )
        )
        self.assertEqual(("build_tooling",), features.task_types)

    def test_build_specific_failure_does_not_also_create_bug(self) -> None:
        features = extract_task_features(self._record(title="typecheck fail"))
        self.assertEqual(("build_tooling",), features.task_types)

    def test_deprecation_misleading_alias_stays_refactor(self) -> None:
        features = extract_task_features(
            self._record(title="Deprecate misleading CachedAccessor alias")
        )
        self.assertEqual(("refactor",), features.task_types)

    def test_library_mode_does_not_match_build_tooling(self) -> None:
        features = extract_task_features(
            self._record(title="Add an option for library mode")
        )
        self.assertEqual(("feature",), features.task_types)
        self.assertNotIn("build_tooling", features.task_types)

    def test_negative_clause_inside_add_task_does_not_create_bug(self) -> None:
        features = extract_task_features(
            self._record(
                title="Add a linter for images not included in the build manifest",
                labels=["ci"],
            )
        )
        self.assertEqual(("build_tooling",), features.task_types)
        self.assertNotIn("bug_fix", features.task_types)
        self.assertTrue(
            any(
                item["reason"] == "suppressed_inside_explicit_non_bug_task"
                for item in features.feature_evidence["rejected_task_type_evidence"]
            )
        )

    def test_documentation_body_action_does_not_also_create_feature(self) -> None:
        features = extract_task_features(
            self._record(
                title="Documentation task",
                body_text="We want to add the documentation for the cache option.",
            )
        )
        self.assertEqual(("documentation",), features.task_types)

    def test_below_threshold_evidence_is_rejected_but_scored(self) -> None:
        features = extract_task_features(
            self._record(title="Neutral task", labels=["integration"])
        )
        self.assertEqual(("other",), features.task_types)
        self.assertEqual(2.0, features.feature_evidence["task_type_scores"]["feature"])
        self.assertTrue(
            any(
                item["task_type"] == "feature"
                and item["reason"] == "below_acceptance_threshold"
                for item in features.feature_evidence["rejected_task_type_evidence"]
            )
        )

    def test_feature_capability_title_is_recognized(self) -> None:
        for title in (
            "Support DuckLake tables",
            "Allow positional parameters in SQL",
            "Introduce reason codes in HTTP requests",
            "Generate CLI for the REST API",
            "New lint for disallowed trait usage",
        ):
            with self.subTest(title=title):
                features = extract_task_features(self._record(title=title))
                self.assertIn("feature", features.task_types)

    def test_bug_symptom_titles_are_recognized(self) -> None:
        for title in (
            "Controller crashing after duplicate configuration",
            "Metadata endpoint does not return all segments",
            "NPE around decoder",
            "Deadlock at process shutdown",
            "Query 500 when group by is missing",
            "Lag with many elements",
        ):
            with self.subTest(title=title):
                features = extract_task_features(self._record(title=title))
                self.assertIn("bug_fix", features.task_types)

    def test_missing_value_support_is_not_mistaken_for_bug(self) -> None:
        features = extract_task_features(
            self._record(title="Add missing-value support to the parser")
        )
        self.assertEqual(("feature",), features.task_types)

    def test_conservative_body_rules_accept_explicit_task_actions(self) -> None:
        cases = (
            (
                "Neutral title",
                "This is a feature request. Users need to add custom transports.",
                "feature",
            ),
            (
                "Neutral title",
                "The issue causes a crash whenever the input is empty.",
                "bug_fix",
            ),
            (
                "Neutral title",
                "Please add regression tests for the empty-input path.",
                "testing",
            ),
            (
                "Neutral title",
                "We need to update the documentation for this option.",
                "documentation",
            ),
            (
                "Neutral title",
                "Update the dependencies to their supported versions.",
                "build_tooling",
            ),
        )
        for title, body, expected in cases:
            with self.subTest(expected=expected):
                features = extract_task_features(
                    self._record(title=title, body_text=body)
                )
                self.assertIn(expected, features.task_types)

    def test_code_blocks_comments_and_links_do_not_create_task_types(self) -> None:
        features = extract_task_features(
            self._record(
                title="Question about configuration",
                body_text=(
                    "<!-- Add regression tests and update docs -->\n"
                    "```text\nrun tests; build docs; optimize performance\n```\n"
                    "See https://example.com/documentation/testing/build"
                ),
            )
        )
        self.assertEqual(("other",), features.task_types)

    def test_incidental_body_words_do_not_overclassify(self) -> None:
        features = extract_task_features(
            self._record(
                title="Support a custom transport",
                body_text=(
                    "Testing strategy: run the existing tests. "
                    "See documentation for build instructions."
                ),
            )
        )
        self.assertEqual(("feature",), features.task_types)

    def test_performance_regression_maps_to_bug_auxiliary_signal(self) -> None:
        features = extract_task_features(
            self._record(title="PERF: Performance regression in DataFrame apply")
        )
        self.assertEqual(("bug_fix",), features.task_types)
        self.assertTrue(features.feature_evidence["auxiliary_signals"]["performance"])
        self.assertNotIn("performance", features.task_types)

    def test_performance_optimization_maps_to_refactor_auxiliary_signal(self) -> None:
        features = extract_task_features(
            self._record(title="PERF: Skip full array allocation")
        )
        self.assertEqual(("refactor",), features.task_types)
        mapping_rules = {
            item["rule_id"]
            for item in features.feature_evidence["task_type_evidence"]["refactor"]
        }
        self.assertIn("performance.map.optimization_to_refactor", mapping_rules)

    def test_performance_capability_remains_feature_with_auxiliary_signal(self) -> None:
        features = extract_task_features(
            self._record(title="Add query cache to reduce latency")
        )
        self.assertEqual(("feature",), features.task_types)
        self.assertTrue(features.feature_evidence["auxiliary_signals"]["performance"])

    def test_benchmark_test_maps_to_testing_with_performance_signal(self) -> None:
        features = extract_task_features(
            self._record(title="Add benchmark test for query execution")
        )
        self.assertEqual(("testing",), features.task_types)
        self.assertTrue(features.feature_evidence["auxiliary_signals"]["performance"])

    def test_performance_tracker_remains_other(self) -> None:
        features = extract_task_features(
            self._record(
                title="Low precision training roadmap tracker",
                labels=["performance", "roadmap"],
            )
        )
        self.assertEqual(("other",), features.task_types)
        rejected = features.feature_evidence["rejected_task_type_evidence"]
        self.assertTrue(
            any(item["rule_id"] == "performance.map.ambiguous_tracker" for item in rejected)
        )

    def test_real_bug_regressions_remain_bug_fix(self) -> None:
        cases = (
            {
                "title": (
                    "No keepAliveTimeout for HTTP server after answering a POST "
                    "request synchronously"
                ),
            },
            {
                "title": (
                    "SQLModel columns are flagged when a database function is "
                    "called onto them"
                ),
            },
            {
                "title": (
                    '@pytest.fixture(scope="package") works like session if placed '
                    "in one file"
                ),
            },
            {
                "title": "--log-cli-level also increases verbosity level",
            },
            {
                "title": (
                    "`effect_orphan` thrown when `$effect` is called after `await`"
                ),
            },
            {
                "title": "Waterfall: Async functions always called sequentially",
            },
            {
                "title": "std-instead-of-core: core not in scope",
                "labels": ["I-suggestion-causes-error"],
            },
            {
                "title": (
                    'experimental.async: "Batch has scheduled roots" when a sibling '
                    "mutates shared state"
                ),
                "body_text": "The bug is real and reproducible. Svelte throws during flush.",
            },
        )
        for record in cases:
            with self.subTest(title=record["title"]):
                features = extract_task_features(self._record(**record))
                self.assertIn("bug_fix", features.task_types)
                self.assertNotEqual(("other",), features.task_types)

    def test_missing_capability_is_feature_but_regression_is_bug(self) -> None:
        missing = extract_task_features(
            self._record(title="Lakehouse connector doesn't support table functions")
        )
        regression = extract_task_features(
            self._record(title="Regression: connector no longer supports table functions")
        )

        self.assertEqual(("feature",), missing.task_types)
        self.assertIn("bug_fix", regression.task_types)
        self.assertNotIn("feature", regression.task_types)
        self.assertTrue(
            any(
                item["reason"] == "reclassified_as_missing_capability"
                for item in missing.feature_evidence["rejected_task_type_evidence"]
            )
        )

    def test_increase_semantics_are_disambiguated(self) -> None:
        coverage = extract_task_features(
            self._record(title="Increase code coverage for the parser")
        )
        latency = extract_task_features(
            self._record(title="Increase the latency of a query when ORDER BY is applied")
        )
        capability = extract_task_features(
            self._record(title="Increase supported image formats")
        )

        self.assertEqual(("testing",), coverage.task_types)
        self.assertEqual(("bug_fix",), latency.task_types)
        self.assertTrue(latency.feature_evidence["auxiliary_signals"]["performance"])
        self.assertEqual(("feature",), capability.task_types)

    def test_performance_intent_priority(self) -> None:
        cases = (
            (
                "Next.js development high memory usage",
                [],
                "bug_fix",
            ),
            (
                "PERF: Reduce array allocations in the hot path",
                [],
                "refactor",
            ),
            (
                "ENH: Add JIT compilation engine option",
                ["performance"],
                "feature",
            ),
            (
                "Cache max size",
                [],
                "feature",
            ),
            (
                "Warn that collecting a vector might retain high memory",
                ["A-lint"],
                "feature",
            ),
            (
                "Add benchmark test for query execution",
                [],
                "testing",
            ),
            (
                "Memory usage increases rapidly as the number of tests grows",
                ["type: performance"],
                "bug_fix",
            ),
        )
        for title, labels, expected in cases:
            with self.subTest(title=title):
                features = extract_task_features(
                    self._record(title=title, labels=labels)
                )
                self.assertEqual((expected,), features.task_types)
                self.assertTrue(
                    features.feature_evidence["auxiliary_signals"]["performance"]
                )

    def test_explicit_feature_requests_in_body_are_recognized(self) -> None:
        cases = (
            (
                "A new cluster policy",
                "I think we should support this feature internally.",
            ),
            (
                "Code blocks",
                "It would be wonderful if we have code blocks with syntax highlighting.",
            ),
            (
                "Bindable prop state",
                "There is no way to tell if a prop was passed using bind.",
            ),
        )
        for title, body_text in cases:
            with self.subTest(title=title):
                features = extract_task_features(
                    self._record(title=title, body_text=body_text)
                )
                self.assertEqual(("feature",), features.task_types)

    def test_warning_capability_and_security_exposure_are_disambiguated(self) -> None:
        warning = extract_task_features(
            self._record(
                title="Issue `state_referenced_locally` warnings for declaration tags"
            )
        )
        exposure = extract_task_features(
            self._record(
                title="Connector can access host file-system by default"
            )
        )
        confusing = extract_task_features(
            self._record(title="Confusing behavior of module handler")
        )

        self.assertEqual(("feature",), warning.task_types)
        self.assertEqual(("bug_fix",), exposure.task_types)
        self.assertEqual(("bug_fix",), confusing.task_types)

    def test_testing_requires_explicit_test_work(self) -> None:
        testing_titles = (
            "Add regression tests for parser failures",
            "Write unit tests for the cache",
            "Improve test coverage for the API",
            "Create fixture for database sessions",
        )
        bug_titles = (
            "Test reports each case twice",
            "Fixture is not discovered in a test class",
            "Test collection fails on Python 3.13",
        )

        for title in testing_titles:
            with self.subTest(title=title):
                features = extract_task_features(self._record(title=title))
                self.assertEqual(("testing",), features.task_types)

        for title in bug_titles:
            with self.subTest(title=title):
                features = extract_task_features(self._record(title=title))
                self.assertEqual(("bug_fix",), features.task_types)
                self.assertNotIn("testing", features.task_types)

    def test_non_actionable_trackers_and_discussions_stay_other(self) -> None:
        titles = (
            "Roadmap for stabilization of vm modules",
            "Performance roadmap tracker",
            "Umbrella issue for parser improvements",
            "Discussion: future cache architecture",
        )
        for title in titles:
            with self.subTest(title=title):
                features = extract_task_features(self._record(title=title))
                self.assertEqual(("other",), features.task_types)

        actionable = extract_task_features(
            self._record(title="[Discussion Proposal] Introduce a query result cache")
        )
        self.assertEqual(("feature",), actionable.task_types)

    def test_cjk_adjacent_npe_is_recognized(self) -> None:
        for title in ("调用时导致NPE", "解码器抛出NPE"):
            with self.subTest(title=title):
                features = extract_task_features(self._record(title=title))
                self.assertEqual(("bug_fix",), features.task_types)

    def test_stack_trace_in_support_question_does_not_create_bug(self) -> None:
        features = extract_task_features(
            self._record(
                title="How should the serialization allowlist be configured?",
                body_text=(
                    "I am asking how to configure this option.\n"
                    "Servlet.service() threw exception while processing the request.\n"
                    "Please see the following environment output."
                ),
                labels=["type/discussion"],
            )
        )
        self.assertEqual(("other",), features.task_types)

    def test_long_rfc_does_not_overclassify_incidental_sections(self) -> None:
        features = extract_task_features(
            self._record(
                title="[Feature] Query result cache",
                body_text=(
                    "## Testing strategy\nExisting tests will be run.\n"
                    "## Documentation\nSee the documentation link.\n"
                    "## Build\nThe normal build instructions apply.\n"
                    "## Performance\nPerformance considerations are discussed."
                ),
            )
        )
        self.assertEqual(("feature",), features.task_types)
        self.assertTrue(features.feature_evidence["auxiliary_signals"]["performance"])

    def test_plain_issue_template_without_task_content_stays_other(self) -> None:
        features = extract_task_features(
            self._record(
                title="Question about configuration",
                body_text=(
                    "### Steps to reproduce\n_No response_\n"
                    "### Expected Behavior\n_No response_\n"
                    "### Actual Behavior\n_No response_\n"
                    "- [x] I searched existing issues"
                ),
            )
        )
        self.assertEqual(("other",), features.task_types)

    def test_empty_body_uses_title_and_exact_label(self) -> None:
        title_features = extract_task_features(
            self._record(title="Controller crashing", body_text="")
        )
        label_features = extract_task_features(
            self._record(title="Neutral title", body_text="", labels=["kind/feature"])
        )
        self.assertEqual(("bug_fix",), title_features.task_types)
        self.assertEqual(("feature",), label_features.task_types)

    def test_output_contract_only_contains_public_types_or_other(self) -> None:
        records = (
            self._record(title="Support query caching"),
            self._record(title="Controller crashing"),
            self._record(title="Unclear request"),
            self._record(title="PERF: Reduce allocations"),
        )
        for record in records:
            features = extract_task_features(record)
            self.assertTrue(set(features.task_types) <= (ALLOWED_TASK_TYPES | {"other"}))
            if "other" in features.task_types:
                self.assertEqual(("other",), features.task_types)

    def test_evidence_is_structured_stable_and_json_serializable(self) -> None:
        record = self._record(
            title="Bug: parser crashes; add regression tests",
            labels=["type/bug"],
            body_text="Please add regression tests for the failure.",
        )
        first = extract_task_features(record)
        second = extract_task_features(record)
        self.assertEqual(first.feature_evidence, second.feature_evidence)
        json.dumps(first.feature_evidence, sort_keys=True)
        self.assertEqual(
            {
                "task_type_evidence",
                "task_type_scores",
                "auxiliary_signals",
                "rejected_task_type_evidence",
            },
            {
                "task_type_evidence",
                "task_type_scores",
                "auxiliary_signals",
                "rejected_task_type_evidence",
            }.intersection(first.feature_evidence),
        )
        for task_type, items in first.feature_evidence["task_type_evidence"].items():
            self.assertIn(task_type, ALLOWED_TASK_TYPES)
            self.assertGreaterEqual(first.feature_evidence["task_type_scores"][task_type], 3.0)
            for item in items:
                self.assertTrue(
                    {"source", "rule_id", "matched_value", "weight"} <= set(item)
                )
                if item["source"] == "label":
                    self.assertIn("normalized_value", item)
            keys = [
                (
                    item["source"],
                    item["rule_id"],
                    item["matched_value"],
                    item.get("normalized_value", ""),
                    item["weight"],
                )
                for item in items
            ]
            self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(tuple(sorted(first.task_types)), first.task_types)

    def test_skill_requirement_logic_uses_public_task_types_unchanged(self) -> None:
        record = self._record(
            title="Add query cache to reduce latency",
            primary_language="Java",
        )
        features = extract_task_features(record)
        requirements = infer_skill_requirements(record, features)
        names = {item.skill_name for item in requirements}
        self.assertIn("Java", names)
        self.assertIn("feature", names)
        self.assertNotIn("performance", names)


if __name__ == "__main__":
    unittest.main()