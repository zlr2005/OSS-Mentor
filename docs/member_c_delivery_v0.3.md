# Member C 推荐算法 v0.3 交付说明

## 1. 交付结论

本分支已实现 `team_work_plan_v0.2.md` 中 Member C 的代码与制品范围：排序模型 v0.3、多样性重排、结构化解释、推荐快照、双轨评估、专用推荐页面、契约 fixture 和测试。

OAuth、登录推荐 HTTP 路由和 OpenAPI 的最终接入由 Member D 负责。C 分支提供传输无关的 session 推荐服务；D 平台集成分支已补上 `GET /api/v1/me/recommendations`、登录反馈上下文、静态推荐页、OpenAPI 和 PostgreSQL 推荐快照迁移。

## 2. C1 排序模型 v0.3

版本：`developer-task-match-v0.3`

硬门槛：

- 仅消费 `candidate_availability=available` 的任务；
- 排除不符合偏好语言、任务类型和声明操作系统的任务；
- 代码难度和搭建难度不得超过画像上限；
- 新人轨要求新人标签，且关键技能、平台或主要语言缺口不能越过门槛；
- `recommend()` 只接受 `DeveloperProfileV2`、返回 `RecommendationBatchV3`，skills 统一使用 casefold 键。

软评分采用可审计分量，最终仍保持 `0–100` 的内部匹配分，再映射为 `RecommendationItemV3.score` 的 `0–1`：

| 特征 | newcomer 最大分 | growth 最大分 |
|---|---:|---:|
| 语言偏好 | 8 | 8 |
| 任务类型偏好 | 8 | 8 |
| 技能覆盖 | 30 | 20 |
| 合理成长跨度 | — | 20 |
| Issue 清晰度 | 12 | 10 |
| 仓库活跃度 | 8 | 8 |
| Issue 新鲜度 | 8 | 8 |
| 新人标签 | 18 | — |
| 任务成长价值 | — | 10 |
| CONTRIBUTING 指南 | 8 | 8 |

新鲜度以候选验证时间与最近活动时间的差值计算，因此相同候选快照会得到稳定结果，不依赖运行时当前日期。

## 3. C2 多样性

`RecommendationService` 在负反馈降权后、截取 Top N 前执行多样性重排：

- 默认每个仓库最多 3 条；
- 优先补充尚未覆盖的任务类型；
- 多样性只改变位置，不突破硬门槛、不伪造匹配分；
- 保存 `raw_score`、`final_score`、`raw_rank`、`final_rank` 和 `diversity_reranked`；
- 候选不足时允许放宽仓库上限或类型约束，并输出 `diversity_*_relaxed` warning；
- 排名发生变化时增加 `diversity_rerank` 结构化原因。

## 4. C3 推荐解释

每个 `RecommendationItemV3` 包含结构化 `Reason`：

- 固定原因码；
- 中文用户标签；
- 可核验的特征证据；
- 与最终分数相同的 `0–1` 尺度 `score_delta`；
- `feature_version`。

当前原因覆盖语言、任务类型、技能覆盖、成长跨度、新人信号、仓库活跃度、Issue 新鲜度、贡献指南、Issue 清晰度、成长价值、负反馈和多样性重排。

`not_suitable` 反馈只从 `user:{user_id}:profile:{profile_key}` 上下文读取，在排序前降低 `0.25`；其他反馈状态只恢复展示，不改变排序。

## 5. C4 推荐快照

迁移：`db/sqlite/010_recommendation_runs.sql`

表：

- `recommendation_run`：批次、反馈上下文、轨道、匹配版本、画像哈希、候选哈希、全局 warning 和时间；
- `recommendation_run_item`：任务、原始/最终分数、原始/最终排名、重排标记、公开推荐 JSON、原因和 warning。

快照只保存排序所需哈希与公开推荐结果，不保存 GitHub 原始用户数据。实现位于 `storage/recommendations.py`。

## 6. C5 评估与交付物

### 双轨评估

评估数据来自 `data/oss_mentor_demo_v07.sqlite3` 的临时副本；原数据库未被修改。

| 轨道 | 推荐数 | P@5 | P@10 | 最大仓库占比 | 最大类型占比 | 仓库超限 | 失效泄漏率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| growth | 9 | 0.800 | 0.889 | 0.333 | 0.556 | 0 | 0.000 |
| newcomer | 4 | 1.000 | 1.000 | 0.750 | 0.750 | 0 | 0.000 |

产物：

- `data/reports/ranking_evaluation_v0.3.json`
- `data/reports/ranking_evaluation_newcomer_v0.3.json`
- `docs/ranking_evaluation_v0.3.md`
- `docs/ranking_evaluation_newcomer_v0.3.md`

评估同时保留 v0.1、v0.2、v0.3 指标和 v0.2→v0.3 Top 10 变化。

### 页面和契约

- `web/recommendations.html`
- `web/assets/recommendations.js`
- `web/assets/recommendations.css`
- `fixtures/contracts/v0.5/recommendations.json`

页面覆盖加载、空结果、未登录、无画像、错误、反馈、键盘焦点、响应式和减少动态效果状态。按计划，页面静态路由和登录推荐 API 均等待 D 接入，本分支不修改 `api.py`。

## 7. 给 Member D 的接入说明

建议新增：

```http
GET /api/v1/me/recommendations?limit=10
```

接入步骤：

1. 从 `oss_mentor_session` cookie 读取 session ID；
2. 初始化 `RecommendationService`，注入 candidate、profile、auth 和 recommendation storage；
3. 调用 `recommend_for_session(session_id=..., limit=...)`；
4. 将 `AuthenticationRequired` 映射为 401，`ProfileRequired` 映射为 404；
5. 响应包含 `run_id`、画像摘要、`feedback_context`、`warnings`、`count` 和 `[item.to_dict()]`；
6. 当前用户反馈接口必须从 session 重新生成 feedback context，不能信任客户端提交的 `user:*` 上下文；
7. 在 OpenAPI 中加入 `Reason.feature_version` 和新增原因码。
8. 在静态路由白名单注册 `/recommendations` 以及两个推荐页资源文件。

匿名自定义画像和公开演示画像可逐步迁移到 `RecommendationService.recommend_batch()`，以消除旧 API 的 `MatchResult` 响应结构。

## 8. 测试范围

新增或扩展测试覆盖：

- v0.3 可用性、操作系统和软评分分量；
- `DeveloperProfileV2`/`RecommendationItemV3` 契约；
- 大小写归一化；
- 负反馈在 Top N 前降权；
- 仓库上限、任务类型多样性、原始/最终位置；
- 推荐详情与候选排除；
- 真实 SQLite session 画像消费；
- 010 迁移和快照读写；
- 推荐 fixture；
- 推荐页面关键状态与静态资源。

## 9. 验收边界

代码实现可以由自动化测试验证，但当前人工标注集仍只有 `codex_pseudo` 一名标注来源，双人复标数量为 0，因此报告中的 `annotation_acceptance.passed` 会保持 `false`。这不是代码缺陷，最终数据验收仍需要团队至少两名真实成员参与，并完成至少 10 个任务的双人复标；不得用生成数据伪造该签字过程。
