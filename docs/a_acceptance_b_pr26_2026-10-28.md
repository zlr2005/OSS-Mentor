# A 对 B PR #26 的验收记录

日期：2026-10-28（Asia/Shanghai）。验收角色：A，范围为真实贡献、身份状态、缺失代码、来源边界及 ProfileEvidence 解释。

结论：核心数据解释检查通过；来源边界存在一项 P2 待修复，A 暂不签署完整验收通过。PR 保持 Draft，不合并。本记录不替代 C 的增强画像契约验收或 D 的持久化/API 验收。

## 固定版本与执行环境

- B PR：https://github.com/zlr2005/OSS-Mentor/pull/26
- B Head：`f212b360d12beb6cf91bf8fa363fb3fe71c7fb6f`
- A 数据提交：`7315e9b6d64ee123a975ed38eae155a4677c6ed3`，也是本次主工作区 HEAD。
- 数据版本：`b-real-contributions-v0.2-20261028`
- manifest SHA-256：`790e73a1b050ae9055a187e8fa944bc483d1170a92670f3fc0b78a105de1d044`
- 全量真实数据检查的 as_of：`2026-10-28T00:00:00Z`。

主工作区包含其他未提交工作，本次未切换分支或修改其业务实现。将 B 固定提交中的 profiling、公共契约、storage、SQLite 迁移、相关 fixtures 和两个专项测试文件下载到 `tmp/b26_a_acceptance/`；其余支持模块复制自本地主工作区。使用真实 `ProfileEvidence` 类，无契约替身。此环境用于 A 范围验收，不宣称是 B 全仓干净 checkout，也未复跑 618 项全量测试。

## 检查结果

| 项目 | 结果 |
| --- | --- |
| A 独立交付校验 | `delivery_verified`，字节哈希、引用、代码版本及质量统计通过 |
| B 贡献解释专项及数据适配专项 | 16/16 通过 |
| 全体开发者 | 检查 1,325 个身份，遍历全部 3,865 条贡献 |
| 未确认身份 | 27 个身份全部无画像证据；18 条未确认作者贡献均被排除 |
| 仅有抽样元数据 | 192 个身份全部无画像证据 |
| 缺失代码 | 150 条均无生成证据，且 dataset_boundary_flags 保留原始缺失原因 |
| 缺失原因 | `code_size_limit` 122 条，`file_deleted_at_commit` 28 条 |
| 实际主要排除原因 | 身份未确认 18 条、大小限制 121 条、文件删除 28 条 |
| 实际直接证据 | 8,138 条，均来自 verified 身份和 available 代码版本 |
| 弱兴趣 | 全体身份弱兴趣证据数均为 0，contribution 行为未转为 star/fork 兴趣 |
| 来源限制 | 全体输出保留 manifest 的 not_delivered 项，包括 star、fork、owned_repository_interest、complete_repository_history、CODER_inference |
| fixture 与改名 | 专项测试验证 fixture 必须显式允许、fixture 标记与改名别名保留 |
| 时间与预测边界 | 专项测试覆盖未来贡献排除、观察时间及 outcome-only/任务链接不作为开发者证据 |
| 011 导入 | 独立新建库，chunk-size 500 导入后以 777 重试，均 completed 25255/25255，实体数量一致、外键错误 0 |
| 011 读取 | 真实开发者样本的数据库适配结果与直接 records 适配结果完全一致 |

两次导入 batch ID 均为 `44392bb9232090a5980652c7d54e691d8e401bd56a500a7194ec33e5b8e2d5d0`。

注意：一条大小限制记录同时身份未确认，因此其主 exclusion_reason 为 `author_identity_not_confirmed`，而大小限制仍在 dataset_boundary_flags 中。故主要排除原因只有 121 条 code_size_limit，与 122 条实际缺失原因并不矛盾。

8,138 是规则生成的证据行数，单条贡献可能生成多个特征证据；不是开发者人数、贡献人数或模型评估样本数。当前规则解释文件路径和归属，不代表分析代码语义或估计精确能力概率。

## P2：来源投影、作者时间与身份核验上下文未随证据传递

位置：`src/oss_mentor/profiling/dataset_evidence.py` 的 `_normalize_contributions` 和 `_direct_profile_evidence`。

输入中有 A 的 `source_location`（本地来源投影）、`author_at`（作者时间）及开发者身份核验来源；适配器只选择部分字段，后续 ProfileEvidence.details 无法恢复这些上下文。全量 8,138 条直接证据均未包含 `source_location`、`author_at`、`identity_status` 或 `identity_evidence_location`。

目前仍保留 dataset_version、repository/file/version/commit key、GitHub evidence_location、event_at 和 observed_at，因此可以回查原批次；这里的问题是输出证据本身没有完整表达来源边界。A 明确交付的是经整理的来源投影，完整原始 API 归档留在 A 本地；event_at 是 committer 时间，也不能当作 author_at。

请 B 在 details 或关联的可持久化审计上下文中保留：

1. 贡献 source_location 及代码 source_location/commit_source_location，并明确其为来源投影，不是原始 API envelope。
2. author_at 与 event_at 的时间语义，保留 observed_at。
3. 身份状态和全部可用核验来源；verified 表示账号关联核验，可包含机器人，不表示实名或能力认证。
4. 不应仅因存在代码 content_hash 就暗示执行过代码语义分析；可以保留内容引用/哈希用于追溯。

补真实 v0.2 来源字段回归测试后，A 再复核新 Head。当前已有不丢失的字段不构成撤销或重采 A 数据的理由。

## 复现命令和产物

在仓库根目录用 PowerShell 执行（隔离验收文件已准备在 tmp，未纳入正式交付）：

```powershell
$env:TEMP=(Resolve-Path tmp/b26_a_acceptance/temp).Path
$env:TMP=$env:TEMP
$env:PYTHONPATH=(Resolve-Path tmp/b26_a_acceptance/src).Path
python -m unittest discover -s tmp/b26_a_acceptance/tests/midterm_b -v
python datasets/b_real_contributions_v0.2/verify.py
python datasets/b_real_contributions_v0.2/import_011.py --database tmp/b26_a_acceptance/real_011.sqlite3 --chunk-size 500
python datasets/b_real_contributions_v0.2/import_011.py --database tmp/b26_a_acceptance/real_011.sqlite3 --chunk-size 777
python tmp/b26_a_acceptance/check_real.py
```

全量断言脚本：`tmp/b26_a_acceptance/check_real.py`；机器结果：`tmp/b26_a_acceptance/real_results.json`；生成库：`tmp/b26_a_acceptance/real_011.sqlite3`。数据库不应提交 Git。

本次仅生成本地验收记录，未向 GitHub 发送评论、提交 review、推送提交或合并 PR。
