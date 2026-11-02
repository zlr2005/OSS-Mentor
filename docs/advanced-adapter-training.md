# Advanced Adapter v1 训练管线

## 目标与边界

本管线在完全冻结 `coder-full-search8-v1` 的前提下，只训练 23 维增强画像的残差
adapter。v1 对所有 `growth` 用户统一使用训练用户均值向量作为基向量，不要求用户命中
GFI-Bot `developer_mapping`。

训练样本必须满足：

```text
profile.as_of < outcome.interaction_at <= outcome.outcome_at
outcome.outcome_at <= profile.as_of + observation_window
```

历史未观察到开发者选择的 Issue 只是抽样对照，不是真实负反馈。训练指标是候选集上的
匹配和排序分数，不是成功解决 Issue 的概率。

## 输入

三个业务输入均支持 UTF-8 JSONL 或 Parquet。历史 Issue 向量使用独立 NPZ，必须包含
`issue_keys`、`issue_embeddings` 和与每行对应的 `content_sha256`。候选内容哈希与向量缓存
不一致时直接排除，不复用已漂移向量。

### 1. B 输出的历史画像

每行使用 `enhanced-profile-training-export-v0.1`，且：

- `profile_state=research_frozen`；
- `developer_key` 是经验证的 GitHub 身份；
- `as_of` 是该画像的历史截止时刻；
- `feature_names` 为固定 23 维，名称和顺序不得改变；
- 未观测维度必须是 `value=0` 且 `observed_mask=false`；
- `profile_hash` 为小写 SHA-256；
- `evidence_refs` 保留可追溯来源。

离线 `research_frozen` 只能进入训练加载器，不能通过线上 `confirmed` 画像加载器。

### 2. A 输出的 Developer–Issue 结果

每行使用 `advanced-issue-outcome-v0.1`，必需字段：

| 字段 | 含义 |
|---|---|
| `outcome_id` | 稳定且唯一的结果 ID |
| `developer_key` | 经验证的 GitHub 开发者身份 |
| `issue_key` | 规范化的 `owner/repository#number` |
| `outcome_type` | 如 `verified_closing_pr` |
| `interaction_at` | 开发者开始目标行为的时间，如 PR 创建时间 |
| `outcome_at` | 结果发生时间，必须带时区 |
| `verification_status` | 如 `verified` |
| `source_url` | 可追溯的 GitHub 来源 |

可用的 outcome 类型及权重由训练配置显式开启。未开启或未验证结果会进入失败报告，
不会静默参与训练。

### 3. A 输出的历史候选 Issue

每行使用 `advanced-historical-candidate-v0.1`，必需字段：

| 字段 | 含义 |
|---|---|
| `issue_key` | 规范化 Issue 键 |
| `available_from` | 候选开始可见的时间 |
| `available_until` | 候选不再可用的时间，仍可用时为 null |
| `content_sha256` | 候选内容哈希 |

正样本和抽样对照都必须在画像 `as_of` 时可用，且必须存在冻结 Issue 向量。

## 两阶段运行

### 审计

```powershell
coder-train-advanced-adapter `
  --mode audit `
  --config CODER/configs/advanced_adapter_v1.example.json `
  --profiles data/advanced/advanced_temporal_profiles.parquet `
  --outcomes data/advanced/advanced_issue_outcomes.parquet `
  --candidates data/advanced/historical_issue_candidates.parquet `
  --model-dir data/midterm/models/coder-full-search8-v1 `
  --issue-cache data/advanced/historical_issue_embeddings.npz `
  --output-dir data/midterm/runs/advanced-adapter-audit
```

审计只生成 `audit_report.json` 和 `failures.jsonl`，不生成权重。报告包含有效结果数、样本对数、
三个时间集合的结果数和开发者数。

### 训练

审计通过后将 `--mode` 改为 `train`。管线使用加权 BPR，仅优化 adapter 参数；固定种子，
按 outcome 时间切分 train/validation/test，只用 validation MRR 早停和选择 checkpoint。

输出目录包含：

```text
audit_report.json
failures.jsonl
training_metrics.json
split_manifest.json
weights.pt
adapter_manifest.json
_SUCCESS
```

`adapter_manifest.json` 固定基础模型、用户基向量模式、23 维 Schema、配置、随机种子、输入哈希和
权重哈希。`_SUCCESS` 固定 manifest SHA-256。

## 评估口径

评估候选是每个结果的实际正样本加时间有效抽样对照，不是平台真实曝光集。报告包含：

- pair accuracy 和平均排名；
- Recall/Hit/NDCG/MRR@5/10/20；
- validation 选出的 checkpoint；
- 测试中已见和未见开发者分组。

这些指标不得表述为线上成功概率。平台有真实曝光与反馈后，应使用线上数据继续微调和独立评估。

## 已知限制

- 本管线不采集 GitHub 数据；A 需提供可追溯历史事实。
- 本管线不生成 23 维画像；B 需提供时间安全的 `research_frozen` 快照。
- 当前历史对照是未标记抽样，不是用户明确负反馈。
- 本管线完成不代表已经有正式权重或效果提升结论。
