"""Evidence-based offline reassessment; passing content is not online approval."""
from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from oss_mentor.task_features import TASK_FEATURE_VERSION, DIFFICULTY_FORMULA_VERSION, extract_task_features, infer_skill_requirements
from oss_mentor.task_readiness import task_review_reasons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--directory', required=True, type=Path)
    args = parser.parse_args()
    target = args.directory / 'review_results.jsonl'
    if target.exists():
        parser.error('Review results already exist; use a new directory')
    args.directory.mkdir(parents=True, exist_ok=True)
    payload = args.input.read_bytes()
    rows = json.loads(payload)
    counts, reasons, transitions = Counter(), Counter(), Counter()
    feature_code_hash = hashlib.sha256((ROOT / 'src/oss_mentor/task_features.py').read_bytes()).hexdigest()
    with target.open('x', encoding='utf-8') as output:
        for index, row in enumerate(rows):
            row['labels'] = json.loads(row['labels_json'])
            features = extract_task_features(row)
            review = task_review_reasons({**row, **features.to_dict(),
                                         'requirements': infer_skill_requirements(row, features)})
            old = json.loads(row['feature_evidence_json'])['difficulty_assessment']['information_quality']
            quality = features.feature_evidence['difficulty_assessment']['information_quality']
            if not review:
                decision, next_action = 'passes_content_gate', 'verify_timeline_and_linked_prs'
            elif quality['actionability'] == 'non_actionable':
                decision, next_action = 'not_a_single_task', 'retain_as_project_context'
            elif quality['actionability'] == 'design_pending':
                decision, next_action = 'design_needs_confirmation', 'read_maintainer_discussion'
            elif quality['actionability'] == 'unclear':
                decision, next_action = 'scope_needs_evidence', 'find_goal_reproducer_or_acceptance_evidence'
            else:
                decision, next_action = 'type_needs_disambiguation', 'review_task_type_from_full_context'
            identity = f"{row['repository'].casefold()}#{row['issue_number']}"
            content_hash = hashlib.sha256(json.dumps([identity, str(row['github_issue_id']), row['title'],
                row['body_text'], sorted(row['labels'])], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            entry = {'identity': identity, 'github_issue_id': str(row['github_issue_id']),
                     'title': row['title'], 'source_observed_at': row['github_verified_at'],
                     'content_sha256': content_hash, 'review_method': 'automated_rule_assisted_reassessment',
                     'old_task_types': json.loads(row['task_types_json']), 'new_task_types': features.task_types,
                     'old_scope': old, 'new_scope': quality, 'remaining_review_reasons': review,
                     'decision': decision, 'next_action': next_action,
                     'classification_evidence': features.feature_evidence['task_type_evidence'],
                     'rejected_classification_evidence': features.feature_evidence['rejected_task_type_evidence']}
            output.write(json.dumps(entry, ensure_ascii=False) + '\n')
            counts[decision] += 1
            reasons.update(review)
            transitions[(old['actionability'], quality['actionability'])] += 1
            if (index + 1) % 250 == 0:
                output.flush()
                print(json.dumps({'reviewed': index + 1, 'of': len(rows), 'decisions': dict(counts)}), flush=True)
    summary = {'observed_at': datetime.now(timezone.utc).isoformat(), 'reviewed_count': len(rows),
               'input_sha256': hashlib.sha256(payload).hexdigest(), 'task_feature_version': TASK_FEATURE_VERSION,
               'difficulty_formula_version': DIFFICULTY_FORMULA_VERSION,
               'feature_code_sha256': feature_code_hash,
               'review_method': 'automated_rule_assisted_reassessment', 'decisions': dict(counts),
               'remaining_review_reasons': dict(reasons),
               'scope_transitions': [{'from': a, 'to': b, 'count': n} for (a, b), n in transitions.items()],
               'limitations': ['Content gate only; live ownership and PR checks still required.',
                               'Not 5201 independent human judgements or build validations.',
                               'Unresolved records retain explicit next actions instead of being approved.']}
    (args.directory / 'review_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
