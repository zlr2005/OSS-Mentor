# CODER 消融实验方案

本方案只定义后续实验，不启动训练。所有消融必须使用同一 Dataset、Graph Version、Seed、候选哈希、验证选择规则和 Trial 预算。

## 1. 现有开关的真实计算路径

| Variant | Text | Structure/GAT | Repository 图 | Repository Loss |
|---|---:|---:|---:|---:|
| `lightgcn` | 否 | 否 | 否 | 否 |
| `coder_issue_text` | 是 | 否 | 否 | 否 |
| `coder_issue_structure` | 否 | 是 | 否 | 否 |
| `coder_issue_base` | 是 | 是 | 否 | 否 |
| `coder_issue_repo` | 是 | 是 | 是 | 是 |

这些不是日志别名：

- `use_text=false` 时不注册有效语义矩阵，也不把文本拼入 Issue Fusion。
- `use_structure=false` 时不构建/调用 GAT，结构向量不进入 Issue Fusion。
- `use_repository=false` 时不构建 Repository LightGCN，Developer/Issue Fusion 均不含 Repository 向量，Repository Sampler 和 Repository BPR 不参与联合损失。
- Full 模型中 Issue BPR 与 Repository BPR 共同反向传播到对应可训练路径；冻结 MiniLM Embedding 不接收梯度。

现有单元测试覆盖独立开关输出形状、完整模型各可训练分支梯度和冻结文本无梯度。

## 2. 推荐的主消融矩阵

1. LightGCN：协同过滤基线。
2. LightGCN + Text：测量 creation-time 文本的增益。
3. LightGCN + Structure：测量 Repository–Issue–Label GAT 增益。
4. LightGCN + Text + Structure：测量两类 Issue 特征的组合。
5. Full CODER：加入 Repository LightGCN 与联合损失。

主表只报告 Issue 推荐的冻结协议。Repository 分支在独立 Repository 协议建立前只能作为辅助训练模块，不能用 Issue→Repository 投影指标宣传 Repository 推荐性能。

## 3. Repository Loss Weight 专项

在 Full CODER 的其他参数固定为验证选定组合后，单独比较 `0.0 / 0.1 / 0.5`。其中 `0.0` 应仍构建 Repository 表征但关闭 Repository 损失，用于区分“Repository 表征融合”与“Repository 联合监督”；它不同于完全关闭 Repository 分支。

不得用测试集选择权重。先用 validation/cross_repo/predictable NDCG@10 选定，再一次性报告 test 辅助指标。

## 4. 公平预算

- 每个主消融 Variant 使用相同的 8 Trial 上限或预先登记的等预算设计。
- 最多 50 Epoch、每 5 Epoch 验证、patience=5、min_epochs=10。
- MiniLM revision、文本缓存、冻结候选和 Seed 不变。
- 如果采用严格时间过滤的新 Graph Version，所有含 Structure 的 Variant 必须重训；主表不得混用旧图结果。

## 5. 场景报告

每个 Variant 分别报告：

- same_repo 与 cross_repo；
- all 与 predictable；
- warm/cold_start；
- cross_repository_user（仅表示训练历史跨仓库用户）。

单正样本协议下 Recall 与 HR 相同，论文表可同时保留以对齐领域惯例，但正文需说明这一事实。
