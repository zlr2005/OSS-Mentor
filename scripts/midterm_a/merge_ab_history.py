"""Merge old and backfilled file contributions into a separate working dataset."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / 'datasets/b_real_contributions_v0.2'
NEW = ROOT / 'data/midterm/working/advanced_adapter_backfill_2024_2026_v1'
OUT = NEW.parent / 'merged_ab_history_2024_2026_v1'


def rows(path):
    with path.open(encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timestamp(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat()


def merge_controls(source_roots):
    controls = {}
    for source, root in source_roots.items():
        if source == 'ab':
            continue
        for row in rows(root / 'historical_controls.jsonl'):
            key = row['issue_key'], row['as_of']
            reference = dict(source=source, evidence_ref=row['evidence_ref'])
            if key in controls:
                if controls[key]['content_sha256'] != row['content_sha256']:
                    raise ValueError(f'Candidate snapshot conflict for {key}')
                if reference not in controls[key]['evidence_sources']:
                    controls[key]['evidence_sources'].append(reference)
            else:
                controls[key] = dict(row, evidence_source=source, evidence_sources=[reference])
    return [controls[key] for key in sorted(controls)]


def comparison_groups(outcomes, controls):
    by_window, positives = defaultdict(list), defaultdict(set)
    for row in controls:
        by_window[row['repository'].casefold(), row['as_of']].append(row['issue_key'])
    for row in outcomes:
        positives[row['github_numeric_id']].add(row['issue_key'])
    return [dict(outcome_id=row['outcome_id'], github_numeric_id=row['github_numeric_id'],
                 as_of=row['profile_as_of'], repository=row['repository'], positive_issue_key=row['issue_key'],
                 comparison_issue_keys=sorted(set(by_window[row['repository'].casefold(), row['profile_as_of']])
                                              - positives[row['github_numeric_id']]),
                 training_ready=False)
            for row in outcomes]


def merge_outcomes(source_roots):
    merged = {}
    for source, root in source_roots.items():
        path = root / 'verified_outcomes.jsonl'
        if not path.exists():
            continue
        for row in rows(path):
            reference = dict(source=source, evidence_ref=row['evidence_ref'])
            key = row['outcome_id']
            if key in merged:
                prior = merged[key]
                fields = ('github_numeric_id', 'issue_key', 'profile_as_of', 'outcome_at')
                if any(prior[field] != row[field] for field in fields) or prior['issue_content']['content_sha256'] != row['issue_content']['content_sha256']:
                    raise ValueError('Conflicting duplicate outcome')
                prior['evidence_sources'].append(reference)
            else:
                merged[key] = dict(row, evidence_source=source, evidence_sources=[reference])
    return list(merged.values())


def main(supplements=(), outcome_supplements=()):
    if OUT.exists():
        raise FileExistsError(OUT)
    repos = {r['repository_key']: r['full_name'] for r in rows(OLD / 'tables/repositories.jsonl')}
    docs = {r['file_version_key']: r for r in rows(OLD / 'tables/code_documents.jsonl')}
    people = {r['developer_key']: r for r in rows(OLD / 'tables/developers.jsonl')}
    merged = {}
    source_roots = {'ab': OLD, 'backfill': NEW}
    source_roots.update({f'depth_{i}': path.resolve() for i, path in enumerate(supplements, 1)})
    source_roots.update({f'outcome_{i}': path.resolve() for i, path in enumerate(outcome_supplements, 1)})
    for source, path in source_roots.items():
        if source in ('ab', 'backfill'):
            continue
        manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
        for name, expected in manifest['files_sha256'].items():
            target = (path / name).resolve()
            if not target.is_relative_to(path) or digest(target) != expected:
                raise ValueError('Supplement checksum mismatch')
    source_keys = {source: set() for source in source_roots}
    conflicts = []

    def add(source, repo, commit, path, event, account, blob, sha, location, evidence, version, window=None, reason=None, developer_key=None):
        key = repo.casefold(), commit.lower(), path
        source_keys[source].add(key)
        root = source_roots[source]
        if location:
            content = (root / location).resolve()
            if not content.is_relative_to(root) or digest(content) != sha:
                raise ValueError(f'Invalid content hash for {key}')
        if not (root / evidence).is_file():
            raise ValueError(f'Missing evidence for {key}')
        row = merged.setdefault(key, dict(repository=key[0], commit_sha=key[1], path=path, sources=[]))
        ref = dict(source=source, file_version_key=version, event_at=timestamp(event),
                   github_numeric_id=account, blob_sha=blob, content_sha256=sha,
                   content_location=location, evidence_ref=evidence, missing_reason=reason,
                   developer_key=developer_key, as_of=window)
        if ref not in row['sources']:
            row['sources'].append(ref)

    for c in rows(OLD / 'tables/contributions.jsonl'):
        d = docs[c['file_version_key']]
        if (d['repository_key'], d['commit_sha']) != (c['repository_key'], c['commit_sha']):
            raise ValueError('Legacy file version mismatch')
        person = people[c['developer_key']]
        account = person['github_numeric_id'] if c['author_association_status'] == 'verified' else None
        add('ab', repos[c['repository_key']], c['commit_sha'], d['path'], c['event_at'],
            account, d['blob_sha'], d['content_hash'], d['content_location'], c['source_location'],
            c['file_version_key'], reason=d['exclusion_reason'], developer_key=c['developer_key'])
    for c in rows(NEW / 'historical_contributions.jsonl'):
        add('backfill', c['repository'], c['commit_sha'], c['path'], c['event_at'],
            c['github_numeric_id'], c['blob_sha'], c['content_sha256'], c['content_location'],
            c['evidence_ref'], c['file_version_key'], window=c['as_of'], developer_key=c['source_developer_key'])
    for source, root in source_roots.items():
        if source in ('ab', 'backfill'):
            continue
        for c in rows(root / 'historical_contributions.jsonl'):
            if datetime.fromisoformat(c['event_at']) >= datetime.fromisoformat(c['as_of']):
                raise ValueError('Supplement contribution crosses cutoff')
            add(source, c['repository'], c['commit_sha'], c['path'], c['event_at'],
                c['github_numeric_id'], c['blob_sha'], c['content_sha256'], c['content_location'],
                c['evidence_ref'], c['file_version_key'], window=c['as_of'], developer_key=c['source_developer_key'])

    for key, row in merged.items():
        for field in ('event_at', 'github_numeric_id', 'blob_sha', 'content_sha256'):
            values = {r[field] for r in row['sources'] if r[field] is not None}
            row[field] = next(iter(values)) if len(values) == 1 else None
            if len(values) > 1:
                conflicts.append(dict(key=key, field=field, values=sorted(values)))
        row['content_available'] = row['content_sha256'] is not None and any(r['content_location'] for r in row['sources'])
    outcomes = merge_outcomes(source_roots) if outcome_supplements else rows(NEW / 'verified_outcomes.jsonl')
    samples = rows(NEW / 'joined_samples.jsonl')
    controls = merge_controls(source_roots) if supplements or outcome_supplements else rows(NEW / 'historical_controls.jsonl')
    comparisons = comparison_groups(outcomes, controls)
    profiles = rows(NEW / 'profile_previews.jsonl')
    if outcome_supplements:
        profile_versions = {(p['profile_key'], p['profile_version']): p for p in profiles}
        for root in outcome_supplements:
            samples.extend(rows(root / 'joined_samples.jsonl'))
            for profile in rows(root / 'profile_previews.jsonl'):
                key = profile['profile_key'], profile['profile_version']
                if key in profile_versions and profile_versions[key]['profile_hash'] != profile['profile_hash']:
                    raise ValueError('Conflicting preview profile version')
                profile_versions[key] = profile
        profiles = list(profile_versions.values())
        by_outcome = {r['outcome_id']: r for r in comparisons}
        samples = [dict(row, comparison_issue_keys=by_outcome[row['outcome_id']]['comparison_issue_keys'])
                   for row in samples if by_outcome[row['outcome_id']]['comparison_issue_keys']]
        samples = list({row['outcome_id']: row for row in samples}.values())
    outcome_ids = {r['outcome_id'] for r in outcomes}
    if len(outcome_ids) != len(outcomes) or any(r['outcome_id'] not in outcome_ids for r in samples):
        raise ValueError('Invalid outcome references')
    summary = dict(status='working', frozen=False, training_ready=False,
                   contributions=len(merged), source_unique_counts={k: len(v) for k,v in source_keys.items()},
                   overlap=len(source_keys['ab'] & source_keys['backfill']),
                   contributions_by_year=dict(sorted(Counter(r['event_at'][:4] if r['event_at'] else 'conflict' for r in merged.values()).items())),
                   content_available=sum(r['content_available'] for r in merged.values()),
                   conflicts=len(conflicts), verified_outcomes=len(outcomes), joined_samples=len(samples),
                   historical_controls=len(controls), comparison_groups=len(comparisons),
                   groups_with_controls=sum(bool(r['comparison_issue_keys']) for r in comparisons),
                   groups_with_five_controls=sum(len(r['comparison_issue_keys']) >= 5 for r in comparisons))
    with tempfile.TemporaryDirectory(dir=OUT.parent) as staging:
        dest = Path(staging) / OUT.name
        dest.mkdir()
        for name, data in [('file_contributions.jsonl', (merged[k] for k in sorted(merged))), ('conflicts.jsonl', conflicts),
                           ('historical_controls.jsonl', controls), ('comparison_groups.jsonl', comparisons)]:
            with (dest / name).open('w', encoding='utf-8', newline='\n') as handle:
                for row in data:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
        if outcome_supplements:
            for name, data in [('verified_outcomes.jsonl', outcomes), ('joined_samples.jsonl', samples), ('profile_previews.jsonl', profiles)]:
                (dest / name).write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n' for r in data), encoding='utf-8')
        else:
            for name in ('verified_outcomes.jsonl', 'joined_samples.jsonl', 'profile_previews.jsonl'):
                shutil.copyfile(NEW / name, dest / name)
        (dest / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        manifest = dict(schema='merged-ab-history-working-v1',
                        source_roots={k: v.relative_to(ROOT).as_posix() for k,v in source_roots.items()},
                        source_manifest_sha256={k: digest(v/'manifest.json') for k,v in source_roots.items()},
                        copied_tables_reference_root='backfill',
                        files_sha256={f.name: digest(f) for f in dest.iterdir()})
        (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        dest.rename(OUT)
    print(json.dumps(summary))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', type=int, choices=range(1, 100), default=1)
    parser.add_argument('--supplement', type=Path, action='append', default=[])
    parser.add_argument('--outcome-supplement', type=Path, action='append', default=[])
    args = parser.parse_args()
    OUT = OUT.with_name(f'merged_ab_history_2024_2026_v{args.version}')
    main(args.supplement, args.outcome_supplement)
