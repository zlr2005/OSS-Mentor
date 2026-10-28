# A → B：全体可核验账号的弱兴趣采集与同库导入

日期：2026-10-28（Asia/Shanghai）。本轮在用户明确批准的 1,276 个账号、最多 4,700 次只读 API 请求范围内完成采集，并写入原有 011 数据库。

## 当前数据库版本

- 数据库：`data/b_delivery_v02_acceptance.sqlite3`
- 本轮新版本：`b-real-contributions-with-weak-8cc664dd7446b2d8`
- 原始父版本：`b-real-contributions-v0.2-20261028`
- 写入前备份：`data/b_delivery_v02_acceptance.before_weak_20261028T151831961027Z.sqlite3`
- 先前 20 账号试采版本 `b-real-contributions-with-weak-784d2330b3b891b9` 也保留，不能把该版本当作本轮全覆盖结果。

本轮复用原 v0.2 作为贡献/代码父快照，合并全覆盖采集结果，没有将 20 账号试采记录重复追加。未建立另一个需要 B 单独接入的数据包。新增来源投影、采集清单、覆盖状态与身份复核记录均作为资产或 manifest 内容保存于同库。

## 身份与账号覆盖

原始 1,325 个身份中，有 1,276 个满足历史证据中的 numeric ID 与 GitHub type=User 关联；另外 27 个身份未确认，21 个为 Bot、1 个为 Organization，记录排除原因。本轮不要求账号必须有选中文件贡献，因此同时覆盖只有抽样元数据的账号。

type=User 不是真人、实名、非自动化或能力认证。例如自动化服务也可能注册为 User。采集清单中旧有的 human 用词应按此处及 manifest.identity_interpretation 的准确限定理解。

| 状态 | 账号数 | 含义 |
| --- | ---: | --- |
| 两类列表完整且有记录 | 970 | star 与 owned repositories 的所有分页均已完成 |
| 两类列表完整且为空 | 15 | 确认当前两个公开列表均为空，不是请求失败 |
| 仍有后续分页 | 286 | 已有记录，保留下一页位置，未宣称全历史覆盖 |
| 行为请求失败 | 1 | 已有记录；一个后续页的请求失败，保留已有页和错误 |
| 当前身份复核失败 | 4 | 历史 login 当前返回 404，不升级或猜测身份 |
| 原本身份未确认 | 27 | 未采集弱兴趣，也未生成相关画像证据 |
| Bot/Organization | 22 | 本轮按账号类型排除，不称作真人兴趣 |
| 总计 | 1,325 | 全部原始身份均有明确处理结果 |

1,272 个账号通过当前身份复核，1,257 个账号取得至少一条行为记录；985 个账号的两个公开列表均完成分页，其中包含上述 15 个空列表账号。

当前 404 的历史 login：`invalid-email-address`、`a-googler`、`Hello-Liar`、`pupuking723`。不能仅凭 404 判定账号不存在或推断改名后的身份。

`fireairforce` 的 owned repositories 已取得第一页 100 条，第 2 页发生网络请求错误；该账号并非没有数据。

## 记录与分页覆盖

| 行为 | 记录数 |
| --- | ---: |
| star | 92,852 |
| fork | 64,291 |
| owned_repository（非 fork） | 29,645 |
| 弱兴趣合计 | 186,788 |

2,544 个行为端点中：2,198 个已读完，345 个仍需后续页（状态 partial 或 pending），1 个分页请求失败。pending 是等待后续页，并不表示此前没有采集。4 个身份端点的 404 单独记录；不得将所有未完成端点合称请求失败。

采集采用先覆盖全体身份与首轮列表，再轮流翻页的策略。每页 100 条、最多 8 个并行工作线程；复用前一批 20 账号和本轮的成功响应缓存。处理中修复 numeric-ID 分页链接兼容，并启用 gzip 减少传输。

跨重启的保守请求预留账目累计到 4,700 即停止新增请求；它包含为在途批次作的保守预留，不能当作精确 HTTP 请求数。尚未完成的分页位置已经保留，本轮不宣称每个账号的全部历史行为均采完。

## 数据库及 B 侧验证

- 新版本包含仓库 151,569、身份 1,325、代码版本 3,865、贡献 3,865、项目行为 189,006（2,218 条原 contribution + 186,788 条弱兴趣）。
- 186,788 条行为均逐条核对来源投影中的 numeric repository ID、账号 ID、公开状态、starred_at 或 owner/fork 标志与 observed_at；没有重复关系。
- 新旧快照隔离；父快照保持不变。新增快照的身份、贡献与代码 payload_json 与父版本双向比较完全一致。
- SQLite integrity_check=ok，foreign_key_check 无错误；相同缓存重复导入不会重复写入。
- 采集与导入器专项 14 项通过，包括分页公平性、numeric-ID 链接、失败与空数据区分、累计额度不重置、gzip、幂等、回滚及身份/来源边界。
- 使用 B 固定 Head `f212b360d12beb6cf91bf8fa363fb3fe71c7fb6f` 的真实 ProfileEvidence 类型，遍历全部 1,325 个身份：产生 99,093 条 github_weak 语言兴趣证据，涉及 1,232 个账号；87,695 条行为因语言为空或不受当前六种语言映射支持而排除。
- 原有直接贡献证据仍为 8,138 条。未知身份不生成证据，fork/owned 的未知时间保持为 null，弱兴趣不会转换成能力特征。
- 验证器按开发者分组优化全量检查，并对样本与 B 的完整数据库读取入口比较，结果一致。此验证不代表模型效果提升或能力概率准确。

## 来源与解释限制

star 发生时间来自接口的 starred_at；fork/ownership 只表示本次观察到的归属状态，发生时间未知，不使用仓库 created_at 推测取得所有权的时间。仓库 primary_language 是观察时的元数据，不能当作 star 当时的语言。

本轮保留的是公开 API 字段投影及来源 URL、观察时间、投影前序列化 payload 的 SHA-256；不是原始网络响应 envelope。没有采集私有、已取消 star、已删除或不可见的关系。分页期间公开状态可能变化，去重不能将其变成原子历史快照。

B PR #26 的 ProfileEvidence 来源上下文传递 P2 仍需 B 修复；数据库保留完整来源不等于 ProfileEvidence.details 已带齐全部来源解释。PR 未合并，未向 GitHub 发布评论或推送提交。

## B 读取方式与产物

在 B 的环境中，继续调用 `build_profile_evidence_from_a_dataset_store`，将 dataset_version 切换为：

```python
version = 'b-real-contributions-with-weak-8cc664dd7446b2d8'
```

数据库 manifest 的 schema 为 `b-profile-evidence-db-v1`。沿用 011 表，无新业务迁移；旧文件交付的 verify.py 不用于验证该派生数据库快照。B 可读取 manifest.created_at 作为本轮验证的 as_of。

本机缓存目录：`data/b_weak_interest_all_20261028/`，包含：

- `account_coverage.csv`：全部 1,325 个身份的状态、行为数量及分页位置；
- `verification_result.json`：全量来源关系与覆盖校验结果；
- `database_import_result.json`：数据库版本、备份、导入数量与完整性结果；
- `profile_adapter_result.json`：B 固定 Head 的全体身份适配结果；
- `tables/coverage.jsonl`：逐端点分页、错误及下一页信息；
- `collection_plan.json`、`manifest.json`、`evidence/`：账号清单与可恢复的来源缓存。

采集器：`scripts/midterm_a/collect_b_weak_interest_all.py`；来源校验器：`scripts/midterm_a/verify_b_weak_acquisition.py`；同库导入器：`scripts/midterm_a/import_b_weak_interest_db.py`；B 适配校验器：`scripts/midterm_a/verify_b_weak_profile_db.py`。

缓存、数据库和备份在忽略目录 data 内，不应提交 Git。需要继续网络采集时，应明确增加本轮请求预算及剩余分页范围；单纯重跑当前命令不会重置已用额度。
