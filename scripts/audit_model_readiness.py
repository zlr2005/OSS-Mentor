"""Offline model preflight and candidate/cache coverage audit; never writes inputs."""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from oss_mentor.candidate_freshness import verification_is_current
from oss_mentor.task_readiness import task_review_reasons


def read_database(path: Path, table: str) -> list[dict]:
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute('SELECT * FROM ' + table)]


def key(repo, number):
    return f'{repo.casefold()}#{int(number)}'


def paper_cache(path: Path) -> dict:
    """Inspect JSON inside the archive without extracting or executing upstream code."""
    repos, issue_keys = {}, set()
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if '/repo_analysis_output/' not in name or not name.endswith('/issues.json'):
                continue
            repo = '/'.join(name.split('/')[-3:-1])
            data = json.loads(archive.read(name))
            meta_name = name.removesuffix('issues.json') + 'metadata.json'
            meta = json.loads(archive.read(meta_name)) if meta_name in archive.namelist() else {}
            repos[repo.casefold()] = {
                'repository': repo, 'issue_count': len(data['issues']),
                'extracted_at': data.get('repository_info', {}).get('extracted_at'),
                'directory_structure_present': bool(meta.get('directory_structure')),
                'dependency_graph_present': bool(meta.get('dependency_graph')),
                'architecture_analysis_present': bool(meta.get('architecture_analysis')),
            }
            issue_keys.update(key(repo, row['number']) for row in data['issues'])
    return {'archive_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'repositories': repos, 'issue_keys': issue_keys}


def coverage(rows: list[dict], cache: dict) -> dict:
    repositories = {r['repository'].casefold() for r in rows}
    return {
        'candidate_count': len(rows), 'repository_count': len(repositories),
        'paper_repository_overlap': len(repositories & cache['repositories'].keys()),
        'candidates_in_paper_repositories': sum(r['repository'].casefold() in cache['repositories'] for r in rows),
        'paper_issue_identity_overlap': sum(key(r['repository'], r['issue_number']) in cache['issue_keys'] for r in rows),
        'missing_paper_repository_cache': sorted(repositories - cache['repositories'].keys()),
    }


def source_evidence_audit(inventory: list[dict]) -> dict:
    """Count local source evidence separately from the paper's context cache."""
    repositories, details, timelines = set(), {}, set()
    sources = []
    for name in ['growth_projects_20261023_v1', 'newcomer_refresh_20261023_v1',
                 'inventory_screening_20261023_v1', 'inventory_screening_20261023_v5',
                 'inventory_screening_20261024_v1']:
        path = ROOT / 'data' / name / 'complete_evidence.json'
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        sources.append(str(path))
        repositories.update(repo.casefold() for repo in data.get('repositories', {}))
        for identity, entry in data.get('details', {}).items():
            payload = entry.get('payload', {})
            if payload.get('id') is not None:
                details.setdefault(identity.casefold(), set()).add(str(payload['id']))
        timelines.update(identity.casefold() for identity, entry in data.get('timelines', {}).items()
                         if entry.get('complete') is True and not entry.get('error'))
    matched_details = sum(str(r['github_issue_id']) in details.get(key(r['repository'], r['issue_number']), set())
                          for r in inventory)
    return {'sources': sources, 'inventory_count': len(inventory),
            'repository_metadata_present': sum(r['repository'].casefold() in repositories for r in inventory),
            'identity_matched_detail_present': matched_details,
            'complete_timeline_present': sum(key(r['repository'], r['issue_number']) in timelines for r in inventory),
            'meaning': 'Presence in selected source evidence only; not fresh verification, byte-level provenance validation or code/embedding coverage.'}


def business_audit(path: Path, inventory: list[dict], cache: dict, now: datetime) -> dict:
    # One read transaction keeps task/requirements/repository data consistent while a server runs.
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        integrity = conn.execute('PRAGMA quick_check').fetchone()[0]
        foreign_key_errors = len(conn.execute('PRAGMA foreign_key_check').fetchall())
        rows = [dict(r) for r in conn.execute('''SELECT tc.*, r.full_name AS repository,
            r.primary_language, r.is_archived, r.is_disabled, r.maintenance_status
            FROM task_candidate tc JOIN repository r USING(repository_id)''')]
        requirements = collections.defaultdict(list)
        for row in conn.execute('SELECT * FROM task_skill_requirement'):
            requirements[row['task_candidate_id']].append(dict(row))
    by_key = {key(r['repository'], r['issue_number']): r for r in rows}
    identity_conflicts, missing = [], []
    for record in inventory:
        identity = key(record['repository'], record['issue_number'])
        target = by_key.get(identity)
        if target is None:
            missing.append(identity)
        elif str(record['github_issue_id']) != str(target['github_issue_id']):
            identity_conflicts.append(identity)
    ready, available, reasons = [], [], collections.Counter()
    feature_coverage = skill_coverage = 0
    current = 0
    for row in rows:
        row['requirements'] = requirements[row['task_candidate_id']]
        feature_coverage += bool(row['task_feature_version'])
        skill_coverage += bool(row['requirements'])
        is_current = verification_is_current(row, now=now)
        current += is_current
        if row['candidate_availability'] == 'available' and is_current:
            available.append(row)
        # Mirror SQLiteCandidateStorage.matchable_candidates with a fixed audit clock.
        eligible = (row['candidate_eligibility'] == 'eligible'
                    and row['candidate_availability'] == 'available' and is_current
                    and not row['is_archived'] and not row['is_disabled']
                    and row['maintenance_status'] != 'inactive')
        if eligible:
            review = task_review_reasons(row)
            reasons.update(review)
            if not review:
                ready.append(row)
    return {
        'path': str(path), 'quick_check': integrity, 'foreign_key_errors': foreign_key_errors,
        'total': len(rows), 'feature_version_present': feature_coverage,
        'skill_requirements_present': skill_coverage, 'verification_current': current,
        'availability_current': len(available), 'matchable': len(ready),
        'ready_newcomer_label_signal': sum(bool(r['newcomer_label_signal']) for r in ready),
        'ready_languages': dict(collections.Counter(r['primary_language'] for r in ready)),
        'pending_feature_reasons': dict(reasons),
        'inventory_missing': missing, 'inventory_identity_conflicts': identity_conflicts,
        'all_cache_coverage': coverage(rows, cache), 'ready_cache_coverage': coverage(ready, cache),
    }


PROBE = '''import importlib,importlib.util,json,sys
r={'python':sys.version.split()[0],'executable':sys.executable,'packages':{}}
for name in ['torch','transformers','numpy','scipy','sklearn','lightgbm','dgl','torch_geometric']:
 r['packages'][name]=bool(importlib.util.find_spec(name))
for name in ['torch','transformers']:
 if r['packages'][name]:
  try:
   m=importlib.import_module(name); r[name]={'import':'ok','version':m.__version__}
  except Exception as e:r[name]={'import':'failed','error_type':type(e).__name__}
if r.get('torch',{}).get('import')=='ok':
 import torch
 r['cuda_available']=torch.cuda.is_available()
 r['xpu_available']=bool(hasattr(torch,'xpu') and torch.xpu.is_available())
 for device in ['cpu']+(['cuda'] if r['cuda_available'] else [])+(['xpu'] if r['xpu_available'] else []):
  try:
   x=torch.ones((128,128),device=device); r[device]={'matmul_mean':(x@x).mean().item()}
  except Exception as e:r[device]={'error_type':type(e).__name__}
print(json.dumps(r))
'''


def runtime_probe(executable: Path) -> dict:
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               HF_HUB_DISABLE_TELEMETRY='1', PYTHONDONTWRITEBYTECODE='1')
    try:
        result = subprocess.run([str(executable), '-c', PROBE], capture_output=True,
                                text=True, timeout=45, env=env)
        if result.returncode:
            return {'executable': str(executable), 'probe': 'failed', 'exit_code': result.returncode}
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {'executable': str(executable), 'probe': 'failed', 'error_type': type(exc).__name__}


def scan_assets(paths: list[Path]) -> dict:
    assets, denied = [], []
    suffixes = {'.safetensors', '.pt', '.pth', '.onnx'}
    for root in paths:
        if not root.exists():
            continue
        for directory, _, files in os.walk(root, onerror=lambda e: denied.append(e.filename)):
            for name in files:
                path = Path(directory) / name
                if path.suffix in suffixes or name == 'pytorch_model.bin':
                    assets.append(str(path))
    return {'scanned_roots': [str(p) for p in paths], 'weight_files': assets,
            'unreadable_paths': denied,
            'scope': 'Weights presence only; unrelated models do not establish CODER/CodeBERT readiness.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    parser.add_argument('--python', type=Path, action='append', default=[])
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output must be a new directory')
    now = datetime.now(timezone.utc)
    inventory_path = ROOT / 'data/candidate_inventory_20261024_v1/issue_inventory.sqlite3'
    before_hash = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    inventory = read_database(inventory_path, 'issue_inventory')
    cache = paper_cache(ROOT / 'tmp/supplementary_papers/issue-pilot.zip')
    # The stored snapshot timestamp is observed_at; never grant a new TTL during audit.
    fresh = [r for r in inventory if r['review_status'] == 'availability_passed' and r['expires_at'] is not None
             and verification_is_current({'verified_at': r['observed_at'], 'expires_at': r['expires_at']}, now=now)]
    dbs = [business_audit(ROOT / 'data' / name, inventory, cache, now)
           for name in ['oss_mentor.sqlite3', 'oss_mentor_web.sqlite3']]
    home = Path.home()
    hub = Path(os.environ.get('HF_HUB_CACHE') or
               str(Path(os.environ.get('HF_HOME', home / '.cache/huggingface')) / 'hub'))
    runtime = [runtime_probe(p) for p in dict.fromkeys([Path(sys.executable), *args.python])]
    assets = scan_assets([ROOT / 'data/midterm', hub])
    packages = [r for r in runtime if r.get('torch', {}).get('import') == 'ok'
                and r.get('transformers', {}).get('import') == 'ok' and r.get('cpu', {}).get('matmul_mean') == 128]
    summary = {
        'observed_at': now.isoformat(), 'network_requests': 0,
        'inventory_sha256_before': before_hash,
        'inventory_sha256_after': hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
        'inventory_total': len(inventory), 'inventory_current_availability': len(fresh),
        'inventory_cache_coverage': coverage(inventory, cache),
        'fresh_inventory_cache_coverage': coverage(fresh, cache),
        'local_source_evidence': source_evidence_audit(inventory),
        'paper_cache': {k: v for k, v in cache.items() if k != 'issue_keys'},
        'runtime_probes': runtime, 'assets': assets,
        'workspace_disk_free_gib': round(shutil.disk_usage(ROOT).free / 2**30, 2),
        'business_databases': dbs,
        'model_status': 'not_ready',
        'tensor_environment_available': bool(packages),
        'limitations': [
            'No model loading/inference claimed; tensor smoke test is not a model test.',
            'No registered CODER runtime/model gateway exists in the inspected project source.',
            'No verified CodeBERT checkpoint/revision, embedding manifest, graph, split or trained CODER package was supplied.',
            'Unreadable asset paths are unknown, not confirmed absent.',
            'Paper cache repository overlap does not validate code, commit, license, timestamp or task-level context.',
            'No task body, user profile, credential or raw upstream program is copied to this report.',
        ],
    }
    args.output.mkdir(parents=True)
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = [
        '# 模型运行预检与候选缓存覆盖审计', '',
        f"统计时间：{now.astimezone(timezone(timedelta(hours=8))):%Y-%m-%d %H:%M:%S} 北京时间。", '',
        '模型结论：当前项目尚不能运行真实 CODER 推荐。现有 CPU 张量环境是否通过：' + str(bool(packages)) + '。',
        '张量测试不代表 CodeBERT 编码或 CODER 推理通过；模型权重、版本化向量、图、划分、训练产物和加载入口仍需补齐并验证。', '',
        f"库存 {len(inventory):,} 条，按本次时钟仍在核验有效期内且 availability_passed 的为 {len(fresh):,} 条。", '',
        '| 数据库 | 总候选 | 特征版本存在 | 技能存在 | 当前可用 | 满足推荐准入 | 新手标签信号 |',
        '|---|---:|---:|---:|---:|---:|---:|',
    ]
    for db in dbs:
        lines.append(f"| {Path(db['path']).name} | {db['total']} | {db['feature_version_present']} | {db['skill_requirements_present']} | {db['availability_current']} | {db['matchable']} | {db['ready_newcomer_label_signal']} |")
    lines += ['', '网页库与原快照数量不同，是因为网页库已有独立刷新记录；本审计不修改或延长这些时间。', '',
              '运行环境：']
    for probe in runtime:
        lines.append(f"- `{probe['executable']}`：Python {probe.get('python', '未知')}；PyTorch {probe.get('torch', {}).get('version', '未成功导入')}；Transformers {probe.get('transformers', {}).get('version', '未成功导入')}。")
    lines += ['', '已通过的环境为 CPU 张量运算；CUDA/XPU 的不可用结论只针对所测环境，不能推断机器没有 GPU。',
              f"权重扫描发现 {len(assets['weight_files'])} 个文件；文件路径见 JSON。权重存在不等于已验证 CodeBERT/CODER 包。",
              f"有 {len(assets['unreadable_paths'])} 个目录不可读，不能把这些位置记为无文件；具体路径见 JSON。"]
    c = summary['inventory_cache_coverage']
    evidence = summary['local_source_evidence']
    lines += ['', '以上为规则准入计数，不是已训练模型结果，也不代表人工确认难度。', '',
              f"IssuePilot 历史缓存包含 {len(cache['repositories'])} 个仓库、{len(cache['issue_keys'])} 个唯一 Issue 身份。",
              f"累计库存涉及 {c['repository_count']} 个仓库；仓库重叠 {c['paper_repository_overlap']} 个，覆盖这些仓库中的候选 {c['candidates_in_paper_repositories']} 条；Issue 身份重叠 {c['paper_issue_identity_overlap']} 条。",
              '缓存是历史上下文；仓库命中不等于任务文件定位或当前状态核验。不得将 2025 年缓存时间更新为本次审计时间。', '',
              f"另查 {len(evidence['sources'])} 份本地采集证据包：库存中仓库元数据存在 {evidence['repository_metadata_present']} 条、GitHub 全局 ID 匹配的详情存在 {evidence['identity_matched_detail_present']} 条、完整时间线缓存存在 {evidence['complete_timeline_present']} 条。这里只审计存在性，不声称已验证全部原始字节或证据新鲜度。", '',
              '完整缺失仓库清单、当前准入候选的缓存覆盖、身份比对、运行环境与不可读路径见 summary.json。', '',
              '后续顺序：固定模型环境与 CodeBERT 修订 → 小批真实编码并登记向量 manifest → 补候选仓库的可追溯代码/上下文 → 构建图与冻结划分 → 训练并验证模型加载 → 再接入业务接口。', '',
              '本次仅离线读取：不安装依赖、不下载权重、不刷新 GitHub、不续期、不改业务库。原库存前后 SHA256 相同：' + str(summary['inventory_sha256_before'] == summary['inventory_sha256_after']) + '。']
    (args.output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(args.output / 'report.md'), 'model_status': summary['model_status'],
                      'inventory_total': len(inventory), 'inventory_current': len(fresh),
                      'matchable': {Path(d['path']).name: d['matchable'] for d in dbs}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
