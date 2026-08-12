"""Explainable text features and two-track ranking baselines."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from oss_mentor.candidate_rules import has_newcomer_label
from oss_mentor.developer_profiles import ALLOWED_TASK_TYPES


TASK_FEATURE_VERSION = "task-features-v0.4"
DIFFICULTY_FORMULA_VERSION = "difficulty-rules-v0.2.1"
SKILL_REQUIREMENT_RULES_VERSION = "skill-requirements-v0.2.2"
PUBLIC_TASK_TYPES = frozenset(ALLOWED_TASK_TYPES)
_TASK_TYPE_ACCEPTANCE_SCORE = 3.0
_SOURCE_ORDER = {"label": 0, "title": 1, "body": 2, "derived": 3}
_DIFFICULTY_SOURCE_ORDER = {"label": 0, "title": 1, "body": 2, "derived": 3}
_DIFFICULTY_STRENGTH_ORDER = {"weak": 0, "medium": 1, "strong": 2}
_DIFFICULTY_DIMENSIONS = frozenset(
    {"code", "setup", "project_context", "collaboration"}
)
_ACTIONABILITY_VALUES = frozenset(
    {"actionable", "design_pending", "unclear", "non_actionable"}
)
_EFFORT_SCOPE_ORDER = {
    "micro": 0,
    "local": 1,
    "module": 2,
    "cross_module": 3,
    "system": 4,
    "unclear": 5,
    "non_actionable": 6,
}
_NON_ACTIONABLE_EFFORT_PLACEHOLDER = "multi_day"


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
class _SkillSignal:
    skill_name: str
    category: str
    role: str | None
    source: str
    rule_id: str
    matched_value: str
    normalized_value: str
    strength: str
    reason: str
    decision: str
    matching_facing: bool
    minimum_level: int | None = None
    importance: float | None = None
    requirement_source: str | None = None


@dataclass(frozen=True, slots=True)
class _SkillInferenceResult:
    requirements: tuple[SkillRequirement, ...]
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _TextRule:
    task_type: str
    rule_id: str
    pattern: str
    weight: float


@dataclass(frozen=True, slots=True)
class _DifficultyContext:
    title: str
    body: str
    semantic_body: str
    labels: tuple[str, ...]
    normalized_labels: tuple[str, ...]
    task_types: tuple[str, ...]
    performance_signal: bool
    comment_count: int
    has_reproduction_steps: bool
    has_acceptance_criteria: bool
    has_expected_behavior: bool
    has_affected_module_hint: bool


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


def _difficulty_evidence_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(item.get("dimension") or ""),
        _DIFFICULTY_SOURCE_ORDER.get(str(item.get("source") or ""), 99),
        str(item.get("rule_id") or ""),
        str(item.get("matched_value") or ""),
        _DIFFICULTY_STRENGTH_ORDER.get(str(item.get("strength") or ""), 99),
        int(item.get("suggested_level") or 0),
        str(item.get("reason") or ""),
    )


def _add_difficulty_evidence(
    bucket: list[dict[str, Any]],
    *,
    dimension: str,
    source: str,
    rule_id: str,
    matched_value: str,
    strength: str,
    suggested_level: int,
    reason: str,
) -> None:
    if dimension not in _DIFFICULTY_DIMENSIONS:
        raise ValueError(f"unsupported difficulty dimension: {dimension}")
    if source not in _DIFFICULTY_SOURCE_ORDER:
        raise ValueError(f"unsupported difficulty evidence source: {source}")
    if strength not in _DIFFICULTY_STRENGTH_ORDER:
        raise ValueError(f"unsupported difficulty evidence strength: {strength}")
    if not 0 <= suggested_level <= 3:
        raise ValueError(f"invalid suggested difficulty level: {suggested_level}")

    item = {
        "dimension": dimension,
        "source": source,
        "rule_id": rule_id,
        "matched_value": re.sub(r"\s+", " ", str(matched_value)).strip()[:160],
        "strength": strength,
        "suggested_level": int(suggested_level),
        "reason": reason,
    }
    key = _difficulty_evidence_key(item)
    if key not in {_difficulty_evidence_key(existing) for existing in bucket}:
        bucket.append(item)


def _collect_difficulty_regex_evidence(
    text: str,
    *,
    source: str,
    dimension: str,
    rules: Iterable[tuple[str, str, str, int, str]],
    bucket: list[dict[str, Any]],
) -> None:
    for rule_id, pattern, strength, suggested_level, reason in rules:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match is None:
            continue
        _add_difficulty_evidence(
            bucket,
            dimension=dimension,
            source=source,
            rule_id=rule_id,
            matched_value=_matched_value(match),
            strength=strength,
            suggested_level=suggested_level,
            reason=reason,
        )


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


def _build_difficulty_context(
    *,
    title: str,
    body: str,
    labels: list[str],
    task_types: tuple[str, ...],
    performance_signal: bool,
    comment_count: int,
    has_reproduction_steps: bool,
    has_acceptance_criteria: bool,
    has_expected_behavior: bool,
    has_affected_module_hint: bool,
) -> _DifficultyContext:
    raw_labels = tuple(sorted({str(value).strip() for value in labels if str(value).strip()}, key=lambda value: (value.casefold(), value)))
    normalized_labels = tuple(sorted({_normalize_label(value) for value in raw_labels if _normalize_label(value)}))
    return _DifficultyContext(
        title=title,
        body=body,
        semantic_body=_semantic_body(body),
        labels=raw_labels,
        normalized_labels=normalized_labels,
        task_types=tuple(task_types),
        performance_signal=bool(performance_signal),
        comment_count=max(int(comment_count), 0),
        has_reproduction_steps=bool(has_reproduction_steps),
        has_acceptance_criteria=bool(has_acceptance_criteria),
        has_expected_behavior=bool(has_expected_behavior),
        has_affected_module_hint=bool(has_affected_module_hint),
    )


def _assess_information_quality(context: _DifficultyContext) -> dict[str, Any]:
    title = context.title.strip()
    semantic_body = context.semantic_body.strip()
    normalized_labels = set(context.normalized_labels)
    combined = f"{title}\n{semantic_body}".strip()
    reasons: list[str] = []

    non_actionable = bool(
        normalized_labels.intersection({"roadmap", "tracker", "tracking"})
        or re.search(
            r"\b(?:roadmap|tracking issue|umbrella issue|umbrella milestone|"
            r"milestone tracker|dependency dashboard)\b",
            combined,
            flags=re.IGNORECASE,
        )
        or (
            re.search(r"\btracker\b", title, flags=re.IGNORECASE)
            and not _TRACKER_ACTION_TITLE_PATTERN.search(title)
        )
    )
    if non_actionable:
        reasons.append("non_actionable_tracker_or_dashboard")

    explicit_design_signal = bool(
        any(
            marker in normalized_labels
            for marker in (
                "needs discussion",
                "discussion",
                "api design",
                "proposal",
                "rfc",
                "pep request",
            )
        )
        or re.search(
            r"(?:^|\b)(?:rfc|proposal|api design|needs discussion|design discussion)\b|"
            r"\b(?:multiple|several|alternative)\s+(?:options|approaches|designs)\b",
            combined,
            flags=re.IGNORECASE,
        )
    )

    unresolved_design_choice = bool(
        re.search(
            r"^\s*(?:should\s+(?:we\s+)?(?:use|adopt|switch|choose|hash|store|move|replace)|"
            r"should\s+[^?]{1,120}\?)",
            title,
            flags=re.IGNORECASE,
        )
        and re.search(
            r"\b(?:risk|trade[- ]?off|collision|clash|compatib|migration|alternative|"
            r"option|approach|advantage|disadvantage|pros?\b|cons?\b)\b",
            combined,
            flags=re.IGNORECASE,
        )
        and not re.search(
            r"\b(?:should return|should preserve|should support|should produce|"
            r"expected behavior|expected result)\b",
            combined,
            flags=re.IGNORECASE,
        )
    )
    design_pending = explicit_design_signal or unresolved_design_choice
    if design_pending:
        reasons.append(
            "unresolved_design_choice" if unresolved_design_choice else "design_or_discussion_pending"
        )

    body_missing = not context.body.strip()
    if body_missing:
        reasons.append("body_missing")

    support_question = bool(
        re.search(
            r"^\s*(?:how\s+(?:do|does|can|to)|why\s+(?:do|does|is|are|the)|"
            r"what\s+(?:do|does|is|are)|question\s+about|help\s*:)\b",
            title,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\b(?:what do i miss|i am asking how|can someone explain|"
            r"is this expected behavior|why is this happening)\b",
            semantic_body,
            flags=re.IGNORECASE,
        )
    )
    if support_question:
        reasons.append("support_question")

    issue_template_action = bool(
        re.search(
            r"\b(?:describe the solution you(?:'|’)d like|what did you do\??|"
            r"what did you expect to see\??|what did you see instead\??|"
            r"minified repro|steps? to reproduce|how to reproduce|"
            r"expected behavior|actual results?|expected results?)\b",
            combined,
            flags=re.IGNORECASE,
        )
    )
    explicit_implementation_steps = bool(
        re.search(
            r"\b(?:implement|add support|change|move|refactor|fix|remove|deprecate|"
            r"introduce|integrate|prevent|avoid|return\s+404|do not retry|"
            r"must preserve|must support|should return|should preserve)\b",
            combined,
            flags=re.IGNORECASE,
        )
    )
    has_actionable_signal = bool(
        context.has_reproduction_steps
        or context.has_acceptance_criteria
        or context.has_expected_behavior
        or issue_template_action
        or explicit_implementation_steps
        or re.search(
            r"^\s*(?:add|allow|enable|introduce|support|provide|implement|expose|"
            r"create|generate|render|expand|fix|refactor|deprecate|update|upgrade|"
            r"write|remove|rename|move|reduce|optimi[sz]e|share|avoid)\b",
            title,
            flags=re.IGNORECASE,
        )
    )

    if non_actionable:
        actionability = "non_actionable"
    elif design_pending:
        actionability = "design_pending"
    elif body_missing or support_question or not has_actionable_signal:
        actionability = "unclear"
        if not body_missing and not support_question:
            reasons.append("actionable_scope_not_explicit")
    else:
        actionability = "actionable"

    if actionability in {"non_actionable", "unclear"}:
        confidence = "low"
    elif actionability == "design_pending":
        confidence = "medium"
    else:
        clarity_signals = sum(
            (
                context.has_reproduction_steps,
                context.has_acceptance_criteria,
                context.has_expected_behavior,
                context.has_affected_module_hint,
                issue_template_action,
            )
        )
        confidence = "high" if len(semantic_body) >= 200 and clarity_signals >= 2 else "medium"

    return {
        "body_missing": body_missing,
        "actionability": actionability,
        "confidence": confidence,
        "reasons": sorted(set(reasons)),
    }

def _difficulty_priors(
    task_types: tuple[str, ...],
    information_quality: dict[str, Any],
) -> dict[str, int]:
    del information_quality
    documentation_only = set(task_types) == {"documentation"}
    return {
        "code": 0 if documentation_only else 1,
        "setup": 0 if documentation_only else 1,
        "project_context": 0 if documentation_only else 1,
        "collaboration": 0,
    }


def _collect_code_difficulty_evidence(
    context: _DifficultyContext,
    information_quality: dict[str, Any],
) -> list[dict[str, Any]]:
    del information_quality
    evidence: list[dict[str, Any]] = []
    title = context.title
    body = context.semantic_body
    combined = f"{title}\n{body}"
    labels = "\n".join(context.normalized_labels)
    task_type_set = set(context.task_types)
    documentation_only = task_type_set == {"documentation"}
    runtime_validation = bool(
        re.search(
            r"\b(?:run|execute|reproduce|validate|verify|test|compare)\b.{0,100}"
            r"\b(?:runtime|application|behavior|output|result|example|method|implementation)\b|"
            r"\b(?:expected|actual)\s+(?:behavior|results?)\b",
            combined,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )

    documentation_semantic_validation = documentation_only and bool(
        re.search(
            r"\b(?:expected results?|actual results?|different answers?|conditional|interventional|"
            r"semantic|method\s*=|behavior differs?)\b",
            combined,
            flags=re.IGNORECASE,
        )
    )
    if documentation_only and (not runtime_validation or documentation_semantic_validation):
        _add_difficulty_evidence(
            evidence,
            dimension="code",
            source="derived",
            rule_id="difficulty.code.no_code.documentation",
            matched_value="documentation-only task",
            strength="strong",
            suggested_level=0,
            reason=(
                "documentation_semantic_verification_without_code_change"
                if documentation_semantic_validation
                else "documentation_without_code_change"
            ),
        )
    elif documentation_only and runtime_validation:
        _add_difficulty_evidence(
            evidence,
            dimension="code",
            source="derived",
            rule_id="difficulty.code.documentation_runtime_validation",
            matched_value="runtime validation required",
            strength="medium",
            suggested_level=1,
            reason="documentation_requires_runtime_validation",
        )

    # Keep source attribution precise: title rules only inspect title; body rules only inspect body.
    for source, source_text in (("title", title), ("body", body)):
        _collect_difficulty_regex_evidence(
            source_text,
            source=source,
            dimension="code",
            bucket=evidence,
            rules=(
                (
                    "difficulty.code.local_change",
                    r"\b(?:typo|wording|readme|rename|single (?:file|function|assertion)|"
                    r"one assertion|local change|configuration key|config value)\b",
                    "medium",
                    1,
                    "localized_low_risk_change",
                ),
                (
                    "difficulty.code.nontrivial_logic",
                    r"\b(?:non[- ]trivial logic|state machine|query planner|cache invalidation|"
                    r"index traversal|serialization logic|runtime validator|parser state|"
                    r"multiple functions|across several files|finite state)\b",
                    "medium",
                    2,
                    "nontrivial_implementation_logic",
                ),
                (
                    "difficulty.code.cross_module",
                    r"\b(?:cross[- ]module|across (?:multiple|all) modules|shared framework|"
                    r"multiple subsystems)\b",
                    "medium",
                    2,
                    "cross_module_implementation",
                ),
                (
                    "difficulty.code.core_architecture",
                    r"\b(?:core architecture|architectural core|global invariant|"
                    r"system[- ]wide invariant)\b",
                    "strong",
                    3,
                    "core_architecture_change",
                ),
                (
                    "difficulty.code.concurrent_or_distributed",
                    r"\b(?:deadlock|race condition|distributed (?:state|consensus|transaction)|"
                    r"multi[- ]node coordination)\b",
                    "strong",
                    3,
                    "concurrent_or_distributed_core_logic",
                ),
                (
                    "difficulty.code.compiler_or_protocol",
                    r"\b(?:compiler semantics|compiler backend|code generation|"
                    r"core protocol|protocol semantics|wire protocol|query execution engine|"
                    r"storage engine|segment reader)\b",
                    "strong",
                    3,
                    "compiler_protocol_or_core_engine",
                ),
            ),
        )

    def add_composite(rule_id: str, matched_value: str, strength: str, level: int, reason: str) -> None:
        _add_difficulty_evidence(
            evidence,
            dimension="code",
            source="derived",
            rule_id=rule_id,
            matched_value=matched_value,
            strength=strength,
            suggested_level=level,
            reason=reason,
        )

    # C2-A: execution/storage traversal + concrete change + measurable behavior.
    traversal_domain = re.search(r"\b(?:query|scan|reader|segment|storage|index|rows? scanned|docs? scanned)\b", combined, re.I)
    traversal_change = re.search(r"\b(?:read(?:ing)? from (?:the )?bottom|reverse (?:read|scan|traversal)|avoid (?:a )?full scan|scan strategy|reading whole segment|full segment scan)\b", combined, re.I)
    traversal_validation = re.search(r"\b(?:latency|benchmark|correctness|rows? scanned|docs? scanned|time taken|timetaken)\b", combined, re.I)
    if traversal_domain and traversal_change and traversal_validation:
        add_composite("difficulty.code.composite.execution_traversal", _matched_value(traversal_change), "medium", 2, "execution_or_storage_traversal_change_with_validation")

    # C2-B: profiled memory/CPU subsystem.
    profiler = re.search(r"\b(?:pprof|profiler|profiling|heap profile|flame ?graph)\b", combined, re.I)
    resource = re.search(r"\b(?:heap|memory|allocation|allocations|cpu|system load)\b", combined, re.I)
    named_subsystem = re.search(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*|subsystem|agent mode|remote write|classloader|runtime)\b", combined)
    if profiler and resource and named_subsystem:
        add_composite("difficulty.code.composite.profiled_subsystem", _matched_value(profiler), "medium", 2, "profiled_resource_bottleneck_in_named_subsystem")

    # C2-C: real-time geometry / interaction state.
    interaction = re.search(r"\b(?:drag|dragging|move|moving|position|positioning)\b", combined, re.I)
    geometry = re.search(r"\b(?:alignment|guide lines?|spacing|snap(?:ping)?|geometry|evenly distributed)\b", combined, re.I)
    realtime = re.search(r"\b(?:real[- ]time|temporary guide|visual feedback|while (?:moving|dragging)|when (?:moving|positioning))\b", combined, re.I)
    if interaction and geometry and realtime:
        add_composite("difficulty.code.composite.interactive_geometry", _matched_value(geometry), "medium", 2, "realtime_geometry_and_interaction_state")

    # Scaled interactive performance is non-trivial but not automatically architecture-level.
    scale = re.search(r"\b(?:\d{3,}[\s–-]*(?:elements|objects|characters)|thousands? of (?:elements|objects)|large canvas)\b", combined, re.I)
    lag = re.search(r"\b(?:lag|lagging|slow|jank|scroll|render|latency)\b", combined, re.I)
    if scale and lag and context.performance_signal:
        add_composite("difficulty.code.composite.scaled_interactive_performance", _matched_value(scale), "medium", 2, "interactive_performance_degrades_at_explicit_scale")

    # C2-D: exception + retry + outward mapping.
    exception_signal = re.search(r"\b(?:FileNotFound(?:Exception)?|[A-Za-z]+Exception|exception|error)\b", combined, re.I)
    retry_signal = re.search(r"\b(?:retry|retries|attempts? exceeded|do not retry|without retry)\b", combined, re.I)
    outward_mapping = re.search(r"\b(?:http|status|404|500|wrap(?:ped|ping)?|rest|api response)\b", combined, re.I)
    if exception_signal and retry_signal and outward_mapping:
        add_composite("difficulty.code.composite.exception_retry_path", _matched_value(retry_signal), "medium", 2, "exception_retry_and_api_mapping_span_multiple_layers")

    # C2-E: lifecycle + framework integration.
    lifecycle = re.search(r"\b(?:classloader|lifecycle|reload(?:ing)?|memory leak|leak(?:ing)?)\b", combined, re.I)
    framework = re.search(r"\b(?:test(?:ing)? framework|shared framework|framework integration|integrat(?:e|ion).{0,60}framework)\b", combined, re.I | re.S)
    regression = re.search(r"\b(?:regression|prevent future|defen[cs]e|ensure|flaky ci|test utility)\b", combined, re.I)
    if lifecycle and framework and regression:
        add_composite("difficulty.code.composite.lifecycle_framework", _matched_value(lifecycle), "medium", 2, "lifecycle_behavior_integrated_into_shared_framework")

    # C3-A: compiler multi-path/shared behavior.
    compiler_domain = re.search(r"\b(?:torch\.compile|compiler|graph|guard|dynamic shapes?|tracing|decomposition)\b", f"{labels}\n{combined}", re.I)
    compiler_behavior = re.search(r"\b(?:recompil(?:e|ation|ations)|graph cache|guard behavior|codegen|code generation)\b", combined, re.I)
    multi_path = re.search(r"\b(?:multiple|several|following)\b.{0,80}\b(?:ops?|operators?|paths?|variants?)\b|\b(?:bmm|topk|cholesky|linalg\.norm|max)\b.{0,180}\b(?:bmm|topk|cholesky|linalg\.norm|max)\b", combined, re.I | re.S)
    if compiler_domain and compiler_behavior and multi_path:
        add_composite("difficulty.code.composite.compiler_multi_path", _matched_value(compiler_behavior), "strong", 3, "compiler_behavior_affects_multiple_operations_or_paths")

    # C3-B: distributed implementation must include an implementation/validation action.
    distributed = re.search(r"\b(?:fsdp2?|tensor parallel(?:ism)?|all[- ]gather|collective communication|distributed state|multi[- ]node)\b", combined, re.I)
    distributed_action = re.search(r"\b(?:implement|support|add|change|run|test|benchmark|validate|verify|fix)\b", combined, re.I)
    if distributed and distributed_action:
        add_composite("difficulty.code.composite.distributed_implementation", _matched_value(distributed), "strong", 3, "distributed_implementation_or_validation_is_core_task_work")

    # C3-C: explicit algorithm implementation/replacement + benchmark/correctness.
    algorithm_change = re.search(r"\b(?:implement|missing|add|replace|port)\b.{0,80}\b(?:algorithm|bor[uů]vka|solver|indexing algorithm)\b|\b(?:algorithm|bor[uů]vka)\b.{0,80}\b(?:not implemented|missing|implement|replace)\b", combined, re.I)
    algorithm_validation = re.search(r"\b(?:benchmark|performance|faster|slower|correctness|speedup|\d+(?:\.\d+)?\s*(?:x|times))\b", combined, re.I)
    if algorithm_change and algorithm_validation:
        add_composite("difficulty.code.composite.algorithm_implementation", _matched_value(algorithm_change), "strong", 3, "algorithm_implementation_requires_performance_or_correctness_validation")

    # C3-D: API semantic ambiguity + multiple behavior paths + compatibility/design constraints.
    semantic_ambiguity = re.search(r"\b(?:ambiguous|ambiguity|multiple interpretations?|two interpretations?|same syntax|same api)\b", combined, re.I)
    behavior_paths = re.search(r"\b(?:getitem|setitem|read|write|existing|missing|scalar|slice|multiple paths?|different contexts?)\b", combined, re.I)
    compatibility = re.search(r"\b(?:backward compatibility|existing user code|historical behavior|heuristic|design alternative|api design)\b", f"{labels}\n{combined}", re.I)
    if semantic_ambiguity and behavior_paths and compatibility:
        add_composite("difficulty.code.composite.api_semantic_ambiguity", _matched_value(semantic_ambiguity), "strong", 3, "api_semantics_are_ambiguous_across_multiple_behavior_paths")

    # C3-E: multiple layers + lifecycle/correctness concerns + broad surface.
    layers = re.search(r"\b(?:broker.{0,80}server|client.{0,80}server|multiple (?:layers|subsystems)|two[- ]level|multi[- ]layer)\b", combined, re.I | re.S)
    lifecycle_correctness = re.search(r"\b(?:invalidation|version(?:ed|ing)|lifecycle|correctness|consistency|cache key)\b", combined, re.I)
    broad_surface = re.search(r"\b(?:configuration|metrics|tracing|observability|pluggable|both (?:broker|client).{0,80}(?:server|backend)|server segment result cache)\b", combined, re.I | re.S)
    if layers and lifecycle_correctness and broad_surface:
        add_composite("difficulty.code.composite.multi_layer_architecture", _matched_value(layers), "strong", 3, "multi_layer_architecture_with_correctness_and_operational_surface")

    if "testing" in task_type_set:
        match = re.search(
            r"\b(?:flaky|integration|end[- ]to[- ]end|e2e|shared state|timing|"
            r"race condition|periodic task)\b",
            combined,
            flags=re.IGNORECASE,
        )
        if match is not None:
            add_composite("difficulty.code.integration_test_state", _matched_value(match), "medium", 2, "integration_or_flaky_test_state")

    if "testing" in task_type_set:
        property_test = re.search(r"\b(?:property[- ]based|property testing|schema[- ]driven)\b", combined, re.I)
        fixture_scope = re.search(r"\b(?:fixtures?|api fixtures?|endpoints?|schema)\b", combined, re.I)
        if property_test and fixture_scope:
            add_composite("difficulty.code.composite.property_testing", _matched_value(property_test), "medium", 2, "schema_or_property_testing_requires_nontrivial_test_logic")

    if "build_tooling" in task_type_set:
        match = re.search(r"\b(?:native toolchain|compiler toolchain|cross[- ]compil|linker|build graph|package resolver)\b", combined, re.I)
        if match is not None:
            add_composite("difficulty.code.complex_build_tooling", _matched_value(match), "medium", 2, "nontrivial_build_tooling_logic")

    if context.performance_signal:
        add_composite("difficulty.code.performance_auxiliary", "performance auxiliary signal", "weak", 2, "performance_signal_requires_supporting_scope_evidence")

    return sorted(evidence, key=_difficulty_evidence_key)

def _collect_setup_difficulty_evidence(
    context: _DifficultyContext,
    information_quality: dict[str, Any],
) -> list[dict[str, Any]]:
    del information_quality
    evidence: list[dict[str, Any]] = []
    combined = f"{context.title}\n{context.semantic_body}"
    documentation_only = set(context.task_types) == {"documentation"}
    runtime_required = bool(
        re.search(
            r"\b(?:run|execute|reproduce|validate|verify|test|benchmark|profile)\b.{0,100}"
            r"\b(?:runtime|application|service|cluster|backend|platform|filesystem|volume|deployment)\b",
            combined,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )

    semantic_validation = documentation_only and bool(
        re.search(r"\b(?:steps? to reproduce|expected results?|actual results?|compare|method\s*=|behavior differs?)\b", combined, re.I)
    )
    if documentation_only and not runtime_required and not semantic_validation:
        _add_difficulty_evidence(
            evidence,
            dimension="setup",
            source="derived",
            rule_id="difficulty.setup.no_runtime.documentation",
            matched_value="documentation-only task",
            strength="strong",
            suggested_level=0,
            reason="documentation_without_runtime_environment",
        )
        return sorted(evidence, key=_difficulty_evidence_key)

    reported_environment = re.search(
        r"\b(?:environment|operating system|os|versions?)\s*:?\s*"
        r"(?:mac\s?os|macos|windows|linux|docker|kubernetes|k8s|podman)\b|"
        r"\b(?:mac\s?os|macos|windows|linux|docker|kubernetes|k8s|podman)\s+version\b",
        combined,
        flags=re.IGNORECASE,
    )
    if reported_environment is not None:
        _add_difficulty_evidence(
            evidence,
            dimension="setup",
            source="body",
            rule_id="difficulty.setup.reported_environment_only",
            matched_value=_matched_value(reported_environment),
            strength="weak",
            suggested_level=1,
            reason="reported_environment_is_not_requirement",
        )

    _collect_difficulty_regex_evidence(
        context.title,
        source="title",
        dimension="setup",
        bucket=evidence,
        rules=(
            (
                "difficulty.setup.platform_required",
                r"\b(?:mac\s?os|macos|windows|linux)\b.{0,60}\b(?:backend|specific|only|exclusive|native)\b|"
                r"\b(?:backend|specific|only|exclusive|native)\b.{0,60}\b(?:mac\s?os|macos|windows|linux)\b",
                "medium", 2, "platform_specific_reproduction_or_implementation",
            ),
            ("difficulty.setup.gpu_required", r"\b(?:cuda|rocm|gpu)\b", "strong", 3, "gpu_environment_required"),
            ("difficulty.setup.multinode_required", r"\b(?:multi[- ]node|multiple nodes?|distributed cluster)\b", "strong", 3, "multinode_environment_required"),
            ("difficulty.setup.native_toolchain_required", r"\b(?:native toolchain|compiler toolchain|cross[- ]compil)\b", "strong", 3, "native_toolchain_required"),
        ),
    )
    _collect_difficulty_regex_evidence(
        context.semantic_body,
        source="body",
        dimension="setup",
        bucket=evidence,
        rules=(
            (
                "difficulty.setup.platform_required",
                r"\b(?:requires?|must use|only reproducible on|only occurs? on|reproduce on|run on|test on)\b.{0,80}\b(?:mac\s?os|macos|windows|linux)\b|"
                r"\b(?:mac\s?os|macos|windows|linux)\b.{0,80}\b(?:is required|must be used|only reproduces|specific backend)\b",
                "medium", 2, "platform_specific_reproduction_or_implementation",
            ),
            (
                "difficulty.setup.service_required",
                r"\b(?:requires?|start|run|deploy|connect to)\b.{0,80}\b(?:database|server|service|broker|controller|external service)\b",
                "medium", 2, "specific_service_required",
            ),
            (
                "difficulty.setup.container_or_cluster_required",
                r"\b(?:create|deploy|run|requires?|reproduce)\b.{0,100}\b(?:docker|podman|kubernetes|k8s|single[- ]node cluster|cluster)\b",
                "medium", 2, "container_or_cluster_required",
            ),
            (
                "difficulty.setup.gpu_required",
                r"\b(?:requires?|run|test|reproduce|build|using|on)\b.{0,80}\b(?:cuda|rocm|gpu)\b|\b(?:cuda|rocm|gpu)\b.{0,80}\b(?:required|tests?|build|run)\b",
                "strong", 3, "gpu_environment_required",
            ),
            (
                "difficulty.setup.multinode_required",
                r"\b(?:requires?|deploy|run|test|reproduce|benchmark|validate)\b.{0,100}\b(?:multi[- ]node|multiple nodes?|distributed cluster)\b|\b(?:multi[- ]node|multiple nodes?|distributed cluster)\b.{0,100}\b(?:required|deployment|test|run|benchmark|validation)\b",
                "strong", 3, "multinode_environment_required",
            ),
            (
                "difficulty.setup.native_toolchain_required",
                r"\b(?:requires?|build|compile|test|using)\b.{0,80}\b(?:native toolchain|compiler toolchain|cross[- ]compil)\b",
                "strong", 3, "native_toolchain_required",
            ),
        ),
    )

    def add_setup(rule_id: str, match: re.Match[str], strength: str, level: int, reason: str) -> None:
        _add_difficulty_evidence(evidence, dimension="setup", source="derived", rule_id=rule_id, matched_value=_matched_value(match), strength=strength, suggested_level=level, reason=reason)

    # Filesystem/container must be part of the reproduction, not just an environment template.
    fs = re.search(r"\b(?:btrfs|zfs|filesystem|file system|storage volume|volume)\b", combined, re.I)
    container = re.search(r"\b(?:podman|docker|container)\b", combined, re.I)
    reproduce = re.search(r"\b(?:reproduce|ran|run|using|mount|volume|storage)\b", combined, re.I)
    if fs and container and reproduce:
        add_setup("difficulty.setup.filesystem_container_reproduction", fs, "medium", 2, "filesystem_and_container_are_required_for_reproduction")

    # Deployed profiling requirement.
    deployment = re.search(r"\b(?:kubernetes|k8s|cluster|deployment|deployed service)\b", combined, re.I)
    profiler = re.search(r"\b(?:pprof|profiling|profiler|heap profile)\b", combined, re.I)
    compare = re.search(r"\b(?:compare|same environment|agent mode|regular mode|benchmark|measure)\b", combined, re.I)
    if deployment and profiler and compare:
        add_setup("difficulty.setup.deployed_profiling", deployment, "medium", 2, "deployment_environment_required_for_profiling_comparison")

    # Distributed validation only becomes level 3 when execution/validation is explicitly required.
    distributed = re.search(r"\b(?:fsdp2?|tensor parallel(?:ism)?|all[- ]gather|collective communication|distributed test|multi[- ]gpu|multiple gpus?)\b", combined, re.I)
    validation = re.search(r"\b(?:run|test|benchmark|validate|verify|measure|speedup)\b", combined, re.I)
    if distributed and validation:
        add_setup("difficulty.setup.distributed_validation", distributed, "strong", 3, "distributed_or_multi_device_validation_required")

    # Documentation semantic verification can require an ordinary local environment but not a special one.
    if semantic_validation and not any(item["suggested_level"] >= 2 for item in evidence):
        _add_difficulty_evidence(evidence, dimension="setup", source="derived", rule_id="difficulty.setup.documentation_semantic_validation", matched_value="local semantic verification", strength="medium", suggested_level=1, reason="documentation_requires_local_behavior_verification")

    return sorted(evidence, key=_difficulty_evidence_key)

def _collect_context_difficulty_evidence(
    context: _DifficultyContext,
    information_quality: dict[str, Any],
) -> list[dict[str, Any]]:
    del information_quality
    evidence: list[dict[str, Any]] = []
    title = context.title
    body = context.semantic_body
    combined = f"{title}\n{body}"
    labels = "\n".join(context.normalized_labels)
    documentation_only = set(context.task_types) == {"documentation"}

    semantic_doc_validation = documentation_only and bool(
        re.search(r"\b(?:expected results?|actual results?|steps? to reproduce|method\s*=|conditional|interventional|behavior differs?|different answers?)\b", combined, re.I)
        and re.search(r"\b(?:behavior|results?|semantic|method|algorithm|statistic|partial dependence|protocol)\b", combined, re.I)
    )
    scope_signal = re.search(
        r"\b(?:public api|api contract|backward compatibility|cross[- ]module|"
        r"across (?:multiple|all) modules|shared framework|core architecture|"
        r"protocol semantics|compiler semantics|distributed state|lifecycle|"
        r"query execution|storage|retry policy|test framework)\b",
        combined,
        flags=re.IGNORECASE,
    )
    if documentation_only and scope_signal is None and not semantic_doc_validation:
        _add_difficulty_evidence(evidence, dimension="project_context", source="derived", rule_id="difficulty.context.no_project_context", matched_value="documentation-only task", strength="strong", suggested_level=0, reason="content_only_change")

    for source, source_text in (("title", title), ("body", body)):
        _collect_difficulty_regex_evidence(
            source_text,
            source=source,
            dimension="project_context",
            bucket=evidence,
            rules=(
                ("difficulty.context.local_module", r"\b(?:single module|local module|specific class|specific method|one component|local component)\b", "medium", 1, "localized_project_context"),
                ("difficulty.context.cross_module", r"\b(?:cross[- ]module|across (?:multiple|all) modules|multiple subsystems)\b", "medium", 2, "cross_module_context"),
                ("difficulty.context.public_api", r"\b(?:public api|api contract|public type|public interface|backward compatibility|compatibility contract)\b", "medium", 2, "public_api_or_compatibility_context"),
                ("difficulty.context.shared_framework", r"\b(?:shared test framework|test framework module|shared framework|common infrastructure)\b", "medium", 2, "shared_framework_context"),
                ("difficulty.context.lifecycle_or_compatibility", r"\b(?:lifecycle|invalidation|versioning|migration compatibility|serialization compatibility)\b", "medium", 2, "lifecycle_or_compatibility_context"),
                ("difficulty.context.core_architecture", r"\b(?:core architecture|architectural core|global invariant|system[- ]wide invariant)\b", "strong", 3, "core_architecture_context"),
                ("difficulty.context.protocol_semantics", r"\b(?:protocol semantics|wire protocol|core protocol)\b", "strong", 3, "protocol_semantics_context"),
                ("difficulty.context.distributed_state", r"\b(?:distributed state|distributed consensus|multi[- ]node coordination)\b", "strong", 3, "distributed_state_context"),
                ("difficulty.context.compiler_semantics", r"\b(?:compiler semantics|compiler backend|code generation|query execution engine)\b", "strong", 3, "compiler_or_execution_semantics_context"),
            ),
        )

    def add_context(rule_id: str, matched_value: str, strength: str, level: int, reason: str) -> None:
        _add_difficulty_evidence(evidence, dimension="project_context", source="derived", rule_id=rule_id, matched_value=matched_value, strength=strength, suggested_level=level, reason=reason)

    # X2-A: named subsystem execution/storage/retry/memory semantics.
    subsystem = re.search(r"\b(?:query execution|segment scan|segment reader|storage allocation|filesystem|file system|heap|memory|remote write|retry policy|exception propagation|test framework)\b", combined, re.I)
    behavior = re.search(r"\b(?:behavior|semantics|scan|allocate|allocation|retry|wrap|status|lifecycle|validate|profile|benchmark)\b", combined, re.I)
    if subsystem and behavior:
        add_context("difficulty.context.composite.subsystem_execution", _matched_value(subsystem), "medium", 2, "subsystem_behavior_requires_nonlocal_project_context")

    if semantic_doc_validation:
        add_context("difficulty.context.documentation_semantic_validation", "documentation semantic verification", "medium", 2, "documentation_requires_domain_behavior_semantics")

    traversal = re.search(r"\b(?:query|segment|scan|reader|storage|index)\b", combined, re.I)
    traversal_behavior = re.search(r"\b(?:read(?:ing)? from (?:the )?bottom|full scan|reading whole segment|scan strategy|rows? scanned|docs? scanned)\b", combined, re.I)
    if traversal and traversal_behavior:
        add_context("difficulty.context.composite.execution_traversal", _matched_value(traversal_behavior), "medium", 2, "execution_or_storage_traversal_requires_subsystem_context")

    exception_signal = re.search(r"\b(?:FileNotFound(?:Exception)?|[A-Za-z]+Exception|exception)\b", combined, re.I)
    retry_signal = re.search(r"\b(?:retry|retries|attempts? exceeded|do not retry)\b", combined, re.I)
    outward_mapping = re.search(r"\b(?:http|404|500|status|wrap(?:ped|ping)?|rest)\b", combined, re.I)
    if exception_signal and retry_signal and outward_mapping:
        add_context("difficulty.context.composite.exception_retry_path", _matched_value(retry_signal), "medium", 2, "exception_retry_and_http_mapping_require_multiple_layer_context")

    property_test = re.search(r"\b(?:property[- ]based|property testing|schema[- ]driven)\b", combined, re.I)
    fixture_scope = re.search(r"\b(?:api fixtures?|fixtures?|endpoints?|schema)\b", combined, re.I)
    if property_test and fixture_scope:
        add_context("difficulty.context.composite.cross_cutting_qa", _matched_value(property_test), "medium", 2, "schema_driven_testing_requires_api_and_fixture_context")

    # X3-A: public API deprecation/compatibility policy.
    api_target = re.search(r"\b(?:public api|public (?:keyword|parameter|method|function|interface)|api keywords?|api parameter)\b", combined, re.I)
    deprecation = re.search(r"\b(?:deprecat(?:e|ed|ion)|backward compatibility|compatibility cycle|migration path)\b", f"{labels}\n{combined}", re.I)
    policy = re.search(r"\b(?:needs discussion|discussion|should we|users? actually want|policy|replacement|alternative)\b", f"{labels}\n{combined}", re.I)
    if api_target and deprecation and policy:
        add_context("difficulty.context.composite.public_api_policy", _matched_value(api_target), "strong", 3, "public_api_policy_requires_compatibility_and_deprecation_decision")

    # X3-B: API semantic ambiguity.
    ambiguity = re.search(r"\b(?:ambiguous|ambiguity|multiple interpretations?|two interpretations?|same syntax|same api)\b", combined, re.I)
    paths = re.search(r"\b(?:getitem|setitem|read|write|existing|missing|scalar|slice|different contexts?|multiple paths?)\b", combined, re.I)
    compat = re.search(r"\b(?:backward compatibility|existing user code|historical behavior|heuristic|api design)\b", f"{labels}\n{combined}", re.I)
    if ambiguity and paths and compat:
        add_context("difficulty.context.composite.api_semantic_ambiguity", _matched_value(ambiguity), "strong", 3, "api_semantic_ambiguity_spans_multiple_behavior_paths")

    # X3-C: compiler internals with concrete behavior and shared/multiple paths.
    compiler = re.search(r"\b(?:torch\.compile|compiler|graph|guard|dynamic shapes?|tracing|decomposition)\b", f"{labels}\n{combined}", re.I)
    compiler_behavior = re.search(r"\b(?:recompil(?:e|ation|ations)|graph cache|guard behavior|codegen|code generation)\b", combined, re.I)
    multi = re.search(r"\b(?:multiple|several|following)\b.{0,100}\b(?:ops?|operators?|paths?|variants?)\b|\b(?:bmm|topk|cholesky|linalg\.norm|max)\b.{0,180}\b(?:bmm|topk|cholesky|linalg\.norm|max)\b", combined, re.I | re.S)
    if compiler and compiler_behavior and multi:
        add_context("difficulty.context.composite.compiler_internals", _matched_value(compiler_behavior), "strong", 3, "compiler_internal_behavior_spans_multiple_operations_or_paths")

    # X3-D: distributed implementation semantics.
    distributed = re.search(r"\b(?:fsdp2?|tensor parallel(?:ism)?|all[- ]gather|collective communication|distributed state)\b", combined, re.I)
    distributed_core = re.search(r"\b(?:implement|support|change|composability|communication|dequantize|quantize|backward)\b", combined, re.I)
    if distributed and distributed_core:
        add_context("difficulty.context.composite.distributed_semantics", _matched_value(distributed), "strong", 3, "distributed_semantics_are_core_to_task_implementation")

    # X3-E: lifecycle + shared framework + broad rollout.
    lifecycle = re.search(r"\b(?:classloader|lifecycle|reload(?:ing)?|memory leak|leak(?:ing)?)\b", combined, re.I)
    framework = re.search(r"\b(?:testing framework|test framework|shared framework|all extensions|extension maintainers)\b", combined, re.I)
    rollout = re.search(r"\b(?:opt[- ]in|opt[- ]out|globally|all extensions|rollout|extension owners?|eventually)\b", combined, re.I)
    if lifecycle and framework and rollout:
        add_context("difficulty.context.composite.framework_lifecycle", _matched_value(lifecycle), "strong", 3, "shared_framework_lifecycle_change_has_system_wide_rollout")

    # X3-F: multi-layer RFC architecture.
    rfc = re.search(r"\b(?:rfc|proposal)\b", f"{labels}\n{combined}", re.I)
    layers = re.search(r"\b(?:broker.{0,80}server|multiple (?:layers|subsystems)|two[- ]level|multi[- ]layer)\b", combined, re.I | re.S)
    correctness = re.search(r"\b(?:invalidation|version(?:ed|ing)|lifecycle|correctness|consistency|cache key)\b", combined, re.I)
    if rfc and layers and correctness:
        add_context("difficulty.context.composite.multi_layer_rfc", _matched_value(layers), "strong", 3, "rfc_spans_multiple_layers_and_correctness_lifecycle_concerns")

    # X3-G: multi-family mathematical/API aggregation semantics.
    metric_family = re.search(r"\b(?:multiple|different|various)\b.{0,100}\b(?:metrics?|scores?)\b|\b(?:f1|jaccard|precision|recall)\b.{0,160}\b(?:f1|jaccard|precision|recall)\b", combined, re.I | re.S)
    aggregation = re.search(r"\b(?:batch|aggregate|aggregation|weighted average|average|equivalent|equivalence|parallel)\b", combined, re.I)
    correctness_math = re.search(r"\b(?:correctness|mathematically|not equivalent|cannot simply|same result|semantics)\b", combined, re.I)
    if metric_family and aggregation and correctness_math:
        add_context("difficulty.context.composite.multi_family_semantics", _matched_value(metric_family), "strong", 3, "multiple_metric_families_require_aggregation_semantics_and_correctness")

    # Algorithm implementation often requires non-local algorithmic context, but is not always architecture-level.
    algorithm_change = re.search(r"\b(?:implement|missing|add|replace|port)\b.{0,80}\b(?:algorithm|bor[uů]vka)\b|\b(?:algorithm|bor[uů]vka)\b.{0,80}\b(?:not implemented|missing|implement|replace)\b", combined, re.I)
    if algorithm_change:
        add_context("difficulty.context.algorithm_implementation", _matched_value(algorithm_change), "medium", 2, "algorithm_implementation_requires_domain_context")

    if context.performance_signal:
        add_context("difficulty.context.performance_auxiliary", "performance auxiliary signal", "weak", 2, "performance_signal_requires_supporting_scope_evidence")
    return sorted(evidence, key=_difficulty_evidence_key)

def _collect_collaboration_difficulty_evidence(
    context: _DifficultyContext,
    information_quality: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    combined = f"{context.title}\n{context.semantic_body}"
    labels = "\n".join(context.normalized_labels)
    label_and_text = f"{labels}\n{combined}"

    if context.comment_count >= 3:
        _add_difficulty_evidence(evidence, dimension="collaboration", source="derived", rule_id="difficulty.collaboration.comment_volume", matched_value=str(context.comment_count), strength="weak", suggested_level=1, reason="comment_volume_is_weak_coordination_signal")

    _collect_difficulty_regex_evidence(
        label_and_text,
        source="derived",
        dimension="collaboration",
        bucket=evidence,
        rules=(
            ("difficulty.collaboration.needs_discussion", r"\b(?:needs discussion|discussion needed|design discussion)\b", "medium", 2, "unresolved_design_discussion"),
            ("difficulty.collaboration.api_design", r"\bapi design\b", "medium", 2, "public_api_design_coordination"),
            ("difficulty.collaboration.rfc_or_proposal", r"(?:^|\b)(?:rfc|proposal|pep request)\b", "medium", 2, "rfc_or_proposal_coordination"),
            (
                "difficulty.collaboration.multiple_options",
                r"\b(?:multiple|several|alternative)\s+(?:options|approaches|designs)\b|"
                r"\beither\b.{0,80}\b(?:approach|design|strategy|option|implementation|behavior)\b.{0,100}\bor\b|"
                r"\beither\b.{0,100}\bor\b.{0,80}\b(?:approach|design|strategy|option|implementation|behavior)\b",
                "medium", 2, "multiple_unresolved_options",
            ),
            ("difficulty.collaboration.cross_team", r"\b(?:cross[- ]team|multiple teams|several teams|team owners|coordinate with .* team)\b", "strong", 3, "cross_team_decision"),
            ("difficulty.collaboration.breaking_change_decision", r"\b(?:breaking change|backward incompatible|compatibility decision|deprecation policy)\b", "strong", 3, "breaking_compatibility_decision"),
            ("difficulty.collaboration.long_running_dispute", r"\b(?:controversial|long[- ]running dispute|unresolved for years|maintainer disagreement)\b", "strong", 3, "long_running_design_dispute"),
        ),
    )

    def add_collab(rule_id: str, matched_value: str, strength: str, level: int, reason: str) -> None:
        _add_difficulty_evidence(evidence, dimension="collaboration", source="derived", rule_id=rule_id, matched_value=matched_value, strength=strength, suggested_level=level, reason=reason)

    rollout = re.search(r"\b(?:opt[- ]in|opt[- ]out|phased rollout|rollout strategy|extension owners?|adoption strategy)\b", combined, re.I)
    if rollout:
        add_collab("difficulty.collaboration.rollout_strategy", _matched_value(rollout), "medium", 2, "phased_rollout_or_adoption_policy_requires_coordination")

    # API semantic decisions reach level 3 only with compatibility + real alternatives/ambiguity.
    api_design = re.search(r"\bapi design\b", label_and_text, re.I)
    compat = re.search(r"\b(?:backward compatibility|existing user code|breaking|compatibility)\b", combined, re.I)
    alternatives = re.search(r"\b(?:ambiguous|ambiguity|multiple interpretations?|alternative approaches?|heuristics?|several options)\b", combined, re.I)
    if api_design and compat and alternatives:
        add_collab("difficulty.collaboration.api_semantic_decision", _matched_value(api_design), "strong", 3, "api_semantic_compatibility_requires_design_decision")

    # Multi-layer RFC architecture review.
    rfc = re.search(r"\b(?:rfc|proposal)\b", label_and_text, re.I)
    layers = re.search(r"\b(?:broker.{0,80}server|multiple (?:layers|subsystems)|two[- ]level|multi[- ]layer)\b", combined, re.I | re.S)
    policy = re.search(r"\b(?:invalidation|versioning|correctness|consistency|trade[- ]?off|alternative|review|policy)\b", combined, re.I)
    if rfc and layers and policy:
        add_collab("difficulty.collaboration.multi_layer_rfc_review", _matched_value(rfc), "strong", 3, "multi_layer_rfc_requires_architecture_and_correctness_review")

    if information_quality["actionability"] == "actionable" and not evidence:
        add_collab("difficulty.collaboration.ordinary_review", "ordinary review", "weak", 0, "scope_is_explicit_without_coordination_signal")
    return sorted(evidence, key=_difficulty_evidence_key)

def _difficulty_conflict_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("lower_rule_id") or ""),
        str(item.get("higher_rule_id") or ""),
        str(item.get("reason") or ""),
    )


def _aggregate_difficulty_dimension(
    *,
    dimension: str,
    prior: int,
    evidence: list[dict[str, Any]],
    information_quality: dict[str, Any],
) -> dict[str, Any]:
    if dimension not in _DIFFICULTY_DIMENSIONS:
        raise ValueError(f"unsupported difficulty dimension: {dimension}")
    ordered = sorted(
        {_difficulty_evidence_key(item): item for item in evidence}.values(),
        key=_difficulty_evidence_key,
    )
    strong = [item for item in ordered if item["strength"] == "strong"]
    medium = [item for item in ordered if item["strength"] == "medium"]
    weak = [item for item in ordered if item["strength"] == "weak"]

    if strong:
        level = max(int(item["suggested_level"]) for item in strong)
    elif medium:
        level = max(int(item["suggested_level"]) for item in medium)
        if level == 3:
            level = 2
    elif dimension == "collaboration" and weak:
        level = max(prior, min(1, max(int(item["suggested_level"]) for item in weak)))
    else:
        level = prior

    level = max(0, min(int(level), 3))
    conflicts: list[dict[str, Any]] = []
    material = strong + medium
    for index, lower in enumerate(material):
        for higher in material[index + 1 :]:
            lower_level = int(lower["suggested_level"])
            higher_level = int(higher["suggested_level"])
            if abs(lower_level - higher_level) < 2:
                continue
            low_item, high_item = (
                (lower, higher) if lower_level < higher_level else (higher, lower)
            )
            conflicts.append(
                {
                    "lower_rule_id": low_item["rule_id"],
                    "higher_rule_id": high_item["rule_id"],
                    "reason": "material_evidence_level_conflict",
                }
            )
    conflicts = sorted(
        {_difficulty_conflict_key(item): item for item in conflicts}.values(),
        key=_difficulty_conflict_key,
    )

    information_confidence = str(information_quality["confidence"])
    if information_confidence == "low" or conflicts:
        confidence = "low"
    elif strong and information_confidence == "high":
        confidence = "high"
    elif strong or medium:
        confidence = "medium"
    else:
        confidence = "medium" if information_confidence != "low" else "low"

    return {
        "prior": int(prior),
        "level": level,
        "confidence": confidence,
        "evidence": ordered,
        "conflicts": conflicts,
    }


def _infer_effort_scope(
    *,
    context: _DifficultyContext,
    information_quality: dict[str, Any],
    dimensions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    actionability = str(information_quality["actionability"])
    combined = f"{context.title}\n{context.semantic_body}"
    if actionability == "non_actionable":
        return {"scope": "non_actionable", "reason": "task_is_not_single_actionable_work_item"}
    if actionability == "unclear":
        return {"scope": "unclear", "reason": "task_scope_cannot_be_inferred_reliably"}

    code = int(dimensions["code"]["level"])
    project_context = int(dimensions["project_context"]["level"])
    documentation_only = set(context.task_types) == {"documentation"}
    if documentation_only and code == 0 and project_context == 0:
        return {"scope": "micro", "reason": "content_only_change"}
    if re.search(r"\b(?:typo|wording|single assertion|one assertion|single config|configuration key|rename local|one-line|readme text)\b", combined, re.I):
        return {"scope": "micro", "reason": "explicit_micro_scope"}
    if re.search(r"\b(?:cross[- ]module|across (?:multiple|all) modules|multiple subsystems|shared test framework|system[- ]wide|all extensions)\b", combined, re.I):
        return {"scope": "cross_module", "reason": "explicit_cross_module_scope"}
    if code == 3 and project_context == 3:
        return {"scope": "system", "reason": "high_code_and_system_context"}
    if code >= 2 or project_context >= 2:
        return {"scope": "module", "reason": "nontrivial_module_scope"}
    return {"scope": "local", "reason": "localized_actionable_scope"}

def _infer_validation_burden(
    *,
    context: _DifficultyContext,
    dimensions: dict[str, dict[str, Any]],
) -> dict[str, str]:
    combined = f"{context.title}\n{context.semantic_body}"

    # Heavy validation requires a concrete verification burden; performance wording alone is insufficient.
    heavy_patterns = (
        ("profiling_and_benchmark", r"\b(?:pprof|profiler|profiling|heap profile)\b.{0,220}\b(?:benchmark|compare|before|after|same environment|heap|cpu|memory)\b|\b(?:benchmark|compare)\b.{0,220}\b(?:pprof|profiler|profiling|heap profile)\b"),
        ("large_scale_performance", r"\b(?:benchmark|measure|profil(?:e|ing)|latency|throughput|time taken|timetaken)\b.{0,220}(?:\d{4,}[,\d]*\s+(?:rows?|docs?|elements?)|\b(?:millions?|thousands?|replicas?|large dataset|large canvas|rows? scanned|docs? scanned)\b)|(?:\d{4,}[,\d]*\s+(?:rows?|docs?|elements?)|\b(?:millions?|thousands?|replicas?|large dataset|large canvas|rows? scanned|docs? scanned)\b).{0,220}\b(?:benchmark|measure|profil(?:e|ing)|latency|throughput|time taken|timetaken)\b"),
        ("browser_or_device_matrix", r"\b(?:firefox|chrome|browser)\b.{0,160}\b(?:mobile|tablet|phone|desktop|device)\b|\b(?:mobile|tablet|phone|desktop|device)\b.{0,160}\b(?:firefox|chrome|browser)\b"),
        ("distributed_validation", r"\b(?:fsdp2?|tensor parallel(?:ism)?|all[- ]gather|collective communication|multi[- ]node|multi[- ]gpu)\b.{0,180}\b(?:test|benchmark|validate|verify|speedup|run)\b|\b(?:test|benchmark|validate|verify|speedup|run)\b.{0,180}\b(?:fsdp2?|tensor parallel(?:ism)?|all[- ]gather|collective communication|multi[- ]node|multi[- ]gpu)\b"),
        ("compatibility_regression", r"\b(?:backward compatibility|existing user code|api semantic|multiple interpretations?|getitem|setitem)\b.{0,220}\b(?:test|regression|behavior|compatibility|cases?)\b"),
        ("algorithm_correctness", r"\b(?:algorithm|bor[uů]vka|multiple metrics?|aggregation)\b.{0,220}\b(?:benchmark|correctness|equivalence|same result|validation)\b"),
        ("cross_framework_regression", r"\b(?:all extensions|extension maintainers|shared test framework|testing framework)\b.{0,180}\b(?:regression|leak|ci|rollout|test)\b"),
        ("system_scaling", r"\b(?:scales? linearly|replica size|system load|cpu utilization)\b.{0,160}\b(?:replica|pod|probe|benchmark|measure)\b"),
    )
    for reason, pattern in heavy_patterns:
        if re.search(pattern, combined, re.I | re.S):
            return {"level": "heavy", "reason": reason}

    light = re.search(r"\b(?:unit test|regression test|test case|steps? to reproduce|reproduce|verify|validate|expected behavior)\b", combined, re.I)
    if light:
        return {"level": "light", "reason": "bounded_local_validation"}
    return {"level": "none", "reason": "no_explicit_runtime_validation_burden"}

def _estimate_effort(
    *,
    context: _DifficultyContext,
    information_quality: dict[str, Any],
    dimensions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scope_result = _infer_effort_scope(context=context, information_quality=information_quality, dimensions=dimensions)
    scope = str(scope_result["scope"])
    code = int(dimensions["code"]["level"])
    setup = int(dimensions["setup"]["level"])
    project_context = int(dimensions["project_context"]["level"])
    validation = _infer_validation_burden(context=context, dimensions=dimensions)
    validation_level = validation["level"]

    technical_complexity = "strong" if (code == 3 or project_context == 3) else "medium" if (code == 2 or project_context == 2) else "low"
    reasons = set(information_quality.get("reasons") or [])
    actionability = str(information_quality["actionability"])
    unbounded_information = bool(
        information_quality.get("body_missing")
        or "support_question" in reasons
        or ("unresolved_design_choice" in reasons and not context.has_acceptance_criteria)
    )

    base_buckets = {
        "micro": "under_2h",
        "local": "half_day",
        "module": "one_day",
        "cross_module": "multi_day",
        "system": "multi_day",
        "unclear": "half_day",
        "non_actionable": _NON_ACTIONABLE_EFFORT_PLACEHOLDER,
    }
    bucket = base_buckets[scope]

    applicable = scope != "non_actionable"
    if scope == "unclear" and unbounded_information:
        applicable = False
    if actionability == "design_pending" and "unresolved_design_choice" in reasons and not context.has_acceptance_criteria:
        applicable = False

    provisional = (not applicable) or scope in {"unclear", "non_actionable"} or actionability == "design_pending"
    confidence = "low" if not applicable or scope in {"unclear", "non_actionable"} else str(information_quality["confidence"])

    if applicable:
        documentation_only = set(context.task_types) == {"documentation"}
        if documentation_only and code == 0 and project_context == 0:
            bucket = "under_2h"
        elif documentation_only and code == 0 and project_context >= 2:
            bucket = "one_day"
        elif scope in {"cross_module", "system"}:
            bucket = "multi_day"
        elif scope == "module":
            if validation_level == "heavy" or technical_complexity == "strong":
                bucket = "multi_day"
            else:
                bucket = "one_day"
        elif scope == "local":
            if technical_complexity == "strong" and validation_level == "heavy":
                bucket = "multi_day"
            elif technical_complexity in {"medium", "strong"}:
                bucket = "one_day"
            else:
                bucket = "half_day"
        elif scope == "micro":
            bucket = "under_2h"

        # Heavy validation makes non-trivial module/system work multi-day, but does not inflate a truly local low-risk task by itself.
        if validation_level == "heavy" and (scope in {"module", "cross_module", "system"} or technical_complexity in {"medium", "strong"}):
            bucket = "multi_day"

    evidence = [
        {"source": "derived", "rule_id": f"effort.scope.{scope}", "matched_value": scope, "reason": str(scope_result["reason"])},
        {"source": "derived", "rule_id": f"effort.technical_complexity.{technical_complexity}", "matched_value": technical_complexity, "reason": "code_and_context_material_evidence"},
        {"source": "derived", "rule_id": f"effort.validation.{validation_level}", "matched_value": validation_level, "reason": validation["reason"]},
        {"source": "derived", "rule_id": "effort.bucket.decision_table.v0.2.1", "matched_value": bucket, "reason": "scope_actionability_technical_complexity_validation_decision"},
    ]
    if not applicable:
        evidence.append({"source": "derived", "rule_id": "effort.applicability.not_reliable", "matched_value": actionability, "reason": "unbounded_or_non_actionable_scope_uses_compatibility_bucket_only"})

    evidence = sorted(evidence, key=lambda item: (str(item["source"]), str(item["rule_id"]), str(item["matched_value"]), str(item["reason"])))
    return {
        "bucket": bucket,
        "scope": scope,
        "applicable": applicable,
        "provisional": provisional,
        "confidence": confidence,
        "technical_complexity": technical_complexity,
        "validation_burden": validation_level,
        "evidence": evidence,
    }

def _assess_difficulty(
    context: _DifficultyContext,
) -> tuple[int, int, int, int, str, dict[str, Any]]:
    information_quality = _assess_information_quality(context)
    if information_quality["actionability"] not in _ACTIONABILITY_VALUES:
        raise ValueError("invalid actionability")
    priors = _difficulty_priors(context.task_types, information_quality)
    collectors = {
        "code": _collect_code_difficulty_evidence,
        "setup": _collect_setup_difficulty_evidence,
        "project_context": _collect_context_difficulty_evidence,
        "collaboration": _collect_collaboration_difficulty_evidence,
    }
    dimensions = {
        dimension: _aggregate_difficulty_dimension(
            dimension=dimension,
            prior=priors[dimension],
            evidence=collector(context, information_quality),
            information_quality=information_quality,
        )
        for dimension, collector in collectors.items()
    }
    effort = _estimate_effort(
        context=context,
        information_quality=information_quality,
        dimensions=dimensions,
    )
    assessment = {
        "formula_version": DIFFICULTY_FORMULA_VERSION,
        "information_quality": information_quality,
        "dimensions": dimensions,
        "effort": effort,
    }
    return (
        int(dimensions["code"]["level"]),
        int(dimensions["setup"]["level"]),
        int(dimensions["project_context"]["level"]),
        int(dimensions["collaboration"]["level"]),
        str(effort["bucket"]),
        assessment,
    )



_SKILL_STRENGTH_ORDER = {"weak": 0, "medium": 1, "strong": 2}
_SKILL_ROLE_ORDER = {"learnable": 0, "auxiliary": 1, "core": 2}
_SKILL_REQUIREMENT_SOURCE_ORDER = {
    "inferred_task_type": 0,
    "inferred_tool_requirement": 1,
    "repository_primary_language": 2,
    "explicit_platform_signal": 3,
}
_SKILL_CANONICAL_NAMES = {
    "pytest": "pytest",
    "jest": "Jest",
    "docker": "Docker",
    "maven": "Maven",
    "gradle": "Gradle",
}
_SKILL_IMPORTANCE_CEILINGS = {
    "pytest": 0.7,
    "jest": 0.5,
    "docker": 0.7,
    "maven": 0.7,
    "gradle": 0.7,
}
_PLATFORM_PATTERNS = {
    "macos": r"\b(?:macos|os\s*x|osx|macosx)\b",
    "windows": r"\b(?:windows|win32)\b",
    "linux": r"\blinux\b",
}
_SKILL_ACTION = (
    r"(?:add|change|configure|create|document|fix|implement|lint|migrate|modify|"
    r"refactor|remove|separate|support|update|write)"
)
_TOOL_LABEL_NAMESPACES = frozenset({"tool", "component", "module"})


def _skill_semantic_body(body: str) -> str:
    """Remove noisy body regions while preserving inline task artifact names."""

    text = re.sub(r"<!--.*?-->", " ", body, flags=re.DOTALL)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalized_signal_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _skill_signal_key(signal: _SkillSignal) -> tuple[Any, ...]:
    return (
        signal.skill_name.casefold(),
        signal.decision,
        _SOURCE_ORDER.get(signal.source, 99),
        signal.rule_id,
        signal.normalized_value,
        signal.matched_value,
        _SKILL_STRENGTH_ORDER.get(signal.strength, -1),
        signal.minimum_level if signal.minimum_level is not None else -1,
        signal.importance if signal.importance is not None else -1.0,
    )


def _append_skill_signal(
    signals: list[_SkillSignal],
    *,
    skill_name: str,
    category: str,
    role: str | None,
    source: str,
    rule_id: str,
    matched_value: str,
    strength: str,
    reason: str,
    decision: str,
    matching_facing: bool,
    minimum_level: int | None = None,
    importance: float | None = None,
    requirement_source: str | None = None,
) -> None:
    signal = _SkillSignal(
        skill_name=skill_name,
        category=category,
        role=role,
        source=source,
        rule_id=rule_id,
        matched_value=matched_value,
        normalized_value=_normalized_signal_value(matched_value),
        strength=strength,
        reason=reason,
        decision=decision,
        matching_facing=matching_facing,
        minimum_level=minimum_level,
        importance=importance,
        requirement_source=requirement_source,
    )
    if _skill_signal_key(signal) not in {_skill_signal_key(item) for item in signals}:
        signals.append(signal)


def _first_rule_match(text: str, pattern: str) -> re.Match[str] | None:
    return re.search(pattern, text, flags=re.IGNORECASE)


def _controlled_tool_label(raw_label: str, tool_name: str) -> re.Match[str] | None:
    match = re.match(
        r"^\s*([a-z][a-z0-9_-]*)\s*[:/\\]\s*(.+?)\s*$",
        raw_label,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    namespace = _normalize_label(match.group(1))
    value = _normalize_label(match.group(2))
    if namespace not in _TOOL_LABEL_NAMESPACES or value != tool_name.casefold():
        return None
    return match


def _collect_language_and_task_type_signals(
    signals: list[_SkillSignal],
    *,
    record: dict[str, Any],
    task_types: tuple[str, ...],
    estimated_code_difficulty: int,
) -> None:
    language = str(record.get("primary_language") or "").strip()
    documentation_only = (
        task_types == ("documentation",) and estimated_code_difficulty == 0
    )
    if language:
        _append_skill_signal(
            signals,
            skill_name=language,
            category="programming_language",
            role="auxiliary" if documentation_only else "core",
            source="derived",
            rule_id=(
                "skill.language.repository_primary.documentation_only"
                if documentation_only
                else "skill.language.repository_primary"
            ),
            matched_value=language,
            strength="weak" if documentation_only else "strong",
            reason=(
                "repository_language_is_auxiliary_for_documentation_only_task"
                if documentation_only
                else "repository_primary_language_is_core_for_code_bearing_task"
            ),
            decision="included",
            matching_facing=True,
            minimum_level=(
                1
                if documentation_only
                else max(1, min(estimated_code_difficulty, 3))
            ),
            importance=0.3 if documentation_only else 1.0,
            requirement_source="repository_primary_language",
        )

    for task_type in task_types:
        if task_type == "other":
            continue
        _append_skill_signal(
            signals,
            skill_name=task_type,
            category="task_type",
            role="auxiliary",
            source="derived",
            rule_id="skill.task_type.public_type",
            matched_value=task_type,
            strength="medium",
            reason="public_task_type_implies_general_task_capability",
            decision="included",
            matching_facing=True,
            minimum_level=1 if estimated_code_difficulty <= 1 else 2,
            importance=0.6,
            requirement_source="inferred_task_type",
        )


def _platform_rule(
    platform: str,
    *,
    strength: str,
) -> str:
    platform_pattern = _PLATFORM_PATTERNS[platform]
    if strength == "strong":
        return (
            rf"(?:\b{platform_pattern[2:-2]}[- ]specific\b.{0,70}"
            rf"\b(?:backend|implementation|path|filesystem|code|support|integration)\b|"
            rf"\b(?:implement|fix|modify|update|support|handle)\b.{{0,80}}"
            rf"{platform_pattern}.{{0,50}}\b(?:backend|implementation|path|filesystem|code|integration)\b|"
            rf"\b(?:must|required|requires?|need(?:s)?\s+to)\b.{{0,60}}"
            rf"\b(?:verify|validate|test|reproduce|run)\b.{{0,60}}{platform_pattern}|"
            rf"\b(?:requires?|needs?)\b.{{0,30}}{platform_pattern}.{{0,40}}"
            rf"\b(?:to\s+)?(?:verify|validate|test|reproduce|run)\b|"
            rf"{platform_pattern}.{{0,50}}\b(?:must|required|requires?)\b.{{0,40}}"
            rf"\b(?:verify|validate|test|reproduce|run)\b)"
        )
    return (
        rf"(?:\bonly\b.{{0,30}}\b(?:fails?|crash(?:es)?|breaks?|errors?|reproducible)\b"
        rf".{{0,50}}{platform_pattern}|"
        rf"\b(?:fails?|crash(?:es)?|breaks?|errors?)\b.{{0,30}}\bonly\b.{{0,50}}{platform_pattern}|"
        rf"\bonly[- ]on[- ]{platform_pattern}.{{0,40}}"
        rf"\b(?:failure|bug|issue|crash|error|behavior|behaviour)\b|"
        rf"{platform_pattern}.{{0,15}}[- ]only\b.{{0,40}}"
        rf"\b(?:failure|bug|issue|crash|error|behavior|behaviour)\b|"
        rf"\b(?:bug|failure|crash|error|issue)\b.{{0,20}}\bon\b.{{0,10}}{platform_pattern})"
    )


def _collect_platform_signals(
    signals: list[_SkillSignal],
    *,
    title: str,
    body: str,
    labels: list[str],
) -> None:
    semantic_body = _skill_semantic_body(body)
    positive_platforms: set[str] = set()

    for platform in sorted(_PLATFORM_PATTERNS):
        name = f"platform:{platform}"
        for source, text in (("title", title), ("body", semantic_body)):
            direct_title_match = (
                _first_rule_match(
                    text,
                    rf"{_PLATFORM_PATTERNS[platform]}.{{0,30}}\b(?:backend|implementation)\b|"
                    rf"\b(?:backend|implementation)\b.{{0,30}}{_PLATFORM_PATTERNS[platform]}",
                )
                if source == "title"
                else None
            )
            strong_match = direct_title_match or _first_rule_match(
                text, _platform_rule(platform, strength="strong")
            )
            if strong_match is not None:
                _append_skill_signal(
                    signals,
                    skill_name=name,
                    category="platform",
                    role="core",
                    source=source,
                    rule_id=f"skill.platform.{source}.mandatory_or_implementation",
                    matched_value=_matched_value(strong_match),
                    strength="strong",
                    reason="platform_is_required_for_implementation_or_validation",
                    decision="included",
                    matching_facing=True,
                    minimum_level=1,
                    importance=1.0,
                    requirement_source="explicit_platform_signal",
                )
                positive_platforms.add(platform)
                continue

            medium_match = _first_rule_match(text, _platform_rule(platform, strength="medium"))
            if medium_match is not None:
                _append_skill_signal(
                    signals,
                    skill_name=name,
                    category="platform",
                    role="auxiliary",
                    source=source,
                    rule_id=f"skill.platform.{source}.platform_specific_failure",
                    matched_value=_matched_value(medium_match),
                    strength="medium",
                    reason="failure_is_platform_specific_but_platform_is_not_proven_mandatory",
                    decision="included",
                    matching_facing=True,
                    minimum_level=1,
                    importance=0.7,
                    requirement_source="explicit_platform_signal",
                )
                positive_platforms.add(platform)

        platform_pattern = _PLATFORM_PATTERNS[platform]
        environment_match = _first_rule_match(
            body,
            rf"(?m)^\s*(?:os|operating system|system info|environment)\s*[:=-].*?{platform_pattern}.*$",
        )
        if environment_match is not None:
            _append_skill_signal(
                signals,
                skill_name=name,
                category="platform",
                role=None,
                source="body",
                rule_id="skill.platform.body.reporter_environment",
                matched_value=_matched_value(environment_match),
                strength="weak",
                reason="reporter_environment_is_not_task_requirement",
                decision="rejected_context_only",
                matching_facing=False,
            )

        reproduction_match = _first_rule_match(
            semantic_body,
            rf"\b(?:reproduced?|tested|observed|running|runs?)\b.{{0,70}}{platform_pattern}",
        )
        if reproduction_match is not None:
            _append_skill_signal(
                signals,
                skill_name=name,
                category="platform",
                role=None,
                source="body",
                rule_id="skill.platform.body.reproduction_environment",
                matched_value=_matched_value(reproduction_match),
                strength="weak",
                reason="reproduction_environment_alone_is_not_task_requirement",
                decision="rejected_context_only",
                matching_facing=False,
            )

        if platform not in positive_platforms:
            mention = _first_rule_match(semantic_body, platform_pattern)
            if mention is not None and environment_match is None and reproduction_match is None:
                _append_skill_signal(
                    signals,
                    skill_name=name,
                    category="platform",
                    role=None,
                    source="body",
                    rule_id="skill.platform.body.mention_without_requirement_semantics",
                    matched_value=_matched_value(mention),
                    strength="weak",
                    reason="platform_mention_lacks_required_or_platform_specific_task_semantics",
                    decision="rejected_context_only",
                    matching_facing=False,
                )

    for raw_label in labels:
        normalized = _normalize_label(raw_label)
        for platform in sorted(_PLATFORM_PATTERNS):
            platform_alias = _first_rule_match(normalized, _PLATFORM_PATTERNS[platform])
            if platform_alias is None:
                continue
            name = f"platform:{platform}"
            if platform in positive_platforms:
                _append_skill_signal(
                    signals,
                    skill_name=name,
                    category="platform",
                    role="auxiliary",
                    source="label",
                    rule_id="skill.platform.label.corroboration",
                    matched_value=raw_label,
                    strength="medium",
                    reason="platform_label_corroborates_semantic_platform_evidence",
                    decision="included",
                    matching_facing=True,
                    minimum_level=1,
                    importance=0.7,
                    requirement_source="explicit_platform_signal",
                )
            else:
                _append_skill_signal(
                    signals,
                    skill_name=name,
                    category="platform",
                    role=None,
                    source="label",
                    rule_id="skill.platform.label.insufficient_alone",
                    matched_value=raw_label,
                    strength="weak",
                    reason="platform_label_without_task_semantics_is_not_hard_requirement",
                    decision="rejected_insufficient_semantics",
                    matching_facing=False,
                )


def _tool_positive_patterns(tool_key: str) -> tuple[tuple[str, int, float, str, str], ...]:
    if tool_key == "pytest":
        return (
            (
                r"\bpytest\b.{0,70}\b(?:config(?:uration)?|collection|collect(?:ion|ing)?|fixture|plugin|hook|conftest|pytest\.ini)\b|"
                r"\b(?:conftest\.py|pytest\.ini)\b.{0,70}\b(?:pytest|fixture|collection|config(?:uration)?)\b",
                2,
                0.7,
                "core",
                "pytest_configuration_or_collection_is_direct_task_semantics",
            ),
            (
                rf"\b{_SKILL_ACTION}\b.{{0,60}}\bpytest\b.{{0,60}}\b(?:fixture|test suite|tests?)\b",
                1,
                0.5,
                "auxiliary",
                "pytest_is_an_explicit_auxiliary_test_tool",
            ),
        )
    if tool_key == "jest":
        return (
            (
                r"\bjest\b.{0,80}\b(?:open handles?|mock(?:agent|s?)?|fake timers?|runner|transform|watch mode)\b|"
                r"\b(?:open handles?|mock(?:agent|s?)?|fake timers?|runner|transform)\b.{0,80}\bjest\b|"
                rf"\b{_SKILL_ACTION}\b.{{0,60}}\bjest\b.{{0,60}}\bconfig(?:uration)?\b|"
                rf"\bjest\b.{{0,40}}\bconfig(?:uration)?\b.{{0,60}}\b{_SKILL_ACTION}\b",
                1,
                0.5,
                "auxiliary",
                "jest_specific_behavior_is_part_of_task_semantics",
            ),
        )
    if tool_key == "docker":
        return (
            (
                rf"\b{_SKILL_ACTION}\b.{{0,80}}\b(?:docker images?|dockerfile|docker-builds?|docker build|docker compose|container images?)\b|"
                r"\b(?:docker images?|dockerfile|docker-builds?|docker build|docker compose|container images?)\b.{0,80}"
                rf"\b(?:{_SKILL_ACTION}|linter|linting|configuration)\b",
                1,
                0.7,
                "core",
                "docker_artifact_or_build_configuration_is_direct_task_target",
            ),
        )
    if tool_key == "maven":
        return (
            (
                rf"\b{_SKILL_ACTION}\b.{{0,80}}\b(?:multi-module maven|maven multi-module project|maven project|maven configuration|maven plugin|pom\.xml|maven getting started)\b|"
                r"\b(?:multi-module maven|maven project|maven configuration|maven plugin|pom\.xml|maven getting started)\b.{0,80}"
                rf"\b(?:{_SKILL_ACTION}|configuration|setup)\b",
                1,
                0.7,
                "core",
                "maven_project_or_configuration_is_direct_task_target",
            ),
            (
                r"\b(?:compare|separate|distinguish|split)\b.{0,100}\bgradle\b.{0,100}\bmaven\b|"
                r"\bmaven\b.{0,100}\b(?:comparison|existing|current|reference)\b",
                1,
                0.5,
                "auxiliary",
                "maven_is_a_comparison_or_reference_tool",
            ),
        )
    if tool_key == "gradle":
        return (
            (
                rf"\b{_SKILL_ACTION}\b.{{0,80}}\b(?:gradle project|gradle configuration|gradle build|gradle plugin|gradle getting started|build\.gradle|settings\.gradle|gradle\.properties)\b|"
                r"\b(?:gradle project|gradle configuration|gradle build|gradle plugin|gradle getting started|build\.gradle|settings\.gradle|gradle\.properties)\b.{0,80}"
                rf"\b(?:{_SKILL_ACTION}|configuration|setup)\b",
                1,
                0.7,
                "core",
                "gradle_project_or_build_configuration_is_direct_task_target",
            ),
        )
    raise ValueError(f"unsupported production tool skill: {tool_key}")


def _tool_negative_pattern(tool_key: str) -> str:
    patterns = {
        "pytest": r"\b(?:run|running|install|installed|tested using)\s+pytest\b|\bpytest\s+--version\b",
        "jest": (
            r"\b(?:roadmap|dependency|dependencies|reproduction|reproducer)\b.{0,80}\bjest(?:-worker)?\b|"
            r"\b(?:run|running)\s+jest\b"
        ),
        "docker": r"\b(?:reproduced?|tested|running|runs?)\b.{0,70}\b(?:docker|container)\b|\bdocker\s+version\b|\bdocker\s+logs?\b",
        "maven": r"\bmvn\s+(?:test|verify|install)\b|\bmaven\s+version\b|\bsnapshot\b.{0,80}\bmaven\b",
        "gradle": r"\bgradle\s+(?:test|build|--version)\b|\bgradle\s+version\b|\bsnapshot\b.{0,80}\bgradle\b",
    }
    return patterns[tool_key]


def _tool_body_usage_context_match(
    tool_key: str, text: str, positive_match: re.Match[str]
) -> re.Match[str] | None:
    if tool_key == "maven":
        artifact = r"(?:maven|mvn|pom\.xml)"
    elif tool_key == "gradle":
        artifact = r"(?:gradle|gradle project|build\.gradle|settings\.gradle|gradle\.properties)"
    else:
        return None

    start = max(0, positive_match.start() - 120)
    end = min(len(text), positive_match.end() + 120)
    window = text[start:end]
    patterns = (
        rf"\b(?:to|how to)\s+(?:use|consume)\b.{{0,160}}\b{artifact}\b",
        rf"\badd\s+the\s+following\s+repository\b.{{0,140}}\b{artifact}\b",
        rf"\b(?:users?|you)\s+(?:can|may|should)\b.{{0,100}}\b(?:use|consume|add)\b.{{0,100}}\b{artifact}\b",
        rf"\b(?:either|whether)\b.{{0,40}}\b{artifact}\b.{{0,50}}\bor\b.{{0,50}}\b(?:a\s+)?(?:property|config(?:uration)?)\s+file\b",
        rf"\b(?:one|single|central(?:ized)?)\s+(?:place|location)\b.{{0,100}}\b(?:being\s+(?:it\s+)?)?{artifact}\b.{{0,50}}\bor\b.{{0,50}}\b(?:a\s+)?(?:property|config(?:uration)?)\s+file\b",
    )
    for pattern in patterns:
        match = _first_rule_match(window, pattern)
        if match is not None:
            return match
    return None


def _tool_body_alternative_storage_context_match(
    tool_key: str, text: str
) -> re.Match[str] | None:
    if tool_key == "maven":
        artifact = r"(?:maven|mvn|pom\.xml)"
    elif tool_key == "gradle":
        artifact = r"(?:gradle|gradle project|build\.gradle|settings\.gradle|gradle\.properties)"
    else:
        return None

    patterns = (
        rf"\b(?:either|whether)\b.{{0,40}}\b{artifact}\b.{{0,50}}\bor\b.{{0,50}}\b(?:a\s+)?(?:property|config(?:uration)?)\s+file\b",
        rf"\b(?:one|single|central(?:ized)?)\s+(?:place|location)\b.{{0,100}}\b(?:being\s+(?:it\s+)?)?{artifact}\b.{{0,50}}\bor\b.{{0,50}}\b(?:a\s+)?(?:property|config(?:uration)?)\s+file\b",
    )
    for pattern in patterns:
        match = _first_rule_match(text, pattern)
        if match is not None:
            return match
    return None


def _collect_tool_signals(
    signals: list[_SkillSignal],
    *,
    title: str,
    body: str,
    labels: list[str],
) -> None:
    semantic_body = _skill_semantic_body(body)
    for tool_key, canonical_name in _SKILL_CANONICAL_NAMES.items():
        positive_found = False
        usage_context_recorded = False
        patterns = _tool_positive_patterns(tool_key)
        for source, text in (("title", title), ("body", semantic_body)):
            for index, (pattern, level, importance, role, reason) in enumerate(patterns, 1):
                match = _first_rule_match(text, pattern)
                if match is None:
                    continue
                if source == "body":
                    usage_match = _tool_body_usage_context_match(tool_key, text, match)
                    if usage_match is not None:
                        _append_skill_signal(
                            signals,
                            skill_name=canonical_name,
                            category="tool",
                            role=None,
                            source="body",
                            rule_id=f"skill.tool.{tool_key}.body.usage_context_guard",
                            matched_value=_matched_value(usage_match),
                            strength="weak",
                            reason="tool_artifact_is_usage_or_alternative_storage_context_not_direct_task_target",
                            decision="rejected_context_only",
                            matching_facing=False,
                        )
                        usage_context_recorded = True
                        positive_found = True
                        continue
                _append_skill_signal(
                    signals,
                    skill_name=canonical_name,
                    category="tool",
                    role=role,
                    source=source,
                    rule_id=f"skill.tool.{tool_key}.{source}.positive_{index}",
                    matched_value=_matched_value(match),
                    strength="strong" if importance >= 0.7 else "medium",
                    reason=reason,
                    decision="included",
                    matching_facing=True,
                    minimum_level=level,
                    importance=importance,
                    requirement_source="inferred_tool_requirement",
                )
                positive_found = True

        for raw_label in labels:
            label_match = _controlled_tool_label(raw_label, tool_key)
            if label_match is None:
                continue
            _append_skill_signal(
                signals,
                skill_name=canonical_name,
                category="tool",
                role="auxiliary",
                source="label",
                rule_id=f"skill.tool.{tool_key}.label.controlled_namespace",
                matched_value=raw_label,
                strength="medium",
                reason="controlled_tool_label_is_explicit_tool_scope",
                decision="included",
                matching_facing=True,
                minimum_level=1,
                importance=0.5,
                requirement_source="inferred_tool_requirement",
            )
            positive_found = True

        if not usage_context_recorded:
            alternative_storage_match = _tool_body_alternative_storage_context_match(
                tool_key, semantic_body
            )
            if alternative_storage_match is not None:
                _append_skill_signal(
                    signals,
                    skill_name=canonical_name,
                    category="tool",
                    role=None,
                    source="body",
                    rule_id=f"skill.tool.{tool_key}.body.usage_context_guard",
                    matched_value=_matched_value(alternative_storage_match),
                    strength="weak",
                    reason="tool_artifact_is_usage_or_alternative_storage_context_not_direct_task_target",
                    decision="rejected_context_only",
                    matching_facing=False,
                )

        negative_match = _first_rule_match(semantic_body, _tool_negative_pattern(tool_key))
        if negative_match is not None:
            _append_skill_signal(
                signals,
                skill_name=canonical_name,
                category="tool",
                role=None,
                source="body",
                rule_id=f"skill.tool.{tool_key}.body.context_guard",
                matched_value=_matched_value(negative_match),
                strength="weak",
                reason="tool_mention_is_execution_version_reproduction_or_status_context",
                decision="rejected_context_only",
                matching_facing=False,
            )

        if not positive_found:
            generic_mention = _first_rule_match(
                f"{title}\n{semantic_body}",
                rf"\b{re.escape(tool_key)}\b" if tool_key != "docker" else r"\b(?:docker|dockerfile)\b",
            )
            if generic_mention is not None and negative_match is None:
                _append_skill_signal(
                    signals,
                    skill_name=canonical_name,
                    category="tool",
                    role=None,
                    source="title" if _first_rule_match(title, generic_mention.group(0)) else "body",
                    rule_id=f"skill.tool.{tool_key}.mention_without_task_target",
                    matched_value=_matched_value(generic_mention),
                    strength="weak",
                    reason="technology_mention_without_direct_task_target_semantics",
                    decision="rejected_context_only",
                    matching_facing=False,
                )


def _winning_skill_signal(signals: list[_SkillSignal]) -> _SkillSignal:
    return max(
        signals,
        key=lambda item: (
            float(item.importance or 0.0),
            int(item.minimum_level or 0),
            _SKILL_STRENGTH_ORDER.get(item.strength, -1),
            _SKILL_ROLE_ORDER.get(item.role or "", -1),
            _SKILL_REQUIREMENT_SOURCE_ORDER.get(item.requirement_source or "", -1),
            -_SOURCE_ORDER.get(item.source, 99),
            item.rule_id,
        ),
    )


def _signal_evidence_dict(signal: _SkillSignal) -> dict[str, Any]:
    return {
        "source": signal.source,
        "rule_id": signal.rule_id,
        "matched_value": signal.matched_value,
        "normalized_value": signal.normalized_value,
        "strength": signal.strength,
        "reason": signal.reason,
    }


def _merge_skill_signals(signals: list[_SkillSignal]) -> _SkillInferenceResult:
    included_by_key: dict[str, list[_SkillSignal]] = {}
    rejected: list[_SkillSignal] = []
    for signal in signals:
        if signal.decision == "included" and signal.matching_facing:
            included_by_key.setdefault(signal.skill_name.casefold(), []).append(signal)
        else:
            rejected.append(signal)

    requirements: list[SkillRequirement] = []
    skill_evidence: dict[str, Any] = {}
    for key in sorted(included_by_key):
        group = sorted(included_by_key[key], key=_skill_signal_key)
        winner = _winning_skill_signal(group)
        minimum_level = max(int(item.minimum_level or 0) for item in group)
        importance = max(float(item.importance or 0.0) for item in group)
        importance = min(importance, _SKILL_IMPORTANCE_CEILINGS.get(key, 1.0))
        source = max(
            (item.requirement_source or "" for item in group),
            key=lambda value: _SKILL_REQUIREMENT_SOURCE_ORDER.get(value, -1),
        )
        requirement = SkillRequirement(
            skill_name=winner.skill_name,
            minimum_level=minimum_level,
            importance=importance,
            requirement_source=source,
        )
        requirements.append(requirement)
        skill_evidence[requirement.skill_name] = {
            "normalized_skill_name": key,
            "category": winner.category,
            "role": winner.role,
            "decision": "included",
            "matching_facing": True,
            "minimum_level": minimum_level,
            "importance": importance,
            "requirement_source": source,
            "evidence": [_signal_evidence_dict(item) for item in group],
        }

    rejected_rows = [
        {
            "skill_name": item.skill_name,
            "category": item.category,
            "source": item.source,
            "rule_id": item.rule_id,
            "matched_value": item.matched_value,
            "normalized_value": item.normalized_value,
            "strength": item.strength,
            "decision": item.decision,
            "matching_facing": False,
            "reason": item.reason,
        }
        for item in sorted(rejected, key=_skill_signal_key)
    ]
    requirements.sort(key=lambda item: (item.skill_name.casefold(), item.skill_name))
    ordered_skill_evidence = {
        item.skill_name: skill_evidence[item.skill_name] for item in requirements
    }
    return _SkillInferenceResult(
        requirements=tuple(requirements),
        evidence={
            "rules_version": SKILL_REQUIREMENT_RULES_VERSION,
            "skills": ordered_skill_evidence,
            "rejected": rejected_rows,
        },
    )


def _infer_skill_requirements_core(
    record: dict[str, Any],
    *,
    task_types: tuple[str, ...],
    estimated_code_difficulty: int,
) -> _SkillInferenceResult:
    signals: list[_SkillSignal] = []
    title = str(record.get("title") or "")
    body = str(record.get("body_text") or "")
    labels = [str(value) for value in record.get("labels") or []]
    _collect_language_and_task_type_signals(
        signals,
        record=record,
        task_types=task_types,
        estimated_code_difficulty=estimated_code_difficulty,
    )
    _collect_platform_signals(signals, title=title, body=body, labels=labels)
    _collect_tool_signals(signals, title=title, body=body, labels=labels)
    return _merge_skill_signals(signals)


def extract_task_features(record: dict[str, Any]) -> TaskFeatures:
    title = str(record.get("title") or "")
    body = str(record.get("body_text") or "")
    text = f"{title}\n{body}"
    labels = [str(label) for label in (record.get("labels") or [])]

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
    comments = int(record.get("comment_count") or 0)
    difficulty_context = _build_difficulty_context(
        title=title,
        body=body,
        labels=labels,
        task_types=task_types,
        performance_signal=has_performance_signal,
        comment_count=comments,
        has_reproduction_steps=reproduction,
        has_acceptance_criteria=acceptance,
        has_expected_behavior=expected,
        has_affected_module_hint=affected,
    )
    (
        code_difficulty,
        setup_difficulty,
        context_difficulty,
        collaboration_difficulty,
        effort,
        difficulty_assessment,
    ) = _assess_difficulty(difficulty_context)

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

    skill_inference = _infer_skill_requirements_core(
        record,
        task_types=task_types,
        estimated_code_difficulty=code_difficulty,
    )
    evidence = {
        "title_length": len(title),
        "body_length": len(body),
        "has_code_block": has_code_block,
        "newcomer_label_signal": has_newcomer_label(labels),
        "comment_count": comments,
        "formula_version": TASK_FEATURE_VERSION,
        "difficulty_assessment": difficulty_assessment,
        "skill_requirement_evidence": skill_inference.evidence,
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
    return _infer_skill_requirements_core(
        record,
        task_types=features.task_types,
        estimated_code_difficulty=features.estimated_code_difficulty,
    ).requirements
