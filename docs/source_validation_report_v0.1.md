# Ecosyste.ms 与 GitHub 数据源验证报告 v0.1

## 1. 验证结论

Ecosyste.ms 可以作为 OSS-Mentor 的 Issue/PR 候选索引和历史元数据来源，
但不能单独作为实时推荐数据源。所有进入推荐列表的候选任务，仍须通过
GitHub API 做当前状态、正文和资格复核。

建议的数据流为：

```text
Ecosyste.ms 候选发现
  -> GitHub API 当前状态与正文补全
  -> OSS-Mentor 特征计算
  -> 推荐前再次检查 open / assignee / linked PR
```

## 2. 验证条件

| 项目 | 值 |
|---|---|
| 试点仓库 | `eslint/eslint` |
| 执行时间 | 2026-07-12 14:25（Asia/Shanghai） |
| 抽样规则 | 两个来源各取按更新时间倒序的 10 个开放 Issue，排除 PR |
| GitHub 模式 | 匿名公共 REST API |
| Ecosyste.ms 最近同步时间 | 2026-07-09 16:01（Asia/Shanghai） |
| 可复现 JSON | `data/validation/eslint_source_comparison.json`（本地数据，不提交 Git） |

## 3. 字段覆盖结果

Ecosyste.ms 在 10 个样本中完整提供了以下字段：

- Issue 编号、页面 URL、创建时间；
- 标题、标签、状态、负责人状态；
- 作者关系、评论数、最后活动时间。

它不能直接提供：

- GitHub 数字 Issue ID：Ecosyste.ms 的 `uuid` 是自身记录键，不能映射为
  数据字典中的 `github_issue_id`；
- Issue 正文；
- 参与者数量、关联开放 PR；
- 从正文派生的复现步骤、验收标准、预期行为和模块线索。

因此，在本次比较的 11 个通用任务字段中，Ecosyste.ms 可直接承担 10 个，
GitHub 数字 Issue ID 必须在补全阶段取得。正文及基于正文的特征也必须来自
GitHub。

## 4. 两个来源的一致性

两组“最近更新的 10 个 Issue”只有 5 个编号重合，说明约三天的同步延迟已经
足以改变实时候选排序。对这 5 个重合样本：

| 字段 | 一致数 / 可比较数 | 判断 |
|---|---:|---|
| Issue 编号 | 5 / 5 | 一致 |
| 页面 URL | 5 / 5 | 一致 |
| 创建时间 | 5 / 5 | 一致 |
| 标题 | 5 / 5 | 一致 |
| 状态 | 5 / 5 | 一致 |
| 负责人状态 | 5 / 5 | 一致 |
| 作者关系 | 5 / 5 | 一致 |
| 评论数 | 4 / 5 | 存在同步延迟 |
| 标签 | 3 / 5 | 存在标签变更或同步延迟 |
| 最后活动时间 | 0 / 5 | Ecosyste.ms 快照明显落后 |

这说明 Ecosyste.ms 的稳定身份和基础描述可靠，但评论数、标签和最后活动时间
不能直接用于“当前是否适合领取”的最终判断。

## 5. MVP 接入决策

1. 候选发现阶段查询 Ecosyste.ms，降低 GitHub 搜索和历史回填成本。
2. 对进入候选池的 Issue 调用 GitHub API，补齐 `github_issue_id`、正文和最新
   标签、负责人、评论及更新时间。
3. 需要 `has_linked_open_pr`、参与者和认领线索时，再查询 Timeline/Comments，
   不对所有 Issue 全量请求。
4. 推荐展示前执行轻量重新验证，状态变化的任务标记为暂不可推荐。
5. MVP 将结果保存为 SQLite/Parquet；公共数据源不能代替本地状态、推荐日志和
   用户反馈存储。

## 6. 验证命令

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
