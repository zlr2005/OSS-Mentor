"""Explainable text features and two-track ranking baselines."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from oss_mentor.candidate_rules import has_newcomer_label


TASK_FEATURE_VERSION = "task-features-v0.1"


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


def _contains(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _matching_pattern(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return pattern
    return None


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(max(value, lower), upper)


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

    task_types: set[str] = set()
    task_type_evidence: dict[str, list[dict[str, str]]] = {}

    def add_type(task_type: str, source: str, signal: str) -> None:
        task_types.add(task_type)
        evidence_items = task_type_evidence.setdefault(task_type, [])
        item = {"source": source, "signal": signal}
        if item not in evidence_items:
            evidence_items.append(item)

    def add_label_type(
        task_type: str,
        patterns: tuple[str, ...],
    ) -> None:
        for label in labels:
            pattern = _matching_pattern(label, patterns)
            if pattern:
                add_type(task_type, "label", f"{label} ({pattern})")
                return

    def add_text_type(
        task_type: str,
        source: str,
        value: str,
        patterns: tuple[str, ...],
    ) -> None:
        pattern = _matching_pattern(value, patterns)
        if pattern:
            add_type(task_type, source, pattern)

    add_label_type("bug_fix", (r"\bbug\b", r"defect", r"regression", r"security"))
    add_text_type(
        "bug_fix",
        "title",
        title,
        (
            r"\bbug\b",
            r"\berror\b",
            r"incorrect",
            r"inconsistent",
            r"\b(?:fail|fails|failed|failure|broken|crash|hang|vulnerable)\b",
            r"\bdoes(?:n't| not)\b",
            r"\b(?:isn't|aren't|can't|cannot)\b",
            r"\bnot (?:accepted|show|shows|render|work|working|update|preserve|clear|release|respect|notify|used|cached)\b",
            r"\bmissing\b",
            r"\bwrong\b",
            r"\b(?:ignore|ignores|swallow|swallows|drop|drops|break|breaks)\b",
            r"\b(?:lost|leakage|corruption|exception|false positive|silently)\b",
            r"\b(?:hangs|freezes|crashing|failing)\b",
        ),
    )

    add_label_type("documentation", (r"\bdoc(?:s|umentation)?\b", r"readme"))
    add_text_type(
        "documentation",
        "title",
        title,
        (r"documentation", r"\bdocs?\b", r"readme", r"guide", r"tutorial"),
    )
    add_text_type(
        "documentation",
        "body",
        body,
        (r"documentation", r"\bdocs?\b", r"readme"),
    )

    add_label_type("testing", (r"\btest(?:s|ing)?\b", r"coverage", r"ci-failure"))
    add_text_type(
        "testing",
        "title",
        title,
        (r"\btests?\b", r"\btesting\b", r"coverage", r"regression test", r"typecheck fail"),
    )
    add_text_type(
        "testing",
        "body",
        body,
        (r"\btests?\b", r"coverage", r"regression test"),
    )

    add_label_type(
        "refactor",
        (r"refactor", r"cleanup", r"maintenance", r"deprecat", r"kind/cleanup"),
    )
    add_text_type(
        "refactor",
        "title",
        title,
        (r"refactor", r"cleanup", r"simplif", r"\bdepr:", r"deprecat", r"consolidat"),
    )
    add_text_type("refactor", "body", body, (r"refactor", r"cleanup", r"simplif"))

    add_label_type("performance", (r"performance", r"\bperf\b"))
    add_text_type(
        "performance",
        "title",
        title,
        (r"performance", r"optim(?:ize|ise|ization)", r"\bmemory usage\b", r"\blag\b"),
    )
    add_text_type(
        "performance",
        "body",
        body,
        (r"performance", r"optim(?:ize|ise|ization)"),
    )

    add_label_type(
        "build_tooling",
        (r"\bbuild\b", r"\bci\b", r"dependenc", r"packag", r"tooling"),
    )
    add_text_type(
        "build_tooling",
        "title",
        title,
        (
            r"\bci\b",
            r"\bbuild\b",
            r"packag",
            r"dependenc",
            r"github actions?",
            r"\btoolchain\b",
        ),
    )
    add_text_type(
        "build_tooling",
        "body",
        body,
        (r"\bci\b", r"\bbuild\b", r"packag", r"dependenc", r"\btoolchain\b"),
    )

    add_label_type(
        "feature",
        (
            r"enhancement",
            r"feature(?: request)?",
            r"new feature",
            r"kind/feature",
            r"type/proposal",
            r"\ba-lint\b",
        ),
    )
    add_text_type(
        "feature",
        "title",
        title,
        (
            r"\bfeature\b",
            r"change request",
            r"\benh:",
            r"^\s*(?:add|allow|provide|enable|introduce|implement|expose|support|expand|improve|render|disable|suggest|differentiate|classify|generate|handle|increase)\b",
            r"^\s*(?:update|document|move|migrate|use|default to|tell if|issue .*warnings?)\b",
            r"^\s*(?:automatically repair|adding validation|authentication to)\b",
            r"\bnew lint\b",
            r"\blint (?:for|against)\b",
        ),
    )
    add_text_type(
        "feature",
        "body",
        body,
        (r"\bfeature request\b", r"\bnew feature\b"),
    )
    if not task_types:
        task_types.add("other")
        task_type_evidence["other"] = [
            {"source": "fallback", "signal": "no_supported_rule_matched"}
        ]

    code_difficulty = 1
    if "documentation" in task_types and len(task_types) == 1:
        code_difficulty = 0
    if "feature" in task_types or "refactor" in task_types:
        code_difficulty = max(code_difficulty, 2)
    if "performance" in task_types or any(
        signal in label_text for signal in ("core", "architecture", "api change")
    ):
        code_difficulty = 3
    if has_newcomer_label(labels):
        code_difficulty = min(code_difficulty, 1)

    setup_difficulty = 1
    if "documentation" in task_types and len(task_types) == 1:
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
    if "refactor" in task_types or "performance" in task_types:
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
        "task_type_evidence": task_type_evidence,
        "formula_version": TASK_FEATURE_VERSION,
    }
    return TaskFeatures(
        has_reproduction_steps=reproduction,
        has_acceptance_criteria=acceptance,
        has_expected_behavior=expected,
        has_affected_module_hint=affected,
        task_types=tuple(sorted(task_types)),
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
