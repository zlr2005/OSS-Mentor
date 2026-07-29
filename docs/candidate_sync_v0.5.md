# 候选任务同步与复核 v0.5

## 范围

成员 A 负责从已启用的公开 GitHub 仓库增量同步 Issue、复核任务当前
可用性、记录同步批次，并为推荐模块提供只包含 `available` 任务的候选集。

同步模块不会领取 Issue、发送评论、读取私有仓库，日志和同步结果中也不会
保存 Token、邮箱或 Authorization Header。

## 增量同步

每个仓库保存以下检查点：

- `candidate_sync_cursor`：最近已处理 Issue 的 `updated_at`；
- `candidate_sync_etag`：GitHub Issue 列表响应的 ETag；
- `candidate_sync_last_modified`：GitHub Issue 列表响应的 Last-Modified。

没有时间游标的稳定请求优先发送 `If-None-Match`，没有 ETag 时使用
`If-Modified-Since`。已有时间游标时改用 `since`，不复用其他 URL 对应的
ETag。GitHub 返回 304 时记录 `not_modified`，不重复写入候选任务。

增量列表按更新时间升序分页。达到单仓库上限后，仍处理完与边界记录具有
相同 `updated_at` 的全部记录，随后才推进游标，避免跨页或同秒更新被跳过。
候选表通过 `(repository_id, issue_number)` 唯一约束保证幂等。

单个仓库发生 403、404、429、5xx 或网络错误时只记录该仓库失败，不回滚
其他仓库。客户端对 429、5xx 和临时网络错误使用有上限的指数退避；主限流
耗尽后停止继续请求，并把尚未处理的仓库记为跳过。

## 可用性

固定状态如下：

```text
available
closed
assigned
linked_open_pr
locked
repository_inactive
temporarily_unverified
```

`availability` 只表示 GitHub 和仓库的客观可用状态；blocking label 等
推荐限制保留在 `candidate_eligibility`。只有同时满足
`availability=available` 和 `candidate_eligibility=eligible` 的任务才能
进入推荐候选集。GitHub 验证时间超过 24 小时后，任务自动变为
`temporarily_unverified`。任务详情读取陈旧任务时会进行一次轻量复核。

每个非可用状态都包含 `availability_reasons`，调用方不需要从布尔值猜测
排除原因。

## 同步记录

SQLite 迁移 `008_sync_runs.sql` 创建：

- `sync_run`：批次状态、时间、请求/成功/失败/跳过数量、限流和重试；
- `sync_repository_result`：单仓库结果、检查点和脱敏错误。

部分仓库失败而至少一个仓库成功时，批次状态为
`partially_succeeded`。

SQLite 同一时间只允许一个 `running` 批次。启动新批次前会把超过 6 小时
仍未结束的批次恢复为 `failed/abandoned_run`；活动批次冲突由 D 映射为
`409 state_conflict`。

## 联调数据

固定契约位于：

```text
fixtures/contracts/v0.5/candidates.json
fixtures/contracts/v0.5/sync_results.json
```

其中 ID 和时间均固定，内容只使用虚构公开仓库，不含真实身份或凭据。

## 提交给 D 的集成契约

### 服务调用

```python
CandidateService.sync_enabled_repositories(
    limit_per_repository=20,
    requested_by="scheduler",
)
CandidateService.refresh_stale_candidates(
    older_than_hours=24,
    requested_by="scheduler",
)
CandidateService.candidate_detail(task_candidate_id=101)
```

### `GET /api/v1/tasks/{task_candidate_id}`

- 请求示例：`GET /api/v1/tasks/101`
- 是否需要登录：否；只返回公开 Issue 数据。
- 成功响应示例：

```json
{
  "request_id": "req_fixture_001",
  "task_candidate_id": 101,
  "repository_full_name": "example/python-tool",
  "issue_number": 41,
  "title": "Add tests for configuration parsing",
  "body_text": "Add table-driven tests for invalid configuration values.",
  "html_url": "https://github.com/example/python-tool/issues/41",
  "availability": "available",
  "availability_reasons": [],
  "verified_at": "2026-07-29T12:30:00Z",
  "refreshed": false
}
```

- 错误码：不存在时 `404 not_found`；GitHub 复核限流时
  `429 rate_limited`；上游不可恢复错误时 `502 github_upstream_error`。
- 对已有客户端的影响：只新增 v0.5 任务详情字段，不重命名或删除旧字段。

### `GET /api/v1/status` 同步字段

建议由 D 将 `candidate_pool_status()` 的以下字段放入状态响应：

```json
{
  "request_id": "req_fixture_002",
  "candidate_total": 108,
  "recommendable_count": 100,
  "newcomer_count": 30,
  "availability_counts": {
    "available": 100,
    "closed": 2,
    "assigned": 1,
    "linked_open_pr": 1,
    "locked": 1,
    "repository_inactive": 1,
    "temporarily_unverified": 2
  },
  "latest_sync": {
    "sync_run_id": 7001,
    "run_type": "repository_sync",
    "status": "partially_succeeded"
  },
  "failed_repositories": ["example/go-service"],
  "average_github_requests_per_repository": 6.3333
}
```

- 请求示例：`GET /api/v1/status`
- 是否需要登录：否。
- 错误码：数据库未就绪时 `503 service_not_ready`。
- 同步批次冲突时：`409 state_conflict`。
- 对已有客户端的影响：只在现有状态响应中新增同步和候选池字段。

### PostgreSQL 等价变更说明

D 维护的 `db/postgres/001_initial.sql` 需要等价加入：

- `repository` 的 `candidate_sync_cursor`、`candidate_sync_etag`、
  `candidate_sync_last_modified`、`github_updated_at`；
- `task_candidate` 的 `candidate_availability`、
  `availability_reasons_json` 及可用性索引；
- `sync_run`、`sync_repository_result`、固定状态约束、索引和
  `ON DELETE` 行为。
- 只允许一个 `status=running` 的部分唯一索引，以及遗留批次恢复字段。

PostgreSQL 迁移不得保存 GitHub Token、Cookie、OAuth state 或完整原始响应。
D 接入 SQLite 模式时应向 `CandidateService` 注入
`storage.candidates.SQLiteCandidateStorage`，而不是继续向旧 facade 增加业务方法。

候选 fixture 中的 `difficulty`、`operating_systems`、`skill_requirements`、
`feature_evidence` 和 `task_feature_version` 是给 B/C 联调的 v0.5 字段；如果
B 调整特征名称或版本，需要按照契约变更流程由受影响成员共同确认。
