"""Normalize verified snapshots into a portable, uncompressed B evidence delivery."""
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil

from oss_mentor.datasets.snapshot import load_snapshot


def digest(data):return hashlib.sha256(data).hexdigest()
def encode(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()


def projection(envelope, raw_bytes):
    metadata=envelope['metadata'];payload=envelope['payload']
    fields={k:payload[k] for k in ('id','sha','full_name','private','archived','language','size','encoding') if k in payload}
    if 'license' in payload:fields['declared_spdx']=(payload.get('license') or {}).get('spdx_id')
    for role in ('author','committer'):
        if role in payload:
            account=payload[role] or {}
            fields[role]={k:account[k] for k in ('id','login','type') if k in account}
    if isinstance(payload.get('commit'),dict):
        commit=payload['commit'];fields['commit']={'tree_sha':commit['tree']['sha'],
            'author_at':commit['author']['date'],'committer_at':commit['committer']['date']}
    return {'schema_version':'b-sanitized-source-projection-v1','projection_not_raw':True,
            'source_url':metadata['source_url'],'observed_at':metadata['fetched_at'],
            'status_code':metadata['status_code'],'raw_artifact_sha256':digest(raw_bytes),
            'source_payload_sha256':metadata['response_sha256'],'fields':fields}


def merge_developer(existing,row):
    if existing is None:return row
    for name in ('developer_key','source','source_developer_id','github_numeric_id','identity_status'):
        if existing[name]!=row[name]:raise ValueError('Conflicting developer identity: '+row['developer_key'])
    return {**existing,'evidence_locations':sorted(set(existing['evidence_locations']+row['evidence_locations']))}


def export_delivery(coverage_path,target,verifier_path):
    coverage_path=Path(coverage_path);target=Path(target)
    if target.exists():raise ValueError('Delivery directory exists; choose a new version')
    coverage=json.loads(coverage_path.read_text(encoding='utf-8'))
    if coverage['covered_repositories']!=coverage['inventory_repositories']:raise ValueError('Incomplete coverage inventory')
    target.mkdir(parents=True)
    kinds=('repositories','developers','files','code_documents','contributions','project_behaviors','file_aliases',
           'license_documents','source_snapshots','excluded_commits','weak_interest_behaviors')
    tables={kind:[] for kind in kinds};developers={};repositories=set();validation_count=0
    def put(relative,data):
        path=target/relative;path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and path.read_bytes()!=data:raise ValueError('Conflicting content-addressed asset')
        path.write_bytes(data)
        return relative
    for position,entry in enumerate(coverage['repositories'],1):
        snapshot=Path(entry['snapshot'])
        manifest_bytes=(snapshot/'manifest.json').read_bytes()
        if digest(manifest_bytes)!=entry['manifest_sha256']:raise ValueError('Input manifest hash mismatch')
        manifest,records,contents=load_snapshot(snapshot)
        if manifest['fixture_only']:raise ValueError('Synthetic input rejected')
        validation_count+=1
        repository=records['repositories'][0]
        if repository['repository_key'] in repositories:raise ValueError('Duplicate repository input')
        repositories.add(repository['repository_key'])
        source_paths={}
        def evidence(location):
            if not location:return None
            if location.startswith('https://'):return location
            if location not in source_paths:
                raw_bytes=contents[location];envelope=json.loads(gzip.decompress(raw_bytes))
                data=encode(projection(envelope,raw_bytes))
                source_paths[location]=put('evidence/'+digest(data)+'.json',data)
            return source_paths[location]
        def remap(row):
            result=dict(row)
            for key,value in list(result.items()):
                if key.endswith('_location') and value and str(value).startswith('provenance/'):
                    result[key]=evidence(value)
            return result
        for kind in ('repositories','files','file_aliases','project_behaviors','contributions'):
            for original in records[kind]:
                row=remap(original)
                if kind in ('project_behaviors','contributions'):
                    key='behavior_key' if kind=='project_behaviors' else 'contribution_key'
                    row[key]=digest(encode([row.get(k) for k in ('developer_key','repository_key','file_version_key','commit_sha','event_at','behavior_type')]))
                tables[kind].append(row)
        for original in records['developers']:
            row=dict(original);locations=[]
            if row.get('identity_evidence_location'):locations.append(evidence(row.pop('identity_evidence_location')))
            row['evidence_locations']=locations
            developers[row['developer_key']]=merge_developer(developers.get(row['developer_key']),row)
        for original in records['code_documents']:
            row=remap(original)
            if row['availability']=='available':
                data=contents[row['content_location']]
                if digest(data)!=row['content_hash']:raise ValueError('Input code content mismatch')
                row['content_location']=put('content/code/'+digest(data)+'.txt',data)
            tables['code_documents'].append(row)
        for item in manifest['source_provenance']['license_evidence']:
            data=contents[item['content_location']]
            tables['license_documents'].append({'repository_key':repository['repository_key'],
                'commit_sha':item['commit_sha'],'declared_spdx':item['spdx_id'],
                'classification_basis':'Current repository declaration; pinned text is not a historical SPDX determination.',
                'content_hash':digest(data),'content_location':put('content/licenses/'+digest(data)+'.txt',data),
                'source_location':evidence(item['source_location']),'redistribution_status':'not_assessed'})
        for excluded in manifest['source_provenance']['selection'].get('excluded_commits',[]):
            tables['excluded_commits'].append({'repository_key':repository['repository_key'],
                'commit_sha':excluded['commit_sha'],'reason':excluded['reason'],
                'source_location':evidence(excluded['commit_source_location']),
                'original_tree_proof_sha256':digest(contents[excluded['tree_proof_location']])})
        tables['source_snapshots'].append({'repository_key':repository['repository_key'],'repository':repository['full_name'],
            'dataset_version':manifest['dataset_version'],'original_manifest_sha256':entry['manifest_sha256'],
            'adapter_version':manifest['source_provenance']['adapter_version'],'time_coverage':manifest['time_coverage'],
            'selected_commit_shas':manifest['source_provenance']['selection']['commit_shas'],
            'quality_gaps':manifest['quality_gaps'],'source_validation':'source_verified_at_export'})
        print(json.dumps({'validated_and_exported':position,'total':len(coverage['repositories'])}),flush=True)
    tables['developers']=sorted(developers.values(),key=lambda row:row['developer_key'])
    for kind,rows in tables.items():
        data=''.join(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n' for row in rows).encode()
        put('tables/'+kind+'.jsonl',data)
    availability=Counter(r['availability'] for r in tables['code_documents'])
    associations=Counter(r['author_association_status'] for r in tables['contributions'])
    quality={'code_availability':dict(availability),'missing_code_reasons':dict(Counter(r['exclusion_reason'] for r in tables['code_documents'] if r['availability']=='missing')),
        'contribution_author_association':dict(associations),'developer_identity':dict(Counter(r['identity_status'] for r in tables['developers'])),
        'missing_code_rate':availability['missing']/len(tables['code_documents']),
        'confirmed_contribution_author_rate':associations['verified']/len(tables['contributions']),
        'weak_interest_behavior_records':0,'unique_developer_key_count':len(developers)}
    put('quality.json',encode(quality))
    put('verify.py',Path(verifier_path).read_bytes())
    put('.gitattributes',b'** -text\n')
    readme='''# A → B：真实贡献与代码数据 v0.1

这是 309 个候选仓库的有界真实样本，fixture_only=false；并非所有提交、文件或开发者的完整历史。

在本目录运行 `python verify.py`，或从仓库根目录运行 `python datasets/b_real_contributions_v0.1/verify.py`。
只需 Python 3.11+ 标准库，不需要网络、Token、数据库密码或 oss_mentor 源码依赖。

## 数据入口

tables/contributions.jsonl 对应 ContributionRecord；tables/code_documents.jsonl 对应 CodeDocument。
tables/project_behaviors.jsonl 仅包含 contribution，不是 star/fork 等弱兴趣。
tables/weak_interest_behaviors.jsonl 为空，表示真实弱兴趣采集尚未交付。
tables/developers.jsonl、repositories.jsonl、files.jsonl、file_aliases.jsonl 提供关联实体。
tables/license_documents.jsonl 保留实际历史许可文本索引；未推定许可分类或发布授权。
tables/source_snapshots.jsonl 记录每个原始快照版本、时间范围和来源校验结论。
tables/excluded_commits.jsonl 保留缺证据的提交排除记录。

## 字段与读取

每行一个 UTF-8 JSON 对象。ContributionRecord 包含 developer_key、repository_key、file_key、
file_version_key、commit_sha、event_at、author_at、observed_at、behavior_type、
author_association_status、evidence_location、source_location；contribution_key 是该关系的稳定键。
CodeDocument 包含 path、language、commit_sha、content_hash、content_location、availability、exclusion_reason。
按 file_version_key 关联贡献和代码；按 content_location（相对本目录）读取原始代码字节。
文件路径是固定 commit 的路径，不是当前主分支路径；改名证据独立保留。

```python
import json
from pathlib import Path
root = Path('datasets/b_real_contributions_v0.1')
rows = [json.loads(line) for line in (root/'tables/contributions.jsonl').read_text(encoding='utf-8').splitlines()]
codes = {r['file_version_key']: r for r in map(json.loads, (root/'tables/code_documents.jsonl').read_text(encoding='utf-8').splitlines())}
for row in rows:
    code = codes[row['file_version_key']]
    if code['availability'] == 'available':
        content = (root/code['content_location']).read_bytes()
```

## 必须保留的解释边界

verified 仅表示 GitHub 将提交作者关联到账号，不是实名或能力认证；机器人也可能是 verified。
开发者按规范 developer_key 合并，GitHub 数字 ID 关联一致才合并；未确认作者不猜测合并。
developer.evidence_locations 保留跨仓库证据。合并后人数与按仓库相加的数量不同。
event_at 是 committer 时间，author_at 单独保留；observed_at 是采集观察时间，不可互相替代。
代码 missing 的版本仍保留贡献和缺失原因，B 不应把缺代码当作无能力或凭空填充。

## 证据与可验证范围

导出时重新验证了所有原始快照来源。evidence/ 是明确标注 projection_not_raw 的白名单投影：
提供原 API URL、观察时间、原始归档/响应哈希、账号 ID 和固定 commit/blob 信息。
它不是原始 API 响应；原始含 Git 作者邮箱的归档留在 A 本地，不随本交付目录复制。
verify.py 校验交付字节、表间引用、代码哈希及版本关系；不把投影当作完整原始来源重放。
需要复核完整树/来源时，使用 source_snapshots 的原始版本及原 manifest 哈希向 A 定位，或按来源 URL 核验。

manifest.json 和 manifest.sha256 固定本批清单；quality.json 给出带口径的质量统计。
该 normalized schema 是 b-profile-evidence-v1，不直接套用旧 fixture 的 DatasetStore.import_snapshot。
B 可直接读取 JSONL 构造画像输入，无需重复实现采集器。

本次尚未提交或推送 GitHub，未生成压缩包。本批不能替代真实 star/fork/拥有仓库行为交付。
'''
    put('README.md',readme.encode())
    files=[{'path':p.relative_to(target).as_posix(),'size_bytes':p.stat().st_size,'sha256':digest(p.read_bytes())}
           for p in sorted(target.rglob('*')) if p.is_file()]
    starts=[row['time_coverage']['start'] for row in tables['source_snapshots']]
    ends=[row['time_coverage']['end'] for row in tables['source_snapshots']]
    manifest={'schema_version':'b-profile-evidence-v1','dataset_version':'b-real-contributions-v0.1-20261028',
        'fixture_only':False,'created_at':datetime.now(timezone.utc).isoformat(),
        'time_coverage':{'start':min(starts),'end':max(ends)},'entity_counts':{k:len(v) for k,v in tables.items()},
        'validated_source_snapshots':validation_count,'coverage_manifest_sha256':digest(coverage_path.read_bytes()),
        'quality':quality,'provenance_mode':'Sanitized projections; complete original API envelopes remain at source.',
        'not_delivered':['star','fork','owned_repository_interest','complete_repository_history','CODER_inference'],
        'files':files}
    put('manifest.json',encode(manifest));put('manifest.sha256',(digest((target/'manifest.json').read_bytes())+'  manifest.json\n').encode())
    return manifest
