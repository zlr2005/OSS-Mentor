"""Export real platform pre-score inputs and coverage gaps without changing the database."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from oss_mentor.gated_scoring import CachedGatedScorer, context_key, content_hash, issue_key
from oss_mentor.services.profile_service import as_developer_profile_v2
from oss_mentor.services.recommendation_service import RecommendationService
from oss_mentor.sqlite_store import SQLiteCandidateStore, _ClosingConnection
from oss_mentor.candidate_freshness import inventory_assumption, ASSUME_INVENTORY_ENV
import os


class ReadOnlyStore(SQLiteCandidateStore):
    def initialize(self):
        pass  # Inspection must never migrate or seed the source database.

    def connect(self):
        connection = sqlite3.connect(self.database_path.as_uri() + '?mode=ro', uri=True,
                                     factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        return connection


def export_inputs(database, bundle, cache, output, profile_path=None, developer_key=None):
    if not database.is_file():
        raise ValueError('Existing initialized database required')
    if developer_key and profile_path is None:
        raise ValueError('--developer-key requires an explicit --profile')
    scorer = CachedGatedScorer(bundle, cache)
    store = ReadOnlyStore(database, ROOT / 'db/sqlite/001_mvp.sql')
    service = RecommendationService(store, model_scorer=scorer)
    if profile_path:
        raw = json.loads(profile_path.read_text(encoding='utf-8'))
        profiles = [as_developer_profile_v2(raw.get('profile', raw))]
    else:
        profiles = [as_developer_profile_v2(store.profile_for_matching(p['profile_key']))
                    for p in store.list_profiles_public()]
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for profile in profiles:
        key = context_key(profile, developer_key)
        folder = output / key
        folder.mkdir()
        tasks = service.eligible_candidates(profile)
        (folder / 'profile.json').write_text(json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding='utf-8')
        (folder / 'candidates.json').write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding='utf-8')
        reason = 'No currently eligible matching tasks'
        ready = False
        if tasks:
            try:
                scorer.score(profile=profile, developer_key=developer_key, candidates=tasks)
                ready, reason = True, None
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                reason = str(exc)
        results.append(dict(context_key=key, profile_key=profile.profile_key, eligible_candidates=len(tasks),
                            ready=ready, reason=reason,
                            candidate_bindings=[dict(issue_key=issue_key(t), content_hash=content_hash(t)) for t in tasks]))
    report = dict(as_of=datetime.now(timezone.utc).isoformat(), database=str(database.resolve()),
                  model_version=scorer.model.manifest['model_version'],
                  backbone_sha256=scorer.model.manifest['backbone_sha256'],
                  ready=bool(results) and all(r['ready'] for r in results), contexts=results,
                  inventory_availability_assumed=os.environ.get(ASSUME_INVENTORY_ENV) == '1',
                  feature_production_required=True,
                  note='Snapshot inputs only; form skills are not CODER evidence. No features fabricated or timestamps refreshed.')
    (output / 'readiness.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['database', 'bundle', 'cache', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--developer-key')
    parser.add_argument('--assume-inventory-available', action='store_true')
    args = parser.parse_args()
    with inventory_assumption(args.assume_inventory_available):
        report = export_inputs(args.database, args.bundle, args.cache, args.output, args.profile, args.developer_key)
    print(json.dumps(dict(ready=report['ready'], contexts=len(report['contexts']), report=str(args.output / 'readiness.json'))))
    raise SystemExit(0 if report['ready'] else 2)
