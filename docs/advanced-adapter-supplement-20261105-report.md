# Adapter v1 补采数据实验报告（2026-11-05）

## 结论

A 的补采包可以由 C 侧从原始证据重新生成正式 23 维 `research_frozen` 画像，并可用于
Adapter v1 训练。本轮已完成全部补采、季度截断和 PR 前一秒截断三组训练；三组均未全面超过
RC1 第二轮最佳模型，因此不替换当前 shadow 候选。

RC1 的 validation/test 数据、候选和配对保持不变，补采样本只加入 train。由此，指标变化来自
额外训练数据，而不是评估集合变化。

## 输入验收与画像重建

- A 补采 outcome：392 条；
- 可完整联接代码证据和候选快照：384 条；
- 输入不完整并排除：8 条；
- C 侧从原始证据重建画像：275 条、59 个开发者；
- 23 维正式校验：275/275 通过，0 失败、0 重复 hash、0 条全未观测画像；
- 候选历史版本：1,663 个；
- 训练比较对：1,732 对；
- 截断类型：季度截断 194 个 outcome，PR 前一秒截断 190 个 outcome；
- 画像已观测维度：最少 2、平均 5.2、最多 8；
- `profiles.jsonl` SHA-256：
  `1bc8f6b05dfffba74375ea5f28a4f63293ee2fcd3c563dd028537b362c5518a1`；
- 候选编码缓存 SHA-256：
  `c14e8b23bae42055ee6308834a4547165039fb130b8ad969eb366960abe4adb2`。

画像生成不读取 outcome/Y 内容，也不使用 A 提供的预览画像。所有贡献证据时间必须早于画像
cutoff；未知维度保持 `values=0.0`、`observed_mask=false`。

## 联合训练审计

| 切分 | RC1 配对 | 加入全部补采后 | 是否改变评估集 |
|---|---:|---:|---|
| train | 48,900 | 50,632 | 只在此处增加 1,732 对 |
| validation | 7,420 | 7,420 | 否 |
| test | 4,240 | 4,240 | 否 |

训练结构、超参数、基础 CODER、数据采样 seed 和训练 seed 均沿用 RC1 第二轮最佳配置。

## 冻结测试集结果

测试集仍为 212 个 outcome、4,240 个比较对。

| 训练数据 | Validation MRR | Test MRR | Pair accuracy | Mean rank | Recall@5 | Recall@10 |
|---|---:|---:|---:|---:|---:|---:|
| RC1 round2 | **0.25019** | **0.26301** | **60.05%** | **8.99** | 36.79% | **60.85%** |
| RC1 + 全部补采 | 0.24314 | 0.25561 | 59.60% | 9.08 | **37.74%** | 59.43% |
| RC1 + 季度截断 | 0.24565 | 0.25566 | 59.67% | 9.07 | 35.38% | 59.91% |
| RC1 + PR 前一秒 | 0.24964 | 0.25200 | 59.34% | 9.13 | 37.26% | 59.43% |

相对 RC1 round2：

- 全部补采的 Recall@5 提升 `0.94pp`、新仓库 MRR 从 `0.32447` 升到 `0.35031`、
  同仓库 pair accuracy 从 `44.44%` 升到 `51.11%`，但总体 MRR、未见开发者和低覆盖画像退化；
- 季度截断的低活跃开发者 MRR 从 `0.14597` 升到 `0.15997`，但总体、中活跃、低覆盖和
  新仓库指标退化；
- PR 前一秒截断的同仓库 pair accuracy 为 `55.56%`，新仓库 MRR 为 `0.34085`，但总体
  MRR 和低活跃指标退化；
- 三组全未知画像均严格回退为基础 CODER，最大绝对误差为 `0.0`。

59 个补采开发者中，只有 12 人进入冻结 test，共 16 个 outcome。全部补采模型在这个小子集上
MRR 基本持平（`0.13551 -> 0.13532`），pair accuracy 提升 `0.63pp`。样本太少，不能据此
判定补采无效或有效。

## 判断与后续建议

补采数据本身可用，当前问题主要是训练权重和对照构造：补采 outcome 通常只有 1～5 个同仓库
未标注对照，而 RC1 每个 outcome 有 20 个随机对照。直接逐 pair 混合会让两类数据贡献不均衡，
且补采开发者在冻结 test 的覆盖很少。

下一轮应保持测试集冻结，只在 validation 上比较以下方案：

1. 每个 outcome 等权，而不是每个 comparison pair 等权；
2. 限制单个 outcome 的最大梯度贡献；
3. 分开训练两类 cutoff，再做 curriculum 或 validation 选择；
4. 补充覆盖这 59 个开发者、但时间晚于训练 cutoff 的独立评估样本。

在这些验证完成前，继续使用 `advanced-adapter-v1-round2-optimized` 作为 shadow 候选；本轮三个
bundle 仅保留为 research 实验产物，不交给 D 替换线上候选。

## 本地产物

```text
data/midterm/working/advanced_adapter_supplement_20261105_v1/
data/midterm/runs/advanced-adapter-supplement-audit-20261105/
data/models/advanced_adapter_supplement_v1/
data/models/advanced_adapter_supplement_v1_calendar_quarter/
data/models/advanced_adapter_supplement_v1_pre_interaction/
```

三个权重 SHA-256 分别为：

- 全部补采：`03ea7dca9de9c5fc1097366f7ca049b52cd29824da9882487a1368802bdcb9ef`；
- 季度截断：`63eff318e93681cdf17b63e0ad2ad57ab562b5fb70490a08df462412c004a636`；
- PR 前一秒：`81036f998c018c38d10202f4213cd5d15a0b48cd5a4b39daa40d701566cb45c4`。
