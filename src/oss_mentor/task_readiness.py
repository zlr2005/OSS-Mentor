"""Versioned content gates; explicit expansion keeps uncertainty visible."""
import json
from typing import Any
from oss_mentor.task_features import PUBLIC_TASK_TYPES
from oss_mentor.candidate_admission import expanded_content, has_minimum_description


def task_review_reasons(record: dict[str, Any]) -> tuple[str, ...]:
    reasons = []
    types = record.get('task_types')
    evidence = record.get('feature_evidence')
    try:
        if types is None:
            types = json.loads(record.get('task_types_json') or '[]')
        if evidence is None:
            evidence = json.loads(record.get('feature_evidence_json') or '{}')
    except (ValueError, TypeError):
        return ('invalid_task_feature_evidence',)
    relaxed = expanded_content(record)
    if not isinstance(types, (list, tuple)) or (not PUBLIC_TASK_TYPES.intersection(str(t) for t in types)
                                              and not (relaxed and len(types) == 1 and types[0] == 'other')):
        reasons.append('task_type_requires_review')
    if not record.get('task_feature_version'):
        reasons.append('missing_task_feature_version')
    assessment = evidence.get('difficulty_assessment', {}) if isinstance(evidence, dict) else {}
    if not isinstance(assessment, dict):
        return ('invalid_task_feature_evidence',)
    quality = assessment.get('information_quality', {})
    if not isinstance(quality, dict):
        return ('invalid_task_feature_evidence',)
    if quality.get('actionability') != 'actionable' and not (relaxed and quality.get('actionability') in {'unclear','design_pending'}):
        reasons.append('task_scope_requires_review')
    if relaxed:
        if not has_minimum_description(record):
            reasons.append('insufficient_minimum_task_description')
    for field in ('estimated_code_difficulty', 'estimated_setup_difficulty'):
        value = record.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 3:
            reasons.append('missing_or_invalid_' + field)
    if not record.get('requirements'):
        reasons.append('skill_requirements_missing')
    return tuple(reasons)


def task_admission_warnings(record: dict[str, Any]) -> tuple[str, ...]:
    if not expanded_content(record):
        return ()
    try:
        types = record.get('task_types')
        if types is None:
            types = json.loads(record.get('task_types_json') or '[]')
        evidence = record.get('feature_evidence')
        if evidence is None:
            evidence = json.loads(record.get('feature_evidence_json') or '{}')
        scope = evidence.get('difficulty_assessment',{}).get('information_quality',{}).get('actionability')
    except (ValueError,TypeError,AttributeError):
        return ('任务信息尚未完整确认',)
    warnings = []
    if not PUBLIC_TASK_TYPES.intersection(str(t) for t in types):
        warnings.append('任务类型待确认，未计入类型偏好加分')
    if scope == 'unclear':
        warnings.append('任务范围说明较少，参与前需与维护者确认目标')
    elif scope == 'design_pending':
        warnings.append('任务涉及方案讨论，参与前需与维护者确认实现方向')
    return tuple(warnings)
