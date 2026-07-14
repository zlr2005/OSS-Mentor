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
    if "bug" in label_text or _contains(title, (r"\bbug\b", r"error", r"incorrect")):
        task_types.add("bug_fix")
    if "doc" in label_text or _contains(text, (r"documentation", r"\bdocs?\b", r"readme")):
        task_types.add("documentation")
    if "test" in label_text or _contains(text, (r"\btests?\b", r"coverage", r"regression")):
        task_types.add("testing")
    if "refactor" in label_text or _contains(text, (r"refactor", r"cleanup", r"simplif")):
        task_types.add("refactor")
    if "performance" in label_text or _contains(text, (r"performance", r"optim(?:ize|ise|ization)")):
        task_types.add("performance")
    if _contains(text, (r"\bci\b", r"build", r"packag", r"dependency")):
        task_types.add("build_tooling")
    if "enhancement" in label_text or _contains(title, (r"feature", r"change request")):
        task_types.add("feature")
    if not task_types:
        task_types.add("other")

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
