"""Export only final candidate tables, without users, sessions or local history."""
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from oss_mentor.candidate_freshness import verification_is_current
from oss_mentor.datasets.publication_secrets import require_publication_safe

ROOT=Path(__file__).resolve().parents[2]


def main():
    target=ROOT/'datasets/c_issue_candidates_v1'
    if target.exists():raise ValueError('Final delivery already exists')
    (target/'tables').mkdir(parents=True)
    source=ROOT/'data/oss_mentor.sqlite3';c=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    tables={};files={};schemas=[];now=datetime.now(timezone.utc)
    try:
        c.execute('BEGIN')
        for name in ('repository','task_candidate','task_skill_requirement'):
            tables[name]=[dict(r) for r in c.execute('SELECT * FROM '+name)]
            schemas.append(c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()[0]+';')
            schemas.extend(r[0]+';' for r in c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",(name,)))
            files[name]=[]
            for start in range(0,len(tables[name]),500):
                relative=f'tables/{name}.{start//500+1:04}.jsonl';files[name].append(relative)
                public_data=''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in tables[name][start:start+500]).encode()
                require_publication_safe(public_data)
                (target/relative).write_bytes(public_data)
        c.rollback()
    finally:c.close()
    (target/'schema.sql').write_bytes(('\n\n'.join(schemas)+'\n').encode())
    shutil.copyfile(Path(__file__).with_name('c_delivery_tool.py'),target/'dataset.py')
    (target/'.gitattributes').write_bytes(b'** -text\n')
    freshness={'as_of':now.isoformat(),'verification_current_by_timestamp':sum(verification_is_current(r,now=now) for r in tables['task_candidate']),
               'stored_eligibility_counts':dict(Counter(r['candidate_eligibility'] for r in tables['task_candidate'])),
               'note':'Stored eligibility is historical; expiry is preserved. No collection time is renewed by export.'}
    readme='''# A → C：最终候选数据快照

本目录取自完整业务候选库，只导出 repository、task_candidate、task_skill_requirement；不含用户、会话、反馈或数据库凭据。
JSONL 按 500 行分片，字段与三张现有业务表一致，保留原 ID、特征版本、原始准入原因、警告、核验和到期时间。
这是供任务特征/推荐算法联调的冻结样本，不承诺过期任务当前仍可推荐。stored eligible 不是当前可推荐数；线上使用前必须重新检查状态及有效期。
历史宽准入信息在 feature_evidence_json 和 warnings_json 等原字段中，缺失/other 不应伪造匹配加分。

```powershell
python datasets/c_issue_candidates_v1/dataset.py
python datasets/c_issue_candidates_v1/dataset.py --database data/c_candidate_delivery.sqlite3
```

独立脚本仅需 Python 标准库；新建数据库保留候选相关三表及索引，不含 API/用户等运行时表，不应直接当作完整服务数据库启动。
也可导入 dataset.read_table(root, 'task_candidate') 按原字段读取；table_files 和 table_counts 见 manifest。
309 个仓库可经 repository.github_repository_id/full_name 与 B 的 repository.source_repository_id/full_name 对齐；不能把两个数据集内部 repository_id/key 直接等同。
未包含 CODER 模型、权重、向量、评分真值或完整 Issue—文件映射。
'''
    (target/'README.md').write_bytes(readme.encode())
    manifest={'schema_version':'c-candidate-tables-v1','dataset_version':'c-issue-candidates-v1-20261028',
              'created_at':now.isoformat(),'source':'Canonical local oss_mentor.sqlite3 candidate tables; no personal tables exported',
              'table_counts':{k:len(v) for k,v in tables.items()},'table_files':files,'freshness':freshness,
              'files':[{'path':p.relative_to(target).as_posix(),'size_bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                       for p in sorted(target.rglob('*')) if p.is_file()]}
    (target/'manifest.json').write_bytes((json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode())
    (target/'manifest.sha256').write_bytes((hashlib.sha256((target/'manifest.json').read_bytes()).hexdigest()+'  manifest.json\n').encode())
    print(json.dumps({'table_counts':manifest['table_counts'],'freshness':freshness},ensure_ascii=False))


if __name__=='__main__':main()
