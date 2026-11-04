# Advanced Adapter v1 两轮优化实验报告

## 结论

在不改变原始数据、时间切分、候选集合和基础 CODER 的前提下，两轮共完成 23 次独立训练，
最终第二轮模型在冻结测试集上优于基础 CODER、原 Adapter v1 和第一轮最佳模型。

最终建议模型为 `advanced-adapter-v1-round2-optimized`：

- residual scale：`0.75`；
- hidden dim：`32`；
- learning rate：`0.001`；
- feature dropout：`0`；
- residual L2：`0`；
- coverage gate：关闭；
- dynamic hard-negative weight：`1.0`；
- hard-negative temperature：`1.0`；
- 训练 seed：`20261105`；
- 数据采样 seed：固定为 `20261102`。

该模型可以替换原 Adapter 进入 shadow，但仍不批准直接 serve。

## 实验约束

- 所有模型使用同一批 1,630 条冻结画像和 60,560 个训练配对。
- `data_seed=20261102` 固定；改变训练 seed 不会重新抽取对照 Issue。
- train / validation / test 时间边界保持不变。
- 基础模型固定为 `coder-full-search8-v1`，只优化 Adapter。
- 结构、超参数、seed 和 checkpoint 只根据 validation 选择。
- 两轮 winner 锁定后才统一读取 test 指标。

validation 选择分由以下指标加权：总体 MRR 50%、未见开发者 MRR 20%、低活跃开发者
MRR 15%、低覆盖画像 MRR 10%、同仓库 pair accuracy 5%。

## 第一轮

第一轮完成 8 组结构/超参数实验，并对最佳结构运行 5 个训练 seed。搜索范围包括：

- residual scale：0.25、0.50、0.75；
- hidden dim：16、32、64；
- learning rate：0.0005、0.001；
- feature dropout：0、0.10；
- residual L2：0、1e-4、1e-3；
- 5 个固定训练 seed；
- checkpoint averaging。

最佳模型为 `residual_scale=0.75, hidden_dim=32, seed=20261105`：

- validation MRR：`0.24284`；
- validation 分组选择分：`0.25034`。

10% feature dropout 和 residual L2 没有带来提升。当前画像平均只观测约 5.29 维，额外随机
遮蔽会进一步损失有效信号。

## 第二轮

第二轮基于第一轮最佳结构，完成 7 组 gate / 困难对照实验，再运行 5 个训练 seed：

- coverage gate；
- hard-negative weight：0、0.5、1.0、2.0；
- temperature：0.5、1.0；
- feature dropout 0.20 对照；
- 不使用 gate 的 hard-negative 对照；
- checkpoint averaging。

coverage gate 的 validation MRR 约为 `0.222～0.228`，低于不使用 gate 的模型，因此被拒绝。
最终胜出的是不使用 gate、`hard_negative_weight=1.0`、seed `20261105`：

- validation MRR：`0.25019`；
- validation 分组选择分：`0.25225`。

两个 round 的直接 checkpoint 参数平均均明显退化（validation MRR 约 `0.155～0.157`）。不同
MLP seed 存在隐藏单元置换，直接平均参数并不等价于平均函数，因此正式模型采用最佳单 seed，
不采用权重平均。

## 冻结测试集性能

测试集包含 212 个 outcome 和 4,240 个成对比较。

| 模型 | MRR | Pair accuracy | Mean rank | Recall@5 | Recall@10 | 未见开发者 MRR |
|---|---:|---:|---:|---:|---:|---:|
| 基础 CODER | 0.16999 | 49.55% | 11.09 | 21.70% | 48.58% | 0.16433 |
| 原 Adapter v1 | 0.24879 | 58.73% | 9.25 | 34.43% | 60.85% | 0.23804 |
| 第一轮最佳 | 0.24894 | 59.17% | 9.17 | 35.85% | 60.38% | 0.25871 |
| 第二轮最佳 | **0.26301** | **60.05%** | **8.99** | **36.79%** | **60.85%** | **0.27205** |

第二轮最佳相对原 Adapter v1：

- MRR：`+0.01421`，相对提升约 5.71%；
- pair accuracy：`+1.32pp`；
- mean rank：改善约 `0.26` 名；
- Recall@5：`+2.36pp`；
- 未见开发者 MRR：`+0.03402`，相对提升约 14.29%。

第二轮最佳相对基础 CODER：

- MRR：`+0.09302`，相对提升约 54.72%；
- pair accuracy：`+10.50pp`；
- mean rank：改善约 `2.10` 名；
- Recall@5：`+15.09pp`。

## 分组风险

优化没有消除所有风险：

- 训练期仅有 1～2 个 outcome 的开发者：基础 CODER MRR `0.19988`，原 Adapter
  `0.16112`，第一轮 `0.14862`，第二轮 `0.14597`；该组持续退化。
- 第二轮低覆盖画像 MRR 为 `0.22833`，高于原 Adapter 的 `0.18527`，稀疏画像总体有所改善。
- 同仓库对照只有 45 对；原 Adapter pair accuracy 变化为 `-4.44pp`，第一轮为 `+2.22pp`，
  第二轮为 `0pp`。样本量不足，仍需 shadow 观察。
- 新仓库只有 18 个 outcome；第二轮 MRR `0.32447`，方向仍优于基础 CODER，但略低于
  原 Adapter 的 `0.33504`。
- 全未知画像仍严格退化为基础 CODER，最大绝对误差 `0.0`。

因此最终模型适合进入 shadow 收集真实曝光和反馈，不应依据离线总体提升直接进入 serve。

## 正式产物

- 优化总报告 SHA-256：`3a2005302936d01a4174d6adf38e2219080cbf7c5311182c4a98cda1f0d5c813`
- 第一轮权重 SHA-256：`09b6929b83603048df96ecda0d768f9d37ed226c95ff49dac92d7a7b45b5c404`
- 第一轮 manifest SHA-256：`f0beff1f1315d10c36c0ff53ae3ef65b02c699501d8d3ea34416624536062e5c`
- 第二轮权重 SHA-256：`6112db508fd1f14876c272624a5e8d4d711d0d32a694fb02d3267f5f04091b78`
- 第二轮 manifest SHA-256：`3d82e7948f5ab4923c842796dd1aff4906275f7e95e1cd30ad0cd40c3eca43d5`
- 第二轮完整评估 SHA-256：`2ae0248974f76ec42efedb44bb901a749972d0cc34d8b7a0f5eb4d05e56c02f2`

完整机器可读报告和两个可加载 bundle 位于：

```text
data/models/advanced_adapter_optimization_v1/
```
