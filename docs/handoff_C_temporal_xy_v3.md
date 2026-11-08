# C 交付 · 用 v3 训练和评估画像 Adapter

数据版本为 `temporal_xy_2024_2026_v3`，GitHub 交付提交为 `eb35864e`。C 可以与 B 并行实现读取器、历史 Issue 编码、候选组装、基线及训练管线，并用小批预览画像验证形状和联接。正式 Adapter 训练依赖 B 按同一版 X 生成的 `research_frozen` 画像，以及通过审计的历史候选和基础模型产物。

[GitHub 交付目录](https://github.com/zlr2005/OSS-Mentor/tree/eb35864e/datasets/temporal_xy_2024_2026_v3) · [现有 Adapter 与编码契约](https://github.com/zlr2005/OSS-Mentor/blob/main/docs/profile-coder-next-stage.md) · [B 的交付说明](handoff_B_temporal_xy_v3.md)

## 1. 数据怎么读

| 文件 | C 的用法 |
|---|---|
| `train.jsonl`、`validation.jsonl`、`test.jsonl` | 读取开发者、截止点、X 引用和一组 Y。按文件给定的 split 运行 |
| `file_contributions.jsonl` | 解析 X 三元组并复查时间、账号、来源和内容哈希。向 B 提供语义证据所需的代码编码可以按内容哈希复用 |
| `source_manifest.json` | 核对源数据版本和哈希。两个 `source_roots` 需由团队内部提供，GitHub 交付目录只有索引和元数据 |
| 合并包 `verified_outcomes.jsonl` | 按 `outcome_id` 查正样本，取 `issue_content` 的截止点标题和正文、实际关闭事件来源 |
| 合并包 `joined_samples.jsonl`、`historical_controls.jsonl` | 先按 `outcome_id` 查 `comparison_issue_keys`，再以 Issue key 和 `as_of` 查当时可用的对照文本 |
| B 的冻结画像 | 按 `(github_numeric_id, as_of=cutoff_at)` 联接。旧预览的 `profile_key/profile_version` 仅用于旧批次追溯 |

上述合并包位于 A 本地 `data/midterm/working/merged_ab_history_2024_2026_v3`，需一并取得。其 `copied_tables_reference_root=backfill`，因此 Issue 证据的相对路径用 backfill 根目录解析。文件贡献每条 `sources` 用自身 `source` 决定根目录。

| 切分 | 画像截止点 | 组数 | 已核实 Y | 在旧证据联表中有候选对照的 Y |
|---|---|---:|---:|---:|
| 训练 | 2024–2025 年 | 1,193 | 2,465 | 2,416 |
| 验证 | 2026 年上半年 | 268 | 386 | 378 |
| 测试 | 2026 年下半年 | 169 | 220 | 220 |

对照覆盖是 B 冻结与 C 审计前的上限。按开发者截止点生成多正例组，标签按 Issue key 去重，同时保留所有 outcome ID 以便追溯。排除与该组正例冲突的对照。缺对照的标签可以补同截止点的候选，或从排序实验排除并记录原因。当前未标记对照适合作为弱比较项，实验说明需标明其含义。

## 2. 时间安全与编码

1. 核对 X 的账号和 `event_at < cutoff_at`，Y 的账号相同，`cutoff_at < interaction_at <= outcome_at <= cutoff_at + 90 天`。训练标签早于 2026-01-01 UTC，验证标签早于 2026-07-01 UTC。同一开发者可跨集合出现，评估目标是对已有开发者预测未来。测试中 85 组为 2026 Q4，随访窗口未结束，主测试与 Q4 分开报覆盖和指标。
2. Issue 候选版本按 `(issue_key, as_of, content_sha256)` 管理，使用当时的标题和正文及开放状态证据。`content_sha256` 对应 `{title, body}` 的 UTF-8 JSON，键排序、紧凑分隔符、保留 Unicode，末尾加换行后取 SHA-256。历史标签未恢复，缺失标签保留未知状态。当前 Issue 文本、关闭结果、目标 PR 和事后文件定位不进入候选特征。
3. 固定统一编码器的 revision、tokenizer、分段与池化，记录行映射、失败记录和缓存哈希。贡献文件 CodeBERT 缓存与 Issue 向量缓存按现有模型契约分别管理。检查基础 CODER、用户历史、图和向量的训练来源与时间。即使冻结 checkpoint，若它已消费验证或测试窗口的真实结果，该实验仍需要按时间重新准备基础产物。
4. 先核对 B 的 `feature_schema_version`、23 项顺序、mask、时点和身份映射。只在可验证的映射上用已知用户向量，其他情况按已有冷启动契约使用训练用户总体均值。均值和其它拟合统计量仅基于训练数据。

## 3. Adapter 实现和训练

复用主分支已实现的 `CODER/src/coder_issue/models/profile.py` 与 `CoderIssueScorer.score_paired()`。第一轮固定基础模型、Issue 向量和候选条件，只训练画像残差模块与经约定的残差系数。

```text
u_base = known_history_user | training_user_population_mean
p = MLP(concat(values * observed_mask, observed_mask))
u_profile = u_base + residual_scale * p
score = dot(u_profile, issue_embedding)
```

全未知画像显式输出零残差，结果应与 `coder_original` 一致。`locked_mask` 和 `evidence_refs` 用于校验和解释。联调时先用少量样本检查前向、梯度、保存加载和全未知回退。正式训练可先做组内成对排序，以已核实关闭 Issue 对同截止点的未标记对照计算弱排序损失。同组多个正例分别参与比较，避免互当对照。记录每组的有效对照数、损失权重和丢弃原因。

验证集选模型和超参数，测试集用于最终评估。`coder_original` 与 `coder_profile` 使用同一基础 checkpoint、候选集合、顺序、过滤、编码和切分。报告 Recall@K、MRR 或 NDCG@K 时说明相关性来自观测关闭事件，分别给出候选覆盖、有效组数、已知用户与冷启动、完整随访与 Q4 的结果。排序指标按同一对照协议比较，并记录每个候选的原始分与排名变化。

## 4. 交付与依赖顺序

| 阶段 | B | C |
|---|---|---|
| 并行开发 | 解析 X、验证贡献、生成预览并约定冻结 schema | 解析 X/Y、准备候选与编码、接 Adapter、跑通小样本和基线 |
| 联调验收 | 提供按数字 ID 和截止点的画像、规则与证据覆盖报告 | 核对键、时点、维度、mask 和编码行映射，确认有效样本清单 |
| 正式实验 | 提供 `research_frozen` 及版本哈希 | 使用冻结画像训练，保存 Adapter 权重、manifest、配置、随机种子、切分和指标 |

正式产物还应能在独立进程按 manifest 加载，输出基础分、画像分及回退状态。离线冻结画像用于研究实验，线上评分接入仍按既有 `confirmed` 契约验收。
