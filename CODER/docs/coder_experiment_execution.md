# CODER 系统实验执行指南（Windows PowerShell）

以下命令均从仓库根目录执行。不要复用已存在的成功 run-id，也不要把 Smoke/Probe 结果当正式实验。

## 1. 从已有最佳 Checkpoint 独立评估

先只评估验证集，不读取测试结果：

```powershell
& '.\CODER\.venv\Scripts\python.exe' `
  '.\CODER\scripts\evaluate_coder_checkpoint.py' `
  --config '.\CODER\configs\coder_full_single.toml' `
  --run-id 'coder-full-single-v1' `
  --output '.\data\reports\gfibot-m0-v1.1\coder-full-single-v1.valid.metrics.json'
```

该命令不训练、不改原 Run，并拒绝覆盖已有输出。只有在验证胜者已经冻结且不再据测试结果调参时，才增加 `--include-test`，并使用新的输出文件名：

```powershell
& '.\CODER\.venv\Scripts\python.exe' `
  '.\CODER\scripts\evaluate_coder_checkpoint.py' `
  --config '.\CODER\configs\coder_full_single.toml' `
  --run-id 'coder-full-single-v1' `
  --include-test `
  --output '.\data\reports\gfibot-m0-v1.1\coder-full-single-v1.final.metrics.json'
```

这不会把原本异常退出的 Run 改写为 `_SUCCESS`；输出保留 Checkpoint、Epoch、验证分数、参数、输入哈希和测试门禁信息。

## 2. 8 Trial 搜索 Preflight

```powershell
& '.\CODER\.venv\Scripts\python.exe' `
  '.\CODER\scripts\preflight_multigranular.py' `
  --config '.\CODER\configs\coder_full_search8.toml' `
  --variant coder_issue_repo `
  --output '.\CODER\docs\coder_full_search8_preflight.json'
```

当前实测结果：8 Trial、最多 400 Epoch、CPU、MiniLM 权重和 Embedding 缓存 READY、18 项冻结输入哈希匹配。磁盘尚有约 40.5 GiB。

已有 32 维单 Trial 用时约 91.6 分钟。若所有 Trial 都跑满，线性基准约 12.2 小时；其中一个 64 维 Trial 通常更慢，因此保守准备约 12–18 CPU 小时。Early Stopping 可能缩短时间，但不能预先承诺。Checkpoint 估算按“每 Trial 一个 best + latest/previous”计算，约需数 GiB；以实际 Preflight JSON 为准。

## 3. 启动 8 Trial 正式搜索

先选择全新 run-id，例如：

```powershell
& '.\CODER\.venv\Scripts\python.exe' `
  '.\CODER\scripts\train_multigranular.py' `
  --config '.\CODER\configs\coder_full_search8.toml' `
  --variant coder_issue_repo `
  --run-id 'coder-full-search8-v1' `
  --execute-training
```

本指南生成过程没有执行该命令。

## 4. 查看进度

```powershell
Get-Content `
  '.\data\runs\gfibot-m0-v1.1\coder\coder-full-search8-v1\training.log' `
  -Tail 30 -Wait
```

另一个终端可查看结构化状态：

```powershell
Get-Content -Raw `
  '.\data\runs\gfibot-m0-v1.1\coder\coder-full-search8-v1\_RUNNING'
```

## 5. 安全中断与恢复

在训练终端按一次 `Ctrl+C`。等待日志显示安全 Batch 边界和最后有效 Checkpoint 后再关机；不要连续按两次。

恢复必须使用相同配置和 run-id：

```powershell
& '.\CODER\.venv\Scripts\python.exe' `
  '.\CODER\scripts\train_multigranular.py' `
  --config '.\CODER\configs\coder_full_search8.toml' `
  --variant coder_issue_repo `
  --run-id 'coder-full-search8-v1' `
  --execute-training `
  --resume
```

恢复会校验数据、配置、搜索顺序、代码身份和文本缓存；不兼容时拒绝继续。

## 6. 生成协议一致的比较报告

```powershell
& '.\CODER\.venv\Scripts\python.exe' `
  '.\CODER\scripts\compare_coder_runs.py' `
  --run-dir '.\data\runs\gfibot-m0-v1.1\coder\lightgcn-v2-earlystop' `
  --run-dir '.\data\runs\gfibot-m0-v1.1\coder\coder-structure-v1-earlystop' `
  --run-dir '.\data\runs\gfibot-m0-v1.1\coder\coder-full-search8-v1' `
  --split test `
  --mode cross_repo `
  --group predictable `
  --cutoff 10 `
  --output-json '.\data\reports\gfibot-m0-v1.1\comparison.cross_repo.json' `
  --output-csv '.\data\reports\gfibot-m0-v1.1\comparison.cross_repo.csv'
```

工具按 Dataset、Graph Version、候选 SHA-256、split、mode、group 和选模协议生成 `protocol_id`。不成功、缺指标或协议不一致的 Run 不进入主比较组，缺失值保持为空。

## 7. 结果解释边界

- NDCG@10 仍是验证选模指标；Recall、HR、MRR 是辅助报告。
- 当前每 Query 一个正样本，因此 Recall@K 与 HR@K 相同。
- Repository 独立协议尚未冻结，正式输出不会报告 Issue→Repository 投影代理指标。
- 当前 GAT 图包含训练截止后的 creation-structure；严格时序论文结论应等待新 Graph Version 下的统一重训。
