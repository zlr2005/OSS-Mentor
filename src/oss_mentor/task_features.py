"""Explainable text features and two-track ranking baselines."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from oss_mentor.candidate_rules import has_newcomer_label
from oss_mentor.developer_profiles import ALLOWED_TASK_TYPES


TASK_FEATURE_VERSION = "task-features-v0.2"
PUBLIC_TASK_TYPES = frozenset(ALLOWED_TASK_TYPES)
_TASK_TYPE_ACCEPTANCE_SCORE = 3.0
_SOURCE_ORDER = {"label": 0, "title": 1, "body": 2, "derived": 3}


@dataclass(frozen=True, slots=True)
class TaskFeatures:
    has_reproduction_steps: bool
    has_acceptance_criteria: bool
    has_expected_behavior: bool
    has_affected_module_hint: bool
    task_types: tuple[str, ...]
    text_clarity_score: float
    estimated_code_difficulty: int
    estimated_setup_difficulty: int
    estimated_project_context_difficulty: int
    estimated_collaboration_difficulty: int
    estimated_effort_bucket: str
    novice_fit_probability: float
    newcomer_score: float
    growth_value_score: float
    feature_evidence: dict[str, Any]
    task_feature_version: str = TASK_FEATURE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SkillRequirement:
    skill_name: str
    minimum_level: int
    importance: float
    requirement_source: str


@dataclass(frozen=True, slots=True)
class _TextRule:
    task_type: str
    rule_id: str
    pattern: str
    weight: float


_LABEL_ALIASES: dict[str, tuple[str, float]] = {
    # bug_fix
    "bug": ("bug_fix", 3.0),
    "type bug": ("bug_fix", 3.0),
    "kind bug": ("bug_fix", 3.0),
    "c bug": ("bug_fix", 3.0),
    "confirmed bug": ("bug_fix", 3.0),
    "defect": ("bug_fix", 3.0),
    "regression": ("bug_fix", 3.0),
    # testing
    "test": ("testing", 3.0),
    "tests": ("testing", 3.0),
    ">test": ("testing", 3.0),
    "testing": ("testing", 3.0),
    "type test": ("testing", 3.0),
    "kind test": ("testing", 3.0),
    "test coverage": ("testing", 3.0),
    "module test suite": ("testing", 3.0),
    # documentation
    "documentation": ("documentation", 3.0),
    "docs": ("documentation", 3.0),
    "doc": ("documentation", 3.0),
    "readme": ("documentation", 3.0),
    "type documentation": ("documentation", 3.0),
    "kind documentation": ("documentation", 3.0),
    # feature
    "feature": ("feature", 3.0),
    "type feature": ("feature", 3.0),
    "kind feature": ("feature", 3.0),
    "feature request": ("feature", 3.0),
    "new feature": ("feature", 3.0),
    "enhancement": ("feature", 3.0),
    "type enhancement": ("feature", 3.0),
    "kind enhancement": ("feature", 3.0),
    "function request": ("feature", 3.0),
    "integration": ("feature", 2.0),
    "pep request": ("feature", 3.0),
    "extension proposal": ("feature", 3.0),
    # refactor
    "refactor": ("refactor", 3.0),
    "type refactor": ("refactor", 3.0),
    "kind refactor": ("refactor", 3.0),
    "cleanup": ("refactor", 3.0),
    "kind cleanup": ("refactor", 3.0),
    "maintenance": ("refactor", 3.0),
    "technical debt": ("refactor", 3.0),
    "tech debt": ("refactor", 3.0),
    "deprecation": ("refactor", 3.0),
    "deprecate": ("refactor", 3.0),
    "better engineering": ("refactor", 3.0),
    # build_tooling
    "build": ("build_tooling", 3.0),
    "type build": ("build_tooling", 3.0),
    "kind build": ("build_tooling", 3.0),
    "build tooling": ("build_tooling", 3.0),
    "ci": ("build_tooling", 3.0),
    "dependency": ("build_tooling", 3.0),
    "dependencies": ("build_tooling", 3.0),
    "packaging": ("build_tooling", 3.0),
    "release": ("build_tooling", 3.0),
    "infrastructure": ("build_tooling", 3.0),
}

_CONTROLLED_LABEL_NAMESPACES = frozenset({"type", "kind", "category"})
_DIRECT_TASK_LABEL_NAMESPACES: dict[str, str] = {
    "bug": "bug_fix",
    "feat": "feature",
}
_NAMESPACE_VALUE_ALIASES: dict[str, str] = {
    "bug": "bug_fix",
    "regression": "bug_fix",
    "defect": "bug_fix",
    "test": "testing",
    "tests": "testing",
    "testing": "testing",
    "test suite": "testing",
    "test coverage": "testing",
    "doc": "documentation",
    "docs": "documentation",
    "documentation": "documentation",
    "readme": "documentation",
    "feature": "feature",
    "enhancement": "feature",
    "feature request": "feature",
    "refactor": "refactor",
    "cleanup": "refactor",
    "cleanup optimisation": "refactor",
    "cleanup optimization": "refactor",
    "optimisation": "refactor",
    "optimization": "refactor",
    "deprecate": "refactor",
    "deprecation": "refactor",
    "maintenance": "refactor",
    "build": "build_tooling",
    "build tooling": "build_tooling",
    "dependency": "build_tooling",
    "dependencies": "build_tooling",
    "infrastructure": "build_tooling",
    "packaging": "build_tooling",
}

_LABEL_PATTERN_RULES: tuple[_TextRule, ...] = (
    _TextRule(
        "bug_fix",
        "bug.label.causes_error",
        r"(?:^|\s)(?:suggestion\s+)?causes?\s+(?:an?\s+)?error(?:\s|$)",
        3.0,
    ),
)

_PERFORMANCE_LABELS = frozenset(
    {
        "performance",
        "perf",
        "type performance",
        "kind performance",
        "optimization",
        "optimisation",
    }
)

_TITLE_RULES: tuple[_TextRule, ...] = (
    # bug_fix: symptoms and failure statements, not generic words such as "missing".
    _TextRule(
        "bug_fix",
        "bug.title.explicit_marker",
        r"^\s*(?:\[[^\]]*\b(?:bug|regression)\b[^\]]*\]|"
        r"(?:bug|regression)\b|[^:\n]{0,20}\b(?:bug|regression)\b\s*:)",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.runtime_failure",
        r"\b(?:crash(?:es|ed|ing)?|deadlock|segfault|"
        r"nullpointerexception|panic(?:s|ked|king)?|hang(?:s|ing)?)\b|"
        r"(?<![a-z0-9_])npe(?![a-z0-9_])|"
        r"\b(?:error|exception|[a-z_][a-z0-9_]*)\s+(?:is\s+)?thrown\b|"
        r"`[a-z_][a-z0-9_]*`\s+(?:is\s+)?thrown\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.explicit_error",
        r"\b(?:fatal error|bad request|error|\w*exception|assertionerror|typeerror|"
        r"referenceerror|econnreset|eofexception|cve-\d{4}-\d+)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.failure_word",
        r"\b(?:fail(?:s|ed|ing|ure)?|flaky|vulnerable|miscompil(?:e|es|ed)|"
        r"inconsistent|race condition|flicker(?:s|ing)?|"
        r"ambiguity|mismatch|stale value|unstable|duplication|duplicate identifier|"
        r"too early|too few|break(?:s|age)?|artif(?:act|cat)(?:ing)?|non-unique|"
        r"inapplicable|problem|probelm)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.miscompilation",
        r"\b(?:miscompil(?:e|es|ed|ing)|wrong[- ]code generation|"
        r"incorrectly compiled)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.behavior_failure",
        r"\b(?:does not|doesn't|isn't|aren't|cannot|can't|won't|"
        r"fails? to|failed to|don't|do not|not working|no longer works?|stops? working|"
        r"should not|unable to|not (?:accepted|allowed|cached|escaped|functional|"
        r"accurate|rendered|rendering|shown|used|found|preserved|returned|executed|"
        r"recognized|visible|mounted|narrowed|supported|shows?|displays?)|"
        r"not \w+(?:ed|ing)|not in scope)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.incorrect_behavior",
        r"\b(?:incorrect(?:ly)?|wrong|unexpected|broken|corrupt(?:ion|ed)?|"
        r"false positives?|false negatives?|not preserved|out of order|"
        r"ignores?|ignored|leak(?:age)?|lost|silently (?:drops?|removes?|skips?)|"
        r"notifies?.{0,40}didn't change|"
        r"(?:is|are|was|were)\s+(?:incorrectly\s+)?(?:flagged|reported|rejected|"
        r"accepted|treated)\s+when|works? like\b.{0,80}\b(?:if|when)|"
        r"(?:also|unexpectedly)\s+(?:increases?|changes?|enables?|disables?|"
        r"triggers?|causes?)|shows? less\b.{0,80}\b(?:compared? to|than)|"
        r"confusing behavior)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.server_error",
        r"\b(?:http|query)\s+5\d\d\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.outcome_anomaly",
        r"\b(?:creates? multiple|differs? between|works? differently|"
        r"silently|permanently|swallows?|strands?|skipped|kill(?:s|ed|ing)?|removed|"
        r"notifies?|padded|full table scan|more splits than|stack trace|fires twice|"
        r"reports?.{0,60}\btwice|without calling|happens when|instead of|waits for)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.capability_blocked",
        r"\bblocks?\b.{0,80}\b(?:features?|functionality|behavior|usage|access)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.performance_symptom",
        r"\b(?:lag(?:gy|ging)?|high overhead|large file size|"
        r"excessive (?:memory|cpu)|too much (?:memory|ram|cpu)|"
        r"high (?:memory|ram|cpu)(?: usage| utilization)?|"
        r"(?:increased?|higher|growing) (?:latency|memory usage|ram usage|cpu usage|"
        r"runtime|overhead)|performance regression|slower than|slowdown|"
        r"taking (?:a )?long time|more heap memory|waterfall|"
        r"always (?:called|executed|run) sequentially|recompilations?|"
        r"wasted (?:disk )?space)\b|"
        r"^\s*increase\s+(?:the\s+)?(?:latency|memory usage|ram usage|cpu usage|"
        r"runtime|overhead)\b|\b(?:memory|ram|cpu) usage (?:increases?|grows?|"
        r"spikes?|rises?)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.security_exposure",
        r"\b(?:can|allows?\s+\w+\s+to)\s+(?:access|read|write)\b.{0,80}"
        r"\b(?:host|sensitive|filesystem|file-system|credentials?)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.no_effect_in_case",
        r"^\s*no\s+[a-z0-9_.-]+(?:\s+[a-z0-9_.-]+){0,3}\s+for\b"
        r".{0,120}\b(?:after|when|while|if)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.support_regression",
        r"\b(?:no longer|stopped|ceased to)\s+(?:be\s+)?support(?:ed|ing)?\b|"
        r"\b(?:previously|formerly)\s+(?:worked|supported)\b|"
        r"\bused to\s+(?:work|be supported)\b|"
        r"\bsupport\s+(?:was|is)\s+broken\b|"
        r"\b(?:expected|documented)\s+(?:to be|as)\s+supported\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.title.build_failure",
        r"\b(?:build(?: workers?)?|compiler|compilation|ci|packaging|bundler)\b"
        r".{0,140}\b(?:fail(?:s|ed|ing|ure)?|crash(?:es|ed|ing)?|"
        r"ignore(?:s|d)?|strip(?:s|ped|ping)?|lose(?:s|st)?|"
        r"kill(?:s|ed|ing)?|ooms?|out[- ]of[- ]memory)\b|"
        r"\b(?:compiler flag|build option|ci command|packaging step)\b"
        r".{0,100}\b(?:is\s+)?(?:ignored|stripped|lost|changed)\b",
        3.0,
    ),
    # Explicit test work must be evaluated before the generic feature verb rule.
    _TextRule(
        "testing",
        "testing.title.explicit_work",
        r"\b(?:unit|integration|regression|property[- ]based|"
        r"end[- ]to[- ]end|e2e|benchmark)\s+(?:query\s+)?tests?\b|"
        r"\bregression testing\b|"
        r"\bproperty[- ]based(?:\s+[a-z0-9_-]+){0,3}\s+testing\b|"
        r"\b(?:test suite|test coverage|code coverage|test cases?|flaky tests?)\b|"
        r"^\s*tests?\s*:",
        3.0,
    ),
    _TextRule(
        "testing",
        "testing.title.action",
        r"^\s*(?:\[[^\]]+\]\s*)?(?:add|write|create|improve|expand|update|"
        r"refactor|share|move|extract|centralize)\b.*\b(?:tests?|fixtures?|"
        r"test suite|test coverage|test code)\b",
        3.0,
    ),
    _TextRule(
        "testing",
        "testing.title.flaky_or_check",
        r"^\s*(?:\[[^\]]*\bflaky[- ]?test\b[^\]]*\]|flaky[- ]?test\b)|"
        r"^\s*check behavior\b|^\s*disabled test",
        3.0,
    ),
    # Documentation-specific work.
    _TextRule(
        "documentation",
        "documentation.title.explicit",
        r"^\s*(?:\[[^\]]+\]\s*)?(?:doc|docs|document|documentation)\b|"
        r"\b(?:readme|user guide|tutorial|typo|wording|update documents?|"
        r"[a-z0-9_-]+ example)\b",
        3.0,
    ),
    _TextRule(
        "documentation",
        "documentation.title.action",
        r"^\s*(?:\[[^\]]+\]\s*)?(?:add|update|improve|fix|write|create)\b"
        r".*\b(?:docs?|documentation|readme|guide|tutorial|examples?)\b",
        3.0,
    ),
    # Build and dependency maintenance.
    _TextRule(
        "build_tooling",
        "build.title.dependencies",
        r"^\s*(?:\[[^\]]+\]\s*)?(?:add|update|upgrade|bump|remove|fix|"
        r"consolidate)\b.*\b(?:dependencies|dependency|libraries|library versions?|"
        r"packages?|docker image)\b",
        3.0,
    ),
    _TextRule(
        "build_tooling",
        "build.title.ci_migration",
        r"^\s*(?:\[[^\]]+\]\s*)?(?:add|update|migrate|move|fix)\b"
        r".*\b(?:github actions?|ci workflow|build system|toolchain|packaging)\b",
        3.0,
    ),
    _TextRule(
        "build_tooling",
        "build.title.named_tool",
        r"\b(?:maven|gradle|cmake|cargo|npm|pnpm|yarn)\b",
        2.0,
    ),
    _TextRule(
        "build_tooling",
        "build.title.compile_or_artifact",
        r"\b(?:build error|compile error|compilation failure|missing .* wheels?|"
        r"build flags?|typecheck fail|ci .* timeout)\b",
        3.0,
    ),
    _TextRule(
        "build_tooling",
        "build.title.direct_subject",
        r"\b(?:ci|build|wheels?|dependencies|dependency|"
        r"docker image|github actions?|gradle|maven|cmake|cargo|npm|pnpm|yarn)\b",
        3.0,
    ),
    # Refactoring and maintenance work.
    _TextRule(
        "refactor",
        "refactor.title.explicit",
        r"\b(?:refactor|cleanup|clean up|consolidate|deprecat(?:e|ion)|"
        r"technical debt|tech debt|modernize|streamline|harmonization|stabilization|"
        r"remove the dependency|reduce the number of patches|investigate redundancy)\b",
        3.0,
    ),
    _TextRule(
        "refactor",
        "refactor.title.structural_action",
        r"^\s*(?:\[[^\]]+\]\s*)?(?:rename|simplify|replace|move|rewrite)\b|"
        r"\bmove\b.{0,60}\bto\b",
        3.0,
    ),
    # Feature work. Generic capability actions are suppressed when the same title
    # clearly targets tests, documentation, build tooling, or refactoring.
    _TextRule(
        "feature",
        "feature.title.explicit_marker",
        r"^\s*(?:\[?(?:fea|feature req|feature request|feature|enh|enhancement)\]?\s*:?)|"
        r"\bfeature request\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.title.new_lint",
        r"\b(?:new rule|new lint|lint idea|lint for|lints for|have a lint|"
        r"how about .*\blint|suggest(?:ion|ing)?\s*:?.*\blint|should also suggest)\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.title.capability_action",
        r"^\s*(?:\[[^\]]+\]\s*)?(?:add|allow|enable|introduce|support|supports|"
        r"provide|implement|expose|create|generate|render|expand|make|change|"
        r"import|disable|disallow|classify|use|handle|serve|separate|"
        r"configure|accelerate|distribute|distributing|send|specify|specifies|enhance|"
        r"improve|match|adding|supporting|improving|automatically repair|default to)\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.title.increase_capability",
        r"^\s*(?:\[[^\]]+\]\s*)?increase\b.{0,80}\b(?:supported|support for|"
        r"maximum|max|limit|capacity|range|compatibility|padding|spacing|width|"
        r"height|font size|retention)\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.title.warning_capability",
        r"^\s*(?:\[[^\]]+\]\s*)?(?:warn|flag|detect|check)\s+(?:that|when|"
        r"for|uses?|instances?)\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.title.issue_warning",
        r"^\s*issue\s+`?[^`\n]+`?\s+warnings?\s+for\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.title.distributed_performance_capability",
        r"\b(?:low[- ]bit|quantized|compressed)\b.{0,100}"
        r"\b(?:all[- ]gather|collective|communication|distributed\s+"
        r"(?:training|operation|transfer)|gradient exchange)\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.title.performance_capability",
        r"^\s*(?:\[[^\]]+\]\s*)?(?:cache|buffer|pool)\s+"
        r"(?:max(?:imum)?\s+)?(?:size|limit|capacity)\b|"
        r"\b(?:jit|cache|parallel|batch)\b.{0,50}\b(?:engine|option|mode|"
        r"capability|support|limit|size)\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.title.user_capability",
        r"\b(?:better ux|improve(?:d|ment)?\s+.*\bux\b|tool for\s+\w+|"
        r"new (?:api|command|option|integration|capability|policy)|ability to|"
        r"support for|[a-z][\w-]{2,} support|[a-z][\w-]{2,} integration|"
        r"(?:authentication|replication) to|table truncate|sans font)\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.title.idea_or_proposal",
        r"^\s*(?:idea|proposal|suggestion)\s*:|\badditional api endpoints?\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.title.capability_noun",
        r"^\s*(?:async|realtime)\b|\b(?:layered .* transition|native histograms?|"
        r"json schema|default to microseconds|new prompt)\b",
        3.0,
    ),
)

_BODY_RULES: tuple[_TextRule, ...] = (
    _TextRule(
        "bug_fix",
        "bug.body.explicit_bug_statement",
        r"\b(?:the bug is real(?: and reproducible)?|i think this is a regression|"
        r"this is a regression|filing as a bug report)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.body.explicit_failure",
        r"\b(?:this|the issue|it)\s+(?:causes?|results? in|leads? to)\s+"
        r"(?:an?\s+)?(?:crash|error|failure|deadlock|exception|incorrect result)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.body.runtime_throw",
        r"\bthe\s+(?:process|application|server|client|compiler|runtime)\s+"
        r"(?:throws?|threw)\s+(?:an?\s+)?(?:error|exception|"
        r"[a-z_][a-z0-9_]*)\b|(?:导致|引发|抛出|出现)\s*"
        r"(?:npe|nullpointerexception|exception|error)",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.body.problem_statement",
        r"\bthe problem is that\b.{0,160}\b(?:flags?|reports?|rejects?|fails?|"
        r"crashes?|throws?|errors?)\b",
        3.0,
    ),
    _TextRule(
        "bug_fix",
        "bug.body.current_behavior",
        r"\b(?:currently|actual(?:ly)?|observed behavior)\b.{0,100}"
        r"\b(?:does not|doesn't|fails? to|incorrect|wrong|crash(?:es|ing)?)\b",
        2.0,
    ),
    _TextRule(
        "testing",
        "testing.body.explicit_action",
        r"\b(?:add|write|create|improve|expand|update)\s+(?:an?\s+)?(?:new\s+)?"
        r"(?:(?:unit|integration|regression|property[- ]based|end[- ]to[- ]end)\s+)?"
        r"tests?\b",
        3.0,
    ),
    _TextRule(
        "documentation",
        "documentation.body.explicit_action",
        r"\b(?:add|update|write|improve|fix)\s+(?:the\s+)?"
        r"(?:documentation|docs?|readme|user guide|tutorial)\b|"
        r"\bdocument how\b",
        3.0,
    ),
    _TextRule(
        "build_tooling",
        "build.body.dependencies",
        r"\b(?:update|upgrade|bump|consolidate)\s+(?:the\s+)?"
        r"(?:dependencies|dependency|libraries|packages?|docker image)\b",
        3.0,
    ),
    _TextRule(
        "build_tooling",
        "build.body.ci_migration",
        r"\b(?:migrate|move|add|update)\s+(?:the\s+)?(?:ci|workflow)\s+"
        r"(?:to|from|using)\b|\bmigrate\b.{0,60}\bgithub actions?\b",
        3.0,
    ),
    _TextRule(
        "refactor",
        "refactor.body.explicit_action",
        r"\b(?:refactor|clean up|cleanup|consolidate|deprecate|remove legacy|"
        r"technical debt)\b",
        2.0,
    ),
    _TextRule(
        "feature",
        "feature.body.explicit_request",
        r"\b(?:feature request|new feature)\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.body.capability_request",
        r"\b(?:we|users?|the project)\s+(?:need|needs|want|wants|would like|should)\s+"
        r"(?:to\s+)?(?:add|support|enable|allow|introduce|provide|implement|expose)\b"
        r"(?!\s+(?:the\s+)?(?:documentation|docs?|document|readme|tests?|testing|"
        r"dependencies|dependency|ci|workflow)\b)",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.body.absent_capability",
        r"\bthere is no way to\b.{0,120}\b(?:tell|detect|configure|enable|access|"
        r"support|provide|use)\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.body.desired_capability",
        r"\bit would be\b.{0,100}\b(?:if we (?:have|had)|to have)\b|"
        r"\bwe should have\b.{0,100}\b",
        3.0,
    ),
    _TextRule(
        "feature",
        "feature.body.proposal",
        r"\b(?:this (?:proposal|rfc) proposes?|this issue (?:proposes?|aims? to)|"
        r"the goal is to)\b.{0,100}\b(?:add|introduce|support|enable|provide|"
        r"implement)\b",
        2.0,
    ),
)

_PERFORMANCE_TITLE_RULES: tuple[tuple[str, str], ...] = (
    ("performance.title.marker", r"^\s*(?:\[?perf\]?\s*:|\[performance\])"),
    (
        "performance.title.signal",
        r"\b(?:performance|benchmark(?:ing)?|optim(?:ize|ise|ization|isation)|lag(?:gy|ging)?|"
        r"latency|throughput|high overhead|high (?:memory|ram|cpu)|memory usage|"
        r"ram usage|cpu usage|heap memory|fast[- ]path|allocation|cach(?:e|ing)|"
        r"jit|parallel(?:ism)?|slower|slowdown|taking a long time|waterfall|"
        r"large file size|full table scan|more splits than|ooms?|out[- ]of[- ]memory|"
        r"recompilations?|wasted (?:disk )?space|"
        r"(?:low[- ]bit|quantized|compressed).{0,60}(?:all[- ]gather|"
        r"communication|collective))\b",
    ),
)

_PERFORMANCE_BODY_RULES: tuple[tuple[str, str], ...] = (
    (
        "performance.body.signal",
        r"\b(?:improve|reduce|lower|increase|optimi[sz]e)\b.{0,50}"
        r"\b(?:performance|latency|throughput|memory|allocation|runtime|speed)\b",
    ),
    (
        "performance.body.symptom",
        r"\b(?:too much|excessive|high)\s+(?:memory|ram|cpu|overhead)\b|"
        r"\b(?:increased?|higher|growing)\s+(?:latency|memory usage|ram usage|"
        r"cpu usage|runtime|overhead)\b|\bperformance regression\b|"
        r"\b(?:waterfall|large file size|always (?:called|executed|run) sequentially|"
        r"recompilations?|wasted (?:disk )?space)\b",
    ),
    (
        "performance.body.distributed_communication",
        r"\b(?:reduce|lower|compress|quantize|use low[- ]bit)\b.{0,80}"
        r"\b(?:communication (?:cost|volume|overhead)|all[- ]gather|collective|"
        r"distributed transfer)\b",
    ),
)

_GENERIC_FEATURE_RULE_ID = "feature.title.capability_action"
_GENERIC_FEATURE_BLOCKERS = frozenset(
    {"testing", "documentation", "build_tooling", "refactor"}
)
_TRACKER_LABELS = frozenset({"roadmap", "tracker", "tracking"})
_TRACKER_TITLE_PATTERN = re.compile(
    r"\b(?:roadmap|tracker|tracking issue|umbrella issue|discussion)\b",
    flags=re.IGNORECASE,
)
_TRACKER_ACTION_TITLE_PATTERN = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)?(?:add|allow|enable|introduce|support|provide|"
    r"implement|expose|create|generate|render|expand|fix|refactor|deprecate|"
    r"update|upgrade|write|remove|rename|move|reduce|optimi[sz]e)\b",
    flags=re.IGNORECASE,
)
_CAPABILITY_ABSENCE_PATTERN = re.compile(
    r"\b(?:does not|doesn't|do not|don't|cannot|can't)\s+support\b|"
    r"\bno support for\b|"
    r"\b(?:is|are)\s+not\s+supported\b|"
    r"\bunsupported\s+by\b|"
    r"\bnot\s+available\s+(?:in|for)\b",
    flags=re.IGNORECASE,
)
_REGRESSION_CONTEXT_PATTERN = re.compile(
    r"\b(?:regression|no longer|previously|formerly|used to|stopped working|"
    r"stopped supporting|ceased to support|existing support|support was broken|"
    r"expected to be supported|documented as supported)\b",
    flags=re.IGNORECASE,
)


def _contains(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _matching_pattern(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return pattern
    return None


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(max(value, lower), upper)


def _normalize_label(value: object) -> str:
    normalized = str(value).strip().casefold()
    normalized = re.sub(r"[_:/\\-]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _match_label_alias(
    raw_label: str,
    normalized_label: str,
) -> tuple[str, float, str] | None:
    """Match exact labels and explicitly controlled classification namespaces."""

    exact = _LABEL_ALIASES.get(normalized_label)
    if exact is not None:
        task_type, weight = exact
        return task_type, weight, f"{task_type}.label.alias"

    namespace_match = re.match(
        r"^\s*([a-z][a-z0-9_-]*)\s*[:/\\]\s*(.+?)\s*$",
        raw_label,
        flags=re.IGNORECASE,
    )
    if namespace_match is None:
        return None

    namespace = _normalize_label(namespace_match.group(1))
    value = _normalize_label(namespace_match.group(2))

    direct_task_type = _DIRECT_TASK_LABEL_NAMESPACES.get(namespace)
    if direct_task_type is not None:
        return direct_task_type, 3.0, f"{direct_task_type}.label.namespace"

    if namespace not in _CONTROLLED_LABEL_NAMESPACES:
        return None

    task_type = _NAMESPACE_VALUE_ALIASES.get(value)
    if task_type is None:
        return None
    return task_type, 3.0, f"{task_type}.label.namespace"


def _semantic_body(body: str) -> str:
    """Remove code, comments, and URLs before applying conservative body rules."""

    text = re.sub(r"<!--.*?-->", " ", body, flags=re.DOTALL)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]+`", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _matched_value(match: re.Match[str], *, maximum: int = 160) -> str:
    value = re.sub(r"\s+", " ", match.group(0)).strip()
    return value if len(value) <= maximum else f"{value[: maximum - 1]}…"


def _evidence_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _SOURCE_ORDER.get(str(item.get("source")), 99),
        str(item.get("rule_id")),
        str(item.get("matched_value")),
        str(item.get("normalized_value") or ""),
        float(item.get("weight") or 0.0),
    )


def _add_evidence(
    evidence_by_type: dict[str, list[dict[str, Any]]],
    *,
    task_type: str,
    source: str,
    rule_id: str,
    matched_value: str,
    weight: float,
    normalized_value: str | None = None,
) -> None:
    if task_type not in PUBLIC_TASK_TYPES:
        raise ValueError(f"unsupported public task type: {task_type}")
    item: dict[str, Any] = {
        "source": source,
        "rule_id": rule_id,
        "matched_value": matched_value,
        "weight": float(weight),
    }
    if normalized_value is not None:
        item["normalized_value"] = normalized_value
    bucket = evidence_by_type.setdefault(task_type, [])
    if _evidence_key(item) not in {_evidence_key(existing) for existing in bucket}:
        bucket.append(item)


def _collect_regex_evidence(
    text: str,
    *,
    source: str,
    rules: Iterable[_TextRule],
    evidence_by_type: dict[str, list[dict[str, Any]]],
) -> None:
    for rule in rules:
        match = re.search(rule.pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match is None:
            continue
        _add_evidence(
            evidence_by_type,
            task_type=rule.task_type,
            source=source,
            rule_id=rule.rule_id,
            matched_value=_matched_value(match),
            weight=rule.weight,
        )


def _collect_performance_evidence(
    *,
    label_values: tuple[tuple[str, str], ...],
    title: str,
    semantic_body: str,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for raw_label, normalized_label in label_values:
        if normalized_label in _PERFORMANCE_LABELS:
            evidence.append(
                {
                    "source": "label",
                    "rule_id": "performance.label.alias",
                    "matched_value": raw_label,
                    "normalized_value": normalized_label,
                    "weight": 3.0,
                }
            )
    for source, text, rules in (
        ("title", title, _PERFORMANCE_TITLE_RULES),
        ("body", semantic_body, _PERFORMANCE_BODY_RULES),
    ):
        for rule_id, pattern in rules:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match is not None:
                evidence.append(
                    {
                        "source": source,
                        "rule_id": rule_id,
                        "matched_value": _matched_value(match),
                        "weight": 3.0 if source != "body" else 2.0,
                    }
                )
    deduplicated = {_evidence_key(item): item for item in evidence}
    return [deduplicated[key] for key in sorted(deduplicated)]


def _suppress_generic_feature_evidence(
    evidence_by_type: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    title_blockers = {
        task_type
        for task_type in _GENERIC_FEATURE_BLOCKERS
        if any(
            item["source"] == "title"
            for item in evidence_by_type.get(task_type, [])
        )
    }
    if not title_blockers:
        return []

    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in evidence_by_type.get("feature", []):
        if item["rule_id"] != _GENERIC_FEATURE_RULE_ID:
            kept.append(item)
            continue
        rejected.append(
            {
                "task_type": "feature",
                **item,
                "reason": "suppressed_by_specific_title_target",
                "blocking_task_types": sorted(title_blockers),
            }
        )
    if kept:
        evidence_by_type["feature"] = kept
    else:
        evidence_by_type.pop("feature", None)
    return rejected


_CONTEXTUAL_TESTING_RULE_IDS = frozenset({"testing.title.explicit_work"})
_TEST_FAILURE_CONTEXT_PATTERN = re.compile(
    r"\b(?:tests?|e2e tests?|fixtures?|test collection|test runner)\b"
    r".{0,100}\b(?:fail(?:s|ed|ing|ure)?|broken|breakage|crash(?:es|ed|ing)?|"
    r"reports?.{0,40}twice|not discovered|not found|lost|regression|"
    r"consume(?:s|d)? excessive|take(?:s)? too much)\b|"
    r"\b(?:broken|breakage|failure|regression|lost)\b.{0,100}"
    r"\b(?:tests?|e2e tests?|fixtures?)\b",
    flags=re.IGNORECASE,
)
_TEST_WORK_ACTION_PATTERN = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)?(?:add|write|create|improve|expand|update|"
    r"refactor|share|move|extract|centralize)\b.*\b(?:tests?|fixtures?|"
    r"test suite|test coverage|test code)\b|"
    r"\b(?:increase|improve)\s+(?:the\s+)?(?:test|code) coverage\b",
    flags=re.IGNORECASE,
)
_GENERIC_BODY_FEATURE_RULE_IDS = frozenset(
    {
        "feature.body.capability_request",
        "feature.body.desired_capability",
        "feature.body.proposal",
    }
)


def _suppress_contextual_testing_evidence(
    *,
    title: str,
    evidence_by_type: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Drop test-context evidence when the task itself is an application bug."""

    if not _TEST_FAILURE_CONTEXT_PATTERN.search(title):
        return []
    if _TEST_WORK_ACTION_PATTERN.search(title):
        return []

    testing_items = evidence_by_type.get("testing", [])
    if any(item["source"] == "label" for item in testing_items):
        return []

    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in testing_items:
        if item["rule_id"] not in _CONTEXTUAL_TESTING_RULE_IDS:
            kept.append(item)
            continue
        rejected.append(
            {
                "task_type": "testing",
                **item,
                "reason": "suppressed_test_context_inside_bug_report",
            }
        )
    if kept:
        evidence_by_type["testing"] = kept
    else:
        evidence_by_type.pop("testing", None)
    return rejected


def _suppress_generic_body_feature_evidence(
    evidence_by_type: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Prefer a specific task intent over broad capability verbs in the body."""

    blockers = {
        task_type
        for task_type in (
            "bug_fix",
            "testing",
            "documentation",
            "build_tooling",
            "refactor",
        )
        if evidence_by_type.get(task_type)
    }
    if not blockers:
        return []

    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in evidence_by_type.get("feature", []):
        if not (
            item["source"] == "body"
            and item["rule_id"] in _GENERIC_BODY_FEATURE_RULE_IDS
        ):
            kept.append(item)
            continue
        rejected.append(
            {
                "task_type": "feature",
                **item,
                "reason": "suppressed_by_specific_task_intent",
                "blocking_task_types": sorted(blockers),
            }
        )
    if kept:
        evidence_by_type["feature"] = kept
    else:
        evidence_by_type.pop("feature", None)
    return rejected


def _reclassify_missing_capability(
    *,
    title: str,
    evidence_by_type: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Treat absent, never-supported capabilities as features, not regressions."""

    match = _CAPABILITY_ABSENCE_PATTERN.search(title)
    if match is None or _REGRESSION_CONTEXT_PATTERN.search(title):
        return []

    bug_items = evidence_by_type.get("bug_fix", [])
    strong_bug_rule_ids = {
        "bug.title.explicit_marker",
        "bug.title.runtime_failure",
        "bug.title.explicit_error",
        "bug.title.server_error",
        "bug.title.performance_symptom",
        "bug.body.explicit_bug_statement",
        "bug.body.explicit_failure",
        "bug.body.runtime_throw",
    }
    if any(
        item["source"] == "label" or item["rule_id"] in strong_bug_rule_ids
        for item in bug_items
    ):
        return []

    _add_evidence(
        evidence_by_type,
        task_type="feature",
        source="title",
        rule_id="feature.title.missing_capability",
        matched_value=_matched_value(match),
        weight=3.0,
    )

    rejected = [
        {
            "task_type": "bug_fix",
            **item,
            "reason": "reclassified_as_missing_capability",
        }
        for item in bug_items
    ]
    evidence_by_type.pop("bug_fix", None)
    return rejected


def _suppress_ambiguous_tracker_evidence(
    *,
    normalized_labels: tuple[str, ...],
    title: str,
    evidence_by_type: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Keep non-actionable roadmaps, trackers, and discussions as ``other``."""

    label_tokens = {
        token
        for label in normalized_labels
        for token in re.findall(r"[a-z0-9]+", label)
    }
    has_tracker_signal = bool(label_tokens.intersection(_TRACKER_LABELS)) or bool(
        _TRACKER_TITLE_PATTERN.search(title)
    )
    if (
        not evidence_by_type
        or not has_tracker_signal
        or _TRACKER_ACTION_TITLE_PATTERN.search(title)
    ):
        return []

    rejected = [
        {
            "task_type": task_type,
            **item,
            "reason": "suppressed_non_actionable_tracker",
        }
        for task_type, items in evidence_by_type.items()
        for item in items
    ]
    evidence_by_type.clear()
    rejected.append(
        {
            "task_type": "other",
            "source": "derived",
            "rule_id": "classification.non_actionable_tracker",
            "matched_value": title.strip() or "tracker/discussion label",
            "weight": 0.0,
            "reason": "no_single_actionable_task",
        }
    )
    return rejected


def _suppress_weak_bug_evidence(
    *,
    title: str,
    evidence_by_type: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Avoid treating a negative phrase inside explicit feature/test work as a bug."""

    if _TEST_FAILURE_CONTEXT_PATTERN.search(title):
        return []

    has_specific_title_action = any(
        item["source"] == "title"
        for task_type in ("feature", "testing", "documentation", "build_tooling", "refactor")
        for item in evidence_by_type.get(task_type, [])
    )
    if not has_specific_title_action:
        return []

    strong_bug_rule_ids = {
        "bug.title.explicit_marker",
        "bug.title.runtime_failure",
        "bug.title.server_error",
        "bug.title.build_failure",
        "bug.title.miscompilation",
        "bug.title.capability_blocked",
        "bug.body.explicit_failure",
    }
    bug_items = evidence_by_type.get("bug_fix", [])
    if any(
        item["source"] == "label" or item["rule_id"] in strong_bug_rule_ids
        for item in bug_items
    ):
        return []

    rejected = [
        {
            "task_type": "bug_fix",
            **item,
            "reason": "suppressed_inside_explicit_non_bug_task",
        }
        for item in bug_items
    ]
    evidence_by_type.pop("bug_fix", None)
    return rejected


def _has_bug_symptom(title: str, semantic_body: str) -> bool:
    bug_rules = tuple(rule for rule in _TITLE_RULES if rule.task_type == "bug_fix")
    return any(
        re.search(rule.pattern, title, flags=re.IGNORECASE | re.DOTALL)
        for rule in bug_rules
    ) or _contains(
        semantic_body,
        (
            r"\b(?:causes?|results? in|leads? to)\s+(?:an?\s+)?"
            r"(?:crash|error|failure|deadlock|exception)\b",
            r"\bperformance regression\b",
        ),
    )


def _map_performance_signal(
    *,
    accepted_types: set[str],
    evidence_by_type: dict[str, list[dict[str, Any]]],
    performance_evidence: list[dict[str, Any]],
    normalized_labels: tuple[str, ...],
    title: str,
    semantic_body: str,
) -> list[dict[str, Any]]:
    """Map a performance-only result to the six-type public contract."""

    if not performance_evidence or accepted_types:
        return []

    normalized_label_set = set(normalized_labels)
    if normalized_label_set.intersection(_TRACKER_LABELS) or _TRACKER_TITLE_PATTERN.search(title):
        return [
            {
                "task_type": "other",
                "source": "derived",
                "rule_id": "performance.map.ambiguous_tracker",
                "matched_value": performance_evidence[0]["matched_value"],
                "weight": 0.0,
                "reason": "performance_signal_without_single_actionable_task",
            }
        ]

    if _has_bug_symptom(title, semantic_body):
        target = "bug_fix"
        rule_id = "performance.map.symptom_to_bug"
    elif any(
        item["source"] == "title"
        for item in evidence_by_type.get("testing", [])
    ):
        target = "testing"
        rule_id = "performance.map.benchmark_to_testing"
    elif any(
        item["source"] == "title"
        for item in evidence_by_type.get("feature", [])
    ):
        target = "feature"
        rule_id = "performance.map.capability_to_feature"
    else:
        target = "refactor"
        rule_id = "performance.map.optimization_to_refactor"

    _add_evidence(
        evidence_by_type,
        task_type=target,
        source="derived",
        rule_id=rule_id,
        matched_value=str(performance_evidence[0]["matched_value"]),
        weight=3.0,
    )
    accepted_types.add(target)
    return []


def _classify_task_types(
    *,
    title: str,
    body: str,
    labels: list[str],
) -> tuple[tuple[str, ...], dict[str, Any], bool]:
    label_values = tuple(
        sorted(
            {
                (str(label).strip(), _normalize_label(label))
                for label in labels
                if str(label).strip()
            },
            key=lambda item: (item[1], item[0].casefold(), item[0]),
        )
    )
    normalized_labels = tuple(normalized for _, normalized in label_values)
    semantic_body = _semantic_body(body)
    evidence_by_type: dict[str, list[dict[str, Any]]] = {}

    for raw_label, normalized_label in label_values:
        alias = _match_label_alias(raw_label, normalized_label)
        if alias is not None:
            task_type, weight, rule_id = alias
            _add_evidence(
                evidence_by_type,
                task_type=task_type,
                source="label",
                rule_id=rule_id,
                matched_value=raw_label,
                normalized_value=normalized_label,
                weight=weight,
            )
        for rule in _LABEL_PATTERN_RULES:
            match = re.search(rule.pattern, normalized_label, flags=re.IGNORECASE)
            if match is None:
                continue
            _add_evidence(
                evidence_by_type,
                task_type=rule.task_type,
                source="label",
                rule_id=rule.rule_id,
                matched_value=raw_label,
                normalized_value=normalized_label,
                weight=rule.weight,
            )

    _collect_regex_evidence(
        title,
        source="title",
        rules=_TITLE_RULES,
        evidence_by_type=evidence_by_type,
    )
    _collect_regex_evidence(
        semantic_body,
        source="body",
        rules=_BODY_RULES,
        evidence_by_type=evidence_by_type,
    )

    rejected = _reclassify_missing_capability(
        title=title,
        evidence_by_type=evidence_by_type,
    )
    rejected.extend(_suppress_generic_feature_evidence(evidence_by_type))
    rejected.extend(
        _suppress_contextual_testing_evidence(
            title=title,
            evidence_by_type=evidence_by_type,
        )
    )
    rejected.extend(_suppress_generic_body_feature_evidence(evidence_by_type))
    rejected.extend(
        _suppress_weak_bug_evidence(
            title=title,
            evidence_by_type=evidence_by_type,
        )
    )
    rejected.extend(
        _suppress_ambiguous_tracker_evidence(
            normalized_labels=normalized_labels,
            title=title,
            evidence_by_type=evidence_by_type,
        )
    )
    performance_evidence = _collect_performance_evidence(
        label_values=label_values,
        title=title,
        semantic_body=semantic_body,
    )

    scores = {
        task_type: round(sum(float(item["weight"]) for item in items), 2)
        for task_type, items in evidence_by_type.items()
    }
    accepted_types = {
        task_type
        for task_type, score in scores.items()
        if score >= _TASK_TYPE_ACCEPTANCE_SCORE
    }

    rejected.extend(
        {
            "task_type": task_type,
            **item,
            "reason": "below_acceptance_threshold",
            "task_type_score": scores[task_type],
        }
        for task_type, items in evidence_by_type.items()
        if task_type not in accepted_types
        for item in items
    )

    mapping_rejections = _map_performance_signal(
        accepted_types=accepted_types,
        evidence_by_type=evidence_by_type,
        performance_evidence=performance_evidence,
        normalized_labels=normalized_labels,
        title=title,
        semantic_body=semantic_body,
    )
    rejected.extend(mapping_rejections)

    # Recompute after a possible performance-derived mapping.
    scores = {
        task_type: round(sum(float(item["weight"]) for item in items), 2)
        for task_type, items in evidence_by_type.items()
    }
    accepted_types = {
        task_type
        for task_type, score in scores.items()
        if score >= _TASK_TYPE_ACCEPTANCE_SCORE
    }
    task_types = tuple(sorted(accepted_types)) if accepted_types else ("other",)

    accepted_evidence = {
        task_type: sorted(evidence_by_type[task_type], key=_evidence_key)
        for task_type in sorted(accepted_types)
    }
    classification_evidence = {
        "task_type_evidence": accepted_evidence,
        "task_type_scores": {
            task_type: scores[task_type] for task_type in sorted(scores)
        },
        "auxiliary_signals": {
            "performance": sorted(performance_evidence, key=_evidence_key)
        },
        "rejected_task_type_evidence": sorted(
            rejected,
            key=lambda item: (
                str(item.get("task_type")),
                *_evidence_key(item),
                str(item.get("reason")),
            ),
        ),
    }
    return task_types, classification_evidence, bool(performance_evidence)


def extract_task_features(record: dict[str, Any]) -> TaskFeatures:
    title = str(record.get("title") or "")
    body = str(record.get("body_text") or "")
    text = f"{title}\n{body}"
    labels = [str(label) for label in (record.get("labels") or [])]
    label_text = " ".join(labels).casefold()

    reproduction = _contains(
        text,
        (
            r"steps? to reproduce",
            r"reproduction",
            r"reproducible example",
            r"minimal (?:example|reproducer)",
            r"how to reproduce",
        ),
    )
    acceptance = _contains(
        text,
        (r"acceptance criteria", r"definition of done", r"^- \[ \]", r"\n- \[ \]"),
    )
    expected = _contains(
        text, (r"expected behavio(?:u)?r", r"expected result", r"what .* should")
    )
    affected = _contains(
        text,
        (
            r"`[^`\n]*(?:/|\\)[^`\n]+`",
            r"`[^`\n]+\.(?:py|js|ts|tsx|jsx|rs|go|java|cpp|c|h)`",
            r"(?:module|component|package|backend|subsystem):?\s+[\w./-]+",
        ),
    )
    has_code_block = "```" in body

    clarity = 0.0
    clarity += 20 if len(title.strip()) >= 15 else 10 if title.strip() else 0
    clarity += 20 if len(body) >= 200 else 10 if len(body) >= 80 else 0
    clarity += 15 if reproduction else 0
    clarity += 15 if acceptance else 0
    clarity += 15 if expected else 0
    clarity += 10 if affected else 0
    clarity += 5 if has_code_block else 0

    task_types, classification_evidence, has_performance_signal = _classify_task_types(
        title=title,
        body=body,
        labels=labels,
    )
    task_type_set = set(task_types)

    # Preserve the v0.1 difficulty behavior while performance becomes an
    # auxiliary signal rather than a seventh public task type.
    code_difficulty = 1
    if "documentation" in task_type_set and len(task_type_set) == 1:
        code_difficulty = 0
    if "feature" in task_type_set or "refactor" in task_type_set:
        code_difficulty = max(code_difficulty, 2)
    if has_performance_signal or any(
        signal in label_text for signal in ("core", "architecture", "api change")
    ):
        code_difficulty = 3
    if has_newcomer_label(labels):
        code_difficulty = min(code_difficulty, 1)

    setup_difficulty = 1
    if "documentation" in task_type_set and len(task_type_set) == 1:
        setup_difficulty = 0
    if _contains(
        text,
        (
            r"compile",
            r"native",
            r"toolchain",
            r"backend",
            r"macos|windows|linux",
            r"docker|kubernetes",
        ),
    ):
        setup_difficulty = 2

    context_difficulty = 1
    if any(signal in label_text for signal in ("core", "architecture", "api")):
        context_difficulty = 2
    if "refactor" in task_type_set or has_performance_signal:
        context_difficulty = 3
    if has_newcomer_label(labels):
        context_difficulty = min(context_difficulty, 1)

    comments = int(record.get("comment_count") or 0)
    collaboration_difficulty = 0 if comments < 3 else 1 if comments < 10 else 2
    if _contains(label_text, (r"discussion", r"design")):
        collaboration_difficulty = max(collaboration_difficulty, 2)

    difficulty_sum = (
        code_difficulty + setup_difficulty + context_difficulty + collaboration_difficulty
    )
    if difficulty_sum <= 2:
        effort = "under_2h"
    elif difficulty_sum <= 4:
        effort = "half_day"
    elif difficulty_sum <= 6:
        effort = "one_day"
    else:
        effort = "multi_day"

    eligible = record.get("candidate_eligibility") == "eligible"
    novice = (
        0.25
        + (0.30 if has_newcomer_label(labels) else 0.0)
        + clarity / 100.0 * 0.25
        + (3 - code_difficulty) / 3.0 * 0.20
        - setup_difficulty / 3.0 * 0.10
        - context_difficulty / 3.0 * 0.10
    )
    novice = _clamp(novice) if eligible else 0.0
    growth = (
        clarity / 100.0 * 25
        + code_difficulty / 3.0 * 35
        + context_difficulty / 3.0 * 25
        + min(len(task_types), 3) / 3.0 * 15
    )
    growth = _clamp(growth / 100.0) * 100 if eligible else 0.0

    evidence = {
        "title_length": len(title),
        "body_length": len(body),
        "has_code_block": has_code_block,
        "newcomer_label_signal": has_newcomer_label(labels),
        "comment_count": comments,
        "formula_version": TASK_FEATURE_VERSION,
        **classification_evidence,
    }
    return TaskFeatures(
        has_reproduction_steps=reproduction,
        has_acceptance_criteria=acceptance,
        has_expected_behavior=expected,
        has_affected_module_hint=affected,
        task_types=task_types,
        text_clarity_score=round(clarity, 2),
        estimated_code_difficulty=code_difficulty,
        estimated_setup_difficulty=setup_difficulty,
        estimated_project_context_difficulty=context_difficulty,
        estimated_collaboration_difficulty=collaboration_difficulty,
        estimated_effort_bucket=effort,
        novice_fit_probability=round(novice, 3),
        newcomer_score=round(novice * 100, 2),
        growth_value_score=round(growth, 2),
        feature_evidence=evidence,
    )


def infer_skill_requirements(
    record: dict[str, Any], features: TaskFeatures
) -> tuple[SkillRequirement, ...]:
    requirements: dict[str, SkillRequirement] = {}
    language = str(record.get("primary_language") or "").strip()
    if language:
        requirements[language.casefold()] = SkillRequirement(
            language,
            max(1, min(features.estimated_code_difficulty, 3)),
            1.0,
            "repository_primary_language",
        )
    for task_type in features.task_types:
        if task_type == "other":
            continue
        requirements[task_type.casefold()] = SkillRequirement(
            task_type,
            1 if features.estimated_code_difficulty <= 1 else 2,
            0.6,
            "inferred_task_type",
        )

    title_and_labels = (
        f"{record.get('title') or ''}\n"
        + " ".join(str(value) for value in record.get("labels") or [])
    ).casefold()
    body = str(record.get("body_text") or "").casefold()
    platform_patterns = {
        "macos": ("macos", "osx", "macosx"),
        "windows": ("windows", "win32"),
        "linux": ("linux",),
    }
    explicit_platforms = {
        platform
        for platform, patterns in platform_patterns.items()
        if any(pattern in title_and_labels for pattern in patterns)
    }
    detected_platforms = explicit_platforms or {
        platform
        for platform, patterns in platform_patterns.items()
        if any(pattern in body for pattern in patterns)
    }
    for platform in sorted(detected_platforms):
        name = f"platform:{platform}"
        requirements[name] = SkillRequirement(
            name, 1, 1.0, "explicit_platform_signal"
        )
    return tuple(sorted(requirements.values(), key=lambda item: item.skill_name.casefold()))