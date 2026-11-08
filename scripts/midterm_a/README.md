# A：离线样本、数据库导入与后续顺序

当前新手采集入口已切换为按 action 对齐排序键的计划（2026-10-22），见 `ACTION_INDEXED_NEWCOMER_WORKING.md`。
`run_newcomer_teacher_collection.ps1` 默认新计划；指定 ResumeDirectory 时读取该目录保存的原计划，避免跨版本断点错配。

全部历史新手相关任务统一筛选（2026-10-21）：40 个本地来源去重 1638 条，覆盖旧 75 条和最新 83 条；
111 条通过当前可参与性检查（其中 4 条优先查看），1312 条排除、213 条待核实、2 条读取失败。
结果未接网页，难度非人工真值；详见 `ALL_NEWCOMERS_SCREENING_WORKING.md`。

当前采集重点（2026-10-20）：优先发现可供新手推荐的任务。使用
`run_newcomer_teacher_collection.ps1` 和独立标签查询计划，明确新手信号优先，
`help wanted` / 欢迎贡献类单独复核难度。查询后仍须检查当前可参与性。
计划已离线准备，真实老师库采集尚待本机输入凭据；详见 `NEWCOMER_DISCOVERY_WORKING.md`。

老师库约束（用户于 2026-10-16 明确要求）：**仅查询并导出到本地，严禁修改远端数据、表结构或配置**。
后续接入必须遵守 `TEACHER_DATABASE_READONLY.md`。目前只读连接已验证，取得 events 的 112 个字段及 3 条探测样本。

当前推荐库存进展（2026-10-15）：已用 IssuePilot 的仓库种子重新采集 GitHub，
最新 `live_issuepilot_v0.3` 有 110 条通过当前状态检查的候选，覆盖 14 个仓库，
其中 20 条有新手/求助相关标签。详见 `LIVE_ISSUES_WORKING.md`。
工作库未冻结、未接入网页；历史论文数据和当前任务库存分开保存。

最新论文数据进展（2026-10-15）：已实际下载、校验并导入 GFI-Bot 0.0.3 的完整
`resolved_issue` 成员，工作库含 159,919 个历史 Issue、99 个仓库、18,864 个解决者来源身份。
详见 `GFIBOT_WORKING.md`。数据保持 working / frozen=false；任务特征成员未导入，
不代表 CODER 原版数据恢复、完整训练数据就绪或线上库存扩充完成。A 专属测试现为 72 项通过。

状态：离线 fixture、数据库导入/去重/失败恢复和标准化导出已验证；已接入第一批 pytest GitHub 历史补采数据。
CODER 原始附件未取得，完整实验数据/模型尚未完成。前一阶段 A 专属测试 50 个通过；相关既有测试 28 个通过。

最新进展：已扩充到 100 个 commit 的工作数据。按用户要求，**本批未冻结、未正式交付**，
数据位于 `data/midterm/working/`，详见同目录 `WORKING_DATA.md`。
此前章节保留前几步小样本记录，不代表本批已进入实验冻结。

后续工作：已完成第一轮 Issue→PR/commit→文件关联，仍未冻结，
详见 `ISSUE_LINKS_WORKING.md`。当前结果为 4 个抽查 commit、4 个关联 PR、3 个明确解决 Issue、5 条文件关联证据。
本轮 A 专属 64 个测试通过；报告与关联模块的 14 个测试另已通过回归。

日期：2026-10-15（Asia/Shanghai）。本地起点提交：
`aff30cbd487ad25aac7525a849e5d8ae1958e05f`。
分支：`zlr/midterm-a-data`。团队共同 base_commit 尚待团队确认。

开始时已有网页、脚本、测试及未跟踪文件；本次均保留，未提交、暂存或清理。
创建分支会保留这些已有改动，并不代表它们全部属于 A。

## 已完成什么

- 复用现有采集能力前，运行 24 个相关既有测试并通过。
- 新增 `oss_mentor.datasets.snapshot.validate_snapshot(Path)` 离线读取入口。
- 新增可重复生成的小型合成快照及 SHA-256 manifest。
- 新增 9 个测试，覆盖校验和、路径边界、引用、commit 对齐、事后证据、未知时间及失效任务。
- 第二步新增 16 个测试，覆盖存储、失败恢复、迁移升级和命令行（A 专属测试共 25 个）。
- 实现精确重复关系去重、冲突拒绝、不可变版本、分批事务检查点与自包含导出。
- 新增 `db/sqlite/011_midterm_datasets.sql`；现有 D 的迁移执行器已按文件名自动发现它，无需修改公共 facade。
- 没有修改公共契约、API、前端、数据库连接或既有采集业务。

## 运行命令

在仓库根目录用 PowerShell 执行：

```powershell
$env:PYTHONPATH = "src"
python scripts/midterm_a/validate_snapshot.py fixtures/midterm/a/snapshot_v0.1
```

预期退出码为 0，返回 `status: fixture_verified` 和 `fixture_only: true`。
非法输入退出码为 1。此状态仅说明本草案读取检查通过，不代表真实数据或团队集成通过。

重建相同 fixture：

```powershell
python scripts/midterm_a/build_fixture.py
```

运行本次相关测试（沙箱内默认临时目录可能没有权限，因此改用工作区目录）：

```powershell
New-Item -ItemType Directory -Force -Path tmp/midterm_a_tests | Out-Null
$env:TEMP = (Resolve-Path tmp/midterm_a_tests).Path
$env:TMP = $env:TEMP
$env:PYTHONPATH = "src"
python -m unittest discover -s tests/midterm_a -v
python -m unittest tests.test_github_client tests.test_raw_store tests.test_candidate_sync tests.test_candidate_refresh_report tests.test_sqlite_store tests.test_migration -v
python scripts/ci_sqlite_migration_check.py
```

这里的 TEMP/TMP 设置只对当前 PowerShell 进程和子进程有效。

## 第二步：导入、检查状态、导出

本地验收库为 `data/midterm/validation/a/fixture_pipeline.sqlite3`，只放合成测试数据。
保留 D 计划使用的 `data/midterm/db/oss_mentor_midterm.sqlite3` 给真实运行配置。
脚本拒绝使用既有 `data/oss_mentor.sqlite3`。无需网络或新增第三方依赖。

```powershell
$env:PYTHONPATH = "src"
python scripts/midterm_a/dataset_pipeline.py --database data/midterm/validation/a/fixture_pipeline.sqlite3 import fixtures/midterm/a/snapshot_v0.1 --chunk-size 2
python scripts/midterm_a/dataset_pipeline.py --database data/midterm/validation/a/fixture_pipeline.sqlite3 status synthetic-a-v0.1
```

再次运行 import 是幂等的；失败后重新运行相同命令会继续同一批次，chunk-size 可调整。
预期完成状态为 `completed`，检查点为 19/19：4 个数据文件，加 15 条实体/关系记录。
`entity_counts` 才是各类数据数量，不能把检查点当作交互条数。

导出到一个尚不存在的目录（下面路径在本地已实际生成，再运行时换新目录）：

```powershell
python scripts/midterm_a/dataset_pipeline.py --database data/midterm/validation/a/fixture_pipeline.sqlite3 export synthetic-a-v0.1 data/midterm/validation/a/export_fixture_v0.1
python scripts/midterm_a/validate_snapshot.py data/midterm/validation/a/export_fixture_v0.1
```

导出版本为 `synthetic-a-v0.1-normalized-3e75bcd3f5a4`，由标准化内容生成。
导出 manifest 记录 parent_dataset_version 和 normalization_version，并更新数量/校验和。
导出包包含所有内容，无需依赖原始路径；已有目标不覆盖，未完成批次不导出。

业务接入入口为 `DatasetStore(connection)`：调用方提供由 D 初始化、开启外键、无活动事务的连接。
支持 `import_snapshot(Path, chunk_size=...)`、`status(dataset_version)`、
`export_snapshot(dataset_version, Path)`。该模块拥有每批事务的 commit/rollback，
不可嵌套在调用方活动事务内；不关闭调用方连接。关闭连接由调用方负责。

新增表全部带 `midterm_dataset_` 前缀，以 dataset_version 隔离；外键删除行为为 RESTRICT。
来源版本、许可和原始文件校验和保存在不可变 manifest 中；每种实体/关系独立表保留 JSON 字段。
未完成数据可能已经提交到表中，消费者必须先确认导入状态 completed。
错误记录只保存异常类型，保留已提交检查点。崩溃留下 running 的批次也可重跑恢复。

去重仅处理已经提供规范 key 的数据。完全相同关系合并，同身份不同内容拒绝，要求来源适配器
先处理冲突；不靠姓名/邮箱推测合并身份。更改内容必须更改 dataset_version。
当前整包验证/归一化在内存中完成，适合小包闭环；大规模导入前需增加流式分片和来源映射。
`load_snapshot` 区分 `midterm-a-draft-v1`（fixture_only=true）与
`midterm-a-source-v1`（fixture_only=false，需要 GitHub 原始来源证据）。
真实来源额外检查 commit/作者/目录树/blob/历史许可的一致性，不能把真实数据标成 fixture 绕过来源校验。

实际验证：A 专属 25 个测试、相关既有 28 个测试，以及空库全部 12 个迁移检查。
恢复测试通过 SQLite 触发器模拟中途失败，关闭并重新打开连接后继续，验证了批内回滚和批间恢复。
本地另已实际执行相同小包两次导入、导出和导出包校验，批次/计数保持一致。
验证汇总位于 `data/midterm/validation/a/pipeline_v0.1.json`，只包含合成数据统计。

## 样本结构和限制

样本目录：`fixtures/midterm/a/snapshot_v0.1/`。

- `manifest.json`：版本、来源、时间覆盖、数量、许可、缺口及每个数据文件的校验和。
- `records.json`：仓库、开发者、逻辑文件、文件版本、贡献、项目行为、Issue、文件关联及改名证据。
- `content/`：合成代码和 Issue 文本；使用 `.txt` 避免把示例代码当作项目模块。

包含 1 个仓库、2 个开发者、2 个逻辑文件、2 个文件版本、2 条贡献、1 条 star 行为、
2 个 Issue、2 条 Issue—文件关联、1 条改名记录。

所有 ID、commit、身份确认、可领取状态和 `example.invalid` 证据地址均为虚构。
`verified` 只模拟身份关联状态，不表示真实 GitHub 认证。未知作者保持独立 key；
committer 与贡献作者分别记录。star 的发生时间为 null，观察时间单独保存。
缺失代码保留文件版本记录和原因，不生成假内容。

`resolution_pr` 关联必须标记 `outcome_only`，且 available_at 不早于解决时间；
训练时还需要 C 按预测时点过滤所有输入。本读取器不负责数据划分或模型训练。

字段是 `midterm-a-draft-v1` 本地 JSON 草案；仅供 S0 前联调，未定义新的公共类型。
`developers`、`files`、`file_aliases` 及部分字段是 A 维护身份、逻辑文件和改名映射的草案扩展，
需要 D 确认。校验器聚焦样本完整性和关键关系，不是公共契约的完整 schema 验证器。
真实来源适配目前只支持小规模公开 MIT 仓库历史补采，其余来源仍待增加。

## 第三步：CODER 资源核查与第一批真实数据

核查结果详见 `CODER_RESOURCE_AUDIT.md` 和
`config/midterm_datasets/coder_resource_audit_v0.1.json`。
CODER 官方仓库固定 commit 为 `d5f88cf76520e0ab0863f4f01f8f7dd37521d589`。
原始附件链接本次返回 404；本批是独立 GitHub 补采，不能叫作原版实验数据恢复。

实际产物：

| 产物 | 位置 |
|---|---|
| GitHub 原始响应与请求缓存 | `data/midterm/sources/github/pytest_history_v0.1/` |
| 本批冻结输入 | `data/midterm/normalized/pytest_history_v0.2/` |
| 独立运行库（仅导入本批真实数据） | `data/midterm/db/oss_mentor_midterm.sqlite3` |
| 数据库最终导出包 | `data/midterm/normalized/pytest_history_export_v0.2/` |
| 本批质量与交接记录 | `data/midterm/validation/a/github_history_v0.1.json` |

输入数据版本：`github-history-e61ee99c5271fa484243`。
最终导出数据版本：`github-history-e61ee99c5271fa484243-normalized-112d74f68445`。
schema 为 `midterm-a-source-v1`；fixture_only=false。
本批 1 个仓库、3 个 commit 和对应完整目录树、2 份历史 Python 代码、2 条文件贡献。
记录中有 2 个可关联 GitHub 的作者，其中 1 人在选中的 Python 文件中有贡献。
另外两次 commit 没有本次选中的 Python 文件，不把它们补造成用户—文件边。
没有 Issue、Issue—文件关联、历史 star 或实验划分，不能直接拿来宣称训练/推荐完成。

验证冻结输入、查询导入状态：

```powershell
$env:PYTHONPATH = "src"
python scripts/midterm_a/validate_snapshot.py data/midterm/normalized/pytest_history_v0.2
python scripts/midterm_a/dataset_pipeline.py --database data/midterm/db/oss_mentor_midterm.sqlite3 status github-history-e61ee99c5271fa484243
```

预期分别为 source_verified（fixture_only=false）和 completed（检查点 30/30）。
30 包含 20 个数据文件与 10 条实体/关系，不能当作贡献数量。
source_verified 表示本地来源证据及 hash/关联校验通过；不是任意第三方包的真实性认证。
网页尚未配置为消费此库，D 的运行接入及 B/C 独立验收仍待完成。

重建本批（先选择不存在的输出目录；没有 --allow-network 时只读缓存）：

```powershell
python scripts/midterm_a/collect_github_history.py --repo pytest-dev/pytest --until 2022-02-01T00:00:00Z --commits 3 --files-per-commit 2 --source-dir data/midterm/sources/github/pytest_history_v0.1 --output data/midterm/normalized/pytest_history_rebuilt
```

缓存不在本机时，在相同命令末尾增加 `--allow-network` 重新采集，网络结果可能产生新版本。
公共数据可匿名获取；若设置 GITHUB_TOKEN，使用当前会话环境变量，不写到文件。
网络请求有超时和有限重试；成功响应逐条缓存，限流/部分失败后重跑会复用已成功部分。
原始响应可能含公开作者邮箱，保留在 Git 忽略的 data 下；规范记录不含作者姓名或邮箱。
CodeBERT 编码与分段仍归 C，本模块只交付代码 bytes 和文件版本。

本批历史时间为 2022-01-31T14:37:05Z 至 2022-01-31T22:20:16Z。
event_at/snapshot_at 使用该 commit 的 committer 时间，作者归属使用 GitHub author 数字 ID，
author_at 独立保留。当前仓库元数据和当前身份关联的观察时间另存，不假装成历史已知特征。
逻辑文件 key 只在有显式改名证据时关联；跨更大批次的持久别名目录仍待扩展。

最终导出采用 midterm-a-normalize-v2，版本校验覆盖来源/许可/格式等 manifest 元数据及所有资产，
并正确标注 gzip-json 文件格式。此前的 normalize-v1 导出包保留作为阶段记录，交接使用上表 v0.2。

## 下一步按这个顺序继续

1. 请 D 确认 S0 字段、存储连接接口和 011 迁移编号；请 C 确认最小训练输入。
2. 请 B/C 核验本批真实小包中的代码/作者/时间口径，按 C 的训练需求选择后续仓库。
3. 扩充真实提交与用户—文件关系，增加持久来源/别名映射、流式分片和规模验证。
4. 请 D 审核 011 表结构和存储事务约定，确认 API 层的消费接入方式。
5. 请 B/C 用独立环境读取真实小包，再扩大历史数据和当前候选。

当前未完成：跨批次持久来源/别名映射、CODER 原始附件与其他论文真实适配、大规模 GitHub 历史代码补采、
当前库存扩充、老师库 mock/真实连接、真实规模报告，以及团队接收方验收。

## 给 D/C 的消息草稿（由本人发送）

给 D：

> 我开始做 A，已准备 synthetic-a-v0.1 小样本与离线校验命令，状态为 fixture_verified，
> 尚未集成。请确认 S0 字段、数据存储连接接口、011 迁移编号及新增测试 discovery。
> A 的 JSON 暂按 midterm-a-draft-v1 开发，不复制公共类型。
> 验收命令：`python scripts/midterm_a/validate_snapshot.py fixtures/midterm/a/snapshot_v0.1`，
> 先设 PYTHONPATH=src。当前团队 base_commit 也请共同确认。
> 第二步已补 011 迁移和 DatasetStore(connection)，存储事务由模块分批管理；现有迁移自动发现可用。
> 请审核连接/事务约定和 011 schema。小包导入/恢复/导出及旧库升级已验证，均限合成数据。
> 因新增迁移，A 的既有候选刷新测试中迁移期望列表补了 011；公共测试/CI 文件未修改。

给 C：

> A 已准备小样本，含贡献作者—commit—逻辑文件—代码版本、未知身份、缺代码、改名、
> 未知时间 star，以及 Issue 事前路径提示/事后解决关联。请确认最小 CODER 输入是否缺字段，
> 并确定首批仓库和历史时间要求。此包纯合成，仅用于联调，不用于真实训练结论。

给 B：

> A 的小样本已包含作者/提交者区分、未确认身份和代码版本证据入口。
> 请先确认 records.json 能否支持你的证据消费流程，真实采集接入后再核验画像证据。
