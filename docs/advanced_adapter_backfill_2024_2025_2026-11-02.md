# Advanced Adapter：2024/2025 历史数据补采交接

后续已扩展到全部 309 个仓库；本文件保留 60 仓库阶段记录。最新结果见 [309 仓库交接](advanced_adapter_backfill_full_scope.md)。

日期：2026-11-02。根据用户要求重点补 2024、2025 年，未采入 2026 年贡献或结果。状态为 working / frozen=false；未训练、提交、推送或发送团队消息。

## 本批实测结果

从原有 309 个目标仓库中选择 60 个仓库，以既有核验开发者为身份范围。年度搜索返回 6,885 条记录，另对 12 个仓库的 2025 年第二至第四季度补查 1,215 条记录，按 PR URL 去重后选择 744 条 PR 核查。

| 年份 | 核实的 PR–Issue 结果 | 串联完整的证据样本 | 去重文件贡献 | 独立 commit |
|---|---:|---:|---:|---:|
| 2024 | 84 | 70 | 330 | 244 |
| 2025 | 151 | 134 | 136 | 96 |
| 合计 | 235 | 204 | 466 | 340 |

核实结果涉及 90 位开发者；最终串联样本涉及 82 位开发者、45 个仓库。生成 137 份按开发者及历史截止点聚合的画像预览，提供 612 组低置信度未标记候选对照。文件贡献按发生年份统计，样本按画像截止年份统计，两者不能混为同一个分母。

470 条按画像窗口关联的文件记录去重为 466 条，重复历史不会增加独立贡献计数。结果 ID 唯一，同一结果没有被多个画像窗口重复使用。本批与早期 5 条试采样例分开保存。

## 每条通过记录的证据要求

- PR 作者的 GitHub 数字 ID 与既有核验身份一致，PR 已合并。
- Issue 的实际关闭事件明确指向该 PR；仅有正文关键词或关联引用不足以通过。
- 季度画像截止点严格早于 PR 创建；结果在其后 90 天内发生。
- Issue 在截止时已经创建且开放，关闭/重开/标题改名事件列表返回完整。
- 正文的明确最后编辑时间为空或不晚于截止点；截止后修改且未恢复历史版本的正文被排除。
- 画像仅使用截止前的 commit 与固定文件版本，校验 commit 作者、作者/提交时间、Git blob SHA-1、内容 SHA-256。
- 文件按源码、测试、文档分层选取，大文件与缺失证据保留排除记录，不填假代码或假画像。

2025 年初的画像会合理使用 2024 年贡献。因此单独补查了 2025 年中、年末窗口，避免将“2025 年解决的 Issue”误当成“2025 年发生的画像贡献”。

## 本地数据入口

工作目录：`data/midterm/working/advanced_adapter_backfill_2024_2025_v1/`。

| 文件 | 用途 |
|---|---|
| `REPORT.md`、`summary.json` | 本批报告和精确统计 |
| `advanced_issue_outcomes.jsonl` | 已核实结果，采用 `advanced-issue-outcome-v0.1` 字段 |
| `verified_outcomes.jsonl` | 带历史内容、身份和关闭证据的详细结果 |
| `historical_contributions.jsonl` | 截止前贡献、固定文件与来源引用 |
| `historical_candidate_snapshots.jsonl` | 按 Issue 与截止点保存的历史候选内容快照 |
| `historical_controls.jsonl` | 当时开放且正文可核验的未标记对照 |
| `profile_previews.jsonl` | 复用 B 现有规则生成的 23 维画像预览 |
| `profile_evidence_batches.jsonl` | 画像各维度对应的 B 侧解释证据 |
| `joined_samples.jsonl` | 已串联的画像—正样本—对照索引 |
| `failure_summary.json`、`*_exclusions.jsonl` | 排除原因与对应记录 |
| `remaining_backfill_jobs.json` | 未覆盖仓库、搜索分页游标及后续缺口 |
| `manifest.json` | 文件、采集源码和 B 规则 SHA-256 |

文本哈希使用带结尾换行的 UTF-8 JSON：`json.dumps({"title": title, "body": body}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"`。C 在生成向量前需确认该内容格式，不应套用另一种拼接后直接比较哈希。

## 当前边界与接收方工作

204 条是证据已经串联的离线样本，不表示已经完成正式模型验收。画像使用 `research_preview` 和独立 pilot schema，B 仍需审核证据解释、采集覆盖及冻结为 `research_frozen` 的导出。未知维度保留零占位和 false mask，没有补造问卷或兴趣。

137 份画像目前只有 8 种模型输入组合，说明现有路径规则的区分度仍有限；增加记录数不能替代 B 对更丰富画像证据的解释。源码、测试和文档路径是代理特征，不能表述为精确能力或任务成功概率。

C 仍需确认候选内容格式、历史标签的使用、编码和时间切分，运行自己的输入审计与模型训练。候选快照按截止点保存，不能将重开区间或多个文本版本直接压成一条全局有效区间。未标记候选不是真实负反馈。

本批仍是有界补采：年度搜索有 46 个范围达到首 100 条上限，补充季度有 17 个范围达到首 50 条上限；原 309 仓库中另 249 个尚未在本批查询。每个开发者—仓库—截止点最多回溯 3 个 commit。所有缺口和游标均保留，不能宣称 2024/2025 全部公开历史已经采齐。

## 验证和复现

离线回放与报告：

```powershell
python scripts/midterm_a/backfill_adapter_2024_2025.py --stage history
python scripts/midterm_a/backfill_adapter_2024_2025.py --stage report
python scripts/midterm_a/backfill_adapter_2024_2025.py --stage verify
```

后续有界采集可使用同一脚本的 discover、late-discover、details、controls、history 阶段和 `--allow-network`，复用已校验的请求缓存；不需要在脚本或聊天中提供 Token。范围扩展通过 `--extend-scope --repositories N` 显式保留父计划。

本批 2,002 个产物文件校验通过；235 条结果、470 条窗口关联文件记录、137 份画像和 204 条串联样本均完成来源/时间/哈希检查。8 项新增时间和采样边界测试、7 项 B 原有贡献解释测试通过。未执行 C 官方验证器或模型训练。
