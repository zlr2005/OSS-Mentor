# A → B：真实贡献与代码数据交付 v0.1

后续接入优化见 [v0.2 交付说明](b_real_data_handoff_v0.2_2026-10-28.md)。推荐提交 v0.2；本文件与 v0.1 数据保留作版本历史。

交付目录：`datasets/b_real_contributions_v0.1/`，数据版本 `b-real-contributions-v0.1-20261028`，schema `b-profile-evidence-v1`，fixture_only=false。本目录可独立读取和校验，不需要 A 本机数据库、凭据或采集源码。

2026-10-28 已完成导出并运行目录内独立校验器，结果 delivery_verified。共 10,478 个文件（含 manifest 和 manifest.sha256），128,337,817 字节，未压缩。manifest SHA-256 为 `98c49bc3ea0d1c2bcffb0239f306dfda816eb40b287a115826979f2a81d5ff03`。

## 数据内容

| 产物 | 数量 | 入口 |
|---|---:|---|
| 候选仓库 | 309 | tables/repositories.jsonl |
| 文件贡献记录 | 3,865 | tables/contributions.jsonl |
| 文件版本 | 3,865 | tables/code_documents.jsonl |
| 可用代码版本 | 3,715 | CodeDocument.content_location → content/code/ |
| 缺失代码版本 | 150 | 同表保留 missing 和 exclusion_reason |
| 逻辑文件 | 3,180 | tables/files.jsonl |
| 开发者规范 key | 1,325 | tables/developers.jsonl |
| 贡献行为 | 2,218 | tables/project_behaviors.jsonl，仅 contribution |
| 文件改名 | 11 | tables/file_aliases.jsonl |

按 developer_key 合并后有 1,325 个身份记录；其中 1,133 个至少有一条选中的文件贡献。其他身份可能来自抽样 commit 中未选中文件的作者，不能仅凭其出现在 developers 表就推断技术画像。未确认作者不按姓名/邮箱猜测合并。身份账号关联 verified=1,298、unconfirmed=27；贡献作者关联 verified=3,847、unconfirmed=18。verified 不等于真人认证或能力认证，可能包含机器人。

代码缺失率 150/3865（约 3.88%）：122 个超过本批内容大小限制，28 个在相应 commit 已被删除。缺失不代表文件贡献不存在；不要自动用当前主分支代码替代。贡献作者关联确认率为 3847/3865（约 99.53%）。质量统计在 quality.json 和 manifest 中。

每库有界采样：一般最近 10 个 commit、每次最多 2 个识别的源文件；修复批次最多扫描 100 次提交后选 10 次具备证据的源代码变更。全部沿用截止 `2026-10-26T00:00:00Z`。每库实际时间范围、原快照版本和已知局限见 tables/source_snapshots.jsonl；排除提交见 tables/excluded_commits.jsonl。覆盖所有候选仓库不等于覆盖全部历史或全部目标用户。

## B 怎么用

```powershell
python datasets/b_real_contributions_v0.1/verify.py
```

预期 status=delivery_verified。只需 Python 3.11+ 标准库，不依赖网络、Token 或数据库。读取示例、完整字段说明和证据边界见目录内 README.md。

从 contributions 的 developer_key 构建画像输入；按 file_version_key 连接 code_documents，按 content_location 读取代码；source_location 指向证据摘要，evidence_location 保留原 GitHub commit URL。仓库 repository_key 可经 source_repository_id/full_name 与 Issue 库仓库身份对齐。

event_at 为 committer 时间；author_at 和 observed_at 分别保留作者时间及来源观察时间。历史画像必须按评估时点过滤，不能把观察时间当行为时间，也不能把采样缺失当负例。

## 来源与校验

全部 309 个源快照在导出时重新调用原来源校验器验证，包括 commit、作者账号关联、代码 blob 和历史路径证据。交付包含固定代码内容及实际许可文本索引。

evidence/ 是白名单来源投影，明确标记 projection_not_raw，记录 API URL、观察时间、原始归档/响应哈希、账号 ID 和 commit/blob 信息；不复制原始 Git 作者邮箱或提交正文。原始完整 API 归档留在 A 本地，需要完整复核时按原快照版本和原 manifest 哈希定位。许可原文和代码自身的字节保持不变。

独立 verify.py 验证 manifest 和文件哈希、数量、关联引用、代码内容、贡献对应版本及时间、作者关联状态。它不把白名单投影宣称为完整原始 API 重放。7 项新增回归测试覆盖篡改代码、错误 commit/时间、缺失原因和身份冲突等情况。

本 normalized schema 不直接兼容旧 fixture 的 DatasetStore.import_snapshot；B 可直接读取 JSONL。旧 fixture 继续用于合成异常场景验收，不能混入真实画像样本。

## B 的需求还差什么

本批完整整理了已经取得的真实贡献与代码数据，但并不完成 B 所有数据需求：star、fork、拥有仓库等真实弱兴趣行为尚未采集。tables/weak_interest_behaviors.jsonl 是明确的空表；project_behaviors 中的 contribution 不能当作 star/fork。该事实也写入 manifest.not_delivered。

许可证据保留原文和声明口径，redistribution_status=not_assessed，不把当前 SPDX 声明当作历史许可证或发布授权结论。

## 手动提交 GitHub

本轮没有执行 git add、commit 或 push，也没有生成压缩包。当前分支为 zlr/midterm-a-data。以下只纳入独立数据目录和本说明，不包含本机数据库、原始 API 归档或其他工作区修改：

```powershell
git add -- datasets/b_real_contributions_v0.1 docs/b_real_data_handoff_2026-10-28.md
git diff --cached --stat
git diff --cached --check
git commit -m "Deliver real contribution and code evidence for B"
git push -u origin zlr/midterm-a-data
git rev-parse HEAD
```

推送成功后把最后的 commit SHA 和本说明路径发给 B。B 只需该提交中的交付目录即可读取及校验数据。
