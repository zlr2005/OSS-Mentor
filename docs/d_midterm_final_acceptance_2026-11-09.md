# 中期 D 侧最终运行与在线库存验收

日期：2026-11-09

基线：`0ac6303e61b3ce987afe719d81b5d77d1193e981`

本记录覆盖中期清单第 11 节 D1-D4 和 14.1 中 D 侧剩余联验项：在线库存复核、最新 main
严格模式 E2E、失败恢复和演示材料。

## 1. 在线库存复核

输入是 PR #44 的正式 v2 request：

- context：`bcbe441091cabe3bb9cef79f263b1938488af29dd82f24a8f1c2eb410e451de5`
- 候选：1,195
- 模型：`coder-gated-marginal_positive-s20261102`
- request SHA-256：`3857fadad4cb982dd1774cd337b8d266821688c5d04c49f458cad86976cb502e`

新增 GraphQL 批量复核工具：

- `scripts/midterm_d/verify_request_inventory_graphql.py`
- 每批读取 issue state、locked、assignees、labels、正文/标题、更新时间、comments 和
  cross-referenced PR state；
- 正文 hash 与正式 cache 不一致时标记 `content_changed_since_handoff`，不会继续使用旧缓存；
- 检查 repository archived/disabled/pushedAt；
- 更新隔离 SQLite 的严格 24 小时验证时间、availability 和 eligibility；
- 不依赖 `--assume-inventory-available`。

本轮实际复核 600 条 request 候选：

| 状态 | 数量 |
|---|---:|
| available | 501 |
| linked_open_pr | 35 |
| closed | 55 |
| temporarily_unverified | 6 |
| assigned | 3 |

最终严格模式、画像匹配、正文 hash 与正式 cache 全部一致的可用候选为 **502 条**：

- 仓库：49
- 语言：Python 502（当前 growth 验收画像限定）
- 新手标签：2
- 难度：1 级 472，2 级 30
- 任务类型：bug_fix 258、feature 121、other 126、testing 30、build_tooling 17、
  documentation 7、refactor 3
- 未缓存 eligible：0
- 正文变化 eligible：0
- GraphQL batch：30
- 错误：0

达到中期清单“至少 500 条可推荐任务”的目标。该结果对应一个真实画像的可推荐池，不是跨全部
6364 条候选的全局在线复核。

## 2. 最新 main 严格模式 E2E

服务使用最新 main、隔离 SQLite、正式 v2 cache 和严格库存模式启动：

```text
--refresh-interval-seconds 0
--gated-bundle <frozen bundle>
--gated-cache coder_gated_v2_cache_20261109
```

没有 `--assume-inventory-available`，没有 legacy ranking。

通过项：

- `/health`、`/`、`/profile`、`/recommendations`、`/status`：200
- OAuth start：200，`oauth_configured=true`，授权域名 github.com
- 登录 session `/api/v1/me`：200
- `/api/v1/me/profile`：200
- `/api/v1/me/profile/questionnaire`：200
- `/api/v1/me/profile/enhanced-features`：200
- `/api/v1/me/recommendations?limit=100`：200
- items / rankings：100 / 100
- 全部 `scoring_engine=coder_gated`
- 全部 `profile_mode=prepared_profile_gated`
- 全部 `coverage_state=gated_prepared_context`
- task detail：200
- recommendation run 所有者读取：200
- 另一用户读取同一 run：403 `insufficient_permission`
- feedback 与 run_id 关联：200
- logout 后原 session `/me`：401 `authentication_required`

真实 OAuth 回调仍沿用此前的真实验收记录；本次 E2E 使用隔离的已认证 session，不重复交换
GitHub OAuth code。

## 3. 重启和失败恢复

停止并重新启动同一服务后：

- `/api/v1/me`：200
- `/api/v1/me/recommendations?limit=100`：200
- 100/100 rankings 仍为 `coder_gated`
- cache 重新加载成功

使用空的 gated cache 启动：

- recommendations：503 `model_features_required`
- 没有 bootstrap 或 legacy 静默回退

## 4. 部署边界

- GitHub Actions 的 Docker build、gated runtime smoke、SQLite/PostgreSQL migration、
  Node、Web smoke 和 secret scan 已通过；
- 本机没有 Docker CLI，因此本轮不能在当前机器执行真实容器启动；
- 本机 PostgreSQL 14 客户端和服务监听存在，但没有可用的项目运行凭据，不能声明生产
  PostgreSQL 运行时已验证；
- 已完成的替代验证是：最新 main 独立进程启动、真实 cache 加载、严格 HTTP、重启恢复和缺缓存
  fail-closed。

PostgreSQL 生产运行和容器部署仍是发布环境门禁，不影响本轮 SQLite 中期演示闭环。

## 5. 复现命令

在线复核：

```powershell
$env:PYTHONPATH = '<repo>\src'
python scripts/midterm_d/verify_request_inventory_graphql.py `
  --database '<isolated.sqlite3>' `
  --request-json '<v2-request.json>' `
  --target 500 --batch-size 40 `
  --output '<refresh_report.json>' --allow-network
```

严格模式启动：

```powershell
python -m oss_mentor serve-api `
  --database '<isolated.sqlite3>' `
  --gated-bundle '<bundle>' `
  --gated-cache '<cache>' `
  --refresh-interval-seconds 0
```

## 6. 保留限制

- 当前 cache 仍是 live cache，`expires_at=2026-11-09T13:22:13.423907+00:00`，过期后必须重新生成；
- gated scores 是排序分数，不是成功概率；
- 1,132/1,195 条模型输入使用零协同/结构特征加冻结文本特征；
- `history_complete=false`，表示历史证据不完整，不是 cold start；
- 502 条在线可用候选属于当前 growth 验收画像，不代表全部语言和全部用户画像。
