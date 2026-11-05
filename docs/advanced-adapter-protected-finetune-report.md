# Advanced Adapter v1：RC1 保护式补采微调报告

日期：2026-11-05
状态：validation 完成；未解锁 test；不替换 RC1

## 结论

本轮实现并验证了“以 RC1 为 teacher、用补采数据做保护式微调”的训练方案。三组实验都成功避免了已知的整体退化，但没有任何微调 checkpoint 在冻结的 RC1 validation 上超过 epoch 0 的 RC1 teacher。因此训练器按预设门禁自动回退到 RC1 权重，未读取 test，也不建议发布新的 Adapter bundle。

这说明保护方案有效，但当前补采信号仍不足以带来可验证的泛化增益。

## 固定实验条件

- teacher：`advanced_adapter_optimization_v1/round2_best`（RC1 round2）
- RC1 train：2,445 outcomes / 48,900 pairs
- 补采池：194 个 `calendar_quarter` outcomes / 3,880 pairs
- 每个 epoch：完整回放 RC1 outcomes，并把补采 outcomes 重采样到 10%
- RC1 与补采内部均采用 outcome-equal 权重
- RC1 低覆盖画像（observed dimensions <= 3）权重乘 1.5
- learning rate：0.0002
- seed：20261103
- patience：10
- validation：保持 RC1 原始 371 outcomes / 7,420 pairs
- test：保持 RC1 原始 212 outcomes / 4,240 pairs，但本轮未读取

## 实验定义

| 实验 | RC1 热启动 | 90/10 回放 | RC1 margin 蒸馏 | 参数锚定 |
|---|---:|---:|---:|---:|
| P1 | 是 | 是 | 0 | 0 |
| P2 | 是 | 是 | 0.1 | 0 |
| P3 | 是 | 是 | 0.1 | 0.0001 |

## Validation 结果

| 实验 | 最佳可发布 epoch | 最佳 validation MRR | 微调 epoch 中最佳 MRR | 相对 RC1 | 决策 |
|---|---:|---:|---:|---:|---|
| RC1 teacher | 0 | 0.250194845 | — | — | 当前基线 |
| P1 | 0 | 0.250194845 | 0.246901678（epoch 2） | -0.003293166 | 回退 RC1 |
| P2 | 0 | 0.250194845 | 0.249448397（epoch 2） | -0.000746447 | 回退 RC1 |
| P3 | 0 | 0.250194845 | 0.249448397（epoch 2） | -0.000746447 | 回退 RC1 |

P2/P3 明显缩小了 P1 的退化，说明 RC1 margin 蒸馏有效；但参数锚定在当前系数下没有带来额外可测收益。三组最终 `weights.pt` 的 SHA-256 均为：

`6112db508fd1f14876c272624a5e8d4d711d0d32a694fb02d3267f5f04091b78`

该哈希与 RC1 teacher 完全相同，证明 epoch-0 回退不是“指标相同但模型已变化”，而是精确保留了 RC1 权重。

## 为什么没有跑 test

实验协议预先规定：超参数只允许用 validation 选择；只有 validation 明确超过 RC1 的方案才能解锁一次 test。P1/P2/P3 均未达到该条件，因此继续查看 test 会造成不必要的 test 泄漏，也无法改变“不升级 RC1”的发布结论。

## 实现的保护机制

1. 从 RC1 bundle 加载完整 Adapter 权重，而不是随机初始化。
2. 每个 epoch 完整保留 RC1 outcomes，只让补采 outcomes 占目标训练流的 10%。
3. 对同一个 outcome 的多个负样本做等总权重，避免候选数量改变样本影响力。
4. 对 RC1 稀疏画像加权，重点保护此前最容易退化的低覆盖人群。
5. P2/P3 在 RC1 pair 上约束 student margin 接近 teacher margin。
6. P3 额外约束 student 参数不要远离 teacher 参数。
7. 把 epoch 0 纳入 checkpoint 候选；没有真实提升时自动保存原 RC1。
8. 默认不评估 test，必须显式解锁。

## 后续建议

暂时继续使用 RC1。若要让补采数据真正产生增益，优先改进数据而不是继续盲调系数：增加与 RC1 validation 分布一致、候选集合完整、时间截断统一的补采 outcomes；扩大当前只有 32 名开发者的有效补采覆盖；并为补采数据建立独立 validation。数据补齐后，可沿用本训练器重新跑 P2，只有 validation 超过 RC1 才进入多 seed 和最终 test。
