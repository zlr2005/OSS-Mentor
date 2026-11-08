from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/midterm_a'))
from build_growth_projects import metadata_reasons, pathway_reasons


class ProjectEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 23, tzinfo=timezone.utc)
        self.source = dict(full_name='example/project', github_repository_id=42)
        self.meta = dict(observed_at='2026-10-23T00:00:00Z', payload=dict(
            id=42, full_name='example/project', archived=False, disabled=False,
            fork=False, pushed_at='2026-10-22T00:00:00Z', license=dict(spdx_id='MIT')))
        self.config = dict(repository='example/project', contribution_doc='CONTRIBUTING.md', module_paths=['src'])
        self.ev = dict(repositories={'example/project': self.meta}, reads={
            'example/project/CONTRIBUTING.md':dict(payload='development guide ' * 12, observed_at='2026-10-23T00:00:00Z'),
            'example/project/root':dict(payload=[dict(path='src')]),
            'example/project/recent_pulls':dict(observed_at='2026-10-23T00:00:00Z', payload=[dict(
                merged_at='2026-10-22T00:00:00Z', user=dict(type='User'))])})

    def test_stale_snapshot_cannot_be_renewed_by_recent_push(self):
        meta = deepcopy(self.meta)
        meta['observed_at'] = '2026-10-01T00:00:00Z'
        self.assertIn('metadata_stale', metadata_reasons(self.source, meta, self.now))

    def test_identity_mismatch_and_archive_block_metadata(self):
        meta = deepcopy(self.meta)
        meta['payload'].update(id=43, archived=True)
        reasons = metadata_reasons(self.source, meta, self.now)
        self.assertIn('repository_identity_mismatch', reasons)
        self.assertIn('archived_disabled_or_unknown', reasons)

    def test_bot_merges_do_not_establish_human_collaboration(self):
        self.ev['reads']['example/project/recent_pulls']['payload'][0]['user']['type'] = 'Bot'
        self.assertIn('no_recent_human_merge_in_sample', pathway_reasons(self.config, self.ev, self.now))

    def test_old_merge_with_new_updated_date_does_not_count(self):
        pr = self.ev['reads']['example/project/recent_pulls']['payload'][0]
        pr.update(merged_at='2019-04-06T00:00:00Z', updated_at='2026-10-23T00:00:00Z')
        self.assertIn('no_recent_human_merge_in_sample', pathway_reasons(self.config, self.ev, self.now))

    def test_unobserved_module_path_requires_review(self):
        self.config['module_paths'] = ['invented-module']
        self.assertIn('module_path_unverified', pathway_reasons(self.config, self.ev, self.now))

    def test_missing_guide_or_license_cannot_enter_pool(self):
        del self.ev['reads']['example/project/CONTRIBUTING.md']
        self.meta['payload']['license'] = None
        reasons = pathway_reasons(self.config, self.ev, self.now)
        self.assertIn('contribution_guide_unverified', reasons)
        self.assertIn('license_requires_review', reasons)


if __name__ == '__main__':
    unittest.main()
