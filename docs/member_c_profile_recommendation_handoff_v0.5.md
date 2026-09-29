# Member C：画像到推荐模块集成答复 v0.5

## 1. 结论摘要

| 问题 | 结论 |
|---|---|
| 推荐模块是否接受 `DeveloperProfileV2`？ | **是。** `RecommendationService.recommend()` 和会话推荐入口只接受 `DeveloperProfileV2`，返回 `RecommendationBatchV3`。传入旧字典会抛出 `TypeError`。 |
| skills 是否统一为 casefold 键？ | **是。** `DeveloperProfileV2` 在构造时将技能名统一为 `casefold()`，匹配适配层和需求查找继续使用 casefold 键。`Python` 与 `python` 会被视为同一技能。 |
| `MatchResult` 如何映射到 `RecommendationItemV3`？ | 通过 `recommendation_item_from_match()` 映射；内部 `0–100` 分数除以 100，形成公共契约的 `0–1` 分数，并将评分分量转换成固定原因码的结构化 `Reason`。 |
| 是否新增登录用户推荐 HTTP 接口？ | **Member C 未直接注册 HTTP 路由。** 已提供传输无关的 `recommend_for_session()`；建议 Member D 注册 `GET /api/v1/me/recommendations`，由 session cookie 决定用户，不能接受客户端指定用户 ID。 |
| 负反馈是否使用当前用户画像和反馈上下文？ | **是。** 上下文固定为 `user:{user_id}:profile:{profile_key}`。只读取当前登录用户、当前画像下的反馈，并在 Top N 和多样性重排之前应用 `not_suitable` 降权。 |
| 是否有契约和真实会话测试？ | **是。** 分支 `feat/v05-ranking-v3`，核心实现 commit `f2af66d`；包含契约、大小写归一化、负反馈和真实 SQLite session 画像消费测试。 |

## 2. `DeveloperProfileV2` 输入契约

推荐服务的公共边界为：

```python
def recommend(
    *,
    profile: DeveloperProfileV2,
    limit: int = 10,
    feedback_context: str | None = None,
    excluded_candidate_ids: tuple[int, ...] = (),
) -> RecommendationBatchV3:
    ...
```

实现位置：

- `src/oss_mentor/contracts.py`：`DeveloperProfileV2`、`RecommendationItemV3` 和 `RecommendationBatchV3`；
- `src/oss_mentor/services/recommendation_service.py`：契约校验与推荐服务；
- `profile_for_matching()`：将共享契约适配为匹配引擎所需的内部结构。

服务通过 `isinstance(profile, DeveloperProfileV2)` 执行严格边界检查，不继续接受历史字典画像。这可以防止 API 层与算法层分别维护一套含义不同的画像结构。

## 3. skills 的 casefold 归一化

`DeveloperProfileV2.__post_init__()` 会将所有技能键转换为 `casefold()` 结果，并检查归一化后的重复键：

```text
Python -> python
PYTHON -> python
Straße -> strasse
```

匹配层查找需求技能时同样执行：

```python
profile["skills"].get(name.casefold(), 0)
```

因此画像输入使用 `Python`、`python` 或其他大小写组合，不会产生不同的技能覆盖结果。同一输入中的等价键若等级相同会合并；若等级不同，契约会拒绝该输入，避免静默覆盖等级。

## 4. `MatchResult` 到 `RecommendationItemV3` 的映射

映射函数为 `recommendation_item_from_match()`，规则如下：

| `MatchResult` / 候选字段 | `RecommendationItemV3` 字段 | 规则 |
|---|---|---|
| `match_score` | `score` | `round(match_score / 100.0, 4)`，从 `0–100` 转为 `0–1`。 |
| `repository` | `repository_full_name` | 原值映射。 |
| `skill_gaps` 中 `gap == 0` | `matched_skills` | 输出已覆盖技能。 |
| `skill_gaps` 中 `gap > 0` | `missing_skills` | 只表示成长空间，不能绕过硬门槛。 |
| `score_components` | `reasons` | 转换为结构化 `Reason`，分量同样除以 100。 |
| 候选难度 | `difficulty` | 映射为 `Difficulty(code, setup)`。 |
| 候选验证字段 | `availability`、`verified_at` | 直接来自候选快照。 |
| 当前上下文反馈 | `feedback_state` | 返回当前用户、当前画像的反馈状态。 |

每个结构化 `Reason` 包含：

```json
{
  "code": "skill_match",
  "label": "技能覆盖良好",
  "evidence": "skill_coverage=0.900",
  "score_delta": 0.27,
  "feature_version": "task-features-v0.3"
}
```

原因码来自固定集合，包括语言、任务类型、技能覆盖、成长跨度、新人信号、活跃度、新鲜度、贡献指南、描述清晰度、成长价值、负反馈和多样性重排。`score_delta` 与公共最终分数均使用 `0–1` 尺度，但原因增量之和不要求等于最终分数。

## 5. 登录用户推荐接口的职责边界

Member C 已实现：

```python
RecommendationService.recommend_for_session(
    session_id=session_id,
    limit=limit,
)
```

该方法依次完成：

1. 通过 `AuthService` 校验 session；
2. 从 session 获取当前 `user_id`；
3. 只读取该用户拥有的当前画像；
4. 生成用户与画像绑定的反馈上下文；
5. 调用推荐排序并返回 `AuthenticatedRecommendations`。

建议 Member D 接入：

```http
GET /api/v1/me/recommendations?limit=10
Cookie: oss_mentor_session=<session-id>
```

接口必须由 session 决定用户，不应提供 `user_id`、`github_login` 或任意 `profile_key` 查询参数来切换身份。建议错误映射：

- session 缺失、过期或无效：`401 authentication_required`；
- 当前用户尚无画像：`404 profile_required`；
- limit 非法：`400 invalid_request`；
- 推荐服务或数据库未就绪：`503 service_not_ready`。

按团队分工，Member C 不修改 `api.py` 和 OpenAPI；Member D 负责路由注册、cookie 解析、响应序列化和 `docs/openapi_v0.5.yaml` 更新。

## 6. 当前用户画像与负反馈降权

反馈上下文由服务端生成：

```python
feedback_context_for_user(user_id, profile_key)
# user:7:profile:profile-1
```

推荐流程为：

```text
session → 当前 user_id → 当前用户拥有的 DeveloperProfileV2
        → user:{user_id}:profile:{profile_key}
        → 读取该上下文反馈
        → not_suitable 降低 0.25
        → 多样性重排
        → Top N
```

这保证了：

- A 用户的反馈不会影响 B 用户；
- 同一用户切换画像后不会错误复用旧画像反馈；
- 降权发生在截取 Top N 之前，确实能够改变结果顺序；
- 客户端即使提交伪造的 `feedback_context`，D 的接口层也应根据 session 重新生成并覆盖它。

当前只有 `not_suitable` 参与 `-0.25` 降权；其他反馈状态可以展示或统计，但不会被算法擅自解释成新的权重。

## 7. 分支、commit 与测试证据

交付分支：

```text
feat/v05-ranking-v3
```

核心实现 commit：

```text
f2af66d feat: complete member C recommendation v0.3
```

关键测试：

- `test_service_accepts_profile_contract_and_maps_match_result`：验证 `DeveloperProfileV2` 输入、`RecommendationItemV3` 输出、分数范围和结构化 reasons；
- `test_rejects_legacy_dictionary_profile`：拒绝旧字典画像；
- `test_current_context_negative_feedback_is_applied_before_ranking`：验证当前上下文负反馈在排序前降权，并保存 raw/final score；
- `test_real_session_consumes_its_current_owned_profile`：使用真实 SQLite 用户、session 和归属画像验证会话消费链路；
- `tests/test_contracts.py`：验证 skills casefold、原因码、fixture 与契约序列化；
- `tests/test_recommendation_migration.py`：验证 SQLite 010 推荐快照迁移。

完整测试结果：

```text
Ran 530 tests
OK
```

## 8. Member D 最终接入清单

- 注册 `GET /api/v1/me/recommendations` 和推荐页面静态路由；
- 从 `oss_mentor_session` cookie 获取 session ID；
- 调用 `recommend_for_session()`，不接受客户端指定用户；
- 序列化 `run_id`、画像摘要、warnings、count 和 `RecommendationItemV3` 列表；
- 服务端重新生成反馈上下文；
- 在 OpenAPI 中加入 `RecommendationBatchV3`、`Reason.feature_version` 和完整原因码；
- 保持 `score`、`score_delta` 的 `0–1` 语义；
- 映射本文件第 5 节列出的错误码；
- 运行契约 fixture、真实 session 测试和完整回归测试。

