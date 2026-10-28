# A → B：真实弱兴趣数据已写入原 011 数据库

后续已扩展到全部 1,276 个可核验 User 类型账号。最新版本与覆盖状态见 [全账号交接记录](b_weak_interest_all_accounts_2026-10-28.md)。本文件保留 20 账号试采的历史记录。

日期：2026-10-28（Asia/Shanghai）。本次按用户批准，对既有 20 个已核验人类账号读取公开 GitHub 账号、starred 和 owned repositories 接口，最多 60 个 GET 请求。没有执行 GitHub 写操作。

## 数据库及读取版本

- 原数据库：`data/b_delivery_v02_acceptance.sqlite3`。
- 新增 dataset_version：`b-real-contributions-with-weak-784d2330b3b891b9`。
- 保留的父版本：`b-real-contributions-v0.2-20261028`。
- 写入前备份：`data/b_delivery_v02_acceptance.before_weak_20261028T142612083950Z.sqlite3`。
- 未生成需要 B 单独接入的数据包；`data/b_weak_interest_20261028/` 仅为本机请求缓存、来源、采集计划和检查结果。所有引用的新增来源也已存入同库的 `midterm_dataset_asset`，前缀为 `weak_interest/`。

同一数据库中的新旧快照按 dataset_version 隔离。B 要读取新增数据，必须把查询版本切到上述新版本；继续读取 v0.2 会得到原有的 contribution-only 数据。

新快照沿用 011 表结构，不新增业务迁移；manifest schema 为 `b-profile-evidence-db-v1`。它是数据库中的派生快照，当前 payload_json 行为权威数据；继承的归档表资产只是父版本输入，不能当作新快照的表导出。B 固定 Head 的数据库适配入口已验证可读取此 schema。旧文件交付 verify.py/reader.py 仍只用于原 v0.2，不用于验证这个派生数据库版本。

## 采集结果与边界

| 行为 | 记录数 | 时间语义 |
| --- | ---: | --- |
| star | 1,305 | GitHub star media type 返回的 starred_at |
| fork | 945 | 观察到当前账号拥有且 fork=true，event_at=null |
| owned_repository | 479 | 观察到当前账号拥有且 fork=false，event_at=null |
| 合计 | 2,729 | 每条保留 observed_at、来源、身份核验引用 |

20 个账号的身份重新核对 GitHub numeric ID 和 type=User；40 个行为端点均完成，失败 0。20 个端点有后续页，首批每端点只读取前 100 条；不可声明全历史、全体 1,325 个身份覆盖或全部兴趣行为已交付。

选择规则为父数据中有文件贡献的 verified 人类账号，按贡献数降序取 20 个，开发者 key 打破并列。这是有偏样本，不能作为所有目标用户兴趣的代表。完整账号清单在本机缓存的 collection_plan.json 和新快照 manifest.acquisition_plan 中。

fork 表示当前归属的 fork 仓库，并不证明初始 fork 操作者或原仓库的兴趣强度。owned_repository 不等于实际写过代码。fork 与 owned_repository 的发生时间均保持未知，没有使用仓库 created_at 推测取得所有权的时间。仓库 primary_language 是采集时元数据，不是历史 star 时点语言。

原 v0.2 的 not_delivered 声明仍然有效。新快照已记录三类有界样本，同时保留 full_population_weak_interest、完整历史和模型推断等限制。

## 同库数据与验证

- 新快照含仓库 2,908、开发者 1,325、贡献 3,865、代码版本 3,865、项目行为 4,947（原 contribution 2,218 + 新弱兴趣 2,729）。
- 原 v0.2 快照保持不变；新快照继承的开发者、贡献、代码 payload_json 与父版本完全一致。
- 写入使用单事务；失败回滚整个新快照。重复导入相同缓存返回 already_imported，不重复写入。
- SQLite integrity_check=ok，foreign_key_check 无错误。
- 新导入器专项 6/6 通过，覆盖原数据保留、幂等、重复记录回滚、身份不匹配、来源字节篡改、未知时间及父 manifest 不匹配。
- 已逐条对照公开 API 缓存验证 2,729 条关系、账号 ID、star 时间、owner/fork 标志、观察时间及 public 状态。
- 用 B 固定 Head `f212b360d12beb6cf91bf8fa363fb3fe71c7fb6f` 的真实 ProfileEvidence 对象和数据库适配器遍历 20 个账号：1,647 条生成 github_weak 语言兴趣证据；1,082 条因语言为空或不在支持范围内排除。所有弱证据均为 preference.language.*，无能力特征；fork/owned 未知时间及 recency_applied=false 保留。

本次并未修复 A 对 B PR #26 指出的 ProfileEvidence 来源上下文传递 P2；数据库保留完整来源不代表 B 已把这些上下文传到 ProfileEvidence.details。PR 仍不合并。

## B 读取示例

在 B 的代码环境中：

```python
import json
import sqlite3
from oss_mentor.profiling.dataset_evidence import build_profile_evidence_from_a_dataset_store

version = 'b-real-contributions-with-weak-784d2330b3b891b9'
connection = sqlite3.connect('data/b_delivery_v02_acceptance.sqlite3')
try:
    manifest = json.loads(connection.execute(
        'SELECT manifest_json FROM midterm_dataset_snapshot WHERE dataset_version = ?',
        (version,),
    ).fetchone()[0])
    developer_key = manifest['acquisition_plan']['developer_keys'][0]
    result = build_profile_evidence_from_a_dataset_store(
        connection, version, developer_key=developer_key, as_of=manifest['created_at'],
    )
finally:
    connection.close()
```

采集器：`scripts/midterm_a/collect_b_weak_interest.py`；事务导入器：`scripts/midterm_a/import_b_weak_interest_db.py`。采集缓存可恢复复用；对同一缓存重复导入安全。数据与数据库均位于忽略目录，不应提交 Git。

接口依据：https://docs.github.com/en/rest/activity/starring 和 https://docs.github.com/en/rest/repos/repos 。
