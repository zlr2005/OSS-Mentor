# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

主要用户是希望开始或继续开源贡献的开发者。他们需要从仍可领取的真实 Issue 中找到符合当前技能、设备环境和成长目标的任务。团队成员也使用离线评估与推荐快照审查算法变化。

## Product Purpose

OSS-Mentor 将开发者成长画像与开源任务特征进行可解释匹配。成功意味着用户能理解任务为何适合自己、需要补齐什么技能，并能对推荐给出反馈；团队能复现和审计每次推荐。

## Positioning

产品以“能力可完成、又保留合理成长空间”为机制，同时用硬门槛、结构化理由、任务可用性和版本化快照约束推荐质量。

## Operating Context

用户通过浏览器登录、维护画像、查看推荐并反馈任务是否适合。算法通过本地 SQLite 候选池运行，不在推荐过程中调用 GitHub 网络。开发团队使用固定标注集、JSON/Markdown 报告和契约 fixture 验收版本变化。

## Capabilities and Constraints

- 支持 newcomer 与 growth 两条推荐轨道。
- 推荐输入契约为 `DeveloperProfileV2`，输出为 `RecommendationItemV3`。
- 任务必须满足可用性、偏好、平台和难度硬门槛。
- 推荐结果需要可解释、可复现并可保存快照。
- OAuth、公共 API 路由和 OpenAPI 由成员 D 负责；推荐模块提供传输无关的服务边界。
- 本文件内容根据 `docs/team_work_plan_v0.2.md` 与现有实现推断；用户未提供额外品牌或商业约束。

## Brand Commitments

产品名称为 OSS-Mentor。现有界面使用简洁、克制、可信的中文产品语言，避免把开源贡献包装成竞赛或游戏。

## Evidence on Hand

- 固定候选、画像和错误契约位于 `fixtures/contracts/v0.5/`。
- 人工标注接口位于 `data/annotations/task_fit_v0.1.csv`。
- 新人轨与成长轨评估报告位于 `data/reports/` 和 `docs/`。
- 当前没有客户背书、商业指标或可公开宣称的线上效果数据；界面不得虚构这些内容。

## Product Principles

- 先保证任务真实可领取，再谈匹配分。
- 硬约束不可被软评分或多样性重排突破。
- 每次排序变化都应能从特征、理由和快照中追溯。
- 用户负反馈只影响当前用户及其当前画像。
- 推荐页面优先帮助用户做决定，而不是展示算法炫技。

## Accessibility & Inclusion

界面使用语义化 HTML、键盘可达控件、清晰焦点状态、状态文本和响应式布局，并尊重减少动态效果的系统偏好。
