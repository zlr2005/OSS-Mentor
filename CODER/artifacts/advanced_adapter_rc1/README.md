# CODER + Advanced Adapter RC1

这是 C 冻结给 D 的正式 RC1 Adapter 最小 bundle。它批准用于 `shadow`，不批准直接进入
`serve`。

## PR 内文件

- `weights.pt`：23 维画像到 32 维 CODER 用户空间的残差 Adapter；
- `adapter_manifest.json`：输入顺序、结构、基础模型版本及训练输入哈希；
- `experiment_config.json`：RC1 获胜训练配置；
- `validation_selection.json`：冻结 validation 分组结果；
- `test_metrics.json`：胜者锁定后唯一一次冻结 test 结果；
- `release_manifest.json`：Adapter、基础 CODER、mapping 和候选缓存的大小与 SHA-256；
- `_SUCCESS`：值为 `adapter_manifest.json` 的 SHA-256。

## D 必须另外挂载

基础 `coder-full-search8-v1` 约 308 MB，不放进普通 Git PR。D 必须使用
`release_manifest.json` 指定的基础 CODER bundle、developer/issue mappings 和候选缓存，并在
启动时逐项校验版本、大小和 SHA-256。任何一项缺失或不一致都应返回
`503 model_unavailable`，不得静默回退 legacy ranking。

Adapter 路径应配置为：

```text
CODER/artifacts/advanced_adapter_rc1
```

加载时继续使用现有 `CODERScorer(..., profile_adapter_dir=...)`。在线 API 的 off/shadow/serve
开关、画像读取和双路日志属于 D 的接线范围，详见 `docs/advanced-adapter-rc1-d-handoff.md`。

接入前从仓库根目录运行完整哈希验收：

```powershell
$env:PYTHONPATH = 'CODER/src'
& '.\CODER\.venv\Scripts\python.exe' '.\CODER\scripts\verify_advanced_adapter_rc1.py'
```
