# A → B：真实贡献与代码数据 v0.2

这是 309 个候选仓库的有界真实样本，fixture_only=false；并非所有提交、文件或开发者的完整历史。

在本目录运行 `python verify.py`，或从仓库根目录运行 `python datasets/b_real_contributions_v0.2/verify.py`。
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
root = Path('datasets/b_real_contributions_v0.2')
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
