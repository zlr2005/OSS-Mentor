# A 全量库存可用假设：D 侧平台导入与推荐验收

日期：2026-11-09

基线：`58389dd9564ebf0ebfce97236149cbf65955e3ee`

状态：平台导入与规则推荐通过；CODER 全量候选缓存尚未生成；本轮不是在线库存复核。

## 输入与边界

- 交接包：`a_full_inventory_assumed_20261109_v1`
- 包内全量：11,766 条候选、309 个仓库
- `recommendable_under_assumption.json`：6,373 条
- 包 manifest SHA-256：`6925a79294b9710b0a10e57210c747417b001f420fca8df6c6b12f9cf56a66f4`
- A 明确声明：`inventory_availability_assumed=true`、`online_reverified_this_export=false`
- 原运行库未被修改；验收使用复制出来的隔离 SQLite。

现有 `import-inventory` 接受 `issue_inventory.sqlite3`，而 A 的交付是 JSONL/JSON。本轮新增
`scripts/import_a_inventory_handoff.py`，只导入 `recommendable_under_assumption.json`，并：

1. 校验 manifest、文件大小、SHA-256、validation/report 状态；
2. 重新计算每条候选的 `content_hash`；
3. 保留 default/web `task_candidate_id` 映射到 `inventory_record_json`；
4. 保留 A 的 `task_types` 和 `skill_requirements`；
5. 从交接正文重算难度与清晰度，并显式标记这是离线段；
6. 只在 `--assume-inventory-available` 下运行；
7. 不导入用户、会话、反馈或凭据。

## 导入结果

| 指标 | 结果 |
|---|---:|
| 交接可推荐候选 | 6,373 |
| 仓库 | 273 |
| default ID 映射保留 | 6,373 |
| web ID 映射保留 | 6,373 |
| 正文脱敏候选 | 1 |
| 导入隔离库 | 6,373 |
| 假设模式下通过当前平台 readiness | 6,364 |
| 严格时效模式下通过 | 0 |

6,364 条与 A 报告相差 9 条。9 条都因当前 `has_minimum_description` 门槛仍被拦截，不能为了
凑数绕过：

- `bazelbuild/bazel#31700`
- `bazelbuild/bazel#31751`
- `bazelbuild/bazel#31943`
- `bazelbuild/bazel#31944`
- `bazelbuild/bazel#31980`
- `facebook/pyrefly#4988`
- `OpenAPITools/openapi-generator#25094`
- `spring-projects/spring-batch#5270`
- `trinodb/trino#28407`

需要 A 修正这 9 条的推荐子集，或提供它们在源库中通过 readiness 的完整证据。D 不把正文过短
的任务强行标成已通过。

## HTTP 与推荐结果

使用隔离运行库、真实登录会话和 `--assume-inventory-available --refresh-interval-seconds 0`
启动 `serve-api`：

- 未登录 `GET /api/v1/me/recommendations`：HTTP 401，`authentication_required`
- 已登录：HTTP 200，返回 10 条推荐
- 返回值包含 `availability=available`
- 每条推荐明确包含：
  - `inventory_availability_assumed`
  - `offline_inventory_handoff_not_online_reverified`
  - `handoff_omits_issue_labels_and_activity`
  - `newcomer_signal_not_reconstructable`

说明：交接包没有 labels、created_at 和活动时间，因此本轮只能验证 growth 画像与已知任务类型/技能
要求；不能把结果称为 newcomer 路径已完整验证。

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
- 候选输入 SHA-256：`ebad1ecbe7b045584aa83ac0506efc0b356fb651f5f462a15ef1b5293e11e3d7`
- worker request SHA-256：`8752e0288d510f4bd3cbeb8d14fc18df229f5854938ea07dcf9c2dd8dba58872`
- worker request 大小：18,377,023 bytes

使用现有真实账户证据和冻结运行资产启动完整编码后，CPU 编码耗时过长，按用户中断结束，未发布
缓存。因此当前正式 gated 推荐仍以 fail-closed 为准，不能宣称 6,364 条候选均已被 CODER 评分。

## 回归结果

- `tests.test_inventory_handoff`：6/6
- 库存、推荐服务、API 与推荐 API 定向回归：72/72
- Node 测试：23/23
- `compileall`、JSON 解析、`git diff --check`：通过
- 本机系统 Python 的全量 discover：1,027 项通过；另有 4 个 pytest 测试模块因系统 Python
  未安装 pytest 未能由 unittest 收集，使用 `gated-venv` 单独执行后 27/27 通过

## 后续门禁

1. A 核对并修正 9 条最小描述不足但仍被列入可推荐的记录。
2. C 基于上述 1,195 条匹配候选完成特征编码、映射覆盖和缓存发布。
3. D 在缓存发布后重新跑 gated HTTP，确认返回 `scoring_engine=coder_gated`。
4. 恢复严格测试前重新核验 GitHub 状态和有效期；当前数据不能作为线上实时库存。
