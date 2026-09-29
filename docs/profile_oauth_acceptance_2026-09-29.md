# 真实 GitHub OAuth 与画像闭环验收（2026-09-29）

## 结论与范围

本机单进程 SQLite 环境的真实 GitHub OAuth 与画像闭环验收通过。B 完成登录、授权及业务操作，助手检查页面、刷新结果和注销后的接口行为。本记录不代表 PostgreSQL 运行时、生产部署、C 侧推荐消费或整个 v0.5 已验收完成。

- 验收代码：`9845fe57520a7e8c9e6f030168e2b5b1b80e887c`。
- 本地集成分支：`feat/v05-profile-integration`。
- [PR #14](https://github.com/zlr2005/OSS-Mentor/pull/14) 的源分支：`feat/v05-profile-import`；本次核对时两者代码提交一致。
- 环境：Windows、Python 3.12.14、独立 SQLite 测试数据库、单进程服务。
- 服务地址：`http://127.0.0.1:8765`；页面使用真实 API 模式，不是 demo 或 fixture GitHub 源。
- 验收时间采用 Asia/Shanghai。

## 实际验证

| 项目 | 结果 |
|---|---|
| 登录与回调 | 完成真实 GitHub 授权后回到画像页，服务创建实际登录会话 |
| 创建、保存与刷新 | 手工创建测试画像，保存并刷新后名称与字段保留 |
| 明确同意后导入 | 真实公开数据导入成功，页面显示 4 个公开仓库、1 个近期活跃仓库、3 条建议；观察时间为 10:46 |
| 证据展示 | 可见语言、来源和置信度；语言占比不代表个人熟练度或完整贡献历史 |
| 接受建议 | 接受 JavaScript 技能建议后，技能值为 1，来源为用户已确认 |
| 拒绝与手工值保护 | 拒绝偏好语言与 Python 技能建议，原有手工值保持 |
| 决策持久化 | 刷新并切换全部建议后，仍为 1 条已接受、2 条已拒绝、0 条待处理，画像值与来源保留 |
| 退出页面跳转 | 点击退出后回到 `/login?return_to=%2Fprofile` |
| 服务端注销 | 唯一测试会话从 10:41:54 创建至 10:50:56 注销，检查时有效会话数为 0 |
| 匿名个人接口 | 无 Cookie 请求 `GET /api/v1/me` 返回 `401 authentication_required` |
| 已注销会话失效 | 在本机内存中使用刚注销的会话重新请求同一接口，仍返回 `401 authentication_required`；会话值未输出或保存到报告 |

匿名请求 ID：`2ceb979a-a53f-4a97-83dd-8f8ef22951a4`。
已注销会话请求 ID：`9e121032-091d-4ce7-968f-f571ad86c189`。

过程限制：最初 GitHub 授权未完成时接口返回 401，不能把 GitHub 中间页的绿色勾当作应用登录成功。内置浏览器直接打开个人接口时曾出现 `ERR_BLOCKED_BY_CLIENT`，不将其当作 HTTP 状态证据；上述 401 由独立本机 HTTP 检查确认。

## CI、交接与未覆盖内容

- 2026-09-29 核对 GitHub：[Test #22](https://github.com/zlr2005/OSS-Mentor/actions/runs/35813609925) 在上述代码提交上的 8 个作业成功。这是既有 CI 结果，不是当天重新运行整套测试。
- [OAuth 验收摘要评论](https://github.com/zlr2005/OSS-Mentor/pull/14#issuecomment-5888962689) 已发布；本报告整理时 PR 正文尚未同步最新验收状态，PR 仍未合并。
- D 平台侧最终审核、C 侧契约确认及真实会话画像的推荐消费联调仍需确认。本报告不替代审核批准。
- PostgreSQL 迁移检查不等于应用已有 PostgreSQL 运行时适配器；本次实际使用 SQLite。
- 服务重启、多进程凭据方案、会话自然过期及完整生产部署不在本次闭环验收结论内。
- B3 固定数据快照复跑另见 [B3 数据质量复跑记录](b3_quality_rerun_2026-09-29.md)。

## 脱敏

仓库记录仅保留验收结果、计数、状态码与请求 ID，不包含 GitHub 账号标识、Client Secret、access token、Cookie 值、Authorization 头、OAuth code/state 或本机用户目录。测试数据库、原始会话及运行时环境变量均不随文档入库。
