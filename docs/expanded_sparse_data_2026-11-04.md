# 61 位低历史用户：扩大检索及历史上下文补充

最新结果见 [2026-11-05 续作说明](sparse_data_completion_2026-11-05.md)：392 条补充记录中 384 条有代码和候选对照，原 61 人中 57 人达到数量目标，仍缺 4 人。下文保留前轮阶段统计。

本轮按用户指示扩大检索，不覆盖 v6 数据或改变现有测试时间边界。所有 GitHub 操作只读，未发送消息或发布数据。

## 前轮交接入口

汇总包为 data/midterm/working/sparse_expansion_20261104_handoff。

- outcome_index.jsonl：391 条新增、按 outcome ID 去重的结果；合并时去掉 12 条来源重叠记录。同 ID 有多个画像时点时优先保留 PR 前版本，不重复计数。
- comparison_index.jsonl：211 条同时具有截止前贡献及可用对照的记录；已按所有已知正例再次过滤对照。
- account_coverage.jsonl：原 61 人逐人的原始结果数、新增结果数、不同 Issue 数和画像时点。
- remaining_gaps.jsonl：仍不足 3 个不同 Issue 的 5 人。
- source_manifest.json / manifest.json：各独立证据包根目录及清单哈希。

在补充结果准入后，56 人达到至少 3 个不同 Issue，5 人仍不足。该数量统计不代表 391 条都已有完整排序输入：目前 211 条同时具备代码和对照，画像仍需重建、审核和冻结。正式 v6 统计保持原样。

最终逐条重放 391 条记录的数字身份、实际关闭事件、历史正文和时间条件，核验 211 条贡献/候选联接以及汇总包和来源清单哈希，全部通过。所有输出仍为 training_ready=false。

## 2024—2025 年的扩大检索

取消 linked:issue 搜索限制，按 7 个季度检索全部已合并 PR；对 GitHub 1,000 条搜索上限自动按日期拆分，并读取末页。仅接受数字身份一致的结果。61 人共完成 441 个分页/分片请求，取得 8,182 条去重 PR 索引，涉及 543 个仓库，无未读完的叶子查询。

advanced_adapter_expanded_20261104_v1 包内完整核查首批 1,000 条未处理 PR，未新增符合当前季度/90 天/历史正文/实际关闭事件要求的 outcome。空结果包完整验证通过，保留原始请求、缓存、索引和排除表。

expanded_closing_link_triage_20261104_v1 检查另外 5,567 条符合选样条件、尚未完整核查的 PR，全部返回 closingIssuesReferences.totalCount=0，失败批次为 0。该证据说明当前 API 未提供关闭关联，不代表这些 PR 没有贡献，也不把它们记作负反馈。

## 历史语言、技术栈及环境证据

historical_repository_context_20261104_v1 针对这 61 人的训练结果仓库/截止点，从已有贡献证据选取截止前的固定 commit，读取 package.json、tsconfig.json、pyproject.toml、setup.py、requirements.txt、Cargo.toml、go.mod、pom.xml、build.gradle、README.md 和 CONTRIBUTING.md。

- 请求覆盖 82 个仓库截止点，76 个有可用的截止前 commit；另外 6 个保留缺少 commit 原因。
- 成功收集 203 份固定版本文档，读取失败为 0。
- 73 个仓库时间点有 manifest 生态证据，57 个有安装/依赖环境原文。
- 每份原文以 Git blob SHA-1 和内容 SHA-256 校验；commit oid、作者时间和提交时间均检查。

这些 commit 是已核实的历史采样状态，不能声称是截止时仓库最新状态。manifest_ecosystems 描述仓库生态；package_dependencies 描述当时 package.json 中的直接声明，不自动等于目标 Issue 的必需技术栈。

candidate_context_enrichment_20261104_v1 按 repository、as_of 与既有历史候选特征联接，共 521 个候选；500 个有 manifest 生态配置证据，389 个有安装环境原文。保留原有带直接证据的规则难度估计：代码 5 个、环境 28 个。它们仍是 rule_estimated，不是人工真实评分。

primary_language、code_difficulty、setup_difficulty 的事实字段继续保持未知；不能仅凭文件配置或安装说明伪造主语言与难度真值。语言采样、仓库生态、Issue 必需语言应分别使用。

## 2022—2023 年及更早结果

earlier_sparse_outcomes_2022_2023_v1 对原 61 人检索 2022/2023 年关联 PR，选择 1,041 条，核实 207 条完成结果、覆盖 35 人。其中 696 条排除记录是 Issue 在原季度截止点之后创建；这说明季度画像选样约束会排除不少真实结果，不能解释成用户没有真实贡献。

一个不可访问仓库使六条 PR 的联合查询失败，已拆成单条读取；五条邻接记录成功恢复，只有一条不可访问 PR 保留在 read_exclusions.jsonl。所有 207 条已接受结果重新核验身份、实际关闭事件、历史正文和 90 天窗口，并核对文件哈希。

earlier_sparse_history_2022_2023_v1 完成 78 个窗口、90 条窗口文件贡献、358 个候选快照，92 条 outcome 同时有贡献和对照。失败窗口重试后为 0。

针对后续仍不足的 7 位用户，按账号真实创建年份检索 2021 年及更早历史，earlier_sparse_outcomes_2008_2021_v1 核查 15 条 PR，取得 14 条完成结果，覆盖 2 人；不可访问记录为 0。

## PR 发生前时点的独立补充集

《数据扩充.md》要求截止前证据和多个画像时间点，并没有要求只能季度采样。本轮独立建立 preinteraction_sparse_supplement_20261104_v1：截止点为 PR 创建前一秒，Issue 必须已经存在，实际关闭与合并在其后 90 天内，标签早于原训练结束时间。未来编辑的正文仍拒收，未把目标 PR 的代码用作发生前画像。

该包从已有 PR 证据重放中挑选 182 条新增、不同 Issue 的真实结果，覆盖 60 人，其中 156 条来自 2024/2025 年。选样优先原训练年份，每人补至最多 5 条；已排除原 v6 的 outcome ID 和该人的已知正例 Issue。按原 v6 结果加本包计算，54 人达到至少 3 条，7 人仍不足。

preinteraction_sparse_history_20261104_v1 完成 158 个窗口、194 条窗口文件贡献、743 个历史候选快照，125 条 outcome 有截止前代码和同仓库对照，读取失败为 0。每位最多 3 个时间点、每窗口最多 1 个 commit，是有界证据而非完整 GitHub 历史。

此处改变了采样时点策略，不能直接替换原季度实验或把新旧指标混称为同一版本。B 需在新时点重建并冻结画像，C 需审计时间契约及候选采样。所有补充包 training_ready=false。

## 最终仍不足的 5 位

新增更早历史使 layday 和 wjandrea 达到数量目标，其余仍为 auroratwinkle、blickly、dmihai、reggi、sdelicata。remaining_five_final_probe_20261104_v1 另查这五人中此前未处理的 15 条 PR，没有新增合格结果。

已核查证据中，主要缺口为没有 closingIssuesReferences，或没有实际由 PR 关闭 Issue 的事件。没有用手工关闭、普通提交或未证实的关联替代当前结果口径。剩余缺口也没有用 2026 年验证/测试标签回填训练。

## 平台真实曝光反馈

platform_feedback_audit_20261104_v1 保存三个本地应用数据库的只读汇总审计，未导出账号或自由文本：

- oss_mentor.sqlite3：7 个推荐批次、70 个推荐条目、1 条反馈事件。
- oss_mentor_web.sqlite3 和 midterm 数据库：上述表均为 0 条。
- 未发现能证明实际前端展示的事件表/展示时间字段。推荐条目不是曝光。
- 现有反馈没有 run_id、拒绝原因或放弃原因，真实用户来源未核实，不计入真实反馈训练集。

因此没有把曝光目标 2,000～5,000、明确反馈 500+、尝试/完成 100～200 宣称补齐。它们必须由真实平台使用产生，历史 GitHub 检索不能替代。验收/fixture 数据库没有计入真实交互统计。

## 后续使用

上下文 enrichment 是待审核的独立旁表，不改变 v6 标签或画像；B/C 可按仓库、截止点、Issue key、正文哈希和固定 commit 联接。不同时间范围的数据须分别报告。

扩展搜索的季度连续性/饱和拆分、早期年份显式隔离、PR 前截止点边界、固定文档 blob 损坏和未来作者时间拒收等 11 项相关测试通过。上下文包及旁表提供文件哈希清单。
