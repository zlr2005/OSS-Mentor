# B/C 交接 · temporal_xy_2024_2026_v5

本轮按用户确认保留 2024—2026 年采集范围，使用原训练、验证和测试时间边界。v5 合并最新四个结果补充包和两批贡献加深包，状态为 working / training_ready=false。

## 规模与审计

合计 4,641 条核实 outcome、12,416 条去重文件贡献，来源冲突为 0。原采集器可联接样本表为 4,114 条；以下为按 v5 全量合并贡献重建的 X/Y 和排序覆盖。

| 切分 | 开发者截止点组 | outcome 标签 | 按组去重 Issue 标签 | 可排序 outcome | 同仓库比较项 |
|---|---:|---:|---:|---:|---:|
| train | 1553 | 3503 | 3491 | 3391 | 10754 |
| validation | 271 | 391 | 389 | 391 | 1165 |
| test | 227 | 477 | 477 | 430 | 1448 |

- X/Y 共覆盖 527 位开发者。训练期标签早于 2026-01-01 UTC，验证期标签早于 2026-07-01 UTC。
- 112 条训练标签、47 条测试标签没有可用对照，已从对应 ranking 文件排除。
- new_repository_test_ready.jsonl 包含 191 条可排序测试结果，覆盖 152 个未出现在 v5 训练 X/Y 中的仓库。此处训练仓库集合包含 747 个仓库；实际基础 checkpoint 映射仍需 C 复核。
- low_history_gaps.jsonl 保存原低历史组中仍不足 3 条训练 outcome 的 61 人及其真实时间点。针对他们的 200 条普通 PR 核查全部因无关闭 Issue 关联而排除。

## 本轮新增训练数据

advanced_adapter_targeted_20261104_v3 核查 600 条 PR，新增 116 条核实训练 outcome，其中 97 条有画像和候选对照。advanced_adapter_targeted_20261104_v4 核查 1,000 条 PR，新增 333 条核实训练 outcome，其中 282 条有画像和候选对照。两批共新增 449 条核实训练结果；这些结果主要补充此前未进入 v4 训练集的开发者。

## 读取入口

工作包位于 data/midterm/working/temporal_xy_2024_2026_v5，合并表位于 data/midterm/working/merged_ab_history_2024_2026_v5。

- train.jsonl、validation.jsonl、test.jsonl 按 github_numeric_id 和 cutoff_at 给出 X 三元组及 Y outcome。
- file_contributions.jsonl 保留多来源。source_manifest.json 的 source_roots 包含 ab、backfill、depth_1、depth_2 以及 outcome_1 至 outcome_4；按每条 sources 的 source 解析内容和证据路径。
- 合并表中的 verified_outcomes.jsonl 按 evidence_source 选择实际关闭证据根目录，evidence_sources 保留重复来源。
- historical_controls.jsonl 按 issue_key、as_of 联接，evidence_source 和 evidence_sources 指示历史文本证据来源。
- ranking_train.jsonl、ranking_validation.jsonl、ranking_test.jsonl 已按开发者剔除所有已知正例对照。仍需 C 按同一基础模型分数筛选困难对照，固定采样版本和候选集合哈希。
- Y 保留 outcome_id 便于追溯；同组多条 outcome 可能指向同一 Issue，训练相关性和排序指标按 Issue key 去重。
- profile_previews.jsonl 为各源的研究预览版本，不能替代基于 v5 X 重建的冻结画像。B 需合并全部贡献、严格按时点过滤、保留未知 mask 后生成新冻结版本。

## 历史结构化特征

目录为 data/midterm/working/historical_candidate_features_20261104_v2，按 issue_key、as_of、content_sha256 联接候选。

- 共 11,113 个历史快照，7,298 个有任务类型规则证据。
- 8,949 个有截止前采样文件语言证据，包含文件键与采样范围，供版本化代理分析使用。
- 197 个有直接历史文本依据的代码难度估计，716 个有环境难度估计，26 个有显式技术要求规则证据。
- 难度通过 difficulty_estimates 输出，状态为 rule_estimated，保留置信度、直接证据和冲突记录；只有中等以上置信度、存在直接标题或正文依据且无冲突时才输出值。
- 规则版本、源码 SHA-256、正文内容哈希均已固定。事实字段 primary_language、technology_stack、code_difficulty、setup_difficulty 继续保留 null；缺少直接证据的估计字段也保留未知。

## 边界与后续

核实 outcome 数量 4,641 与可排序 outcome 数量 4,212 是不同口径，正式训练仍依赖 B 冻结和 C 审核。270 条 outcome 因缺少截止前可用代码未进入 X/Y；候选缺失则按 audit.json 的切分统计排除。

2026 Q4 随访未结束，仍按原约定单独报告。贡献时间口径是 commit 时间，首次公开可用时间尚未独立核验。跨仓库 PR 关闭关系已验证实际 ClosedEvent，并分别保留源 PR 仓库和目标 Issue 仓库。

尚需继续取得低历史用户的真实完成结果、可靠主语言等事实字段，以及平台真实曝光与明确反馈。规则估计和历史未标记对照均保留来源状态。
