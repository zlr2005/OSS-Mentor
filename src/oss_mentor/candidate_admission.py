"""Per-candidate admission policy; source text never selects a policy."""
import json
import re
from typing import Any

EXPANDED_CONTENT_POLICY = 'expanded-content-v1'
SOFT_INFORMATION_LABELS = frozenset({
    'needs clarification','needs info','needs-info','status: needs clarification',
    'status: needs information','waiting for response','awaiting response',
})


def has_minimum_description(record: dict[str, Any]) -> bool:
    title = str(record.get('title') or '').strip()
    body = str(record.get('body_text') or '').strip()
    if len(title) < 3 or len(body) < 40:
        return False
    text = re.sub(r'<!--.*?-->', '', body, flags=re.S)
    text = '\n'.join(line for line in text.splitlines() if not re.match(
        r'^\s*(?:#{1,6}\s|[-*]\s*\[[ x]\]|_?no response_?\s*$|n/?a\s*$|'
        r'(?:please\s+)?(?:describe|provide|insert|paste)\b[^.]*here\s*[.!]?\s*$|'
        r'[*_]*(?:请填写|简要描述您碰到的问题|如何操作可以重现该问题))', line, re.I))
    return len(re.sub(r'[\W_]', '', text)) >= 20


def expanded_content(record: dict[str, Any]) -> bool:
    if record.get('admission_policy') == EXPANDED_CONTENT_POLICY:
        return True
    evidence = record.get('feature_evidence')
    if evidence is None:
        try:
            evidence = json.loads(record.get('feature_evidence_json') or '{}')
        except (TypeError, ValueError):
            return False
    return (isinstance(evidence, dict)
            and isinstance(evidence.get('recommendation_admission'), dict)
            and evidence['recommendation_admission'].get('policy_version') == EXPANDED_CONTENT_POLICY)


def admission_evidence(record: dict[str, Any]) -> dict[str, Any]:
    if not expanded_content(record):
        return {}
    return {'recommendation_admission': {'policy_version': EXPANDED_CONTENT_POLICY,
        'content_uncertainty_is_warning': True, 'origin': 'user_requested_inventory_expansion'}}
