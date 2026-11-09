# A 全量库存可用假设 v2：D 侧平台导入与推荐验收

日期：2026-11-09

基线：`58389dd9564ebf0ebfce97236149cbf65955e3ee`

状态：v2 平台导入、growth/newcomer 规则推荐和缺缓存 fail-closed 通过；CODER 全量候选缓存尚未生成；本轮不是在线库存复核。

## 输入与边界

- 交接包：`a_full_inventory_assumed_20261109_v2`
- 上游 ZIP SHA-256：`cfeabd8de41bdd41bc4089e06892c39ef7015a5684560bc34a5b34dbb91ede56`
- 包内全量：11,766 条候选、309 个仓库
- `recommendable_under_assumption.json`：6,364 条、272 个仓库
- 包 manifest SHA-256：`a251698be4fd7651c8be3f8dd43c93166a97177bd670db530e70b8f6a8a0fb16`
- v1 已被 v2 取代；v2 将 9 条最小描述不足记录移出推荐子集并保留在全量文件
- A 明确声明：`inventory_availability_assumed=true`、`online_reverified_this_export=false`
- 原运行库未被修改；验收使用复制出来的隔离 SQLite。

现有 `import-inventory` 接受 `issue_inventory.sqlite3`，而 A 的交付是 JSONL/JSON。本轮新增
`scripts/import_a_inventory_handoff.py`，只导入 `recommendable_under_assumption.json`，并：

1. 校验 manifest、文件大小、SHA-256、validation/report 状态；
2. 重新计算每条候选的 `content_hash`；
3. 保留 default/web `task_candidate_id` 映射到 `inventory_record_json`；
4. 保留 A 的 `task_types` 和 `skill_requirements`；
5. 接入 v2 的 labels、created_at、last_activity_at，并从 labels 重算 newcomer 信号；
6. 从交接正文重算难度与清晰度，并显式标记这是离线段；
7. 只在 `--assume-inventory-available` 下运行；
8. 不导入用户、会话、反馈或凭据。

## 导入结果

| 指标 | 结果 |
|---|---:|
| 交接可推荐候选 | 6,364 |
| 仓库 | 272 |
| default ID 映射保留 | 6,364 |
| web ID 映射保留 | 6,364 |
| 正文脱敏候选 | 1 |
| labels 字段可用 | 6,364 |
| created_at 可用 | 6,286 |
| last_activity_at 可用 | 6,364 |
| 源 newcomer 信号 | 32 |
| D 从 labels 重算的 newcomer 信号 | 32 |
| 导入隔离库 | 6,364 |
| 假设模式下通过当前平台 readiness | 6,364 |
| 严格时效模式下通过 | 0 |

v1 的 9 条差异已在 v2 中处理。A 确认这 9 条在旧 v0.4 路径下没有执行 expanded-content 最小正文
检查；v2 已将其移出推荐集合，没有降低门槛或补写正文：

- `bazelbuild/bazel#31700`
- `bazelbuild/bazel#31751`
- `bazelbuild/bazel#31943`
- `bazelbuild/bazel#31944`
- `bazelbuild/bazel#31980`
- `facebook/pyrefly#4988`
- `OpenAPITools/openapi-generator#25094`
- `spring-projects/spring-batch#5270`
- `trinodb/trino#28407`

## HTTP 与推荐结果

使用隔离运行库、真实登录会话和 `--assume-inventory-available --refresh-interval-seconds 0`
启动 `serve-api`：

- 未登录 `GET /api/v1/me/recommendations`：HTTP 401，`authentication_required`
- growth 登录画像：1,195 条匹配候选，HTTP 200 返回 10 条推荐
- newcomer 隔离验收画像：5 条匹配候选，HTTP 200 返回 5 条推荐
- 返回值包含 `availability=available`
- 正常推荐只保留实际数据边界 warning：
  - `inventory_availability_assumed`
  - `offline_inventory_handoff_not_online_reverified`

v2 已补齐 newcomer 所需 labels，且 D 重算的 32 个 newcomer 信号与 A 导出值一致。newcomer 的
平台硬过滤与推荐路径已通过；这仍不是在线 GitHub 状态复验。

## CODER 门控路径

未准备完整候选缓存时，默认 gated 路由返回 HTTP 503
`model_features_required`，没有静默回退到 legacy ranking。

外部 worker 模式返回 HTTP 200，但明确标记：

- `model_features_preparing`
- `profile_bootstrap_recommendations`
- `scoring_engine=profile_bootstrap_rules`
- `coverage_state=initializing`

同时生成 C 侧真实特征请求：

- context key：`bcbe441091cabe3bb9cef79f263b1938488af29dd82f24a8f1c2eb410e451de5`
- confirmed 画像：`platform-gated-e2e`
- profile version：`profile:f313195d7ef15aae5054`
- 匹配候选：1,195
- 候选 issue/content binding 集合 SHA-256：`be5d884c9e5515cac0fba9c30ff9099d8fbf8a46d13ec63cac85c5543bd89b51`
- v2 worker request SHA-256：`3857fadad4cb982dd1774cd337b8d266821688c5d04c49f458cad86976cb502e`
- worker request 大小：19,051,456 bytes

与 v1 相比，当前匹配的 1,195 个 issue key 和正文内容哈希集合相同，但数据库候选 ID、顺序和 v2
元数据变化，因此 C 必须使用 v2 request 重新发布缓存。已有 issue/content 编码结果可以在校验
哈希和模型版本后复用，不能直接沿用旧上下文目录。

C 已完成 v2 正式缓存发布：

- C 分支：`codex/c-gated-v2-cache`
- C commit：`29991221ec7bc30df9eebbf4c6d3b4e0b15178eb`
- cache generation：`17ce3fe58f664c7b954160f33e101600`
- context manifest SHA-256：`e0cfb04bf1de94fa64796bf86260425a2a53c466ef71abdee1597611624bf00e`
- prepared cache SHA-256：`1d65ca5593177892c16c6d9539dca22a775abf03519b81203ffcf07806f59c30`
- 最小交付 archive SHA-256：`b96085779b97f65cb8a2435bdee50ed0eb74df7015d3fcd7774a13cbab0a5962`

D 独立读回结果：

- `CachedGatedScorer.score`：1,195 个评分、1,195 个唯一 task ID
- `model_version=coder-gated-marginal_positive-s20261102`
- `profile_mode=prepared_profile_gated`
- 所有分数 `coverage_state=gated_prepared_context`
- 仅保留正常 warning `gated_scores_are_not_success_probabilities`

默认 gated HTTP 复验：

- 未登录：HTTP 401 `authentication_required`
- 登录 growth 画像请求 `limit=100`：HTTP 200
- 返回 100 条推荐和 100 条 rankings
- 所有 rankings 的 `scoring_engine=coder_gated`
- 所有 rankings 使用同一正式模型版本和 `prepared_profile_gated`
- 没有 bootstrap 或 legacy 静默回退

C 的映射审计说明：冻结 repository mapping 覆盖 4 个仓库、63 条候选；其余 55 个仓库、1,132
条候选使用“零协同/结构特征 + 冻结文本特征”，不是失败。历史模式为
`no_verified_resolved_history`，`history_complete=false`，但不是 cold start。该限制应继续保留
在验收解释中。

该 live cache 的 `expires_at` 为 `2026-11-09T13:22:13.423907+00:00`（北京时间约 21:22）。
D 的本次读回和 HTTP 验收发生在其有效期内；过期后必须重新生成 evidence 和 cache。

## 回归结果

- `tests.test_inventory_handoff`：7/7
- 库存、推荐服务、API 与推荐 API 定向回归：73/73
- Node 测试：23/23
- `compileall`、JSON 解析、`git diff --check`：通过
- 本机系统 Python 的全量 discover：1,027 项通过；另有 4 个 pytest 测试模块因系统 Python
  未安装 pytest 未能由 unittest 收集，使用 `gated-venv` 单独执行后 27/27 通过

## 后续门禁

1. B 基于 D 的最终 gated HTTP 结果完成最终确认。
2. 恢复严格测试前重新核验 GitHub 状态和有效期；当前数据不能作为线上实时库存。
3. live cache 过期后不得继续用于推荐，必须重新生成 evidence 和 cache。
