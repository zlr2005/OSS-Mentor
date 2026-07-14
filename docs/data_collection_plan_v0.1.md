# OSS-Mentor 数据采集方案 v0.1

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 状态 | Draft / 可执行试采版 |
| 版本 | v0.1 |
| 更新日期 | 2026-07-12 |
| 配套文档 | [数据字典 v0.1](./data_dictionary_v0.1.md)、[GitHub API 字段映射 v0.1](./github_api_field_mapping_v0.1.md) |
| 仓库配置 | [`config/pilot_repositories_v0.1.csv`](../config/pilot_repositories_v0.1.csv) |

本方案的目标不是立即抓取大规模 GitHub 数据，而是用 5 个首轮仓库验证采集链路、字段可得性、Issue–PR 关联、任务快照和数据质量。首轮通过验收后，再扩展到另外 5 个仓库。

## 2. 已确认的试采决策

| 决策项 | v0.1 决策 |
|---|---|
| 技术生态 | Python、JavaScript/TypeScript |
| 历史时间范围 | 默认最近 18 个月；若数据量过大，第一轮缩短到 12 个月 |
| 数据对象 | 公共仓库、Issue、PR、Review、文件变更、CI、项目文档和目录结构 |
| 身份范围 | 第一轮只采公开贡献者的假名化 ID，不建立完整个人画像 |
| 首贡主要口径 | “观察范围内，对某个项目的首次公开外部 PR” |
| 补充口径 | 同时保留“观察范围内首次公开外部 PR”标志，但不得称为绝对人生首次贡献 |
| 推荐快照 | 以 `task_snapshot.snapshot_at` 冻结推荐时信息 |
| 标签定位 | `good first issue`、`help wanted` 等只作为弱特征，不作为难度真值 |
| 原始代码 | 默认不做全仓永久镜像；只按固定 commit SHA 读取必要结构 |
| API 版本 | `X-GitHub-Api-Version: 2026-03-10` |

GitHub REST API 已版本化；本版本明确固定 `2026-03-10`，避免未指定版本导致行为随默认版本变化。官方说明见 [API Versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions)。

## 3. 首轮仓库分组

### 3.1 Wave 1：先运行

| 仓库 | 生态 | 试采作用 |
|---|---|---|
| `matplotlib/matplotlib` | Python | 当前有开放 newcomer 标签任务，适合验证新手候选筛选；许可证需人工确认后才允许代码读取 |
| `scikit-learn/scikit-learn` | Python | Issue/PR 历史丰富，包含 `Easy`、`good first issue`、`help wanted` 等标签 |
| `pytest-dev/pytest` | Python | 测试领域项目，贡献指南完整，适合验证文档、测试、Bug 等任务类型 |
| `microsoft/vscode` | TypeScript | 当前有开放 `good first issue`，规模大，用于压力和分页测试 |
| `eslint/eslint` | JavaScript | 规模相对可控，包含 contributor/help-wanted 标签，适合验证 JS 工具链任务 |

### 3.2 Wave 2：Wave 1 验收后加入

| 仓库 | 生态 | 试采作用 |
|---|---|---|
| `pandas-dev/pandas` | Python | 大规模历史训练仓，适合首贡与任务结果回填；当前开放 `good first issue` 为 0 |
| `wagtail/wagtail` | Python | Django 全栈项目，补充框架和前后端协作任务 |
| `vitejs/vite` | TypeScript | 前端构建工具，包含 `contribution welcome` 等项目自定义标签 |
| `freeCodeCamp/freeCodeCamp` | TypeScript | 文档、课程和代码任务并存，适合非代码贡献与新人流程分析 |
| `sveltejs/svelte` | JavaScript | 前端框架项目，Issue 模板和贡献文件较完整 |

仓库元数据已于 2026-07-12 通过 GitHub 公共 API 核验。所有候选仓库均为公共、非 fork、未归档仓库，且在 2026-07-07 至 2026-07-11 之间仍有 push 活动。详细快照见仓库配置文件。

## 4. 采集目标与非目标

### 4.1 第一轮必须回答的问题

1. 数据字典中的 P0 字段能否从 GitHub API 稳定获得？
2. Issue API 返回的数据能否正确排除 Pull Request？
3. Issue–PR 的 Timeline/closing reference 关联覆盖率是多少？
4. 在推荐时刻，任务是否仍开放、未认领且没有关联开放 PR？
5. PR 文件变更、Review 和 CI 数据的缺失率分别是多少？
6. 每个项目实际使用哪些 newcomer/help-wanted 标签？
7. 18 个月回填需要多少 API 请求、时间和存储？
8. 原始数据能否完整追溯到标准化记录和任务快照？

### 4.2 第一轮不做

- 不训练 CodeBERT、GNN 或大模型；
- 不抓取整个 GitHub；
- 不采集私有仓库；
- 不使用多个 token、代理或轮换账号绕过限流；
- 不将所有未点击、未合并或未标注任务作为负样本；
- 不长期保存完整 patch、评论全文或无必要的个人 login；
- 不开始在线 A/B 实验。

## 5. 数据分层

```text
GitHub API / GH Archive / Git snapshot
                    │
                    ▼
raw：不可变原始响应 + 请求元数据
                    │
                    ▼
normalized：repository / task / PR / review / file change
                    │
                    ▼
snapshot：T0 任务与仓库快照、历史结果事实
                    │
                    ▼
annotation：人工难度、技能要求和新手适配标签
                    │
                    ▼
feature：有版本的模型特征与训练/验证/测试集
```

### 5.1 Raw 层

保存 GitHub 原始 JSON 和响应元数据，内容不可原地覆盖。建议目录：

```text
data/raw/github/rest/<endpoint>/<owner>/<repo>/<yyyy-mm-dd>/<request_hash>.json.gz
data/raw/github/graphql/<query_name>/<yyyy-mm-dd>/<request_hash>.json.gz
data/raw/git/<owner>/<repo>/<commit_sha>/<artifact>.json.gz
```

每个 Raw 对象至少配套保存：

- `source_url`
- `request_params`
- `api_version`
- `fetched_at`
- `etag`
- `last_modified`
- `status_code`
- `collection_run_id`
- `response_sha256`
- `rate_limit_remaining`

Raw 目录和实际数据文件不应直接提交到 Git 仓库。

### 5.2 Normalized 层

按照数据字典写入稳定实体和事实表。外部主键优先使用 GitHub 数字 ID；`owner/name`、login、分支名等只作为可变属性。

### 5.3 Snapshot 层

Snapshot 层用于生成“当时真的可推荐什么”：

- `repository_snapshot`
- `task_snapshot`
- `developer_profile_snapshot`（在线用户阶段再启用）
- `recommendation_session`
- `recommendation_impression`

同一个 Issue 可以有多个快照。模型输入必须显式引用某个 `task_snapshot_id`，不得读取 Issue 最终状态覆盖历史。

## 6. 认证与请求规范

### 6.1 认证方式

试采阶段使用一个团队管理的只读 token 或 GitHub App 凭证：

- 凭证通过环境变量或密钥管理系统提供；
- 不写入源码、配置、日志或 Raw JSON；
- 不使用参与试点用户的 OAuth token 承担公共数据回填；
- 权限限制为读取公共仓库所需的最小范围；
- 公开数据端点可匿名调用，但正式采集默认使用认证请求以获得更高限额。

### 6.2 固定请求头

```http
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2026-03-10
User-Agent: OSS-Mentor-research/<collector_version>
Authorization: Bearer <secret-from-runtime>
```

### 6.3 分页与缓存

- `per_page=100`；
- 严格解析 `Link` 响应头，不猜测总页数；
- 保存 ETag，增量同步发送 `If-None-Match`；
- `304 Not Modified` 视为成功同步，不重复写 Raw 内容；
- 每次响应记录 `x-ratelimit-limit`、`remaining`、`used`、`reset` 和 `resource`；
- Search API、REST API 和 GraphQL 限额分别监控。

### 6.4 重试策略

| 情况 | 策略 |
|---|---|
| `429` 或有 `Retry-After` | 等待指定时间后重试 |
| `x-ratelimit-remaining=0` | 等待到 `x-ratelimit-reset` |
| `502/503/504` | 指数退避 + 随机抖动，最多 5 次 |
| 网络超时 | 指数退避，最多 5 次 |
| `301` | 记录对象转移并更新 canonical URL |
| `404` | 重新确认权限；可能已删除或转为私有 |
| `410` | 标记对象 gone，不持续重试 |
| `422` | 记录请求参数和响应，不自动无限重试 |

GitHub 官方建议使用条件请求、避免高并发并遵守二级限流，见 [REST API best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)。

## 7. 历史回填流程

### 7.1 仓库级数据

对每个启用仓库：

1. 获取仓库基本信息；
2. 获取 community profile；
3. 获取语言分布；
4. 获取项目标签列表；
5. 记录默认分支当前 SHA；
6. 读取 README、CONTRIBUTING、行为准则、Issue/PR 模板和构建配置的存在性；
7. 许可证无法由 API 确认时，阻止代码克隆并进入人工审核队列。

### 7.2 Issue 回填

1. 按 `state=all`、`sort=updated` 分页获取最近 18 个月更新的条目；
2. 丢弃响应中包含 `pull_request` 字段的条目，避免把 PR 当 Issue；
3. 写入 `task` 稳定实体；
4. 保存标题、正文、标签、assignee、评论数、创建/关闭/更新时间；
5. 对进入关联和标注样本的 Issue 获取 Timeline；
6. 从 Timeline 提取标签变化、指派、关闭、重开、引用和关联 PR；
7. 评论正文只对认领识别和人工研究按需采集，不默认永久保存。

GitHub 官方明确说明 Issues API 会同时返回 Issue 和 Pull Request，必须用 `pull_request` 字段过滤，见 [List repository issues](https://docs.github.com/en/rest/issues/issues#list-repository-issues)。

### 7.3 PR 回填

1. 按 `state=all` 分页获取时间窗口内 PR；
2. 获取单个 PR 详情，保存 `merged_at`、增删行数、文件数、提交数、head SHA 等；
3. 获取 Review；
4. 获取变更文件；
5. 获取 PR Commit；
6. 使用 head SHA 获取 Check Runs；
7. 依据 GitHub 数字用户 ID 做假名化作者和 Reviewer 关联；
8. Bot、依赖更新和自动格式化 PR 单独标记，默认不进入开发者能力训练。

### 7.4 Issue–PR 关联

关联优先级：

1. Timeline 中的 closing reference；
2. GitHub 明确关联的 cross-reference；
3. PR 正文 closing keywords；
4. 人工确认；
5. 文本正则或模型推断。

每条关联必须保存 `link_method` 和 `link_confidence`。一个 Issue 可以关联多个 PR，一个 PR 也可以关联多个 Issue；不得强制一对一。

### 7.5 首贡识别

主要标签 `is_first_observed_project_pr` 的计算：

1. PR 作者不是 bot；
2. PR 目标仓库不是作者拥有的仓库；
3. 在本系统可见的目标仓库全部历史外部 PR 中，它是该作者时间最早的一条；
4. 使用 PR `created_at` 排序，同一时间使用 GitHub PR ID 稳定排序；
5. 历史窗口左边界不完整时，设置 `first_pr_confidence < 1` 并记录 censoring；
6. 不得把该字段表述为作者人生中的首次开源贡献。

## 8. 候选任务快照

第一轮每日为启用仓库生成一次候选快照。进入候选集应满足：

- Issue 状态为 `open`；
- 仓库未归档且采集状态正常；
- 没有已知关联开放 PR；
- 没有明确 assignee；
- 评论中没有高置信度“已认领”信号，或项目政策允许多人参与；
- 最近同步时间未超过 24 小时；
- 未被标记为 security/private report；
- Issue 未被锁定为仅供跟踪、支持问答或项目内部计划；
- 许可证/展示状态允许分析。

候选快照必须保留：

- 当时的标题、正文、标签；
- 当时的指派和关联 PR 状态；
- `snapshot_at`；
- 原始对象版本或 Raw 哈希；
- 候选资格和排除原因；
- 项目级标签的标准化映射结果。

`good first issue`、`Easy`、`help wanted`、`contribution welcome`、`first timers only` 等统一映射成弱特征，但保留原始标签。

## 9. 项目标签映射

建议新增配置层，而不是在采集器中硬编码：

```yaml
repo: vitejs/vite
label_map:
  contribution_welcome:
    - "contribution welcome"
    - "help wanted"
  maintainer_claimed: []
  difficulty_easy:
    - "good first issue"
```

映射输出：

- `label_signal.newcomer_candidate`
- `label_signal.contribution_welcome`
- `label_signal.difficulty_easy`
- `label_signal.needs_triage`
- `label_signal.blocked`
- `label_signal.security`

映射是项目级、带版本的弱标注。不得将不同仓库同名标签默认视为完全相同含义。

## 10. 增量同步

### 10.1 未安装 GitHub App 的公共仓库

- Issue：按 `since=<last_successful_updated_at>` 每日同步；
- 开放 PR：每日同步详情、Review 和 Checks；
- 已关闭 PR：关闭后 7 天再最终冻结一次，之后低频抽查；
- 仓库和 community profile：每周同步；
- 文档和默认分支结构：默认分支 SHA 变化时同步；
- 标签列表：每周同步；
- 失败任务使用断点续传，不因部分失败推进全局 watermark。

### 10.2 安装 GitHub App 后

对获得安装权限的仓库优先使用 Webhook 接收 Issue、PR、Review、Check Suite 和 push 事件，轮询仅用于补偿漏单和定期对账。GitHub 也建议集成场景优先考虑 Webhook，见 [About the REST API](https://docs.github.com/en/rest/about-the-rest-api/about-the-rest-api)。

## 11. 数据质量报告

每次 `data_collection_run` 生成一份报告，至少包含：

| 指标 | 说明 |
|---|---|
| `request_success_rate` | 成功、304 和预期 gone 请求占比 |
| `raw_normalized_trace_rate` | 标准化记录能找到 Raw 来源的比例，目标 100% |
| `duplicate_external_id_count` | 同表 GitHub 数字 ID 重复数，目标 0 |
| `issue_pr_filter_error_count` | Issues API 中误入 PR 的数量，目标 0 |
| `timeline_coverage_rate` | 目标关联样本中成功获取 Timeline 的比例 |
| `issue_pr_link_rate` | 存在可信 Issue 关联的 PR 比例；只报告，不设虚假高目标 |
| `review_missing_rate` | 目标 PR 缺失 Review 数据的比例 |
| `checks_missing_rate` | 目标 PR 没有或无法获得 Check Runs 的比例 |
| `license_unknown_count` | 许可证无法自动确认的仓库数 |
| `candidate_freshness_hours_p95` | 候选快照距最近同步的 P95 小时数 |
| `open_pr_conflict_rate` | 被发现已有开放 PR 的候选任务比例 |
| `censored_first_pr_rate` | 首贡历史左删失导致低置信标签的比例 |

## 12. Wave 1 验收标准

满足以下条件后才能扩展到 Wave 2：

- 5 个 Wave 1 仓库均完成仓库、Issue 和 PR 回填；
- Raw、Normalized 和 Snapshot 三层可以通过 ID 和哈希相互追溯；
- GitHub 数字主键无重复；
- Issues API 返回的 PR 已全部正确过滤；
- 所有记录均保存 `fetched_at`，所有快照均保存 `snapshot_at`；
- API 分页、ETag、限流等待和断点续传经过实际运行验证；
- Issue–PR 关联保存方法和置信度，不强制一对一；
- 最终 PR 文件数、Review 轮次和 CI 结果只出现在 T1/T2，不泄漏进同一任务的 T0 特征；
- 产生一份字段缺失率和采集成本报告；
- 从结果中抽取 50 个任务，可供两名成员开始小规模标注；
- 未知许可证仓库在人工确认前没有进入代码内容采集。

## 13. 第一轮人工标注抽样

Wave 1 通过后抽取 50 个任务：

- 每个仓库原则上 10 个；
- 至少一半不是 `good first issue`，避免只复现维护者标签；
- 覆盖开放、已完成、无人尝试和 PR 未合并等不同结果；
- 标注员只看 T0 快照，不看最终 PR 结果；
- 其中至少 20 个由两名成员独立标注；
- 先计算一致性并修订 rubric，再扩大到 300–500 个。

## 14. 团队分工建议

| 角色 | 第一轮任务 |
|---|---|
| 数据工程 | API 客户端、Raw 层、分页、限流、回填与断点续传 |
| 后端与安全 | PostgreSQL schema、身份隔离、密钥、数据删除与查询接口 |
| 画像与建模 | 项目标签映射、首贡口径、T0/T2 特征边界、基线特征 |
| 前端与评测 | 任务标注界面、问卷草案、数据质量和缺失率报告 |

## 15. 下一步实施清单

1. 根据字段映射创建 PostgreSQL P0 表 DDL；
2. 创建 collector 项目骨架和配置加载；
3. 实现仓库、community profile、Issue 和 PR 四个基础端点；
4. 对单个小仓库运行端到端 smoke test；
5. 加入 Timeline、Review、Files 和 Checks；
6. 运行全部 Wave 1；
7. 输出采集质量报告和 50 个任务标注样本；
8. 根据试采结果发布数据字典和采集方案 v0.2。
