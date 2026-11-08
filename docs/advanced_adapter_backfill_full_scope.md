# Advanced Adapter：309 仓库历史补采交接

用户随后将范围扩展到 2024—2026 三年，已建立独立工作版本；最新统计与 2026 年覆盖缺口见 [三年采集记录](advanced_adapter_three_year_2026-11-03.md)。

2026-11-03 已完成三批详细续采，累计 6,668 条 PR 核查、1,956 条核实结果、1,811 条完整样本。最新记录见 [历史证据续采](advanced_adapter_continuation_2026-11-03.md)。下文保留续采前的阶段统计。

本轮延续此前 60 仓库批次，重点补充 2024、2025 年。所有 GitHub 操作只读，数据保存在本地工作目录；未训练、提交、推送或发送团队消息。

## 结果

| 年份 | 去重文件贡献 | 核实的 PR–Issue 结果 | 完整串联样本 |
|---|---:|---:|---:|
| 2024 | 1,465 | 414 | 368 |
| 2025 | 430 | 524 | 464 |
| 合计 | 1,895 | 938 | 832 |

完整样本由同一历史截止点的画像证据、此后实际关闭 Issue 的合并 PR、当时开放且文本可验证的候选对照串联而成。938 条核实结果涉及 342 位开发者；832 条完整样本涉及 310 位开发者和 195 个仓库，提供 2,487 组未标记候选对照。

本批有 1,909 条按窗口关联的文件记录，去重后为 1,895 条文件贡献、1,319 个 commit。相同文件在多个画像窗口出现不会增加独立贡献计数。共生成 515 份按开发者和截止点聚合的历史画像预览。

## 搜索覆盖和样本数量分开解释

原有 309 个目标仓库的 618 个年度查询均已检查。193 个首屏饱和范围继续读取第二、三页；余下 70 个范围再按日期拆分和分页，所有可访问叶子查询最终均返回 `hasNextPage=false`，无失败或未完成范围。

去重 PR 索引为 87,343 条，其中 19,864 条作者属于既有核验种子身份。按既定有界选样规则，选择 3,068 条 PR 核实，最终确认上述 938 条结果。**索引记录不等于已核实结果，搜索覆盖完成也不表示所有 PR 都完成了证据核查。**

查询范围保留原协议：贡献从 2024-01-01 起回溯，结果窗口从 2024-04-01 起，2024 年第一季度用于画像暖启动；2025 年补查了年中、年末窗口。没有把 2026 年贡献或当前问卷回填到历史画像。

## 画像和未完成项

画像复用 B 的现有贡献解释及融合规则，使用 `research_preview` 和独立 pilot schema；未知维度保持零占位与 false mask，没有生成用户确认或伪造问卷。B 仍需审核语义并导出正式 `research_frozen`；C 仍需确认候选格式、历史标签的处理、编码与时间切分，并运行正式审计和训练。

515 份预览目前只有 8 种模型输入组合，说明现有路径规则的区分度有限。后续应让 B 结合已提供的真实代码证据完善画像，不能通过人为填值增加差异。

画像按用户和截止点跨仓库聚合。13 条样本的目标仓库未出现在其所采集的画像证据中，此标签只描述本批采样历史。C 评估所需的训练集已见/未见仓库分组须另外计算。

106 条核实结果尚未串成完整样本，主要涉及缺少可用画像或对照。记录保留在排除表，没有改成负反馈。每个开发者—仓库—截止点的主体回溯仍限定 3 个 commit、源码/测试/文档各至多一个文件；缺失画像另补查至多两个种子关联仓库。大文件、非 UTF-8、无截止前贡献和无法恢复历史正文的范围均保留原因。故本批是可审计的有界训练证据，不能宣称覆盖开发者完整 GitHub 历史。

## 交付入口

工作目录：`data/midterm/working/advanced_adapter_backfill_2024_2025_v1/`。

| 文件 | 用途 |
|---|---|
| `REPORT.md`、`summary.json` | 当前报告与精确统计 |
| `historical_pr_index.jsonl` | 87,343 条去重候选 PR 索引，明确标记未核实 |
| `verified_seed_pr_index.jsonl` | 核验种子作者的 PR 索引子集 |
| `verified_seed_identities.json` | 本批核验种子原字节副本，SHA-256 与计划一致 |
| `advanced_issue_outcomes.jsonl` | C 结果字段格式的已核实结果 |
| `verified_outcomes.jsonl` | 身份、关闭事件、历史正文及时间证据 |
| `historical_contributions.jsonl`、`code/` | 固定版本贡献与原始代码字节 |
| `historical_candidate_snapshots.jsonl` | 按截止点保存的历史候选内容 |
| `profile_previews.jsonl`、`profile_evidence_batches.jsonl` | B 规则画像预览及各维证据 |
| `joined_samples.jsonl` | 832 条完整样本索引 |
| `complete_search_coverage.json`、`search_completion.json` | 日期分片、分页末端和覆盖状态 |
| `failure_summary.json`、`*_exclusions.jsonl` | 排除原因与对应记录 |
| `manifest.json` | 数据、脚本和 B 规则 SHA-256 |

相对证据定位字段统一使用正斜杠，代码原文、Git 路径、Issue 正文和内容哈希保持原字节语义。原始 CLI 结果缓存明确标记为投影，移除了邮箱等字段，没有冒充完整 HTTP 原始响应。

此前 60 仓库结果及对应源码保留在 `batch_snapshots/previous_60_repos_e25d47d7a301/`；其原始缓存与代码仍在数据集根目录，已保留的源文件不会因扩范围被删除。

## 验证

```powershell
python scripts/midterm_a/backfill_adapter_2024_2025.py --stage verify
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests/midterm_a -p test_adapter_backfill_2024_2025.py -q
python -m unittest discover -s tests/midterm_b -p test_github_evidence.py -q
```

本轮 16 项补采专项测试和 7 项 B 原有测试通过。数据校验覆盖全部文件哈希、Git blob、核验身份、实际关闭 PR 事件、季度与 90 天窗口、历史文本、画像哈希及未知 mask；另逐行校验去重 PR 索引，并检查日期分片连续、末页已读取。

尚未运行 C 的官方模型验证器，也没有正式 adapter 权重。当前 `training_ready=false` 表示等待 B/C 冻结与验收，不代表上述已核实证据为空。
