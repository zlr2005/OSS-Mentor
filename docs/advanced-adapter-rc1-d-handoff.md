# Advanced Adapter RC1 → D Shadow 接入交接

## 唯一批准产物

D 只能接入：

```text
CODER/artifacts/advanced_adapter_rc1/
```

Adapter 版本为 `advanced-adapter-v1-round2-optimized`，权重 SHA-256 为
`6112db508fd1f14876c272624a5e8d4d711d0d32a694fb02d3267f5f04091b78`。后续 supplement、
protected round2/round3/round4 均是研究实验，不批准接入。

## 外部基础产物

PR 只携带 12 KB Adapter，不携带约 308 MB 的基础 CODER。D 需挂载：

```text
data/models/gfibot-m0-v1.1/coder/coder-full-search8-v1/
data/midterm/normalized/gfibot-m0-v1.1/mappings/
data/midterm/platform/coder-full-search8-v1/candidates.npz
```

所有文件的大小和 SHA-256 位于
`CODER/artifacts/advanced_adapter_rc1/release_manifest.json`。启动时必须完整校验，缺失、版本
不一致、哈希不一致或维度不一致一律 fail-closed。

## D 已有接线参数

最新 main 已提供以下显式配置，本 PR 不重复修改 D 的 runtime mode 实现：

```text
--advanced-adapter-mode off|shadow|serve
--profile-adapter-dir CODER/artifacts/advanced_adapter_rc1
```

- 默认 `off`；
- 本次只批准 `shadow`；
- 虽然平台实现支持 `serve`，但 RC1 发布策略尚未批准它；部署配置必须保持 `shadow`；
- `shadow` 中用户仍看到 `coder_original`，后台同时记录 `coder_profile`；
- 不允许因为 Adapter 路径存在就自动启用。

推荐启动命令：

```powershell
$env:PYTHONPATH = 'CODER/src'
& '.\CODER\.venv\Scripts\python.exe' '.\CODER\scripts\verify_advanced_adapter_rc1.py'
& '.\CODER\.venv\Scripts\python.exe' -m oss_mentor serve-api `
  --coder-model-dir '.\data\models\gfibot-m0-v1.1\coder\coder-full-search8-v1' `
  --coder-mapping-dir '.\data\midterm\normalized\gfibot-m0-v1.1\mappings' `
  --coder-candidate-cache '.\data\midterm\platform\coder-full-search8-v1\candidates.npz' `
  --profile-adapter-dir '.\CODER\artifacts\advanced_adapter_rc1' `
  --advanced-adapter-mode shadow
```

## 画像输入

1. 从已认证 session 取得 `user_id`；
2. 从服务端身份事实取得可空的 verified `developer_key`，执行 `strip().casefold()`；
3. 调用 B 的 `get_enhanced_profile_features(user_id, developer_key)`；
4. 严格解析固定顺序的 23 维 `values`、`observed_mask`、`locked_mask`；
5. `developer_key=null` 正常走 cold-start；浏览器不得提交或覆盖 developer key；
6. 禁止用 `profile_key`、`user_id` 或平台 UUID 冒充训练 developer identity；
7. 全未知画像必须与 original 得到逐值相同的结果。

RC1 使用 `base_user_mode=population_mean`，因此 advanced-profile 路径不因命中训练 developer
mapping 而切换历史用户向量。`coder_original` 与 `coder_profile` 的唯一差异必须是 23 维画像
产生的 Adapter residual。

## Shadow 双路评分

对完全相同、顺序固定的候选集合：

```text
original_user = population_mean
profile_user  = population_mean + adapter(values, observed_mask)
```

两路必须复用同一基础模型、Issue embeddings、候选顺序和打分函数。候选缺失或缓存不完整时
返回 `503 model_unavailable`，不能切换 legacy ranking。

## 每次曝光至少记录

- `run_id`、`created_at`、`service_track`；
- `user_id`、`profile_key`、`profile_version`、`profile_hash`；
- `developer_key` 是否已验证，但不要把敏感映射暴露给浏览器；
- `candidate_hash` 和完整候选 key 顺序；
- 基础模型、dataset、graph、encoder、Adapter 版本与 SHA-256；
- `raw_score_original/profile`、`rank_original/profile`；
- `coverage_state`、已观测画像维度数、已见/未见身份；
- 延迟、异常、fail-closed 原因；
- 与 `run_id` 关联的曝光、点击、尝试和完成结果。

## D 验收清单

- off 模式输出与当前 CODER 完全一致；
- shadow 不改变用户可见排序；
- 同一画像和候选重复评分可复现；
- 全未知画像 original/profile 完全一致；
- 浏览器伪造任何身份字段不能改变服务端 developer identity；
- Adapter、CODER、mapping、候选缓存任一损坏均启动失败或返回 503；
- response 中 `model_version=coder-full-search8-v1`、`profile_mode`、`coverage_state` 与实际路径一致；
- shadow 日志足以按低活跃、低覆盖、未见开发者和同仓库候选分组复盘。

## 离线门禁结果

冻结 test 共 212 outcomes / 4,240 pairs：

| 指标 | coder_original | RC1 coder_profile |
|---|---:|---:|
| MRR | 0.169986 | 0.263006 |
| MRR@5 | 0.101730 | 0.205896 |
| NDCG@10 | 0.215845 | 0.322088 |
| Recall@5 | 0.216981 | 0.367925 |
| Recall@10 | 0.485849 | 0.608491 |
| Mean rank（越低越好） | 11.089623 | 8.990566 |
| Pair accuracy | 0.495519 | 0.600472 |
| 未见开发者 MRR | 0.164333 | 0.272054 |

这些结果批准 shadow，不代表已经批准直接 serve。
