# v0.5 真实在线库存到登录推荐发布验收

验收日期：2026-10-10  
主线复核日期：2026-10-11

## 结论

OSS-Mentor v0.5 已在本机单进程 SQLite 模式下完成真实 GitHub 在线库存到当前登录用户非空推荐的发布验收。

验收链路为：

```text
GitHub 公共 Issue 同步与复核
→ v0.5 candidate_availability 持久化
→ GitHub OAuth 登录会话
→ 当前用户 DeveloperProfileV2
→ GET /api/v1/me/recommendations
→ RecommendationItemV3 非空结果
```

最终接口返回 HTTP 200 和 2 条符合当前画像的公开任务：

- `scikit-learn/scikit-learn#18530`，显示分约 84；
- `scikit-learn/scikit-learn#12505`，显示分约 83。

本结论只覆盖 SQLite 本地运行模式，不代表 PostgreSQL 运行时适配或生产部署已经完成。

## 验收基线

- PR #14 合并提交：`cee233c9871f866e4ff629547bd9e17f63870be1`；
- 候选可用状态修复 PR #17：<https://github.com/zlr2005/OSS-Mentor/pull/17>；
- PR #17 修复提交：`e11330564c035092d5a7451b74205dfdbe69b129`；
- PR #17 合并提交：`67e2b0c8d8e39ec326cf94b5785c16a1ee0a5e6b`；
- C、D 均已在 PR #17 提交 Approve；
- PR #17 和合并后的 `main` 均通过 8 个 GitHub Actions job。

## 在线库存结果

2026-10-08 完成 28 个启用仓库的真实 GitHub 同步和特征提取。最终聚合快照为：

- 仓库：28，健康 28；
- 候选任务：471；
- `eligible`：136；
- 含新人信号的 `eligible`：71；
- `temporarily_ineligible`：294；
- `excluded`：41；
- 当时 471 条候选均在 24 小时验证窗口内；
- 可推荐任务中未检查关联 PR 的数量为 0；
- 新人任务最大单仓库占比为 14.1%，进阶任务为 9.56%。

完整 JSON、Markdown 和本地 SQLite 数据库属于运行产物，受 `.gitignore` 保护，不随仓库提交。仓库文档只保留脱敏后的聚合计数、状态和验收结论。

## 发现并修复的问题

2026-10-10 再次验收时，超过 24 小时的新鲜度规则按预期将旧候选降为 `temporarily_unverified`。随后发现候选刷新命令虽然成功更新 GitHub 验证时间和 eligibility，但 CLI 构造的是旧版 `SQLiteCandidateStore`，没有持久化 v0.5 `candidate_availability`，导致登录推荐持续为空。

PR #17 将候选同步、刷新和报告命令统一改为 `SQLiteCandidateStorage`，并增加以下回归覆盖：

- CLI 候选命令必须使用 v0.5 availability 存储；
- `available`；
- `closed`；
- `assigned`；
- `locked`；
- `linked_open_pr`。

修复后重新同步 `scikit-learn/scikit-learn`：

- 发现并补全 20 条公开 Issue；
- 10 条持久化为 `available`；
- 同步和复核均无失败、无 GitHub 限流；
- 当前登录画像最终获得 2 条非空推荐。

## 验证结果

| 检查 | 结果 |
|---|---|
| Python 完整测试 | 537 项通过 |
| Node 测试 | 13 项通过 |
| SQLite 迁移 | 11 个迁移可应用 |
| PostgreSQL 迁移 CI | 通过 |
| Docker 构建 CI | 通过 |
| Web smoke CI | 通过 |
| Secret scan | 通过 |
| `git diff --check` | 通过 |
| 登录推荐 HTTP 接口 | HTTP 200，2 条结果 |

合并后主线 CI：<https://github.com/zlr2005/OSS-Mentor/actions/runs/38118137463>。

## 安全与隐私

验收记录不包含：

- GitHub Client Secret；
- OAuth access token；
- Cookie 或 session ID；
- `Authorization` 请求头；
- OAuth callback 中的 code/state；
- GitHub 登录名或本机用户目录；
- Issue 正文或本地数据库文件。

OAuth Secret 只在本机 PowerShell 进程环境中使用。公共库存同步使用临时注入的 GitHub CLI 凭据，命令完成后即从子进程环境移除。

## 报告命名说明

`docs/candidate_pool_report_v0.3.md` 保留为历史兼容快照文件名，不再代表当前在线库存或当前报告 schema。v0.5 的发布结论以本文和 PR #17 后的 `candidate_pool_report_v0.5` schema 为准，避免把中途运行或已过期的动态库存误写成长期有效数据。

## 尚未完成的发布工作

以下事项不阻塞本次 SQLite 验收，但仍是 v0.5 正式发布前的后续工作：

1. PostgreSQL 运行时存储适配器；
2. SQLite/PostgreSQL 共用存储契约测试；
3. Docker + PostgreSQL 端到端启动和真实事务验收；
4. 生产凭据存储与部署运维流程；
5. 旧匿名推荐接口响应结构的后续兼容迁移；
6. `v0.5.0` 标签和发布说明。

