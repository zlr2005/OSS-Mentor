from __future__ import annotations

import json
import unittest

from oss_mentor.developer_profiles import ALLOWED_TASK_TYPES
from oss_mentor.task_features import (
    DIFFICULTY_FORMULA_VERSION,
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

    @staticmethod
    def _difficulty(features: object) -> dict[str, object]:
        return features.feature_evidence["difficulty_assessment"]

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


    def test_newcomer_label_does_not_change_difficulty_dimensions(self) -> None:
        record = self._record(
            title="Bug: parser state machine returns the wrong token",
            body_text=(
                "## Steps to reproduce\nRun the parser with nested input.\n"
                "## Expected behavior\nThe parser should preserve the token order."
            ),
            labels=["bug"],
        )
        regular = extract_task_features(record)
        newcomer = extract_task_features({**record, "labels": ["bug", "good first issue"]})
        self.assertEqual(
            (
                regular.estimated_code_difficulty,
                regular.estimated_setup_difficulty,
                regular.estimated_project_context_difficulty,
                regular.estimated_collaboration_difficulty,
            ),
            (
                newcomer.estimated_code_difficulty,
                newcomer.estimated_setup_difficulty,
                newcomer.estimated_project_context_difficulty,
                newcomer.estimated_collaboration_difficulty,
            ),
        )

    def test_newcomer_label_still_increases_newcomer_score(self) -> None:
        record = self._record(
            title="Bug: parser returns the wrong token",
            body_text="Expected behavior: return the normalized token.",
            labels=["bug"],
        )
        regular = extract_task_features(record)
        newcomer = extract_task_features({**record, "labels": ["bug", "good first issue"]})
        self.assertGreater(newcomer.newcomer_score, regular.newcomer_score)

    def test_performance_signal_alone_does_not_force_code_three(self) -> None:
        features = extract_task_features(
            self._record(title="PERF: Reduce temporary array allocations")
        )
        self.assertLess(features.estimated_code_difficulty, 3)

    def test_performance_signal_alone_does_not_force_context_three(self) -> None:
        features = extract_task_features(
            self._record(title="PERF: Reduce temporary array allocations")
        )
        self.assertLess(features.estimated_project_context_difficulty, 3)

    def test_performance_with_strong_distributed_evidence_can_be_high_difficulty(self) -> None:
        features = extract_task_features(
            self._record(
                title="PERF: Optimize distributed all-gather across multiple nodes",
                body_text=(
                    "Implement tensor parallelism with collective communication. "
                    "The change must preserve distributed state across workers."
                ),
            )
        )
        self.assertEqual(3, features.estimated_code_difficulty)
        self.assertEqual(3, features.estimated_project_context_difficulty)

    def test_performance_tracker_is_non_actionable_and_low_confidence(self) -> None:
        features = extract_task_features(
            self._record(
                title="Performance roadmap tracker",
                labels=["performance", "tracker"],
                body_text="Track multiple optimization milestones and child tasks.",
            )
        )
        quality = self._difficulty(features)["information_quality"]
        self.assertEqual("non_actionable", quality["actionability"])
        self.assertEqual("low", quality["confidence"])

    def test_reported_operating_system_does_not_raise_setup(self) -> None:
        features = extract_task_features(
            self._record(
                title="Bug: parser returns an incorrect value",
                body_text=(
                    "## Environment\nOperating System: Windows 11\n"
                    "## Expected behavior\nThe parser should return the normalized value."
                ),
            )
        )
        self.assertEqual(1, features.estimated_setup_difficulty)

    def test_platform_required_by_reproduction_raises_setup(self) -> None:
        features = extract_task_features(
            self._record(
                title="Bug in the macOS rendering backend",
                body_text=(
                    "## Steps to reproduce\nThis issue is only reproducible on macOS "
                    "using the platform-specific backend."
                ),
            )
        )
        self.assertEqual(2, features.estimated_setup_difficulty)

    def test_container_or_single_cluster_requirement_is_setup_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Bug: readiness probe consumes excessive CPU",
                body_text=(
                    "## Steps to reproduce\nCreate a Kubernetes cluster and deploy "
                    "the service before measuring the probe."
                ),
            )
        )
        self.assertEqual(2, features.estimated_setup_difficulty)

    def test_gpu_or_multinode_requirement_is_setup_three(self) -> None:
        features = extract_task_features(
            self._record(
                title="Run quantization tests on ROCm GPU",
                body_text="The test must run on ROCm GPU workers.",
            )
        )
        self.assertEqual(3, features.estimated_setup_difficulty)

    def test_documentation_reference_to_linux_keeps_setup_zero(self) -> None:
        features = extract_task_features(
            self._record(
                title="Update documentation for installation",
                body_text="Update the Linux section and correct the wording.",
                labels=["documentation"],
            )
        )
        self.assertEqual(("documentation",), features.task_types)
        self.assertEqual(0, features.estimated_setup_difficulty)

    def test_local_refactor_keeps_context_one(self) -> None:
        features = extract_task_features(
            self._record(
                title="Refactor local parser helper",
                body_text="Move the local helper without changing the public behavior.",
            )
        )
        self.assertEqual(1, features.estimated_project_context_difficulty)

    def test_cross_module_refactor_has_context_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Refactor shared cache across multiple modules",
                body_text="Move shared cache handling across multiple modules.",
            )
        )
        self.assertEqual(2, features.estimated_project_context_difficulty)

    def test_core_architecture_change_has_context_three(self) -> None:
        features = extract_task_features(
            self._record(
                title="Refactor the core architecture",
                body_text="Preserve the global invariant while changing the architectural core.",
            )
        )
        self.assertEqual(3, features.estimated_project_context_difficulty)

    def test_public_api_contract_has_context_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Align the public API contract with runtime validation",
                body_text="The public type and runtime validator must preserve compatibility.",
            )
        )
        self.assertEqual(2, features.estimated_project_context_difficulty)

    def test_performance_signal_does_not_set_context_level(self) -> None:
        plain = extract_task_features(self._record(title="Reduce temporary allocations"))
        performance = extract_task_features(
            self._record(title="PERF: Reduce temporary allocations")
        )
        self.assertEqual(
            plain.estimated_project_context_difficulty,
            performance.estimated_project_context_difficulty,
        )

    def test_comment_count_alone_cannot_raise_collaboration_above_one(self) -> None:
        features = extract_task_features(
            self._record(
                title="Fix local parser typo",
                body_text="Expected behavior: use the corrected parser message.",
                comment_count=40,
            )
        )
        self.assertEqual(1, features.estimated_collaboration_difficulty)

    def test_needs_discussion_has_collaboration_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Choose the cache invalidation policy",
                body_text="Several approaches need discussion before implementation.",
                labels=["Needs Discussion"],
            )
        )
        self.assertEqual(2, features.estimated_collaboration_difficulty)

    def test_rfc_api_design_has_collaboration_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="RFC: Define the public API contract",
                body_text="This proposal defines a public API and remains open for review.",
                labels=["API Design"],
            )
        )
        self.assertEqual(2, features.estimated_collaboration_difficulty)

    def test_cross_team_breaking_decision_has_collaboration_three(self) -> None:
        features = extract_task_features(
            self._record(
                title="Coordinate a cross-team breaking change",
                body_text="Multiple teams must agree on a backward incompatible API decision.",
            )
        )
        self.assertEqual(3, features.estimated_collaboration_difficulty)

    def test_low_comment_count_does_not_block_design_evidence(self) -> None:
        features = extract_task_features(
            self._record(
                title="RFC: Select an API design",
                body_text="The proposal contains several alternative approaches.",
                comment_count=0,
            )
        )
        self.assertEqual(2, features.estimated_collaboration_difficulty)

    def test_documentation_only_can_remain_code_zero(self) -> None:
        features = extract_task_features(
            self._record(
                title="Update documentation wording",
                body_text="Correct a typo in the README.",
                labels=["documentation"],
            )
        )
        self.assertEqual(("documentation",), features.task_types)
        self.assertEqual(0, features.estimated_code_difficulty)

    def test_documentation_with_runtime_validation_can_have_code_one(self) -> None:
        features = extract_task_features(
            self._record(
                title="Update documentation for parser usage",
                body_text=(
                    "Run the application and validate the runtime output before "
                    "updating the documentation text."
                ),
                labels=["documentation"],
            )
        )
        self.assertEqual(("documentation",), features.task_types)
        self.assertEqual(1, features.estimated_code_difficulty)

    def test_testing_local_assertion_can_be_code_one(self) -> None:
        features = extract_task_features(
            self._record(title="Add a unit test assertion for parser output")
        )
        self.assertEqual(("testing",), features.task_types)
        self.assertEqual(1, features.estimated_code_difficulty)

    def test_flaky_integration_test_can_have_code_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Flaky integration test for periodic task relocation",
                body_text="The integration test has timing-dependent shared state.",
            )
        )
        self.assertIn("testing", features.task_types)
        self.assertEqual(2, features.estimated_code_difficulty)

    def test_build_tooling_config_change_can_be_code_one(self) -> None:
        features = extract_task_features(
            self._record(title="Update CI configuration value")
        )
        self.assertEqual(("build_tooling",), features.task_types)
        self.assertEqual(1, features.estimated_code_difficulty)

    def test_native_toolchain_change_can_have_higher_setup_and_code(self) -> None:
        features = extract_task_features(
            self._record(
                title="Update native compiler toolchain configuration",
                body_text="Build and test using the native compiler toolchain.",
            )
        )
        self.assertEqual(("build_tooling",), features.task_types)
        self.assertGreaterEqual(features.estimated_code_difficulty, 2)
        self.assertEqual(3, features.estimated_setup_difficulty)

    def test_refactor_task_type_is_only_a_prior(self) -> None:
        features = extract_task_features(
            self._record(title="Refactor local parser helper")
        )
        self.assertEqual(("refactor",), features.task_types)
        self.assertEqual(1, features.estimated_code_difficulty)
        self.assertEqual(1, features.estimated_project_context_difficulty)

    def test_missing_body_has_low_information_confidence(self) -> None:
        features = extract_task_features(
            self._record(title="Feature Request: Import animated images")
        )
        quality = self._difficulty(features)["information_quality"]
        self.assertTrue(quality["body_missing"])
        self.assertEqual("low", quality["confidence"])

    def test_support_question_has_unclear_actionability(self) -> None:
        features = extract_task_features(
            self._record(
                title="How should the cache be configured?",
                body_text="I am asking how to configure this option.",
            )
        )
        quality = self._difficulty(features)["information_quality"]
        self.assertEqual("unclear", quality["actionability"])

    def test_roadmap_tracker_is_non_actionable(self) -> None:
        features = extract_task_features(
            self._record(
                title="Low precision training roadmap tracker",
                body_text="Track several milestones and child pull requests.",
                labels=["tracker"],
            )
        )
        quality = self._difficulty(features)["information_quality"]
        self.assertEqual("non_actionable", quality["actionability"])

    def test_design_proposal_is_design_pending(self) -> None:
        features = extract_task_features(
            self._record(
                title="RFC: Introduce a query result cache",
                body_text="This proposal presents several alternative designs.",
            )
        )
        quality = self._difficulty(features)["information_quality"]
        self.assertEqual("design_pending", quality["actionability"])

    def test_missing_body_does_not_infer_multi_day_from_task_type(self) -> None:
        features = extract_task_features(
            self._record(title="Feature Request: Import animated images")
        )
        effort = self._difficulty(features)["effort"]
        self.assertEqual("unclear", effort["scope"])
        self.assertEqual("half_day", features.estimated_effort_bucket)
        self.assertTrue(effort["provisional"])

    def test_effort_is_not_legacy_four_dimension_sum(self) -> None:
        features = extract_task_features(
            self._record(
                title="Bug in the macOS rendering backend",
                body_text=(
                    "## Steps to reproduce\nThis issue is only reproducible on macOS.\n"
                    "## Expected behavior\nThe local renderer should preserve the value."
                ),
                comment_count=20,
            )
        )
        total = sum(
            (
                features.estimated_code_difficulty,
                features.estimated_setup_difficulty,
                features.estimated_project_context_difficulty,
                features.estimated_collaboration_difficulty,
            )
        )
        legacy = "under_2h" if total <= 2 else "half_day" if total <= 4 else "one_day" if total <= 6 else "multi_day"
        self.assertNotEqual(legacy, features.estimated_effort_bucket)

    def test_same_difficulty_sum_can_have_different_effort_scope(self) -> None:
        local = extract_task_features(
            self._record(
                title="Bug: update a local parser value",
                body_text="Expected behavior: use the corrected local value.",
                comment_count=20,
            )
        )
        module = extract_task_features(
            self._record(
                title="Implement non-trivial logic in the parser state machine",
                body_text="Expected behavior: preserve state transitions in one module.",
            )
        )
        local_sum = sum(
            (
                local.estimated_code_difficulty,
                local.estimated_setup_difficulty,
                local.estimated_project_context_difficulty,
                local.estimated_collaboration_difficulty,
            )
        )
        module_sum = sum(
            (
                module.estimated_code_difficulty,
                module.estimated_setup_difficulty,
                module.estimated_project_context_difficulty,
                module.estimated_collaboration_difficulty,
            )
        )
        self.assertEqual(local_sum, module_sum)
        self.assertNotEqual(local.estimated_effort_bucket, module.estimated_effort_bucket)

    def test_collaboration_alone_does_not_force_multi_day(self) -> None:
        features = extract_task_features(
            self._record(
                title="RFC: Choose naming for a local option",
                body_text="The proposal presents several alternative names.",
            )
        )
        self.assertEqual(2, features.estimated_collaboration_difficulty)
        self.assertNotEqual("multi_day", features.estimated_effort_bucket)

    def test_setup_alone_does_not_force_multi_day(self) -> None:
        features = extract_task_features(
            self._record(
                title="Fix a local test on ROCm GPU",
                body_text="The test must run on a ROCm GPU worker.",
            )
        )
        self.assertEqual(3, features.estimated_setup_difficulty)
        self.assertNotEqual("multi_day", features.estimated_effort_bucket)

    def test_cross_module_scope_can_be_multi_day(self) -> None:
        features = extract_task_features(
            self._record(
                title="Refactor cache invalidation across multiple modules",
                body_text="Move shared cache invalidation across multiple modules.",
            )
        )
        effort = self._difficulty(features)["effort"]
        self.assertEqual("cross_module", effort["scope"])
        self.assertEqual("multi_day", features.estimated_effort_bucket)

    def test_non_actionable_effort_is_marked_not_applicable(self) -> None:
        features = extract_task_features(
            self._record(
                title="Dependency Dashboard",
                body_text="Track pending dependency update pull requests.",
            )
        )
        effort = self._difficulty(features)["effort"]
        self.assertFalse(effort["applicable"])
        self.assertTrue(effort["provisional"])
        self.assertEqual("low", effort["confidence"])
        self.assertEqual("multi_day", features.estimated_effort_bucket)

    def test_effort_evidence_bucket_matches_output_field(self) -> None:
        features = extract_task_features(
            self._record(
                title="Implement non-trivial parser state logic",
                body_text="Expected behavior: preserve all state transitions.",
            )
        )
        effort = self._difficulty(features)["effort"]
        self.assertEqual(features.estimated_effort_bucket, effort["bucket"])

    def test_difficulty_evidence_is_stable_deduplicated_and_json_serializable(self) -> None:
        record = self._record(
            title="Refactor cache invalidation across multiple modules",
            body_text="Move shared cache invalidation across multiple modules.",
            comment_count=12,
        )
        first = extract_task_features(record)
        second = extract_task_features(record)
        first_assessment = self._difficulty(first)
        self.assertEqual(first_assessment, self._difficulty(second))
        json.dumps(first_assessment, sort_keys=True)
        for dimension in first_assessment["dimensions"].values():
            keys = [
                (
                    item["dimension"],
                    item["source"],
                    item["rule_id"],
                    item["matched_value"],
                    item["strength"],
                    item["suggested_level"],
                    item["reason"],
                )
                for item in dimension["evidence"]
            ]
            self.assertEqual(len(keys), len(set(keys)))

    def test_difficulty_evidence_contains_all_four_dimensions(self) -> None:
        features = extract_task_features(
            self._record(title="Bug: parser returns an incorrect value")
        )
        assessment = self._difficulty(features)
        self.assertEqual(DIFFICULTY_FORMULA_VERSION, assessment["formula_version"])
        self.assertEqual(
            {"code", "setup", "project_context", "collaboration"},
            set(assessment["dimensions"]),
        )

    def test_difficulty_level_three_requires_strong_evidence(self) -> None:
        features = extract_task_features(
            self._record(
                title="Refactor the core architecture for distributed state",
                body_text="Preserve the global invariant across distributed state.",
            )
        )
        assessment = self._difficulty(features)
        for dimension in assessment["dimensions"].values():
            if dimension["level"] == 3:
                self.assertTrue(
                    any(
                        item["strength"] == "strong"
                        and item["suggested_level"] == 3
                        for item in dimension["evidence"]
                    )
                )

    def test_conflicting_evidence_reduces_confidence(self) -> None:
        features = extract_task_features(
            self._record(
                title="Update documentation for core architecture compiler semantics",
                body_text="Only update wording; no code changes are required.",
                labels=["documentation"],
            )
        )
        code = self._difficulty(features)["dimensions"]["code"]
        self.assertTrue(code["conflicts"])
        self.assertEqual("low", code["confidence"])

    def test_task_type_evidence_is_unchanged_by_difficulty_assessment(self) -> None:
        base = self._record(
            title="Bug: parser crashes; add regression tests",
            body_text="Please add regression tests for the failure.",
            labels=["type/bug"],
        )
        first = extract_task_features({**base, "comment_count": 0})
        second = extract_task_features({**base, "comment_count": 30})
        self.assertEqual(
            first.feature_evidence["task_type_evidence"],
            second.feature_evidence["task_type_evidence"],
        )
        self.assertEqual(
            first.feature_evidence["task_type_scores"],
            second.feature_evidence["task_type_scores"],
        )

    def test_repeated_extraction_produces_identical_difficulty_evidence(self) -> None:
        record = self._record(
            title="RFC: Define a public API contract",
            body_text="This proposal presents several alternative designs.",
            labels=["API Design"],
        )
        self.assertEqual(
            self._difficulty(extract_task_features(record)),
            self._difficulty(extract_task_features(record)),
        )

    def test_infer_skill_requirements_uses_new_code_level_without_logic_change(self) -> None:
        record = self._record(
            title="Implement distributed all-gather protocol semantics",
            body_text="Preserve distributed state across multiple workers.",
            primary_language="Java",
        )
        features = extract_task_features(record)
        requirements = infer_skill_requirements(record, features)
        language = next(item for item in requirements if item.skill_name == "Java")
        self.assertEqual(features.estimated_code_difficulty, language.minimum_level)
        self.assertEqual(3, language.minimum_level)

    def test_ineligible_candidate_still_gets_zero_track_scores(self) -> None:
        features = extract_task_features(
            self._record(
                title="Refactor core architecture",
                body_text="Preserve the global invariant.",
                candidate_eligibility="temporarily_ineligible",
            )
        )
        self.assertEqual(0.0, features.newcomer_score)
        self.assertEqual(0.0, features.growth_value_score)


    # B3-E3 difficulty-rules-v0.2.1 targeted regression tests.

    def test_v021_query_traversal_with_measurement_is_code_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Optimize query scan direction for sorted segments",
                body_text=(
                    "Steps to reproduce: ORDER BY TIME DESC reads the whole segment and scans "
                    "5,700,000 rows while ASC scans 318 rows. Read from the bottom to avoid a "
                    "full scan and reduce latency."
                ),
                labels=["performance"],
            )
        )
        self.assertEqual(2, features.estimated_code_difficulty)
        self.assertEqual(2, features.estimated_project_context_difficulty)

    def test_v021_performance_only_local_optimization_still_not_code_three(self) -> None:
        features = extract_task_features(
            self._record(
                title="PERF: Reduce temporary allocations in local helper",
                body_text="Expected behavior: the local helper allocates one fewer temporary list.",
            )
        )
        self.assertLess(features.estimated_code_difficulty, 3)
        self.assertLess(features.estimated_project_context_difficulty, 3)

    def test_v021_compiler_recompilation_multiple_ops_is_code_three(self) -> None:
        features = extract_task_features(
            self._record(
                title="torch.compile out variants cause recompilations",
                body_text=(
                    "Minified repro: dynamic shapes trigger extra recompilations for multiple ops "
                    "including bmm, topk and cholesky. Fix the shared graph guard behavior and add "
                    "regression tests for the affected operators."
                ),
                labels=["module: dynamic shapes", "bug"],
            )
        )
        self.assertEqual(3, features.estimated_code_difficulty)
        self.assertEqual(3, features.estimated_project_context_difficulty)
        self.assertEqual("multi_day", features.estimated_effort_bucket)

    def test_v021_algorithm_implementation_with_benchmark_is_code_three(self) -> None:
        features = extract_task_features(
            self._record(
                title="Implement the missing Boruvka algorithm for clustering",
                body_text=(
                    "The current implementation is much slower on a large dataset. Implement the "
                    "missing algorithm and benchmark correctness and performance against the existing solver."
                ),
                labels=["performance", "feature"],
            )
        )
        self.assertEqual(3, features.estimated_code_difficulty)
        self.assertGreaterEqual(features.estimated_project_context_difficulty, 2)
        self.assertEqual("multi_day", features.estimated_effort_bucket)

    def test_v021_algorithm_name_without_change_does_not_force_code_three(self) -> None:
        features = extract_task_features(
            self._record(
                title="Document the Boruvka algorithm",
                body_text="Correct wording in the README about the Boruvka algorithm.",
                labels=["documentation"],
            )
        )
        self.assertEqual(0, features.estimated_code_difficulty)

    def test_v021_realtime_alignment_interaction_is_code_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Add alignment guides while moving elements",
                body_text=(
                    "Describe the solution you'd like: when moving or positioning elements, show "
                    "temporary guide lines, equal spacing indicators and snap-to-spacing visual feedback."
                ),
            )
        )
        self.assertEqual(2, features.estimated_code_difficulty)
        self.assertGreaterEqual(features.estimated_project_context_difficulty, 1)
        self.assertNotEqual("half_day", features.estimated_effort_bucket)

    def test_v021_exception_retry_http_mapping_is_code_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Do not retry FileNotFound when downloading a segment",
                body_text=(
                    "FileNotFoundException is retried and wrapped as AttemptsExceededException, "
                    "which returns HTTP 500. Do not retry it and return HTTP status 404 instead."
                ),
                labels=["bug"],
            )
        )
        self.assertEqual(2, features.estimated_code_difficulty)
        self.assertGreaterEqual(features.estimated_project_context_difficulty, 2)
        self.assertEqual("one_day", features.estimated_effort_bucket)

    def test_v021_public_api_deprecation_policy_is_context_three(self) -> None:
        features = extract_task_features(
            self._record(
                title="DEPR: public API parameter compatibility policy",
                body_text=(
                    "Needs discussion: should we deprecate this public API parameter? Define the "
                    "backward compatibility and migration path before implementation."
                ),
                labels=["Deprecate", "Needs Discussion"],
            )
        )
        self.assertEqual(3, features.estimated_project_context_difficulty)
        self.assertEqual(2, features.estimated_collaboration_difficulty)

    def test_v021_public_api_contract_only_remains_context_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Align the public API contract",
                body_text="Update one public interface while preserving compatibility.",
            )
        )
        self.assertEqual(2, features.estimated_project_context_difficulty)

    def test_v021_api_semantic_ambiguity_is_code_and_context_three(self) -> None:
        features = extract_task_features(
            self._record(
                title="API Design: same syntax has ambiguous interpretations",
                body_text=(
                    "The same API syntax has two interpretations across getitem and setitem. Existing "
                    "and missing keys follow different paths, backward compatibility affects existing user "
                    "code, and several heuristics fail."
                ),
                labels=["API Design"],
            )
        )
        self.assertEqual(3, features.estimated_code_difficulty)
        self.assertEqual(3, features.estimated_project_context_difficulty)
        self.assertEqual(3, features.estimated_collaboration_difficulty)
        self.assertEqual("multi_day", features.estimated_effort_bucket)

    def test_v021_documentation_semantic_verification_keeps_code_zero_context_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Document partial dependence method semantics",
                body_text=(
                    "Steps to reproduce show different answers for method=recursion and method=brute. "
                    "Expected results differ from actual results because conditional and interventional "
                    "partial dependence have different semantics. Clarify this behavior in documentation."
                ),
                labels=["documentation"],
            )
        )
        self.assertEqual(0, features.estimated_code_difficulty)
        self.assertEqual(2, features.estimated_project_context_difficulty)
        self.assertEqual(1, features.estimated_setup_difficulty)
        self.assertEqual("one_day", features.estimated_effort_bucket)

    def test_v021_simple_documentation_typo_remains_zero_context(self) -> None:
        features = extract_task_features(
            self._record(
                title="Fix README typo",
                body_text="Correct one wording typo in the README text.",
                labels=["documentation"],
            )
        )
        self.assertEqual(0, features.estimated_code_difficulty)
        self.assertEqual(0, features.estimated_project_context_difficulty)
        self.assertEqual(0, features.estimated_setup_difficulty)
        self.assertEqual("under_2h", features.estimated_effort_bucket)

    def test_v021_profiled_module_with_benchmark_is_multi_day(self) -> None:
        features = extract_task_features(
            self._record(
                title="Fix agent memory regression in remote write",
                body_text=(
                    "What did you do? Run the service in Kubernetes. pprof shows remote.processExternalLabels "
                    "using over 50% of heap memory. Compare agent mode and regular mode in the same environment, "
                    "fix the subsystem and benchmark memory before and after."
                ),
                labels=["performance", "bug"],
            )
        )
        self.assertGreaterEqual(features.estimated_code_difficulty, 2)
        self.assertEqual(2, features.estimated_setup_difficulty)
        self.assertGreaterEqual(features.estimated_project_context_difficulty, 2)
        self.assertEqual("multi_day", features.estimated_effort_bucket)
        self.assertEqual("heavy", self._difficulty(features)["effort"]["validation_burden"])

    def test_v021_bounded_code_two_context_two_bug_is_one_day(self) -> None:
        features = extract_task_features(
            self._record(
                title="Do not retry FileNotFound",
                body_text=(
                    "Expected behavior: FileNotFoundException should not retry; preserve the retry policy for "
                    "other errors and map this exception to HTTP 404. Add one regression test."
                ),
            )
        )
        self.assertEqual(2, features.estimated_code_difficulty)
        self.assertEqual(2, features.estimated_project_context_difficulty)
        self.assertEqual("one_day", features.estimated_effort_bucket)

    def test_v021_simple_local_task_remains_half_day(self) -> None:
        features = extract_task_features(
            self._record(
                title="Fix local parser value",
                body_text="Expected behavior: use the corrected local parser value.",
            )
        )
        self.assertEqual("half_day", features.estimated_effort_bucket)

    def test_v021_micro_wording_remains_under_two_hours(self) -> None:
        features = extract_task_features(
            self._record(
                title="Fix README wording",
                body_text="Correct one wording sentence.",
                labels=["documentation"],
            )
        )
        self.assertEqual("under_2h", features.estimated_effort_bucket)

    def test_v021_filesystem_container_reproduction_is_setup_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Investigate storage allocation on Btrfs",
                body_text=(
                    "Steps to reproduce: run the service in Podman using a Btrfs storage volume. "
                    "Measure the filesystem allocation for each chunk file."
                ),
            )
        )
        self.assertEqual(2, features.estimated_setup_difficulty)

    def test_v021_distributed_validation_is_setup_three(self) -> None:
        features = extract_task_features(
            self._record(
                title="Support Tensor Parallel all-gather",
                body_text=(
                    "Implement tensor parallelism and all-gather support, then run distributed tests and "
                    "benchmark the speedup across multiple GPUs."
                ),
            )
        )
        self.assertEqual(3, features.estimated_setup_difficulty)
        self.assertEqual(3, features.estimated_code_difficulty)
        self.assertEqual(3, features.estimated_project_context_difficulty)
        self.assertEqual("multi_day", features.estimated_effort_bucket)

    def test_v021_either_or_without_design_vocabulary_does_not_raise_collaboration(self) -> None:
        features = extract_task_features(
            self._record(
                title="Investigate local storage bug",
                body_text=(
                    "Expected behavior: preserve the file. The cause is either a filesystem issue or an "
                    "application issue."
                ),
                comment_count=0,
            )
        )
        self.assertLessEqual(features.estimated_collaboration_difficulty, 1)

    def test_v021_rollout_strategy_is_collaboration_two(self) -> None:
        features = extract_task_features(
            self._record(
                title="Integrate classloader leak checks into the testing framework",
                body_text=(
                    "Prevent future regression by integrating the classloader lifecycle check into the test "
                    "framework. Start opt-in for extension owners, then use a phased rollout toward opt-out."
                ),
            )
        )
        self.assertEqual(2, features.estimated_collaboration_difficulty)
        self.assertEqual(3, features.estimated_project_context_difficulty)
        self.assertEqual("multi_day", features.estimated_effort_bucket)

    def test_v021_body_missing_effort_is_not_applicable(self) -> None:
        features = extract_task_features(
            self._record(title="Feature Request: Import animated images")
        )
        effort = self._difficulty(features)["effort"]
        self.assertEqual("unclear", effort["scope"])
        self.assertFalse(effort["applicable"])
        self.assertTrue(effort["provisional"])
        self.assertEqual("low", effort["confidence"])

    def test_v021_unresolved_should_question_has_non_applicable_effort(self) -> None:
        features = extract_task_features(
            self._record(
                title="Should cache keys be hashed before storing?",
                body_text=(
                    "Hashing may reduce storage size but introduces collision risk and migration compatibility "
                    "trade-offs. No implementation approach has been selected."
                ),
            )
        )
        quality = self._difficulty(features)["information_quality"]
        effort = self._difficulty(features)["effort"]
        self.assertEqual("design_pending", quality["actionability"])
        self.assertIn("unresolved_design_choice", quality["reasons"])
        self.assertFalse(effort["applicable"])
        self.assertTrue(effort["provisional"])

    def test_v021_expected_should_return_is_not_unresolved_design(self) -> None:
        features = extract_task_features(
            self._record(
                title="Bug: missing file returns 500",
                body_text="Expected behavior: FileNotFound should return 404 and should preserve other errors.",
            )
        )
        quality = self._difficulty(features)["information_quality"]
        self.assertNotIn("unresolved_design_choice", quality["reasons"])
        self.assertNotEqual("design_pending", quality["actionability"])

    def test_v021_bounded_cross_cutting_qa_protects_one_day(self) -> None:
        features = extract_task_features(
            self._record(
                title="Add property testing for API fixtures",
                body_text=(
                    "RFC child task: extend API fixtures and add schema-driven property testing. "
                    "Acceptance criteria: cover the endpoint fixtures and keep CI stable. This is a bounded QA subtask."
                ),
                labels=["testing", "RFC"],
            )
        )
        self.assertEqual(2, features.estimated_code_difficulty)
        self.assertLessEqual(features.estimated_project_context_difficulty, 2)
        self.assertEqual(2, features.estimated_collaboration_difficulty)
        self.assertEqual("one_day", features.estimated_effort_bucket)

    def test_v021_formula_version_changes_without_task_feature_version_change(self) -> None:
        features = extract_task_features(self._record(title="Fix local parser value"))
        self.assertEqual("task-features-v0.3", TASK_FEATURE_VERSION)
        self.assertEqual("task-features-v0.3", features.task_feature_version)
        self.assertEqual("difficulty-rules-v0.2.1", DIFFICULTY_FORMULA_VERSION)
        self.assertEqual("difficulty-rules-v0.2.1", self._difficulty(features)["formula_version"])

if __name__ == "__main__":
    unittest.main()