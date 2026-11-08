"""Conservative refresh checks: ambiguous claims defer, never establish ownership."""
import re
from typing import Any

CLAIM = re.compile(
    r"\b(?:i(?:['’]m| am|['’]ll| will| would like to|['’]d like to| want to| can| could)|we(?:['’]re| are| will))\s+(?:\w+\s+){0,4}(?:work(?:ing)? on|take (?:this|it)|fix(?:ing)? (?:this|it)|implement(?:ing)?|submit(?:ting)? (?:a |the )?pr|pick (?:this|it) up)"
    r"|\b(?:can|could|may) i\s+(?:\w+\s+){0,3}(?:work|take|fix|pick)"
    r"|\bassign (?:this (?:issue )?to )?me\b|\b(?:working on (?:this|it)|opened (?:a |the )?pr|i have (?:a |an )?(?:fix|pr)|i['’]ve (?:started|submitted|opened))\b"
    r"|(?:我来|我正在|我想|我可以).{0,12}(?:修复|处理|解决|认领|实现)", re.I)
BLOCKED = re.compile(r'needs?.?(?:more.?)?info|waiting.for|awaiting|blocked|in.progress|claimed|duplicate|invalid|wontfix|won.t.fix|(?:^|[: /])questions?(?:$|[: /])|support|discussion|needs.repro|unconfirmed', re.I)
SCOPE = re.compile(r'\b(?:test plan|endgame|iteration plan|release plan|meeting|agenda|tracking issue|umbrella issue|weekly sync)\b', re.I)
PR_URL = re.compile(r'https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)')
HARD_BLOCKED = re.compile(r"(?:^|[: /_-])(?:blocked|in[ _-]?progress|claimed|duplicate|invalid|won['’ ._-]?t[ _-]?fix|support|questions?)(?:$|[: /_-])", re.I)
ACTIVE_CLAIM = re.compile(
    r"\b(?:i(?:['’]m| am)|we(?:['’]re| are))\s+(?:currently\s+)?(?:working on|implementing|fixing)\s+"
    r"(?:this|it|the\s+(?:issue|bug|feature)|a\s+(?:fix|patch))(?!\s+(?:project|application|app|codebase|code))\b"
    r"|\b(?:i have|i['’]ve)\s+(?:already\s+)?(?:started\s+(?:working|implementing|fixing)\b|"
    r"(?:submitted|opened)\s+(?:an?\s+|the\s+)(?:pr|pull request|patch|fix)\b)"
    r"|(?:^|[.!?\n]\s*)(?:working on (?:this|it)|opened (?:a |the )?pr)\b"
    r"|(?:我正在|我已经)\s*(?:为(?:此|这个|该)问题)?(?:修复|处理|解决|实现|提交)"
    r"|已提交\s*PR", re.I)
INTENT_CLAIM = re.compile(
    r"\b(?:can|could|may) i\s+(?:\w+\s+){0,3}(?:work on|take|fix|pick up)\s+(?:this|it)\b"
    r"|\b(?:i can|i want to|i would like to|i['’]d like to)\s+(?:work on|fix|implement)\s+(?:this|it)\b"
    r"|\bassign (?:this (?:issue )?to )?me\b|我想.{0,8}(?:认领|处理这个|修复这个)", re.I)


def context_review_reasons(detail: dict[str, Any], events: list[dict[str, Any]], *, expanded: bool = False) -> list[str]:
    reasons = []
    if not str(detail.get('body') or '').strip():
        reasons.append('empty_body_requires_review')
    blocked = HARD_BLOCKED if expanded else BLOCKED
    if any(blocked.search(str(label.get('name', ''))) for label in detail.get('labels', [])):
        reasons.append('blocking_or_information_needed_label')
    scope = re.compile(r'\b(?:meeting|agenda|weekly sync)\b', re.I) if expanded else SCOPE
    if scope.search(str(detail.get('title') or '')):
        reasons.append('planning_or_umbrella_task')
    texts = [detail.get('body') or ''] + [e.get('body') or '' for e in events if e.get('event') == 'commented']
    claim = ACTIVE_CLAIM if expanded else CLAIM
    if any(claim.search(text) for text in texts):
        reasons.append('claim_or_implementation_text_requires_review')
    if not expanded and any(e.get('event') == 'referenced' and e.get('commit_url') for e in events):
        reasons.append('commit_reference_requires_review')
    return reasons


def expanded_context_warnings(detail: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    warnings = []
    if any(BLOCKED.search(str(label.get('name',''))) and not HARD_BLOCKED.search(str(label.get('name','')))
           for label in detail.get('labels', [])):
        warnings.append('任务仍有讨论或信息待补充，已作为可探索候选纳入')
    if any(e.get('event') == 'referenced' and e.get('commit_url') for e in events):
        warnings.append('存在历史提交引用，已另行检查关联 PR 状态')
    texts = [detail.get('body') or '']+[e.get('body') or '' for e in events if e.get('event')=='commented']
    if any(INTENT_CLAIM.search(t) for t in texts) and not any(ACTIVE_CLAIM.search(t) for t in texts):
        warnings.append('讨论中有参与意向或认领请求，参与前需确认')
    return warnings
