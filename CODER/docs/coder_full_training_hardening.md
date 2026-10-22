# `coder_issue_repo` 联合训练工程加固与操作指南

## 1. 结论与边界

本轮保留 `gfibot-m0-v1.1`、`fixed_train_graph`、18 份冻结输入/候选哈希和验证集选参/测试集门禁，没有重建或修改 Parquet、映射、候选集及旧 run-id，也没有启动正式长训练。

当前已实现：

- 独立 Preflight：依赖、设备、内存、磁盘、写权限、冻结哈希、映射连续性、搜索规模、缓存状态、资源估算和时间可见性审计。
- 独立文本缓存准备入口；完整缓存直接 mmap 复用，不加载 Transformer。
- Issue 分块和 Segment 分批双重上限；每个 Chunk 原子落盘，可在中断后只补缺失块。
- 原子 `latest` / `previous` 恢复点和 Trial `best` checkpoint。
- Batch 边界恢复：模型可训练参数、Adam、RNG、Batch 顺序/负采样确定性重建、累计 loss、Epoch/Trial/验证历史均恢复。
- `--resume` 运行目录保护；拒绝 `_SUCCESS`、真实活跃进程和实验身份不匹配；旧 `_RUNNING` 被归档而非删除。
- 第一次 `Ctrl+C` 只请求停止；完成当前 `optimizer.step()` 后写恢复点并退出。第二次 `Ctrl+C` 为强制退出，可能只能回退到上一恢复点。
- 每 Batch 记录耗时、RSS、可用物理内存；CUDA 时记录 allocated/peak allocated。

当前机器尚不具备真实完整模型 Smoke 条件：虚拟环境没有 `transformers`，固定 revision 的 MiniLM 权重和 Issue Embedding 缓存均不存在。代码因此没有擅自联网或生成伪特征。完整 Preflight 原始报告见 `coder_full_preflight.json`。

## 2. 配置选择

`coder_full_single.toml` 只产生一个 Trial：32 维、Issue LightGCN 4 层、Repository LightGCN 2 层、GAT 2 层、学习率 0.001、项目损失权重 0.1、MiniLM 固定 revision、50 Epoch。选择依据是现有搜索空间中的保守组合：维度和 Issue 层数延续现有 LightGCN 设置，GAT 使用较小层数，学习率和项目损失权重取较保守值。它不是“已验证最优参数”。

`coder_full_smoke.toml` 使用同一模型路径，但只运行 1 Epoch、最多 2 个 Batch，并每 Batch 保存 checkpoint。其指标只能用于工程检查，不能作为正式结果。

优先 MiniLM 是资源建议：其常见隐藏维度为 384，而 CodeBERT-base 常见为 768；实际维度仍由固定 revision 的本地 `config.json` 校验。两者都缺失时，不自动下载，也不修改不可变 revision。

## 3. PowerShell 命令

以下命令均在仓库根目录运行。

### 3.1 训练前 Preflight（已实现并实测）

```powershell
CODER/.venv/Scripts/python.exe CODER/scripts/preflight_multigranular.py `
  --config CODER/configs/coder_full_single.toml `
  --variant coder_issue_repo `
  --output CODER/docs/coder_full_preflight.json
```

Preflight 返回非零表示条件未满足；它不启动训练。原 32 Trial 配置也可检查：

```powershell
CODER/.venv/Scripts/python.exe CODER/scripts/preflight_multigranular.py `
  --config CODER/configs/coder_multigranular.toml `
  --variant coder_issue_repo
```

### 3.2 准备本地编码器（用户显式执行，未在本轮自动执行）

先安装已锁定的可选依赖：

```powershell
CODER/.venv/Scripts/python.exe -m pip install -e "CODER[encoders]"
```

若本地 Hugging Face 缓存没有权重，可在允许联网时显式下载固定 revision：

```powershell
CODER/.venv/Scripts/python.exe -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='sentence-transformers/all-MiniLM-L6-v2', revision='c21050a7ef692090620a6d037dd736908f9c7cf6')"
```

配置仍保持 `allow_download = false`，训练和缓存准备过程不会隐式联网。

### 3.3 首次文本缓存准备及中断恢复（已实现；当前因缺依赖/权重未实跑）

```powershell
CODER/.venv/Scripts/python.exe CODER/scripts/prepare_text_embeddings.py `
  --config CODER/configs/coder_full_single.toml `
  --encoder sentence-transformers/all-MiniLM-L6-v2
```

中断后重复同一条命令即可。已校验 Chunk 被复用，损坏/缺失 Chunk 会重算。只查看状态：

```powershell
CODER/.venv/Scripts/python.exe CODER/scripts/prepare_text_embeddings.py `
  --config CODER/configs/coder_full_single.toml `
  --encoder sentence-transformers/all-MiniLM-L6-v2 `
  --check-only
```

状态含义：`CACHE_READY` 可直接训练；`CACHE_PARTIAL` 可续作；`CACHE_MISSING` 尚未编码；`CACHE_INVALID` 与配置不兼容或损坏。

### 3.4 真实数据 Smoke（接口已实现；需缓存就绪后执行）

先执行严格单 Batch Forward/Backward 探针（会记录真实 RSS/耗时并完成一次
`optimizer.step()`，不会沿用为正式结果）。Probe 在首次验证和原子最佳
Checkpoint 成功后写入 `probe_result.json` 与 `_SUCCESS`，并明确跳过正式模型导出、
重复最佳模型验证及完整 valid/test 评估：

```powershell
CODER/.venv/Scripts/python.exe CODER/scripts/train_multigranular.py `
  --config CODER/configs/coder_full_smoke.toml `
  --variant coder_issue_repo `
  --run-id coder-full-probe-v1 `
  --execute-training `
  --probe-batch
```

再执行 2 Batch Smoke：

```powershell
CODER/.venv/Scripts/python.exe CODER/scripts/train_multigranular.py `
  --config CODER/configs/coder_full_smoke.toml `
  --variant coder_issue_repo `
  --run-id coder-full-smoke-v1 `
  --execute-training
```

中断后用相同 run-id 恢复：

```powershell
CODER/.venv/Scripts/python.exe CODER/scripts/train_multigranular.py `
  --config CODER/configs/coder_full_smoke.toml `
  --variant coder_issue_repo `
  --run-id coder-full-smoke-v1 `
  --execute-training `
  --resume
```

### 3.5 正式单 Trial 联合训练（已实现；本轮禁止并未执行）

```powershell
CODER/.venv/Scripts/python.exe CODER/scripts/train_multigranular.py `
  --config CODER/configs/coder_full_single.toml `
  --variant coder_issue_repo `
  --run-id coder-full-single-v1 `
  --execute-training
```

恢复命令只增加 `--resume`。必须保持原配置、run-id、数据、缓存、Seed 和训练参数完全一致；否则实验身份哈希不匹配并拒绝恢复。

### 3.6 安全暂停与查看进度

前台按一次 `Ctrl+C`，等待终端输出 `last valid checkpoint=.../checkpoints/latest.pt` 后再关机。不要连续按两次或用任务管理器强杀。

```powershell
Get-Content data/runs/gfibot-m0-v1.1/coder/coder-full-single-v1/training.log -Wait
Get-Content data/runs/gfibot-m0-v1.1/coder/coder-full-single-v1/_RUNNING
Get-ChildItem data/runs/gfibot-m0-v1.1/coder/coder-full-single-v1/checkpoints
```

`_RUNNING` 给出 Trial/Epoch/Batch、loss、Batch 耗时、RSS、可用内存、CUDA 内存和 checkpoint 位置。终态为 `_SUCCESS`、`_CANCELLED` 或 `_FAILED`；历史终态不会在恢复时删除。

## 4. Checkpoint 内容和精确恢复语义

`latest.pt` / `previous.pt` 保存：

- 所有可训练参数；静态稀疏邻接、结构边、行对齐文本 Embedding 和 Issue→Repository 映射不重复保存，而是从已哈希冻结输入/缓存重建。
- Adam `state_dict`（含步数和动量）及预留 Scheduler 状态。
- Trial、Epoch、下一个 Batch、累计 Issue/Repository loss 和计数。
- 已完成 Trial、训练/验证历史、当前/全局最佳验证分数和最佳 checkpoint 引用。
- Python、NumPy、PyTorch CPU 及可用时的 CUDA RNG。
- 完整实验身份：数据/图/模型版本、Variant、网格顺序、配置哈希、18 项输入哈希、缓存哈希/形状、Seed、Batch Size、Epoch、Weight Decay。

负采样和 Shuffle 由 `seed + epoch` 重建；恢复时跳过已完成 Batch，再恢复 checkpoint 后的 RNG，因此 CPU 确定性测试中连续训练与中断恢复后的参数和 loss 完全一致。恢复粒度是“最近成功提交的 Batch checkpoint”；不支持在 `backward()` 或 `optimizer.step()` 中间恢复。

## 5. 资源与计算审计

正式单 Trial Preflight 统计：18,360 Developer、148,096 Issue、97 Repository、6,768 Label、123,481 条训练交互。工程估算约为：参数 41.5 MiB、Adam 动量 83.0 MiB、静态图 30.4 MiB、按层展开的激活上界 253.9 MiB、合计工作集约 408.8 MiB；三个主要 checkpoint 约 373.5 MiB。该估算不包含 Python/Arrow/评测候选、Transformer 及底层算子临时空间，不能代替真实 Batch 峰值。当前机器总内存约 15.4 GiB，但 Preflight 时可用内存波动且曾低于 2 GiB，因此会标记 `HIGH` 并阻止 `READY`；正式 Smoke 前应关闭占内存程序并重新检查。

完整 LightGCN、GAT、Repository LightGCN 和 MLP 仍需在每个 Batch 重新执行；由于每个 `optimizer.step()` 都改变参数，不能跨 Batch 缓存带梯度的传播结果，`detach()` 也会错误切断联合梯度。本轮做了一项低风险去重：同一 Batch 的 Issue 融合和 Repository BPR 共享一次 Repository LightGCN 传播，不再重复计算同一图；其余数学定义不变。同时减少静态 checkpoint 复制、限制文本 Segment batch，并增加真实峰值监控。若实际 Smoke 显示不可接受的开销，再单独设计并数值验证梯度累积/全图计算重构。

## 6. 时间可见性审计

当前结构图读取完整 `issue_repo_edges.parquet` 和 `creation_snapshot` 标签边。训练截止时间为 2021-01-01；统计发现：

- 截止时间之后创建的 Issue：19,882；
- 进入结构图的对应 Repository–Issue 边：19,882；
- 进入结构图的对应创建时 Issue–Label 边：22,732；
- 这些节点参与训练期 GAT 消息传播。

没有使用关闭快照标签或结果性字段，因此不是直接标签泄漏；但全局结构可见性与严格时间顺序推荐不一致，属于明确风险。本轮为保持既有评测兼容，没有偷偷改图。建议后续新建 `graph_version`，按每个训练/预测截止时间过滤结构节点和边，再与当前协议并行复核；正式论文/报告应披露当前风险。

## 7. 测试证据与剩余风险

已通过完整自动化测试套件（48 项），新增覆盖：模型/Adam/RNG 恢复、`latest` 损坏回退 `previous`、Batch Shuffle/负采样确定性、中断续训与连续训练（含 Dropout RNG）完全一致、跨 Epoch 恢复不重复历史、Chunk 中断续作、损坏行映射识别、活跃/残留运行状态与 `_SUCCESS` 防护、完整模型四个可训练分支和两项联合损失梯度、冻结文本无梯度。真实冻结输入 dry-run 的 18 项 SHA-256 门禁也已通过。

尚未完成的验收项只有依赖外部资源的真实 `coder_issue_repo` 单 Batch Forward/Backward、真实 RSS 峰值和真实 checkpoint/resume Smoke。必须先准备固定 MiniLM 权重并完成 `CACHE_READY`，随后执行 3.4；在此之前 Preflight 正确返回 `NOT_READY`。
