"""Targeted finite repair queue for previously unresolved contribution snapshots."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from oss_mentor.datasets.github_history import CachedGitHubSource, collect_history, write_snapshot
from collect_b_real_pilot import save, describe
from collect_b_candidate_repositories import QuotaClient


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();source_root=args.source.resolve();root=args.directory.resolve()
    root.mkdir(parents=True,exist_ok=True)
    if not (root/'jobs.json').exists():
        original=json.loads((source_root/'jobs.json').read_text(encoding='utf-8'))
        jobs=[{'repository':j['repository'],'github_repository_id':j['github_repository_id'],
               'original_failure':j['failure'],'status':'pending','attempts':0} for j in original if j['status']=='unresolved']
        save(root/'jobs.json',jobs)
        save(root/'manifest.json',{'source':str(source_root),'source_jobs_sha256':hashlib.sha256((source_root/'jobs.json').read_bytes()).hexdigest(),
            'original_completed':sum(j['status']=='completed' for j in original),'job_count':len(jobs),
            'until':json.loads((source_root/'manifest.json').read_text(encoding='utf-8'))['until'],
            'adapter':'github-history-v3','scanned_commits':100,'selected_code_commits':10,'files_per_commit':2})
    jobs=json.loads((root/'jobs.json').read_text(encoding='utf-8'))
    plan=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    def progress():
        save(root/'jobs.json',jobs)
        counts=Counter(j['status'] for j in jobs);entities=Counter()
        for j in jobs:
            if j['status']=='completed':entities.update(j['result']['entity_counts'])
        summary={'observed_at':datetime.now(timezone.utc).isoformat(),'repair_total':len(jobs),'status':dict(counts),
                 'combined_completed_repositories':plan['original_completed']+counts['completed'],
                 'new_entity_counts_sum':dict(entities)}
        save(root/'progress.json',summary)
        lines=['# B：未解决仓库针对性补采','',f"原有完成 {plan['original_completed']}；本轮新增完成 {counts['completed']}；合计覆盖 {summary['combined_completed_repositories']} / 309。",'',
               '保留原批次，新增快照均按 github-history-v3 验证；许可分类未知仍明确保留，不推定发布许可。','',
               '| 仓库 | 状态 | 原因或备注 |','|---|---|---|']
        for j in jobs:lines.append(f"| {j['repository']} | {j['status']} | {str(j.get('failure',{}).get('reason') or j.get('failure',{}).get('type') or '').replace('|','/')} |")
        (root/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    progress()
    if args.prepare_only:print(json.dumps({'prepared':len(jobs)}));return
    credentials=subprocess.run(['gh','auth','token'],capture_output=True,text=True)
    if credentials.returncode or not credentials.stdout.strip():raise RuntimeError('Existing gh credentials unavailable')
    client=QuotaClient(root,api_base='https://api.github.com',api_version='2026-03-10',user_agent='OSS-Mentor-B-repair/1',
                       token=credentials.stdout.strip(),timeout_seconds=20,max_retries=2)
    for pass_number in range(2):
        for job in jobs:
            if job['status']=='completed' or job['attempts']>=2:continue
            repository=job['repository'];job['attempts']+=1
            snapshot=root/'s'/hashlib.sha256(repository.casefold().encode()).hexdigest()[:12]
            save(root/'runner_state.json',{'pid':os.getpid(),'phase':'collecting','repository':repository,
                'pass':pass_number+1,'observed_at':datetime.now(timezone.utc).isoformat()})
            try:
                source=CachedGitHubSource(client,source_root/'c',repository,allow_network=True)
                response,_=source.get('/repos/'+repository)
                if job['github_repository_id'] is not None and str(response.payload['id'])!=str(job['github_repository_id']):
                    raise ValueError('Candidate repository numeric identity changed')
                if not snapshot.exists():
                    manifest,records,assets=collect_history(source,until=plan['until'],commit_limit=10,
                                                            files_per_commit=2,multilingual=True,repair=True)
                    write_snapshot(snapshot,manifest,records,assets)
                result=describe(snapshot)
                records=json.loads((snapshot/'records.json').read_text(encoding='utf-8'))
                if str(records['repositories'][0]['source_repository_id'])!=str(response.payload['id']):
                    raise ValueError('Snapshot repository numeric identity changed')
                job.update(status='completed',result=result,snapshot=snapshot.relative_to(root).as_posix())
                job.pop('failure',None)
            except Exception as error:
                job.update(status='retry' if job['attempts']<2 else 'unresolved',
                           failure={'type':type(error).__name__,'http_status':getattr(error,'status_code',None),
                                    'reason':str(error) if isinstance(error,ValueError) else None})
            progress();print(json.dumps({'repository':repository,'status':job['status']},ensure_ascii=False),flush=True)
        if any(j['status']=='retry' for j in jobs):time.sleep(10)
    save(root/'runner_state.json',{'pid':os.getpid(),'phase':'finished_with_unresolved' if any(j['status']!='completed' for j in jobs) else 'finished',
                                  'observed_at':datetime.now(timezone.utc).isoformat()})


if __name__=='__main__':main()
