# 运行手册（Windows PowerShell）

所有命令默认从仓库根目录执行。除非命令明确包含 `--execute-training`，本手册的安装、检查、缓存和评估步骤都不会启动正式训练。

## 1. 环境

### 平台轻量环境

根项目要求 Python 3.11+，本身没有强制第三方依赖：

```powershell
py -3.11 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -e .
```

### CODER 环境

CODER 固定了 NumPy、PyArrow、PyTorch 等版本。已有 `CODER/.venv` 时直接复用：

```powershell
& '.\CODER\.venv\Scripts\python.exe' -m pip install -e .
& '.\CODER\.venv\Scripts\python.exe' -m pip install -e '.\CODER[test,encoders]'
```

文本编码器默认 `allow_download=false`。正式 revision 必须是：

```text
sentence-transformers/all-MiniLM-L6-v2
@c21050a7ef692090620a6d037dd736908f9c7cf6
```

不要改成浮动 `main`，也不要用其他 revision 覆盖已有缓存。

## 2. 环境变量

复制 [.env.example](../.env.example) 中需要的变量到当前 PowerShell 会话，不要提交真实值。

```powershell
$env:GITHUB_TOKEN = '<read-only-token>'
$env:GITHUB_OAUTH_CLIENT_ID = '<oauth-client-id>'
$env:GITHUB_OAUTH_CLIENT_SECRET = '<oauth-client-secret>'
$env:OSS_MENTOR_SESSION_SECRET = '<random-local-secret>'
$env:OSS_MENTOR_BASE_URL = 'http://127.0.0.1:8765'
```

只做无网络演示时不需要 GitHub token/OAuth。

## 3. 初始化平台

### 脱敏演示数据库

```powershell
& '.\.venv\Scripts\python.exe' -m oss_mentor init-demo
& '.\.venv\Scripts\python.exe' -m oss_mentor doctor
```

`init-demo` 只在目标数据库不存在时复制 `fixtures/oss_mentor_demo.sqlite3`，不会覆盖已有数据。

### 同步真实公共候选

先 dry-run，再显式允许网络：

```powershell
& '.\.venv\Scripts\python.exe' -m oss_mentor sync-candidates `
  --all-enabled `
  --limit-per-repo 20 `
  --dry-run

& '.\.venv\Scripts\python.exe' -m oss_mentor sync-candidates `
  --all-enabled `
  --limit-per-repo 20 `
  --output data/reports/all_enabled_sync.json `
  --allow-network

& '.\.venv\Scripts\python.exe' -m oss_mentor refresh-candidates `
  --all-enabled `
  --older-than-hours 24 `
  --limit 500 `
  --allow-network

& '.\.venv\Scripts\python.exe' -m oss_mentor extract-features
```

网络采集默认关闭；匿名 GitHub 限额只适合小样本验证。

## 4. 准备平台 CODER 候选缓存

候选 SQLite 或正文变化后，重新生成训练图外 Issue 向量：

```powershell
& '.\CODER\.venv\Scripts\python.exe' `
  '.\CODER\scripts\prepare_platform_coder_cache.py' `
  --database '.\data\oss_mentor.sqlite3' `
  --model-dir '.\data\models\gfibot-m0-v1.1\coder\coder-full-search8-v1' `
  --mapping-dir '.\data\midterm\normalized\gfibot-m0-v1.1\mappings' `
  --output '.\data\midterm\platform\coder-full-search8-v1\candidates.npz'
```

该脚本：

- 只加载本地固定 MiniLM revision，不联网；
- 只编码当前 `matchable_candidates()`；
- 先写同目录短临时 `.npz`，校验形状后 `os.replace()` 原子替换正式缓存；
- 同时写 `candidates.manifest.json`；
- 不修改模型包、冻结训练映射或 SQLite。

平台候选缓存不是训练文本缓存。训练文本缓存位于 `data/midterm/embeddings/gfibot-m0-v1.1/`，由 `prepare_text_embeddings.py` 分 Chunk 原子写入并可从中断继续。

## 5. 启动平台

### 默认 CODER 模式

```powershell
& '.\CODER\.venv\Scripts\python.exe' -m oss_mentor serve-api
```

启动前会检查：

```text
model_manifest.json
embeddings.npz
developer_mapping.parquet
issue_mapping.parquet
candidates.npz
```

缺少任何文件都会拒绝启动。浏览器访问 `http://127.0.0.1:8765/`。

### 自定义产物路径

```powershell
& '.\CODER\.venv\Scripts\python.exe' -m oss_mentor serve-api `
  --coder-model-dir '.\data\models\gfibot-m0-v1.1\coder\coder-full-search8-v1' `
  --coder-mapping-dir '.\data\midterm\normalized\gfibot-m0-v1.1\mappings' `
  --coder-candidate-cache '.\data\midterm\platform\coder-full-search8-v1\candidates.npz'
```

### 旧规则兼容演示

```powershell
& '.\.venv\Scripts\python.exe' -m oss_mentor serve-api --legacy-ranking
```

这是操作者显式选择的兼容模式。CODER 请求失败时系统不会自动走此路径。

## 6. CODER 训练前检查

```powershell
& '.\CODER\.venv\Scripts\python.exe' `
  '.\CODER\scripts\preflight_multigranular.py' `
  --config '.\CODER\configs\coder_full_search8.toml' `
  --variant coder_issue_repo `
  --output '.\CODER\docs\coder_full_search8_preflight.json'
```

Preflight 检查依赖、CPU/GPU、内存、磁盘、写权限、18 份冻结输入/候选哈希、映射连续性、搜索规模和文本缓存状态，不会训练。

正式训练命令只作为复现记录：

```powershell
& '.\CODER\.venv\Scripts\python.exe' `
  '.\CODER\scripts\train_multigranular.py' `
  --config '.\CODER\configs\coder_full_search8.toml' `
  --variant coder_issue_repo `
  --run-id '<new-run-id>' `
  --execute-training
```

必须使用全新 run-id；不要覆盖 `coder-full-search8-v1`。

## 7. 中断、恢复和提前停止

### 安全中断

在训练终端按一次 `Ctrl+C`，等待当前 `optimizer.step()` 完成并写入 checkpoint。正常安全取消会删除 `_RUNNING`、写 `_CANCELLED`，最后有效恢复点为：

```text
checkpoints/latest.pt
checkpoints/previous.pt
```

不要连续按两次，也不要优先使用任务管理器强杀。强退可能遗留 `_RUNNING`，但不会因此证明进程仍存活。

### 从中断恢复

必须使用完全相同的 config、variant 和 run-id：

```powershell
& '.\CODER\.venv\Scripts\python.exe' `
  '.\CODER\scripts\train_multigranular.py' `
  --config '.\CODER\configs\coder_full_search8.toml' `
  --variant coder_issue_repo `
  --run-id '<same-run-id>' `
  --execute-training `
  --resume
```

恢复逻辑会核对 PID、进程创建时间、命令行、实验身份、冻结哈希、搜索顺序和文本缓存；`latest` 损坏时回退 `previous`。旧 `_RUNNING` 会归档，不会直接删除。

### 已完成若干 Trial 后提前结束

可以按一次 `Ctrl+C` 安全停止，但这只产生 `_CANCELLED`，不能把未完成的搜索冒充 `_SUCCESS`。已完成 Trial 的 `trial-NNNN.best.pt` 可保留并独立评估；如果决定采用其中一个模型，应使用新的实验说明/模型版本，明确其搜索预算，而不是改写原 8 Trial run 的终态。

## 8. 观察训练状态

```powershell
Get-Content `
  '.\data\runs\gfibot-m0-v1.1\coder\<run-id>\training.log' `
  -Tail 30 -Wait

Get-Content -Raw `
  '.\data\runs\gfibot-m0-v1.1\coder\<run-id>\_RUNNING'
```

终态含义：

| 文件 | 含义 |
|---|---|
| `_SUCCESS` | 选模、模型冻结、正式评估和结果持久化均完成 |
| `_CANCELLED` | 用户安全取消，可按规则恢复 |
| `_FAILED` | 捕获到异常并完成失败收尾 |
| 仅 `_RUNNING` | 需要结合 PID/创建时间/命令行判断；可能是活跃进程，也可能是异常退出残留 |

只有日志停在 heartbeat 不能证明卡死；应同时检查进程、状态更新时间、checkpoint 修改时间和 Windows 事件记录。

## 9. 独立评估与比较

```powershell
& '.\CODER\.venv\Scripts\python.exe' `
  '.\CODER\scripts\evaluate_coder_checkpoint.py' `
  --config '.\CODER\configs\coder_full_search8.toml' `
  --run-id 'coder-full-search8-v1' `
  --output '.\data\reports\gfibot-m0-v1.1\coder-full-search8-v1.valid.metrics.json'
```

只有验证胜者已冻结且不再依据测试调参时，才增加 `--include-test`。比较工具必须同时匹配 dataset、graph、候选哈希、split、candidate mode 和 group。

## 10. 自动化测试

### 平台全量

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```

### CODER 全量

```powershell
& '.\CODER\.venv\Scripts\python.exe' -m pytest CODER\tests -q
```

### 平台接入定向测试

```powershell
$env:PYTHONPATH = 'src'
python -m unittest `
  tests.test_api `
  tests.test_recommendation_service `
  -v

& '.\CODER\.venv\Scripts\python.exe' -m pytest `
  CODER\tests\test_platform_inference.py `
  -q
```

测试不应启动正式训练，也不应删除已有 run、checkpoint、冻结输入或实验结果。

## 11. 故障排查

| 现象 | 检查 | 处理 |
|---|---|---|
| 启动时报 CODER artifact missing | 默认模型、映射、候选缓存五项 | 补齐路径或显式使用兼容模式 |
| API 返回 `model_unavailable` | 候选缓存是否覆盖所有过滤后任务 | 重新生成平台候选缓存 |
| 未知用户警告 | `developer_key` 是否命中冻结映射 | 这是当前预期回退；不能伪装为画像冷启动 |
| 文本缓存生成中断 | Chunk、manifest、行映射和固定 revision | 原命令重跑，只补缺失/损坏 Chunk |
| 训练目录只剩 `_RUNNING` | PID、创建时间、命令行、checkpoint、日志、事件记录 | 进程不存在时用原身份 `--resume` |
| Docker 服务启动失败 | 镜像是否包含 CODER 包/依赖/产物 | 当前 Docker 骨架未覆盖默认 CODER 部署 |
