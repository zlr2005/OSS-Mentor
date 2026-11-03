# Advanced Adapter v1 → D Shadow 接入说明

## 接入状态

C 已冻结并验证 `advanced-adapter-v1`。该 bundle **只批准用于 shadow**，尚未批准用于 serve。

## D 需要使用的文件

Adapter 运行最小集：

```text
adapter/
  weights.pt
  adapter_manifest.json
  _SUCCESS
```

验收与追溯文件：

```text
evaluation/
  evaluation_report.json
  paired_outcomes.jsonl
  _SUCCESS
training/
  audit_report.json
  failures.jsonl
  split_manifest.json
  training_metrics.json
```

必须同时配置与 manifest 一致的基础模型 `coder-full-search8-v1`、developer/issue mapping 和
完整平台候选向量缓存。任何一个文件缺失或哈希、维度、基础模型版本不一致时启动失败；不得
静默切回 legacy ranking。

## Shadow 行为

1. 只处理 `service_track=growth` 且已有确认版 23 维增强画像的登录用户。
2. 从服务端认证事实取得 `developer_key`；浏览器不得提交或覆盖该字段。
3. 从 B 的画像服务读取完整 `EnhancedProfileFeatures`，不能把普通 `DeveloperProfileV2` 当成
   23 维画像传入 C。
4. 对同一个冻结候选集调用 C 的成对评分：
   - original：CODER 训练用户均值；
   - profile：同一均值向量加 Adapter 画像残差。
5. 用户仍看到 original 排名；profile 结果只写入 shadow 日志。
6. 全未知画像必须得到与 original 完全相同的原始分数和排名。

## 每次曝光至少保存

- `run_id`、`created_at`、`service_track`；
- `user_id`、`profile_key`、`profile_version`、`profile_hash`；
- `candidate_hash` 及完整候选 key 顺序；
- 基础模型、dataset、graph、encoder、Adapter 版本和 SHA-256；
- 每个候选的 `raw_score_original`、`raw_score_profile`、`rank_original`、`rank_profile`；
- `coverage_state`、画像已观测维度数及覆盖分组；
- 是否为已验证 developer identity；
- 延迟、异常类型和 fail-closed 结果；
- 后续反馈、尝试和完成结果（与 `run_id` 关联）。

## 推荐配置接口

`serve-api` 需要新增显式配置，建议使用：

```text
--advanced-adapter-mode off|shadow|serve
--profile-adapter-dir <adapter directory>
```

默认必须为 `off`。`shadow` 必须要求 `profile_adapter_dir` 存在且完整；`serve` 应有独立发布
门禁，不能因为提供了权重目录就自动开启。

## Shadow 重点告警

- 训练期低活跃（1–2 outcome）用户组；
- 同仓库候选间的排序变化；
- 新仓库与低覆盖画像；
- profile 排名大幅变化、非有限分数、候选数量不一致；
- profile 路径额外延迟及模型不可用率；
- 正向收益是否过度集中在少量开发者。

## D 接入验收

- off：不加载 Adapter，行为与当前主线一致；
- shadow：用户仍看到 original，后台完整保存双路结果；
- serve：本轮不得启用；
- Adapter、基础模型或候选缓存任一损坏时返回 `503 model_unavailable`；
- 浏览器伪造 `developer_key`、`profile_key` 或 `user_id` 不能改变认证身份；
- 同一候选、画像和版本重复评分结果可复现。
