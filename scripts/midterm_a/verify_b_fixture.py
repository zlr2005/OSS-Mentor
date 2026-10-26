"""Validate the synthetic B handoff and print reproducible quality statistics."""
import hashlib
import json
from pathlib import Path

from oss_mentor.datasets.snapshot import load_snapshot, validate_snapshot

ROOT = Path(__file__).resolve().parents[2]


def main():
    root = ROOT / 'fixtures/midterm/a/snapshot_v0.1'
    validation = validate_snapshot(root)
    manifest, records, _ = load_snapshot(root)
    developers = records['developers']
    code = records['code_documents']
    behaviors = records['project_behaviors']
    contributions = records['contributions']
    def metric(count, total):
        return {'numerator': count, 'denominator': total,
                'rate': count / total if total else None}
    report = {
        'validation': validation,
        'dataset_version': manifest['dataset_version'],
        'fixture_only': True,
        'identity_statistics_are_synthetic': True,
        'entity_counts': manifest['entity_counts'],
        'time_coverage': manifest['time_coverage'],
        'quality': {
            'confirmed_developer_identity': metric(sum(x['identity_status'] == 'verified' for x in developers), len(developers)),
            'confirmed_contribution_author': metric(sum(x['author_association_status'] == 'verified' for x in contributions), len(contributions)),
            'missing_code': metric(sum(x['availability'] == 'missing' for x in code), len(code)),
            'unknown_behavior_event_time': metric(sum(x['event_at'] is None for x in behaviors), len(behaviors)),
        },
        'manifest_sha256': hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(),
        'file_hashes': {x['path']: x['sha256'] for x in manifest['files']},
        'limits': ['Synthetic identities and code are for integration only.',
                   'No real GitHub profile conclusions or model quality claims.'],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
