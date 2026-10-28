# A → B / C 最终数据交付清单

提交目标分支：`zlr/midterm-a-data`。本轮只提交下列最终目录、此说明及共享测试修复；不提交旧数据版本、采集缓存、过程日志、业务数据库、用户/会话/反馈、原始 API 归档或凭据。已有 Git 历史中的旧 fixture 不在本轮删除，也不需要重新添加。

| 对象 | 最终目录 | 数据版本 | 用途 |
|---|---|---|---|
| B | datasets/b_real_contributions_v0.2/ | b-real-contributions-v0.2-20261028 | 真实贡献、固定代码版本、来源投影、011 接入 |
| C | datasets/c_issue_candidates_v1/ | c-issue-candidates-v1-20261028 | 完整候选库的冻结任务/特征/技能要求样本 |

两个数据目录约 315 MB，未压缩；最大单文件约 10.5 MB。目录内 .gitattributes 固定原始字节，避免 Windows 换行转换破坏校验和。B/C 均有独立 Python 标准库工具，不依赖本机数据库或工作区未提交源码。

## B 的数据与验收

309 仓库、3,865 条文件贡献、3,865 个代码版本，其中 3,715 个可用、150 个保留缺失原因。1,325 个规范身份 key，其中 1,133 个有选中的文件贡献；2,218 条 project_behaviors 全部是 contribution。star/fork/拥有仓库等弱兴趣尚未交付，空表及 manifest.not_delivered 明确标记。它是覆盖每个仓库的有界样本，不是完整历史。

```powershell
python datasets/b_real_contributions_v0.2/verify.py
python datasets/b_real_contributions_v0.2/import_011.py --database data/midterm/b_real_v02.sqlite3 --chunk-size 500
python datasets/b_real_contributions_v0.2/import_011.py --database data/midterm/b_real_v02.sqlite3 --chunk-size 777
```

已验收 delivery_verified；两种 chunk-size 同一批次，completed 25255/25255，SQLite integrity_check=ok、外键错误为 0；12 项接入测试通过。失败恢复、版本不可变、无关表保留已测试。B 的 ProfileEvidence 实现未在本机，仍需 B 运行其侧回归。

reader.py 返回旧 snapshot 的九组 records，import_011.py 写入既有 011 表，方便 B 复用 payload_json 读取路径。normalized schema 保持 b-profile-evidence-v1，不伪装旧原始快照 schema，不能直接换回旧 DatasetStore.import_snapshot。原始 API 归档留在 A 本地，交付的是明确标记的白名单来源投影、原始哈希及来源 URL；代码和许可文本保留原字节。

manifest SHA-256：`790e73a1b050ae9055a187e8fa944bc483d1170a92670f3fc0b78a105de1d044`。

## C 的数据与验收

来源为完整业务候选库的候选相关三表，保留原字段与 ID：309 仓库、11,766 条 task_candidate、22,398 条 task_skill_requirement。网页库是其中一部分，本交付采用完整主库，不混合两库的内部 ID。用户、会话、画像和反馈不导出。

```powershell
python datasets/c_issue_candidates_v1/dataset.py
python datasets/c_issue_candidates_v1/dataset.py --database data/c_candidate_delivery.sqlite3
```

已验收 delivery_verified，并在新建 SQLite 上重建三表、索引，完整性与外键检查通过。JSONL 按每 500 行分片，保留类型、难度、技能要求、规则证据、警告、准入原因、核验时间和到期时间。

重要口径：这是冻结快照。截至导出时核验时间有效的记录为 0，不能把 stored eligible=6,922 当作当前可推荐数。线上使用前应刷新 GitHub 状态并执行有效期、状态、技能等准入检查；导出不会自动续期。特征/难度属于规则推断，不是人工真值。本包不是完整运行服务数据库，不能直接启动 API；没有 CODER 模型、权重、向量或全量 Issue—文件映射。

跨 B/C 对齐仓库请使用 GitHub 数字 ID 或规范 full_name，不直接把 C.repository_id 和 B.repository_key 等同。

manifest SHA-256：`43a36ec9c0e0d3a46d3de219306d268145ac0f83bd5aef5ce5818c0f209f6cb7`。

## 只提交最终版的 PowerShell 命令

在仓库根目录执行。先检查暂存区；如果已有其他暂存文件，先人工确认，避免带入此次交付。不要使用 git add . 或 git add -A。

```powershell
if ((git branch --show-current) -ne 'zlr/midterm-a-data') { throw '请先确认当前分支，应为 zlr/midterm-a-data' }
if (git diff --cached --name-only) { throw '暂存区已有文件，请先检查后再执行' }

$deliveryPaths = @(
  'datasets/b_real_contributions_v0.2'
  'datasets/c_issue_candidates_v1'
  'docs/final_B_C_data_handoff_2026-10-28.md'
  'tests/test_candidate_refresh_report.py'
)
git add -- $deliveryPaths
git diff --cached --shortstat
git diff --cached --check
git commit -m "Deliver final datasets for B and C"
git push -u origin zlr/midterm-a-data
git rev-parse HEAD
```

共享测试改为自动发现 db/sqlite/*.sql，不再硬编码迁移白名单。本次不会引入本地 012_candidate_lifecycle.sql 或 013_candidate_identity_lookup.sql；B 的 012 画像迁移仍需与 D 协调。

## 推送成功后发给 B

B 你好，真实贡献与代码数据最终交付版 v0.2 已推送到 `zlr/midterm-a-data`，commit 为 `<COMMIT_SHA>`。请使用 `datasets/b_real_contributions_v0.2/`，先看目录内 README。

本批覆盖 309 个候选仓库，含 3,865 条贡献及代码版本记录，其中 3,715 个代码版本可用，150 个保留缺失原因。已附 reader、011 导入工具、质量统计、来源投影、许可文本及完整校验清单。独立校验通过，两次不同 chunk-size 导入为同一批次，completed 25255/25255，12 项接入测试通过。

本版是 normalized 数据，通过 import_011.py 写入已有 011 表后，可复用你已完成的 SQLite/payload_json 适配；不要直接套旧 DatasetStore.import_snapshot。请再运行 B 侧 ProfileEvidence 回归。未确认身份、缺代码及观察时间均保留真实状态；star/fork 等弱兴趣尚未交付，contribution 行为没有冒充弱兴趣。共享迁移测试已改为自动发现文件，012 的编号仍请与 D 协调。该批为有界真实样本，不是完整贡献历史。

## 推送成功后发给 C

C 你好，最终候选数据快照已推送到 `zlr/midterm-a-data`，commit 为 `<COMMIT_SHA>`。请使用 `datasets/c_issue_candidates_v1/`，目录内 README 和 dataset.py 提供读取、校验及 SQLite 重建方式。

本批包含 309 个仓库、11,766 条候选和 22,398 条技能要求，保留任务特征、规则证据、准入原因、警告、核验与到期时间，已通过文件校验及独立 SQLite 重建检查；不含用户、会话、画像或反馈。它供离线特征/推荐算法联调，导出时核验时间均已过期，stored eligible 不代表当前可推荐数，线上使用前必须刷新状态和有效期。B 的数据可通过仓库 GitHub 数字 ID/full_name 对齐；本批不包含 CODER 模型权重、向量或全量 Issue—文件映射。
