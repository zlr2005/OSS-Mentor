# CODER 实验与评估协议审计

审计日期：2026-10-20。所有结论来自当前代码、冻结文件、Run Manifest、终态标记和实际指标文件；未把日志中的验证分数当作测试结果。

## 1. `coder-full-single-v1` 的真实状态

目标文件称该运行“成功完成”，但磁盘证据只证明训练主体完成：

- 50 Epoch、121 Batch/Epoch 已完成；最后 Issue Loss 为 `0.010145`，Repository Loss 为 `0.536857`。
- `trial-0001.best.pt`、`latest.pt`、`previous.pt` 均存在；最佳 Epoch 为 50，`valid_cross_repo.predictable.ndcg@10 = 0.28959986714796604`。
- 进程 PID 已不存在，目录仍只有 `_RUNNING`，没有 `_SUCCESS`、`_FAILED`、`run_manifest.json`、`metrics.json` 或最终模型 Bundle。
- Windows 在最后 heartbeat 同秒记录了 `AppCrash_python.exe`。因此这是训练后收尾阶段的原生崩溃，不是一个完成的正式实验。

该最佳 Checkpoint 可以独立加载和评估，但在独立评估产物生成前，`0.289599867` 仅是验证集选模分数。

修复只读 memmap 与 PyTorch Buffer 共享存储的问题后，已实际执行一次不含测试集的独立评估。Checkpoint Epoch 与分数复验一致；`valid_cross_repo.predictable` 共 12,653 个 Query，Recall@10=`0.5401090650`、NDCG@10=`0.2895998671`。结果保存在 `coder_full_single_valid_evaluation.json`，其中 `test_included=false`。

## 2. 已有评估能力与指标定义

Issue 候选文件每行包含一个 `target_issue_id`，即每个 Query 恰好一个正样本。候选由预测时刻仍有效的 Issue 组成，训练历史已解决 Issue 被排除，目标必须存在于候选中，最大候选数为 500。

现有评估器支持 `K = 5, 10, 20` 的：

- Recall@K；
- Hit Rate@K（字段名 `hit@K`）；
- NDCG@K；
- MRR@K。

在当前单正样本协议下，Recall@K 与 Hit Rate@K 数值必然相等；二者并非两个独立信息量的指标。NDCG 使用 `1/log2(rank+1)`，MRR 使用首个相关结果的倒数排名。新增通用汇总器也支持多正样本，明确报告 Query 数、可评估 Query 数、正样本数、无正样本 Query 数以及候选规模统计。

训练日志只展示 NDCG@10，是因为模型选择固定使用 `valid_cross_repo.parquet` 的 `predictable.ndcg@10`；这不代表系统只计算 NDCG@10。其余指标是辅助报告指标，不参与选模。

## 3. 分组与场景

- `all`：候选文件中的全部 Query。
- `predictable`：Developer 在训练图中有历史，可由图模型产生个性化向量。
- `unpredictable`：训练图未知 Developer，当前回退为并列零分并按 Issue ID 稳定排序。
- `warm_start` / `cold_start`：由候选文件冻结的 `is_cold_start` 字段定义；当前实现中与 predictable/unpredictable 对应。
- `cross_repository_user`：Developer 的训练历史覆盖多个 Repository；它不是 cross-repo 候选模式本身。
- `same_repo` / `cross_repo`：两套不同的冻结候选范围，不能混在同一主表。

所有模型必须在相同 split、candidate mode、group、候选文件 SHA-256 下比较。

## 4. 历史实验审计

| Run | Variant | 终态 | Trial | 最大 Epoch | 选择协议 | Best Epoch | Best valid NDCG@10 |
|---|---|---:|---:|---:|---|---:|---:|
| `lightgcn-v2-earlystop` | LightGCN | SUCCESS | 4 | 50 | valid/cross_repo/predictable | 15 | 0.0039120254 |
| `coder-structure-v1-earlystop` | LightGCN + GAT | SUCCESS | 8 | 50 | valid/cross_repo/predictable | 50 | 0.1577303099 |
| `coder-full-single-v1` | Full CODER | INCOMPLETE | 1 | 50 | valid/cross_repo/predictable | 50 | 0.2895998671 |

三者的 Dataset、Graph Version、Seed、训练划分和候选 SHA-256 一致；验证频率均为 5 Epoch，现代两个历史 Run 和 Full 配置均启用 patience=5 的 Early Stopping。主要不公平项是 Trial 预算分别为 4、8、1，且 Full 尚无测试指标和成功终态。

`lightgcn-v1` 是较早的 4 Trial 运行，缺少完整 Early Stopping/Best Epoch 元数据；主比较优先使用 `lightgcn-v2-earlystop`。

自动生成的当前测试表见 `coder_existing_comparison.json/.csv`。它把 Full 的缺失指标保留为 `null`，不会写成 0。

## 5. Repository 级评估结论

训练数据可以从 Developer–Issue 解决记录派生 Developer–Repository 边，并带有时间戳；这足以训练辅助 Repository 分支，但当前没有独立冻结的 Repository Query、候选集合和协议 Manifest。

旧函数把 Issue 候选映射并去重为 Repository 候选，同时把该 Issue 的 Repository 当作真值。这只是 Issue 任务的投影代理，不是独立 Repository 推荐协议。新正式输出将 Repository 标记为 `UNAVAILABLE_INDEPENDENT_PROTOCOL`，不再把该代理值放进主表。

后续可建立独立版本：按交互时刻生成 Developer–Repository Query，以该时刻可见 Repository 为候选，排除训练历史已交互 Repository，冻结 valid/test 文件、正样本、时间戳、候选哈希和 Manifest。该协议必须单独版本化，不能反向修改现有测试真值。

## 6. 时间可见性风险

当前 GAT 结构图读取完整 `issue_repo_edges.parquet` 和 creation-snapshot 标签边，不按训练截止时间过滤。实测：

- 训练截止时间：2021-01-01；
- 截止时间后创建 Issue：19,882；
- 相关 Repository–Issue 边：19,882；
- 相关 creation Issue–Label 边：22,732；
- 这些节点与边参与训练阶段 GAT 消息传播；
- 未使用 resolver、关闭结果或未来结果性标签。

因此当前结果属于“固定全局 creation-structure 可见、交互边严格训练期可见”的假设，不满足严格时序结构可见性。它可能高估 GAT 与 Full CODER 的效果。

建议新增独立 Graph Version，例如 `gfibot-m0-v1.1-structure-traincut-20210101-v1`：只保留训练截止前已创建 Issue 的 Repository/Label 结构边，重新生成并冻结 Manifest 与哈希。不得覆盖 `gfibot-m0-v1.1`。Structure 和 Full 必须在新图上重训；为形成单一严格主表，LightGCN 也应在新实验版本下重新登记或重跑，而不是把不同 Graph Version 直接混表。

## 7. 当前可比性结论

历史 LightGCN v2 与 Structure 可直接比较同一 `test_cross_repo.predictable` 协议。Full CODER 尚不能加入主表，原因是：

1. 只有单 Trial，预算不公平；
2. 正式 Run 没有 `_SUCCESS` 和测试指标；
3. GAT/Full 共享已知未来结构可见性风险；
4. 尚无独立 Repository 评估协议。

完成 8 Trial 搜索并冻结验证胜者后，才允许一次性查看测试结果。
