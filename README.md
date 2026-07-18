# OSS-Mentor

OSS-Mentor 是一个面向开源贡献者的双通道导学系统：

- 新手破冰：帮助零贡献或首次贡献者找到当前仍可做、难度匹配且说明清楚的任务；
- 进阶成长：根据历史贡献和技能证据推荐略高于当前能力的任务，并逐步形成成长路径。

当前仓库已经形成可本地试用的 MVP：使用 Ecosyste.ms 发现候选、GitHub API 复核当前状态、SQLite 保存任务与预设画像，并通过网页提供双通道个性化推荐和自定义画像。

## 文档

- [数据字典 v0.1](docs/data_dictionary_v0.1.md)
- [数据采集方案 v0.1](docs/data_collection_plan_v0.1.md)
- [GitHub API 字段映射 v0.1](docs/github_api_field_mapping_v0.1.md)
- [试采仓库配置](config/pilot_repositories_v0.1.csv)
- [PostgreSQL 初始迁移](db/migrations/001_initial_p0.sql)
- [Collector smoke test report v0.1](docs/smoke_test_report_v0.1.md)
- [Ecosyste.ms 与 GitHub 数据源验证报告 v0.1](docs/source_validation_report_v0.1.md)
- [SQLite 候选任务流水线 v0.1](docs/sqlite_candidate_pipeline_v0.1.md)
- [候选任务采集与刷新 v0.2](docs/candidate_collection_refresh_v0.2.md)
- [候选池聚合报告 v0.2](docs/candidate_pool_report_v0.2.md)
- [推荐库存扩充与选项保障 v0.3](docs/recommendation_inventory_expansion_v0.3.md)
- [候选池聚合报告 v0.3](docs/candidate_pool_report_v0.3.md)
- [任务特征与双通道排序 v0.1](docs/task_features_and_ranking_v0.1.md)
- [开发者画像与个性化匹配 v0.1](docs/personalized_matching_v0.1.md)
- [本地推荐 API v0.1](docs/local_api_v0.1.md)
- [OpenAPI v0.1](docs/openapi_v0.1.yaml)
- [最小网页界面 v0.1](docs/web_ui_v0.1.md)
- [自定义用户画像 v0.2](docs/web_ui_v0.2.md)
- [OpenAPI v0.2](docs/openapi_v0.2.yaml)
- [推荐反馈闭环 v0.3](docs/recommendation_feedback_v0.3.md)
- [OpenAPI v0.3](docs/openapi_v0.3.yaml)
- [OpenAPI v0.4](docs/openapi_v0.4.yaml)

## 当前采集器范围

第一版 collector 使用 Python 标准库，不需要下载第三方包，支持：

- 读取并校验 Wave 1/2/3/4/5 仓库 CSV；
- 获取仓库基本信息；
- 获取 community profile；
- 获取语言分布；
- 分页获取项目标签；
- 固定 GitHub REST API `2026-03-10`；
- 限流识别、指数退避和跨域分页保护；
- 将响应保存为不可变 gzip JSON envelope；
- 默认拒绝真实网络请求，必须显式传入 `--allow-network`；
- token 仅从环境变量读取，不进入 Raw 文件。

Issue、PR、Timeline、Review、Files 和 Checks 将在仓库级 smoke test 通过后接入。

## 本地运行

项目使用 `src` 布局。未安装包时，在 PowerShell 中设置：

```powershell
$env:PYTHONPATH = "src"
```

查看 Wave 1 仓库：

```powershell
python -m oss_mentor list-repositories --wave 1
```

查看请求计划，不访问网络：

```powershell
python -m oss_mentor collect-repositories --wave 1 --dry-run
```

只查看一个仓库：

```powershell
python -m oss_mentor collect-repositories `
  --wave 1 `
  --repo eslint/eslint `
  --dry-run
```

## 真实公共 API smoke test

复制 `.env.example` 中需要的变量到本地环境，但不要创建或提交包含真实 token 的文件。

```powershell
$env:PYTHONPATH = "src"
$env:GITHUB_TOKEN = "<read-only-runtime-secret>"
python -m oss_mentor collect-repositories `
  --wave 1 `
  --repo eslint/eslint `
  --allow-network
```

如果仅进行非常小的匿名公共测试，可显式使用：

```powershell
python -m oss_mentor collect-repositories `
  --wave 1 `
  --repo eslint/eslint `
  --allow-network `
  --allow-anonymous
```

匿名 GitHub API 限额较低，不适合完整 Wave 回填。

Raw 响应默认写入：

```text
data/raw/<endpoint>/<owner>/<repo>/<yyyy-mm-dd>/*.json.gz
```

`data/` 已被 `.gitignore` 排除。

## 运行测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## 对比 Ecosyste.ms 与 GitHub

对单个试点仓库抽样比较两个公共数据源，不需要 PostgreSQL 或 Docker：

```powershell
$env:PYTHONPATH = "src"
python -m oss_mentor compare-issue-sources `
  --wave 1 `
  --repo eslint/eslint `
  --sample-size 10 `
  --allow-network `
  --allow-anonymous `
  --output data/validation/eslint_source_comparison.json
```

Ecosyste.ms 用于候选发现和历史元数据，GitHub API 用于补齐数字 Issue ID、
当前正文以及需要额外端点才能得到的 Timeline、Review 和 CI 信息。

## SQLite 候选池

同步少量候选并通过 GitHub 当前状态复核：

```powershell
$env:PYTHONPATH = "src"
python -m oss_mentor sync-candidates `
  --all-enabled `
  --limit-per-repo 20 `
  --output data/reports/all_enabled_sync.json `
  --allow-network
```

全量同步需要在当前 PowerShell 会话设置只读 `GITHUB_TOKEN`。可先用
`--dry-run` 查看 28 个仓库、标签和请求量估算。刷新超过 24 小时的状态并生成聚合报告：

```powershell
python -m oss_mentor refresh-candidates `
  --all-enabled `
  --older-than-hours 24 `
  --limit 500 `
  --allow-network

python -m oss_mentor candidate-report `
  --output data/reports/candidate_pool_v0.3.json
```

查看当前可推荐候选：

```powershell
python -m oss_mentor list-candidates --eligibility eligible
```

默认数据库为 `data/oss_mentor.sqlite3`，由 Python 标准库直接管理。
同步时会优先查询试点配置中的新人候选标签，再以近期开放 Issue 补足数量；
因此同一套候选池同时支持新手信号和进阶任务发现。

提取可解释特征并查看两个通道的排序：

```powershell
python -m oss_mentor extract-features
python -m oss_mentor rank-candidates --track newcomer --limit 10
python -m oss_mentor rank-candidates --track growth --limit 10
```

导入本地匿名画像并进行个性化匹配：

```powershell
python -m oss_mentor import-profiles --file config/demo_profiles_v0.1.json
python -m oss_mentor match-candidates --profile newcomer_python_macos
python -m oss_mentor match-candidates --profile growth_python_crossplatform
```

启动本地 API 与网页：

```powershell
python -m oss_mentor serve-api
```

然后访问 `http://127.0.0.1:8765/`，可使用两个预设画像，也可填写语言、平台、任务偏好和技能水平生成一次性自定义推荐。语言和任务类型采用硬约束，界面会读取当前组合的真实库存、禁用零库存选项，并在无匹配任务时阻止提交。自定义画像只在内存中参与本次计算，不写入 SQLite。每条推荐可标记为“感兴趣 / 不适合 / 已开始 / 已完成”，当前状态和变化事件保存在本地。

测试不访问 GitHub 网络，覆盖配置校验、分页、跨域保护、Raw envelope、CLI dry-run 和 SQL 迁移依赖顺序。

## PostgreSQL schema

初始迁移需要 PostgreSQL 和 `pgcrypto` 扩展：

```powershell
psql "$env:DATABASE_URL" -v ON_ERROR_STOP=1 `
  -f db/migrations/001_initial_p0.sql
```

迁移创建两个 schema：

- `oss_mentor`：标准化事实、快照、推荐和研究数据；
- `oss_mentor_private`：加密身份映射和 secret reference，应限制分析角色访问。

本机若没有 PostgreSQL，只能执行静态迁移测试，不能替代在真实 PostgreSQL 上的事务验证。

## 安全规则

- 不提交 token、`.env`、Raw 数据或本地数据库；
- 不用用户 OAuth token 承担公共历史回填；
- 不将 GitHub login、邮箱或完整评论正文写入分析主表；
- 不通过代理、多个账号或汇集 token 绕过限流；
- 未确认许可证前，不读取或持久化仓库代码内容；
- T1/T2 的最终改动规模、Review 和 CI 结果不得泄漏到同一任务的 T0 推荐特征。

## 下一实施阶段

1. 扩大候选仓库与任务池，并定期复核 Issue 的可领取状态；
2. 用首次贡献者和进阶开发者各完成一轮可用性测试；
3. 输出感兴趣率、开始率、完成率和不适合率的本地统计页；
4. 根据真实反馈校准匹配权重与技能缺口解释；
5. 再评估是否需要 GitHub 登录、持久化用户画像和 PostgreSQL 部署。
