"""Create an immutable v0.2 delivery with standalone 011 compatibility tools."""
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[2]


def save(path,value):
    path.write_bytes((json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode())


def upgrade(source,target):
    sys.dont_write_bytecode=True
    source=Path(source);target=Path(target)
    if target.exists():raise ValueError('New delivery version must not exist')
    spec=importlib.util.spec_from_file_location('previous_delivery_verify',source/'verify.py')
    verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier);verifier.verify(source)
    previous=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    shutil.copytree(source,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for original,name in [('verify_b_delivery.py','verify.py'),('b_delivery_reader.py','reader.py'),('import_b_delivery_011.py','import_011.py')]:
        shutil.copyfile(Path(__file__).with_name(original),target/name)
    shutil.copyfile(ROOT/'db/sqlite/011_midterm_datasets.sql',target/'011_midterm_datasets.sql')
    tables=target/'tables'
    for kind in ('issues','issue_file_links'):(tables/(kind+'.jsonl')).write_bytes(b'')
    contributions=[json.loads(line) for line in (tables/'contributions.jsonl').read_text(encoding='utf-8').splitlines()]
    developers=[json.loads(line) for line in (tables/'developers.jsonl').read_text(encoding='utf-8').splitlines()]
    codes={row['file_version_key']:row for row in map(json.loads,(tables/'code_documents.jsonl').read_text(encoding='utf-8').splitlines())}
    by_developer=defaultdict(list)
    for contribution in contributions:by_developer[contribution['developer_key']].append(contribution)
    rows=[]
    for developer in developers:
        group=by_developer[developer['developer_key']]
        available=sum(codes[c['file_version_key']]['availability']=='available' for c in group)
        rows.append({'developer_key':developer['developer_key'],'contribution_keys':[c['contribution_key'] for c in group],
                     'contribution_count':len(group),'available_code_count':available,'missing_code_count':len(group)-available,
                     'repository_keys':sorted({c['repository_key'] for c in group})})
    (tables/'developer_contribution_index.jsonl').write_bytes(''.join(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n' for row in rows).encode())
    quality=json.loads((target/'quality.json').read_text(encoding='utf-8'))
    with_contributions=len({row['developer_key'] for row in contributions})
    quality['developers_with_file_contributions']=with_contributions
    quality['developers_without_selected_file_contributions']=len(developers)-with_contributions
    save(target/'quality.json',quality)
    readme=(target/'README.md').read_text(encoding='utf-8').replace('v0.1','v0.2')
    readme+='''

## v0.2：复用 B 的 011 接入

JSONL 记录和代码不变，新增 reader.py、import_011.py、原字节 011_midterm_datasets.sql。
reader.load_records(root) 返回 (manifest, records)，records 按旧 snapshot 的九组实体组织，
developer 增加 identity_evidence_location 兼容别名，同时保留全部 evidence_locations。
reader.read_code(root, code_document) 返回已校验的代码字节；缺代码返回 None。
tables/developer_contribution_index.jsonl 帮助 B 定位每个身份的贡献及可用/缺失代码计数，
这些只是数据索引，不是技能评分或 ProfileEvidence 结论。

先运行 python verify.py，再运行下面命令（路径均从仓库根目录）：

```powershell
python datasets/b_real_contributions_v0.2/import_011.py --database data/midterm/b_real_v02.sqlite3 --chunk-size 500
python datasets/b_real_contributions_v0.2/import_011.py --database data/midterm/b_real_v02.sqlite3 --chunk-size 777
```

两次返回同一 batch_id、completed 和相同实体数量；19/19 只适用于旧合成 fixture，
真实数据 total_items 是本批文件加实体的工作项数。失败批内回滚，已提交检查点保留，重跑恢复；
同 dataset_version 不允许换内容。只需 Python 标准库，不需要额外迁移编号或 B 的 012。
import_011.py 只创建/写入已有 011 定义的 midterm_dataset_* 表，不写画像评估或推荐表。
证据字节在 midterm_dataset_asset，实体仍是 payload_json，B 可复用其 011 读取适配。
数据库 midterm_dataset_snapshot.schema_version 保持真实 b-profile-evidence-v1，不伪装原始快照 schema；
该导入器是 normalized → 011 的桥接，不能改用旧 DatasetStore.import_snapshot 验证本目录。
012_midterm_profile_assessment.sql 的编号和接入仍由 B/D 协调，本包不创建新迁移。
本地未包含 B 的 ProfileEvidence 实现，因此这里只验收数据/011 兼容性，不宣称已通过 B 侧全量回归。
'''
    (target/'README.md').write_bytes(readme.encode())
    manifest=dict(previous)
    manifest.update(dataset_version='b-real-contributions-v0.2-20261028',created_at=datetime.now(timezone.utc).isoformat(),
                    parent_dataset_version=previous['dataset_version'],parent_manifest_sha256=hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest(),
                    quality=quality,sqlite_compatibility='011 tables; normalized schema retained; no new migration')
    manifest['entity_counts']=dict(previous['entity_counts'],issues=0,issue_file_links=0,developer_contribution_index=len(rows))
    manifest['files']=[{'path':p.relative_to(target).as_posix(),'size_bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                       for p in sorted(target.rglob('*')) if p.is_file() and p.name not in {'manifest.json','manifest.sha256'}
                       and '__pycache__' not in p.parts and p.suffix!='.pyc']
    save(target/'manifest.json',manifest)
    (target/'manifest.sha256').write_bytes((hashlib.sha256((target/'manifest.json').read_bytes()).hexdigest()+'  manifest.json\n').encode())
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=upgrade(args.source,args.output)
    print(json.dumps({'dataset_version':result['dataset_version'],'entity_counts':result['entity_counts']},ensure_ascii=False))
