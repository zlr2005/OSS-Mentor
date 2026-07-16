"""Build privacy-safe aggregate reports for the local candidate pool."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from oss_mentor.developer_profiles import ALLOWED_LANGUAGES, ALLOWED_TASK_TYPES
from oss_mentor.sqlite_store import SQLiteCandidateStore


ELIGIBILITY_VALUES = (
    "eligible",
    "temporarily_ineligible",
    "excluded",
    "unknown",
)
MINIMUM_OPTION_INVENTORY = 10
MINIMUM_COMBINATION_INVENTORY = 5


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def build_candidate_report(
    store: SQLiteCandidateStore, *, now: datetime | None = None
) -> dict[str, Any]:
    rows = store.candidate_report_rows()
    repositories = rows["repositories"]
    candidates = rows["candidates"]
    current = now or datetime.now(timezone.utc)
    current = current if current.tzinfo else current.replace(tzinfo=timezone.utc)

    eligibility = Counter(str(row["candidate_eligibility"]) for row in candidates)
    per_repository: dict[str, Counter[str]] = defaultdict(Counter)
    per_language: dict[str, Counter[str]] = defaultdict(Counter)
    task_types: Counter[str] = Counter()
    freshness: Counter[str] = Counter()
    for repository in repositories:
        per_repository[str(repository["full_name"])]

    for row in candidates:
        status = str(row["candidate_eligibility"])
        active = not bool(row["is_archived"] or row["is_disabled"])
        eligible = status == "eligible" and active
        repository = str(row["repository"])
        language = str(row["primary_language"] or "unknown")
        per_repository[repository]["candidate_count"] += 1
        per_language[language]["candidate_count"] += 1
        if eligible:
            per_repository[repository]["eligible_count"] += 1
            per_language[language]["eligible_count"] += 1
            if bool(row["newcomer_label_signal"]):
                per_repository[repository]["eligible_newcomer_count"] += 1
                per_language[language]["eligible_newcomer_count"] += 1

        types = [str(value) for value in _json_list(row["task_types_json"]) if value]
        for task_type in types or ["unclassified"]:
            task_types[task_type] += 1

        verified_at = _parse_time(row["github_verified_at"])
        if verified_at is None:
            freshness["never_verified"] += 1
        else:
            hours = max(0.0, (current - verified_at).total_seconds() / 3600)
            if hours <= 24:
                freshness["within_24_hours"] += 1
            elif hours <= 72:
                freshness["between_24_and_72_hours"] += 1
            else:
                freshness["over_72_hours"] += 1

    healthy = sum(
        not bool(row["is_archived"] or row["is_disabled"])
        for row in repositories
    )
    verified_eligible = sum(
        row["candidate_eligibility"] == "eligible"
        and row["github_verified_at"] is not None
        and not bool(row["is_archived"] or row["is_disabled"])
        for row in candidates
    )
    newcomer_eligible = sum(
        row["candidate_eligibility"] == "eligible"
        and bool(row["newcomer_label_signal"])
        and not bool(row["is_archived"] or row["is_disabled"])
        for row in candidates
    )

    active_eligible = [
        row
        for row in candidates
        if row["candidate_eligibility"] == "eligible"
        and not bool(row["is_archived"] or row["is_disabled"])
    ]

    def coverage(track: str) -> dict[str, Any]:
        selected = [
            row
            for row in active_eligible
            if track == "growth" or bool(row["newcomer_label_signal"])
        ]
        language_counts = {
            language: sum(
                str(row["primary_language"] or "").casefold() == language
                for row in selected
            )
            for language in sorted(ALLOWED_LANGUAGES)
        }
        type_counts = {
            task_type: sum(
                task_type in {str(value).casefold() for value in _json_list(row["task_types_json"])}
                for row in selected
            )
            for task_type in sorted(ALLOWED_TASK_TYPES)
        }
        combinations = {
            language: {
                task_type: sum(
                    str(row["primary_language"] or "").casefold() == language
                    and task_type
                    in {
                        str(value).casefold()
                        for value in _json_list(row["task_types_json"])
                    }
                    for row in selected
                )
                for task_type in sorted(ALLOWED_TASK_TYPES)
            }
            for language in sorted(ALLOWED_LANGUAGES)
        }
        repositories = Counter(str(row["repository"]) for row in selected)
        maximum = max(repositories.values(), default=0)
        return {
            "total_count": len(selected),
            "language_counts": language_counts,
            "task_type_counts": type_counts,
            "language_task_type_counts": combinations,
            "repository_counts": dict(sorted(repositories.items())),
            "repository_with_inventory_count": len(repositories),
            "maximum_repository_share": round(maximum / len(selected), 4) if selected else 0,
            "languages_below_minimum": [
                key for key, value in language_counts.items() if value < MINIMUM_OPTION_INVENTORY
            ],
            "task_types_below_minimum": [
                key for key, value in type_counts.items() if value < MINIMUM_OPTION_INVENTORY
            ],
            "combinations_below_minimum": [
                f"{language}:{task_type}"
                for language, values in combinations.items()
                for task_type, value in values.items()
                if value < MINIMUM_COMBINATION_INVENTORY
            ],
            "zero_combinations": [
                f"{language}:{task_type}"
                for language, values in combinations.items()
                for task_type, value in values.items()
                if value == 0
            ],
        }

    return {
        "schema_version": "candidate_pool_report_v0.3",
        "generated_at": current.isoformat(),
        "repository_summary": {
            "total_count": len(repositories),
            "healthy_count": healthy,
            "archived_or_disabled_count": len(repositories) - healthy,
        },
        "candidate_summary": {
            "total_count": len(candidates),
            "eligibility_counts": {
                key: int(eligibility.get(key, 0)) for key in ELIGIBILITY_VALUES
            },
            "verified_eligible_count": verified_eligible,
            "eligible_newcomer_signal_count": newcomer_eligible,
        },
        "condition_counts": {
            "closed": sum(row["state"] != "open" for row in candidates),
            "assigned": sum(row["assignment_state"] == "assigned" for row in candidates),
            "locked": sum(bool(row["is_locked"]) for row in candidates),
            "linked_open_pr": sum(row["has_linked_open_pr"] == 1 for row in candidates),
        },
        "by_repository": {
            name: dict(sorted(counts.items()))
            for name, counts in sorted(per_repository.items())
        },
        "by_language": {
            name: dict(sorted(counts.items()))
            for name, counts in sorted(per_language.items())
        },
        "by_task_type": dict(sorted(task_types.items())),
        "verification_freshness": {
            key: int(freshness.get(key, 0))
            for key in (
                "within_24_hours",
                "between_24_and_72_hours",
                "over_72_hours",
                "never_verified",
            )
        },
        "warning_counts": {
            "empty_body": sum(not bool(row["body_text"]) for row in candidates),
            "linked_pr_not_checked": sum(
                row["has_linked_open_pr"] is None for row in candidates
            ),
            "eligible_linked_pr_not_checked": sum(
                row["has_linked_open_pr"] is None
                and row["candidate_eligibility"] == "eligible"
                and not bool(row["is_archived"] or row["is_disabled"])
                for row in candidates
            ),
        },
        "coverage_policy": {
            "minimum_option_inventory": MINIMUM_OPTION_INVENTORY,
            "minimum_language_task_type_inventory": MINIMUM_COMBINATION_INVENTORY,
            "newcomer_floor": 100,
            "newcomer_buffer_target": 130,
            "maximum_repository_share_target": 0.30,
        },
        "recommendation_coverage": {
            "newcomer": coverage("newcomer"),
            "growth": coverage("growth"),
        },
        "latest_candidate_sync_at": max(
            (row["last_candidate_sync_at"] for row in repositories if row["last_candidate_sync_at"]),
            default=None,
        ),
        "latest_candidate_refresh_at": max(
            (row["last_candidate_refresh_at"] for row in repositories if row["last_candidate_refresh_at"]),
            default=None,
        ),
    }


def render_candidate_report_markdown(report: dict[str, Any]) -> str:
    repositories = report["repository_summary"]
    candidates = report["candidate_summary"]
    eligibility = candidates["eligibility_counts"]
    lines = [
        "# 候选池报告 v0.3",
        "",
        f"生成时间：`{report['generated_at']}`",
        "",
        "## 核心指标",
        "",
        f"- 仓库：{repositories['total_count']}（健康 {repositories['healthy_count']}，归档或禁用 {repositories['archived_or_disabled_count']}）",
        f"- 候选总数：{candidates['total_count']}",
        f"- 已验证可推荐：{candidates['verified_eligible_count']}",
        f"- 可推荐且含新人信号：{candidates['eligible_newcomer_signal_count']}",
        f"- 状态：eligible {eligibility['eligible']} / temporarily_ineligible {eligibility['temporarily_ineligible']} / excluded {eligibility['excluded']} / unknown {eligibility['unknown']}",
        "",
        "## 按仓库",
        "",
        "| 仓库 | 候选 | 可推荐 | 新人可推荐 |",
        "|---|---:|---:|---:|",
    ]
    for name, counts in report["by_repository"].items():
        lines.append(
            f"| {name} | {counts.get('candidate_count', 0)} | {counts.get('eligible_count', 0)} | {counts.get('eligible_newcomer_count', 0)} |"
        )
    lines.extend(
        [
            "",
            "## 数据质量",
            "",
            f"- 空正文警告：{report['warning_counts']['empty_body']}",
            f"- 全部候选中未检查关联 PR：{report['warning_counts']['linked_pr_not_checked']}",
            f"- 可推荐任务中未检查关联 PR：{report['warning_counts']['eligible_linked_pr_not_checked']}",
            "",
            "## 选项库存保障",
            "",
            f"- 新人可推荐库存：{report['recommendation_coverage']['newcomer']['total_count']}",
            f"- 进阶可推荐库存：{report['recommendation_coverage']['growth']['total_count']}",
            f"- 新人库存不足语言：{', '.join(report['recommendation_coverage']['newcomer']['languages_below_minimum']) or '无'}",
            f"- 新人库存不足任务类型：{', '.join(report['recommendation_coverage']['newcomer']['task_types_below_minimum']) or '无'}",
            f"- 新人零库存组合：{', '.join(report['recommendation_coverage']['newcomer']['zero_combinations']) or '无'}",
            f"- 进阶零库存组合：{', '.join(report['recommendation_coverage']['growth']['zero_combinations']) or '无'}",
            f"- 新人任务最大单仓库占比：{report['recommendation_coverage']['newcomer']['maximum_repository_share']:.1%}",
            "",
            "> 本报告只包含聚合统计，不包含 Token、Issue 正文或用户身份数据。",
            "",
        ]
    )
    return "\n".join(lines)
