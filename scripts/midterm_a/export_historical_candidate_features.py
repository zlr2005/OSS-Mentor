"""从冻结的历史正文生成版本化候选特征；保留未知字段与规则推断来源。"""
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import json
from pathlib import Path

import backfill_adapter_2024_2026 as b
from oss_mentor.task_features import extract_task_features, TASK_FEATURE_VERSION


def main(roots, output, include_estimates=False):
    output.mkdir(parents=True, exist_ok=True)
    snapshots, histories, source_manifests = {}, {}, {}
    rules = b.ROOT / 'src/oss_mentor/task_features.py'
    rules_sha256 = b.digest(rules.read_bytes())
    for root in roots:
        source = root.resolve().relative_to(b.ROOT).as_posix()
        source_manifests[source] = b.digest((root / 'manifest.json').read_bytes())
        for row in b.rows(root / 'verified_outcomes.jsonl') if (root / 'verified_outcomes.jsonl').exists() else []:
            candidate = dict(row['issue_content'], evidence_ref=row['evidence_ref'])
            key = candidate['issue_key'], candidate['as_of']
            if key in snapshots and snapshots[key][0]['content_sha256'] != candidate['content_sha256']:
                raise ValueError('Historical candidate content conflict')
            snapshots.setdefault(key, (candidate, source))
        for candidate in b.rows(root / 'historical_controls.jsonl'):
            key = candidate['issue_key'], candidate['as_of']
            if key in snapshots and snapshots[key][0]['content_sha256'] != candidate['content_sha256']:
                raise ValueError('Historical control content conflict')
            snapshots.setdefault(key, (candidate, source))
        for row in b.rows(root / 'historical_contributions.jsonl'):
            key = row['repository'].casefold(), row['commit_sha'], row['path']
            if key in histories and histories[key]['content_sha256'] != row['content_sha256']:
                raise ValueError('Historical file content conflict')
            histories.setdefault(key, row)
    by_repo = defaultdict(list)
    for row in histories.values():
        if row.get('language'):
            by_repo[row['repository'].casefold()].append(row)
    for values in by_repo.values():
        values.sort(key=lambda r: b.dt(r['event_at']))
    dates = {repo: [b.dt(r['event_at']) for r in values] for repo, values in by_repo.items()}
    rows, task_coverage, language_coverage = [], 0, 0
    for done, key in enumerate(sorted(snapshots), 1):
        candidate, source = snapshots[key]
        title, body = candidate['title_at_cutoff'], candidate['body_at_cutoff']
        if b.digest(b.canonical({'title': title, 'body': body})) != candidate['content_sha256']:
            raise ValueError('Candidate text hash mismatch')
        repo = candidate['issue_key'].rsplit('#', 1)[0]
        features = extract_task_features({'title': title, 'body_text': body, 'labels': []})
        cutoff = b.dt(candidate['as_of'])
        visible = by_repo.get(repo, [])[:bisect_left(dates.get(repo, []), cutoff)]
        languages = dict(Counter(r['language'] for r in visible))
        evidence = features.feature_evidence.get('task_type_evidence', {})
        task_coverage += bool(evidence)
        language_coverage += bool(languages)
        rows.append(dict(issue_key=candidate['issue_key'], as_of=candidate['as_of'], repository=repo,
            created_at=candidate['available_from'], available_from=candidate['available_from'], available_until=None,
            content_sha256=candidate['content_sha256'], feature_version='historical-candidate-features-v1',
            task_feature_rules_version=TASK_FEATURE_VERSION, task_feature_rules_sha256=rules_sha256,
            task_types=list(features.task_types), task_type_status='rule_inferred_from_historical_text',
            task_type_evidence=evidence, primary_language=None, technology_stack=None,
            code_difficulty=None, setup_difficulty=None, labels=None,
            unknown_fields=['primary_language', 'technology_stack', 'code_difficulty', 'setup_difficulty', 'labels', 'available_until'],
            observed_code_languages=languages,
            language_evidence_scope='Partial sampled file contributions strictly before candidate cutoff; not repository primary language.',
            language_evidence_keys=[[r['repository'], r['commit_sha'], r['path']] for r in visible],
            evidence_source_root=source, evidence_ref=candidate['evidence_ref'], training_ready=False))
        if include_estimates:
            assessment = features.feature_evidence['difficulty_assessment']
            estimates = {}
            for dimension in ('code', 'setup'):
                entry = assessment['dimensions'][dimension]
                direct = any(e['source'] in ('title', 'body') for e in entry['evidence'])
                supported = direct and entry['confidence'] in ('medium', 'high') and not entry['conflicts']
                estimates[dimension] = dict(value=entry['level'] if supported else None,
                    status='rule_estimated' if supported else 'unknown',
                    confidence=entry['confidence'], evidence=entry['evidence'], conflicts=entry['conflicts'])
            skills = features.feature_evidence['skill_requirement_evidence']
            requirements = [entry for entry in skills['skills'].values()
                if entry['category'] in ('tool', 'platform', 'language') and entry['decision'] == 'included'
                and entry['requirement_source'].startswith('explicit_')
                and any(e['source'] in ('title', 'body') for e in entry['evidence'])]
            rows[-1].update(feature_version='historical-candidate-features-v2',
                difficulty_estimates=estimates, difficulty_formula_version=assessment['formula_version'],
                inferred_technology_requirements=requirements, skill_rules_version=skills['rules_version'])
        if done % 1000 == 0:
            print(json.dumps({'candidates_done': done, 'total': len(snapshots)}), flush=True)
    if b.digest(rules.read_bytes()) != rules_sha256:
        raise ValueError('Feature rules changed during export')
    target = output / 'candidate_features.jsonl'
    target.write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n' for r in rows), encoding='utf-8')
    summary = dict(candidates=len(rows), candidates_with_task_type_evidence=task_coverage,
                   candidates_with_sampled_language_evidence=language_coverage, training_ready=False)
    if include_estimates:
        summary.update(candidates_with_supported_code_estimates=sum(r['difficulty_estimates']['code']['value'] is not None for r in rows),
            candidates_with_supported_setup_estimates=sum(r['difficulty_estimates']['setup']['value'] is not None for r in rows),
            candidates_with_explicit_technology_requirements=sum(bool(r['inferred_technology_requirements']) for r in rows))
    (output / 'summary.json').write_bytes(b.canonical(summary))
    (output / 'manifest.json').write_bytes(b.canonical(dict(source_manifest_sha256=source_manifests,
        task_feature_rules_sha256=rules_sha256,
        files_sha256={p.name: b.digest(p.read_bytes()) for p in output.iterdir() if p.is_file() and p.name != 'manifest.json'})))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--include-estimates', action='store_true')
    args = parser.parse_args()
    main(args.source, args.output, args.include_estimates)
