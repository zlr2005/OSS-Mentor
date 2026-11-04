"""Checkpointed, quota-aware contribution/code sampling of every candidate repository."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

from oss_mentor.collector.github_client import GitHubClient, RateLimitExceeded
from oss_mentor.datasets.github_history import CachedGitHubSource, collect_history, write_snapshot
from collect_b_real_pilot import save, describe

ROOT = Path(__file__).resolve().parents[2]


def inventory():
    rows = {}
    for name in ('oss_mentor.sqlite3', 'oss_mentor_web.sqlite3'):
        with sqlite3.connect((ROOT/'data'/name).resolve().as_uri()+'?mode=ro', uri=True) as connection:
            for repository, github_id, count in connection.execute('''SELECT r.full_name,r.github_repository_id,count(*)
                    FROM repository r JOIN task_candidate t USING(repository_id) GROUP BY r.repository_id'''):
                key = repository.casefold()
                if key in rows and rows[key]['github_repository_id'] != github_id:
                    raise ValueError('Repository identity differs between databases')
                row = rows.setdefault(key, {'repository': repository, 'github_repository_id': github_id,
                                            'candidate_counts': {}, 'status': 'pending', 'attempts': 0})
                row['candidate_counts'][name] = count
    pilot = {'pytest-dev/pytest','pydantic/pydantic','textualize/textual'}
    return sorted(rows.values(), key=lambda r: (r['repository'].casefold() not in pilot,r['repository'].casefold()))


class QuotaClient(GitHubClient):
    def __init__(self, root, **kwargs):
        super().__init__(**kwargs)
        self.root = root

    def wait_until(self, reset):
        if reset is None:
            raise RuntimeError('Rate-limit reset unavailable')
        save(self.root/'network_state.json', {'phase': 'waiting_for_quota', 'resume_after': reset.isoformat()})
        while datetime.now(timezone.utc).timestamp() < reset.timestamp()+3:
            time.sleep(min(30, max(1, reset.timestamp()+3-time.time())))
        self.rate_limit_remaining = None

    def get(self, *args, **kwargs):
        for attempt in range(3):
            if self.rate_limit_remaining is not None and self.rate_limit_remaining < 100:
                self.wait_until(self.rate_limit_reset_at)
            try:
                response = super().get(*args, **kwargs)
                save(self.root/'network_state.json', {'phase': 'collecting', 'observed_at': datetime.now(timezone.utc).isoformat(),
                     'requests': self.request_count, 'remaining': self.rate_limit_remaining,
                     'reset_at': self.rate_limit_reset_at.isoformat() if self.rate_limit_reset_at else None})
                return response
            except RateLimitExceeded as error:
                self.wait_until(error.reset_at)
        raise RuntimeError('Repeated rate limit')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--until', required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    jobs_path = root/'jobs.json'
    if not jobs_path.exists():
        jobs = inventory()
        save(jobs_path, jobs)
        save(root/'manifest.json', {'until': args.until, 'job_count': len(jobs), 'commits_per_repository': 10,
                                   'files_per_commit': 2, 'scope': 'All distinct repositories in both candidate databases; bounded samples, not full histories'})
    jobs = json.loads(jobs_path.read_text(encoding='utf-8'))
    if json.loads((root/'manifest.json').read_text(encoding='utf-8'))['until'] != args.until:
        raise ValueError('Cutoff differs from saved plan')
    if args.prepare_only:
        print(json.dumps({'prepared': len(jobs)}));return
    credentials = subprocess.run(['gh','auth','token'], capture_output=True, text=True)
    if credentials.returncode or not credentials.stdout.strip():
        raise RuntimeError('Existing gh credentials unavailable')
    client = QuotaClient(root, api_base='https://api.github.com', api_version='2026-03-10',
                         user_agent='OSS-Mentor-B-repositories/1', token=credentials.stdout.strip(),
                         timeout_seconds=20, max_retries=2)
    def progress():
        save(jobs_path, jobs)
        counts = Counter(j['status'] for j in jobs)
        totals = Counter()
        for j in jobs:
            if j['status'] == 'completed':totals.update(j['result']['entity_counts'])
        save(root/'progress.json', {'observed_at': datetime.now(timezone.utc).isoformat(), 'total': len(jobs),
                                   'status': dict(counts), 'entity_counts_sum': dict(totals)})
        lines = ['# B：全部候选仓库贡献与代码覆盖进度', '',
                 f"目标仓库 {len(jobs)}；已完成 {counts.get('completed',0)}；待处理 {counts.get('pending',0)}；待重试 {counts.get('retry',0)}；未解决 {counts.get('unresolved',0)}。", '',
                 f"已取得文件贡献 {totals.get('contributions',0)} 条，代码版本 {totals.get('code_documents',0)} 个（可用性见各快照）。", '',
                 '每库最多最近 10 个 commit、每 commit 最多 2 个识别的源文件；不代表完整历史。', '',
                 '额度等待和最新请求时间见 network_state.json；当前仓库见 runner_state.json；逐仓库证据、质量和失败原因见 jobs.json。', '',
                 '| 仓库 | 状态 | 文件贡献 | 备注 |','|---|---|---:|---|']
        for j in jobs:
            failure = j.get('failure',{})
            note = failure.get('reason') or failure.get('type') or ''
            lines.append(f"| {j['repository']} | {j['status']} | {j.get('result',{}).get('entity_counts',{}).get('contributions',0)} | {str(note).replace('|','/')} |")
        (root/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    progress()
    for pass_number in range(2):
        for job in jobs:
            if job['status'] == 'completed' or job['attempts'] >= 2:continue
            repository = job['repository']; name = repository.replace('/', '__')
            # Short paths also keep Windows raw-store temporary filenames bounded.
            snapshot = root/'snapshots'/hashlib.sha256(repository.casefold().encode()).hexdigest()[:12]
            job['attempts'] += 1
            save(root/'runner_state.json', {'pid': os.getpid(), 'phase': 'collecting', 'repository': repository,
                                           'pass': pass_number+1, 'observed_at': datetime.now(timezone.utc).isoformat()})
            try:
                source = CachedGitHubSource(client, root/'c', repository, allow_network=True)
                response, _ = source.get('/repos/'+repository)
                expected = job['github_repository_id']
                if expected is not None and str(response.payload.get('id')) != str(expected):
                    raise ValueError('Candidate repository numeric identity changed')
                if not snapshot.exists():
                    pilot = ROOT/'data/b_real_contributions_20261026_v1/snapshots'/name
                    if pilot.exists():
                        # Validate before copying; recheck saved numeric identity below.
                        result = describe(pilot)
                        if result['selection']['until'] != args.until:raise ValueError('Pilot cutoff differs')
                        snapshot.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(pilot, snapshot)
                    else:
                        manifest, records, assets = collect_history(source, until=args.until, commit_limit=10,
                                                                   files_per_commit=2, multilingual=True)
                        write_snapshot(snapshot, manifest, records, assets)
                result = describe(snapshot)
                records = json.loads((snapshot/'records.json').read_text(encoding='utf-8'))
                if str(records['repositories'][0]['source_repository_id']) != str(response.payload['id']):
                    raise ValueError('Snapshot repository identity changed')
                job.update(status='completed', result=result, snapshot=snapshot.relative_to(root).as_posix())
                job.pop('failure', None)
            except Exception as error:
                job.update(status='retry' if job['attempts'] < 2 else 'unresolved',
                           failure={'type': type(error).__name__, 'http_status': getattr(error,'status_code',None),
                                    'reason': str(error) if isinstance(error,ValueError) else None})
            progress()
            print(json.dumps({'repository': repository,'status':job['status']}, ensure_ascii=False), flush=True)
        if any(j['status']=='retry' for j in jobs):time.sleep(10)
    save(root/'runner_state.json', {'pid':os.getpid(),'phase':'finished_with_unresolved' if any(j['status']!='completed' for j in jobs) else 'finished',
                                   'observed_at':datetime.now(timezone.utc).isoformat()})


if __name__ == '__main__':main()
