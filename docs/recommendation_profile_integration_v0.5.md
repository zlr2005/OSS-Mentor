# 推荐模块画像接入 v0.5

本分支完成后端算法侧的画像接入，HTTP 路由与 OpenAPI 仍由成员 D 负责。

## 已确定的契约

- 推荐服务只接受 `DeveloperProfileV2`，不接受历史 `dict` 画像。
- `DeveloperProfileV2.skills` 在构造时统一执行 `strip().casefold()`；归一化后同名但等级冲突的数据会被拒绝。
- `MatchResult.match_score` 在进入 `RecommendationItemV3` 时从 `0–100` 除以 100 转为 `0–1`。
- 匹配原因映射为固定原因码的 `Reason`，包含中文标签、可核验证据和统一为 `0–1` 尺度的 `score_delta`。
- 登录推荐的算法入口为 `RecommendationService.recommend_for_session()`。它按 `session -> user_id -> 当前用户画像` 解析，不接受客户端指定画像。
- 登录用户反馈上下文固定为 `user:{user_id}:profile:{profile_key}`，确保负反馈只作用于当前用户及其当前画像。
- `not_suitable` 在截取 Top N 前降低 0.25 分，并输出 `negative_feedback_penalty` 结构化原因；其他反馈状态只恢复展示，不改变排序。

## D 的 API 接入点

建议由 D 新增 `GET /api/v1/me/recommendations`：

1. 从 `oss_mentor_session` cookie 取 session ID；
2. 调用 `RecommendationService.recommend_for_session(session_id=..., limit=...)`；
3. 将 `AuthenticationRequired` 映射为 401，将 `ProfileRequired` 映射为 404 或产品约定状态；
4. 使用 `result.profile`、`result.feedback_context` 和 `[item.to_dict() for item in result.items]` 组装响应；
5. 记录反馈时必须由同一个 session 重新生成 `feedback_context`，不能信任客户端提交的用户上下文。

现有公开演示画像与匿名自定义画像接口可继续保留；后续迁移时应调用 `RecommendationService.recommend()`，不要在 API 层自行复制分数和原因映射。

## 测试覆盖

- `tests/test_contracts.py`：skill casefold 与冲突检测；
- `tests/test_recommendation_service.py`：`DeveloperProfileV2` 输入约束、`MatchResult` 到 `RecommendationItemV3`、0–1 分数、结构化原因、负反馈排序；
- `tests/test_recommendation_service.py`：真实 SQLite 用户、session 和画像绑定消费，验证 session 无法选择其他画像。

## 后续改进

- D 接入路由后增加 HTTP 级 401、无画像、limit 和 cookie 测试；
- 推荐快照迁移 `010_recommendation_runs.sql` 尚未进入本分支；
- 在数据量扩大前补充同仓库最多 3 条、任务类型多样性和反馈惩罚系数的离线评估；
- 将旧公开/匿名推荐路径逐步迁移到同一服务，消除当前 API 中遗留的 `MatchResult` 响应形状。
