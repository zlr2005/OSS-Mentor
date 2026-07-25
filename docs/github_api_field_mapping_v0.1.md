# OSS-Mentor GitHub API 字段映射 v0.1

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 状态 | Draft / 试采映射版 |
| 版本 | v0.1 |
| 更新日期 | 2026-07-12 |
| API | GitHub REST API |
| 固定版本 | `2026-03-10` |
| 配套文档 | [数据字典 v0.1](./data_dictionary_v0.1.md)、[数据采集方案 v0.1](./data_collection_plan_v0.1.md) |

本映射只包含首轮试采需要的 P0/P1 数据。v0.1 使用 REST API 完成可靠基线，GraphQL 仅作为后续减少请求次数的优化项。

## 2. 通用请求和响应字段

### 2.1 请求头

| Header | 值 |
|---|---|
| `Accept` | `application/vnd.github+json` |
| `X-GitHub-Api-Version` | `2026-03-10` |
| `User-Agent` | `OSS-Mentor-research/<collector_version>` |
| `Authorization` | 运行时注入；禁止写入日志和 Raw 数据 |

### 2.2 每次请求必须记录

| 来源 | Raw 元数据字段 |
|---|---|
| 请求 URL | `source_url` |
| 查询参数 | `request_params` |
| 当前时间 | `fetched_at` |
| `ETag` | `etag` |
| `Last-Modified` | `last_modified` |
| `Link` | `pagination_links` |
| `X-RateLimit-*` | `rate_limit_*` |
| HTTP 状态 | `status_code` |
| 响应内容哈希 | `response_sha256` |
| 采集任务 | `collection_run_id` |

## 3. Endpoint 总览

| 优先级 | Endpoint | 用途 | 目标实体 | 分页/限制 |
|---|---|---|---|---|
| P0 | `GET /repos/{owner}/{repo}` | 仓库身份和状态 | `repository`、`repository_snapshot` | 单对象 |
| P0 | `GET /repos/{owner}/{repo}/community/profile` | 社区健康文件 | `repository_snapshot`、`guidance_resource` | 单对象；非 fork |
| P0 | `GET /repos/{owner}/{repo}/languages` | 语言分布 | `repository_snapshot` | 单对象 |
| P0 | `GET /repos/{owner}/{repo}/labels` | 项目标签体系 | 标签映射配置 | 分页 |
| P0 | `GET /repos/{owner}/{repo}/issues` | Issue 回填 | `task`、`task_snapshot` | 分页；会混入 PR |
| P0 | `GET /repos/{owner}/{repo}/issues/{number}` | 单个 Issue 最新详情 | `task_snapshot` | 单对象 |
| P0 | `GET /repos/{owner}/{repo}/issues/{number}/timeline` | Issue 状态变化和 PR 关联 | 关联表、认领信号 | 分页 |
| P1 | `GET /repos/{owner}/{repo}/issues/{number}/comments` | 认领和互动研究 | 过程特征 | 分页；正文按需保存 |
| P0 | `GET /repos/{owner}/{repo}/pulls` | PR 列表回填 | `pull_request_fact` | 分页 |
| P0 | `GET /repos/{owner}/{repo}/pulls/{number}` | PR 最终统计和 head SHA | `pull_request_fact` | 单对象 |
| P0 | `GET /repos/{owner}/{repo}/pulls/{number}/reviews` | Review 状态 | `review_fact` | 分页 |
| P0 | `GET /repos/{owner}/{repo}/pulls/{number}/files` | 文件改动 | `file_change_fact` | 分页；最多 3000 文件 |
| P1 | `GET /repos/{owner}/{repo}/pulls/{number}/commits` | PR Commit | PR 过程特征 | 分页；该端点最多 250 commits |
| P0 | `GET /repos/{owner}/{repo}/commits/{ref}/check-runs` | CI/Checks | PR 最终 CI 汇总 | 分页 |
| P1 | `GET /repos/{owner}/{repo}/git/trees/{tree_sha}` | 目录结构 | 项目结构特征 | `recursive=1` 可能截断 |
| P1 | `GET /repos/{owner}/{repo}/contents/{path}` | 项目文档和配置 | `guidance_resource` | 单路径；固定 ref |
| P1 | `GET /repos/{owner}/{repo}/commits` | 默认分支活动和历史 | 仓库活跃度 | 分页、时间过滤 |

官方 Endpoint 文档：

- [Repositories](https://docs.github.com/en/rest/repos/repos#get-a-repository)
- [Community profile metrics](https://docs.github.com/en/rest/metrics/community#get-community-profile-metrics)
- [Issues](https://docs.github.com/en/rest/issues/issues#list-repository-issues)
- [Issue timeline](https://docs.github.com/en/rest/issues/timeline#list-timeline-events-for-an-issue)
- [Pull requests](https://docs.github.com/en/rest/pulls/pulls)
- [Pull request reviews](https://docs.github.com/en/rest/pulls/reviews#list-reviews-for-a-pull-request)
- [Check runs](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference)
- [Repository contents](https://docs.github.com/en/rest/repos/contents#get-repository-content)

## 4. 仓库映射

### 4.1 `GET /repos/{owner}/{repo}`

| GitHub 字段 | 目标字段 | 转换规则 | 阶段 | 注意事项 |
|---|---|---|---|---|
| `id` | `repository.github_repository_id` | 原样 `bigint` | T0 | 稳定外部主键 |
| `full_name` | `repository.full_name` | 原样 | T0 | 会随仓库转移/改名变化 |
| `html_url` | `repository.html_url` | 原样 | T0 | 展示链接 |
| `fork` | `repository.is_fork` | 原样 | T0 | 试采默认排除 fork |
| `archived` | `repository.is_archived` | 原样 | T0 | 归档仓库不进入候选集 |
| `mirror_url` | `repository.is_mirror` | 非空为 `true` | T0 | 镜像默认排除 |
| `license.spdx_id` | `repository.license_spdx_id` | `NOASSERTION`/空值进入人工审核 | T0 | 不能把空值当无许可证 |
| `default_branch` | `repository.default_branch` | 原样 | T0 | 代码结构读取基准 |
| `language` | `repository_snapshot.primary_language` | 原样 | T0 | 仅主语言 |
| `topics` | `repository_snapshot.topics` | 原样数组 | T0 | 主题由维护者提供 |
| `stargazers_count` | `repository_snapshot.star_count` | 原样 | T0 | 仅用于采样分层，不代表质量 |
| `forks_count` | `repository_snapshot.fork_count` | 原样 | T0 | 背景统计 |
| `open_issues_count` | 临时 `open_items_api` | 原样保存 | T0 | 包含开放 Issue 和 PR，不得作为纯 Issue 数 |
| `pushed_at` | 仓库活跃特征 | UTC 时间 | T0 | 不等同默认分支最后 commit |
| `updated_at` | 同步参考 | UTC 时间 | T0 | 仓库元数据更新时间 |
| `visibility` | 采集资格 | 仅 `public` 进入首轮 | T0 | 私有仓库不采 |
| `has_issues` | 采集资格 | `false` 则不抓 Issue | T0 | 可能使用外部 tracker |

### 4.2 `GET /repos/{owner}/{repo}/community/profile`

| GitHub 字段 | 目标字段 | 转换规则 | 注意事项 |
|---|---|---|---|
| `health_percentage` | 参考特征 | 原样 | 只反映推荐文件存在比例，不代表实际社区友好度 |
| `files.readme` | `has_readme` | 非空为 `true` | 保存 URL/path/SHA |
| `files.contributing` | `has_contributing_guide` | 非空为 `true` | 建立 `guidance_resource` |
| `files.code_of_conduct` | `has_code_of_conduct` | 非空为 `true` | 建立资源索引 |
| `files.issue_template` | `has_issue_template` | 非空为 `true` | 空值不一定代表 `.github/ISSUE_TEMPLATE/` 目录不存在，需 Git Tree 补查 |
| `files.pull_request_template` | `has_pr_template` | 非空为 `true` | 建立资源索引 |
| `files.license` | 许可证辅助证据 | 保存 URL/path | 不替代人工许可证判断 |

官方说明该接口返回文档存在性和总体 health percentage，但 `health_percentage` 不应直接当作 OSS-Mentor 的 `community_support_score`。

### 4.3 `GET /repos/{owner}/{repo}/languages`

响应为 `{language: bytes}`：

- 原样保存 Raw；
- 计算 `bytes / total_bytes`；
- 写入 `repository_snapshot.language_distribution`；
- 不将生成文件或 vendored 代码占比直接解释为任务技能要求。

## 5. Issue 映射

### 5.1 列表请求

```text
GET /repos/{owner}/{repo}/issues
  ?state=all
  &sort=updated
  &direction=asc
  &since=<watermark>
  &per_page=100
```

历史首次回填不设置 `since`，在客户端按 `updated_at` 和目标时间窗口过滤；增量同步使用成功 watermark。

GitHub 将 Pull Request 也视为 Issue。响应含 `pull_request` 字段时必须进入 PR 流程，不能写入 `task`。

### 5.2 字段映射

| GitHub 字段 | 目标字段 | 转换规则 | 阶段 | 注意事项 |
|---|---|---|---|---|
| `id` | `task.github_issue_id` | 原样 `bigint` | T0 | 稳定外部 ID |
| `number` | `task.issue_number` | 原样 | T0 | 仓库内编号 |
| `html_url` | `task.html_url` | 原样 | T0 | 展示链接 |
| `created_at` | `task.created_at` | UTC | T0 | 稳定事实 |
| `user.id` | 作者假名化外部 ID | HMAC/映射 | T0 | login 不进入分析主表 |
| `author_association` | `task.author_association` | 原样 | T0 | 解释 Issue 是否来自维护者 |
| `title` | `task_snapshot.title` | 按快照保存 | T0 | 会被编辑 |
| `body` / `body_text` | `task_snapshot.body_text` | 清洗 Markdown；保留 Raw 哈希 | T0 | 敏感信息扫描和保留期限 |
| `labels[].name` | `task_snapshot.labels` | 保留原名、大小写和 repo 上下文 | T0 | 再执行项目级标签映射 |
| `state` | `task_snapshot.state` | `open`/`closed` | T0/T2 | 需要历史快照 |
| `state_reason` | 结果原因辅助 | 原样 | T2 | 不足以解释全部失败原因 |
| `assignee.id` / `assignees[].id` | `assignment_state` | 非空通常为 `assigned` | T0 | 评论中认领需额外检测 |
| `comments` | `task_snapshot.comment_count` | 原样计数 | T0 | 不是讨论轮次 |
| `locked` | 候选排除特征 | 原样 | T0 | 锁定 Issue 通常不推荐 |
| `updated_at` | `last_activity_at` 辅助 | UTC | T0 | 评论/标签变化均可能更新 |
| `closed_at` | 任务结果事实 | UTC | T2 | 关闭不代表完成 |
| `pull_request` | 类型过滤 | 存在则不是独立 Issue | T0 | Issue ID 与 PR ID 不同 |

### 5.3 Timeline 映射

`GET /repos/{owner}/{repo}/issues/{issue_number}/timeline?per_page=100`

| Timeline event | 用途 | 输出 |
|---|---|---|
| `labeled` / `unlabeled` | 重建历史标签 | 快照标签变化 |
| `assigned` / `unassigned` | 重建指派状态 | `assignment_state` |
| `closed` / `reopened` | 重建状态 | 任务状态时间线 |
| `cross-referenced` | 识别关联 PR/Issue | 关联候选 + 置信度 |
| `connected` / `disconnected` | 识别显式连接 | 关联表 |
| `referenced` | 识别 Commit 引用 | 辅助证据 |
| `commented` | 互动与认领分析 | 评论元数据；正文按需 |
| `renamed` | 恢复历史标题 | 快照标题变化 |

Timeline 事件类型可能扩展；未知类型写入 Raw 并记录 `unknown_event_type_count`，不得使整个任务失败。

## 6. Pull Request 映射

### 6.1 列表与详情

列表：

```text
GET /repos/{owner}/{repo}/pulls
  ?state=all
  &sort=updated
  &direction=asc
  &per_page=100
```

详情：

```text
GET /repos/{owner}/{repo}/pulls/{pull_number}
```

| GitHub 字段 | 目标字段 | 阶段 | 注意事项 |
|---|---|---|---|
| `id` | `github_pull_request_id` | T1 | PR 稳定数字 ID |
| `number` | `pr_number` | T1 | 仓库内编号 |
| `html_url` | `html_url` | T1 | 展示链接 |
| `user.id` | 作者假名化映射 | T1 | 用于 project-first PR 排序 |
| `user.type` | bot 识别证据 | T1 | 仍需结合 login/App/规则 |
| `created_at` | `opened_at` | T1 | PR 打开时间 |
| `closed_at` | `closed_at` | T2 | 空值表示仍开放 |
| `merged_at` | `merged_at` | T2 | 非空表示合并 |
| `state` + `merged_at` | `state` | T1/T2 | `merged_at != null` 映射为 `merged` |
| `draft` | 过程特征 | T1 | Draft 不视为失败 |
| `head.sha` | Checks 查询 ref | T1 | 保存最终和历史 head SHA 时需区分 |
| `base.sha` | 基线版本 | T1 | 可用于 diff/结构定位 |
| `commits` | `commit_count_final` | T2 | 详情端点返回最终统计 |
| `additions` | `additions_final` | T2 | 禁止进入同任务 T0 特征 |
| `deletions` | `deletions_final` | T2 | 禁止进入同任务 T0 特征 |
| `changed_files` | `changed_files_final` | T2 | 禁止进入同任务 T0 特征 |
| `comments` | 过程计数 | T2 | Issue comments |
| `review_comments` | 过程计数 | T2 | Diff review comments |
| `maintainer_can_modify` | 协作背景 | T1 | 不直接作为能力标签 |
| `author_association` | 作者与项目关系 | T1 | project-first 识别辅助 |

## 7. Review 映射

`GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews?per_page=100`

| GitHub 字段 | 目标字段 | 转换规则 |
|---|---|---|
| `id` | `review_id` 外部来源 | 内部生成 UUID，并保留 GitHub ID |
| `user.id` | `reviewer_actor_id` | 假名化映射 |
| `state` | `review_state` | 小写标准化 |
| `submitted_at` | `submitted_at` | UTC |
| `author_association` | `is_maintainer_review` | `MEMBER`、`OWNER`、`COLLABORATOR` 作为候选规则 |
| `commit_id` | Review 版本 | 用于计算 Review/修改轮次 |
| `body` | 默认不长期保存 | 仅经批准的质性研究使用 |

`review_round_count` 不是 API 原生字段。v0.1 暂定：按时间排序 Review，连续的 `CHANGES_REQUESTED` 到作者下一次新 commit 形成一个返工轮次。公式必须带版本。

## 8. 文件变更映射

`GET /repos/{owner}/{repo}/pulls/{pull_number}/files?per_page=100`

| GitHub 字段 | 目标字段 | 注意事项 |
|---|---|---|
| `sha` | 文件版本证据 | 不等于仓库默认分支 SHA |
| `filename` | `file_path` | 仓库相对路径 |
| `status` | `change_status` | `added`、`modified`、`removed`、`renamed` |
| `previous_filename` | 重命名来源 | 仅 rename 时存在 |
| `additions` | `additions` | T2 结果 |
| `deletions` | `deletions` | T2 结果 |
| `changes` | QA 校验 | 通常约等于 additions + deletions |
| `patch` | 默认不落标准化库 | 可能缺失、截断并含敏感信息 |
| `contents_url` | 固定版本内容引用 | 需要时按权限读取 |

官方说明该端点最多返回 3000 个文件。达到上限时设置 `file_list_truncated=true`，该 PR 不用于依赖完整文件列表的训练。

## 9. PR Commit 与 CI 映射

### 9.1 PR Commit

`GET /repos/{owner}/{repo}/pulls/{pull_number}/commits?per_page=100`

用途：

- 计算作者提交时间序列；
- Review 之后的新 commit 用于返工轮次；
- 识别 merge/squash 前的过程；
- 不默认保存 commit message 全文。

该端点最多返回 250 个 commits；达到上限时改用仓库 Commits endpoint，或标记 `commit_list_truncated=true`。

### 9.2 Check Runs

`GET /repos/{owner}/{repo}/commits/{ref}/check-runs?per_page=100`

| GitHub 字段 | 标准化用途 |
|---|---|
| `id` | Check Run 唯一 ID |
| `name` | Check 名称 |
| `app.id` | CI 提供方识别 |
| `status` | `queued`、`in_progress`、`completed` 等 |
| `conclusion` | `success`、`failure`、`cancelled`、`skipped` 等 |
| `started_at` / `completed_at` | CI 时间和耗时 |
| `check_suite.id` | 去重与聚合 |

`ci_final_state` 暂定聚合：

- 存在必需 Check failure → `failure`；
- 所有已知必需 Check success/neutral/skipped → `success`；
- 无 Check Runs → `missing`；
- 状态未完成 → `unknown`；
- 项目必需 Checks 规则无法从公共 API 判断时，保存聚合置信度。

## 10. Git Tree 与文档映射

### 10.1 Git Tree

```text
GET /repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1
```

| GitHub 字段 | 用途 |
|---|---|
| `sha` | 固定目录树版本 |
| `tree[].path` | 文件和目录路径 |
| `tree[].mode` | 文件模式 |
| `tree[].type` | `blob`、`tree`、`commit` |
| `tree[].size` | Blob 大小 |
| `truncated` | 为 `true` 时不能视为完整项目结构 |

大型仓库出现 `truncated=true` 时，改为按一级目录递归请求或只读取候选任务相关模块。

### 10.2 Contents

优先读取：

- `README*`
- `CONTRIBUTING*`
- `CODE_OF_CONDUCT*`
- `.github/ISSUE_TEMPLATE/**`
- `.github/PULL_REQUEST_TEMPLATE*`
- `pyproject.toml`、`tox.ini`、`package.json`
- CI workflow 配置
- 项目明确的架构和开发文档

必须指定 `ref=<commit_sha>`，否则后续无法复现推荐时版本。

## 11. 项目标签标准化

### 11.1 原始标签

`GET /repos/{owner}/{repo}/labels?per_page=100`

保存：

- label ID；
- 原始 name；
- description；
- color；
- `repository_id`；
- 采集时间。

### 11.2 标准信号

| 标准信号 | 常见原始标签示例 |
|---|---|
| `newcomer_candidate` | `good first issue`、`first-contribution`、`first timers only` |
| `difficulty_easy` | `Easy`、`Difficulty: Easy` |
| `contribution_welcome` | `help wanted`、`contribution welcome`、`contributor pool` |
| `blocked` | `blocked`、`needs decision`、`waiting for author` |
| `security` | `security`、`vulnerability` |

映射规则按仓库配置和版本保存。名称匹配不等于任务适合新人，输出只能作为 `label_signal`。

## 12. 不能直接从 API 得到的字段

| 数据字典字段 | 计算/标注方式 |
|---|---|
| `claimed_in_comments` | 评论文本规则 + 人工抽查；保存置信度 |
| `has_reproduction_steps` | Issue 模板结构、规则或文本模型 |
| `has_acceptance_criteria` | 规则/模型 + 人工标注 |
| `task_types` | 标签规则、文件类型、文本模型和人工标签 |
| `estimated_*_difficulty` | T0 人工标注集训练；不能使用同任务最终 diff |
| `novice_fit_probability` | 人工标注监督模型 |
| `growth_value_score` | 技能需求与用户画像差距 + 后续学习反馈 |
| `community_support_score` | 历史响应、文档和首贡结果的版本化公式 |
| `review_round_count` | Review 与新 commit 时间序列计算 |
| `outcome_reason_codes` | Timeline、关闭上下文、规则和人工标注 |
| `is_first_observed_project_pr` | 同作者、同目标仓库的历史 PR 排序 |
| `learning_gain` | 用户前后测和后续迁移表现，GitHub API 无法直接提供 |

## 13. Raw 到标准化的追溯字段

每个标准化对象建议包含或通过关联表可查询：

- `source_system`
- `source_endpoint`
- `source_external_id`
- `raw_object_uri`
- `raw_response_sha256`
- `fetched_at`
- `normalized_at`
- `normalizer_version`
- `schema_version`
- `collection_run_id`

派生字段还必须包含：

- `feature_definition_version` 或 `rubric_version`；
- 输入快照 ID；
- `source_cutoff_at`；
- 计算时间；
- 置信度或缺失原因。

## 14. v0.1 已知限制

- 公开 API 无法证明某次 PR 是用户人生中的绝对首次贡献；
- 删除、转私有或平台外贡献会造成历史缺失；
- `open_issues_count` 混合 Issue 和 PR；
- community profile 的模板检测可能漏掉目录式 Issue Forms；
- Timeline 和正文启发式无法保证识别所有 Issue–PR 关联；
- CI Checks 可能不存在、权限不可见或无法判断哪些是必需项；
- PR files 和 PR commits 端点存在最大返回量；
- 项目标签语义不统一；
- 用户未尝试某任务不是可靠负反馈。

这些限制必须进入最终数据集说明和论文的 validity threats，不能只在采集代码中隐含处理。

## 15. v0.2 计划

- 增加 GraphQL 查询模板及 cost 预算；
- 根据 Wave 1 实际响应生成机器可读 field mapping；
- 增加 Webhook payload 映射；
- 增加 GitHub App 最小权限清单；
- 固化项目级标签映射文件；
- 增加 API fixture 和 schema contract test；
- 对 REST 与 GraphQL 结果做一致性抽查。
