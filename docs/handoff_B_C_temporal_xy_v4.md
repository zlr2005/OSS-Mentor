# B/C 交接 · temporal_xy_2024_2026_v4

本轮已将最新历史结果、两批加深证据和候选合并为 v4 工作输入，并完成内容哈希、来源清单、X 作者与时间、Y 联接以及候选正例冲突检查。当前状态为 working / training_ready=false。

## 规模

| 切分 | 开发者截止点组 | outcome 标签 | 按组去重 Issue 标签 | 可排序 outcome | 同仓库比较项 |
|---|---:|---:|---:|---:|---:|
| train | 1345 | 3039 | 3028 | 2995 | 9087 |
| validation | 268 | 386 | 384 | 386 | 1150 |
| test | 169 | 220 | 220 | 220 | 648 |

合并包共 11,255 条去重文件贡献，其中 11,104 条有可用内容，来源冲突为 0。保留 3,900 条核实 outcome 和旧采集器串联的 3,540 条样本；v4 的时序 X/Y 和候选覆盖按上表单独统计。

## 文件入口

工作输入目录为 `data/midterm/working/temporal_xy_2024_2026_v4/`，合并来源目录为 `data/midterm/working/merged_ab_history_2024_2026_v4/`。

| 文件 | 使用方式 |
|---|---|
| train.jsonl、validation.jsonl、test.jsonl | B 按 github_numeric_id、cutoff_at 读取 X 和 Y；每条 X 三元组联接 file_contributions.jsonl |
| file_contributions.jsonl | 合并后的贡献元数据，sources 保留多来源、内容位置、时间与身份 |
| source_manifest.json | 使用 source_roots 动态解析 ab、backfill、depth_1、depth_2，并核对四个来源清单哈希 |
| ranking_train.jsonl、ranking_validation.jsonl、ranking_test.jsonl | C 按 outcome_id 联接正例；comparison_issue_keys 为同仓库、同截止点的未标记对照，已排除该开发者已知正例 |
| audit.json | 各切分覆盖、无对照数量和重复 Issue 标签口径 |
| manifest.json | 工作包文件哈希 |
| 合并来源目录中的 verified_outcomes.jsonl | 正例 Issue 的历史标题、正文及实际关闭证据 |
| 合并来源目录中的 historical_controls.jsonl | 按 issue_key、as_of 读取对照文本；通过 evidence_source 选择证据根目录，evidence_sources 保留重复来源 |

## B 的下一步

按 v4 X 重建画像。旧 profile_previews.jsonl 仅用于追溯；本轮新增贡献尚未进入这些预览。按账号和截止点过滤贡献，严格检查 event_at < cutoff_at，使用内容哈希复用编码。缺失维度保持 value=0、observed_mask=false，审核后生成与 v4 输入哈希绑定的 research_frozen 画像。

## C 的下一步

按 v4 outcome 和 B 的新冻结画像联接。训练集中有 44 条具备 X/Y 的 outcome 缺少对照，已从 ranking_train.jsonl 排除并在 audit.json 计数。

同一组内的多个 outcome 可能指向同一 Issue。Y 保留全部 outcome_id 以便追溯，排序相关性和多正例组须按 Issue key 去重，避免将重复关闭事件计为多个独立相关 Issue。

现有对照尚未按基础 CODER 得分筛选困难样本。请固定编码和基础模型，在相同候选条件下比较模型；本表 648 个测试同仓库比较项是候选覆盖，不能当作新一轮效果指标。每个 outcome 超过目标候选数时，由版本化采样规则选择，并保留候选集合哈希。

时间边界沿用原协议，训练标签早于 2026-01-01 UTC，验证标签早于 2026-07-01 UTC。2026 Q4 随访窗口未结束，按原约定单独报告。当前贡献时间口径为 commit 发生时间，首次公开可用时间尚未独立核实。

## 平台反馈

真实曝光、放弃原因和与 run_id 绑定的反馈仍需平台采集。当前历史结果与未标记候选无法替代这些反馈信号。

## 复现

```powershell
python scripts/midterm_a/merge_ab_history.py --version 4 --supplement data/midterm/working/advanced_adapter_depth_20261104_v1 --supplement data/midterm/working/advanced_adapter_depth_20261104_v2
python scripts/midterm_a/build_temporal_xy.py --version 4
python scripts/midterm_a/verify_temporal_xy_handoff.py --version 4
```

合并和切分入口要求目标版本目录尚未存在；已有 v4 可直接运行最后一条审计命令。生成新版本时同时调整三个命令的版本号。
