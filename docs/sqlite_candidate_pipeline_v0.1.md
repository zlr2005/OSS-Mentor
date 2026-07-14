# SQLite 候选任务流水线 v0.1

## 1. 当前能力

流水线使用 Python 标准库和 SQLite，不依赖 PostgreSQL、Docker 或第三方 Python
包。它完成以下步骤：

1. 按试点配置中的新人标签从 Ecosyste.ms 优先发现任务；
2. 用近期开放 Issue 补充进阶候选；
3. 逐个通过 GitHub API 补齐数字 ID、正文和当前状态；
4. 仅对通过基础门槛的少量任务查询 Timeline，检查关联开放 PR；
5. 计算候选资格并幂等写入 `data/oss_mentor.sqlite3`。

## 2. 资格规则 v0.1

| 条件 | 结果 | 原因码 |
|---|---|---|
| 实际对象是 PR | `excluded` | `pull_request_not_issue` |
| Issue 已关闭 | `excluded` | `not_open` |
| 已分配 | `temporarily_ineligible` | `already_assigned` |
| 已锁定 | `temporarily_ineligible` | `locked` |
| 已有关联开放 PR | `temporarily_ineligible` | `linked_open_pr` |
| 带 blocked / needs clarification 等标签 | `temporarily_ineligible` | `blocking_label:*` |
| 尚未经过 GitHub 复核 | `unknown` | `requires_github_verification` |
| 当前开放、未分配、未锁定、无关联开放 PR | `eligible` | 无 |

`good first issue`、`first-contribution`、`help wanted` 等标签只设置
`newcomer_label_signal`，不直接决定是否可推荐。没有新人标签但通过门槛的任务，
可以进入进阶候选池。

## 3. 真实试跑结果

执行日期：2026-07-12。

### `eslint/eslint`

- 发现并通过 GitHub 复核 5 个近期候选；
- 4 个已分配；
- 1 个未分配，但 Timeline 显示已有开放关联 PR；
- 最终 0 个可推荐。该结果验证了推荐前 Timeline 门禁的必要性。

### `matplotlib/matplotlib`

- 优先发现并复核 3 个带 `first-contribution` 标签的任务；
- 1 个已有开放关联 PR；
- 1 个带 `status: needs clarification`；
- 1 个通过 v0.1 门槛，可进入新手候选池。

通过门槛的任务为 `matplotlib/matplotlib#31936`。这只是数据规则结果，产品展示
前仍需进一步计算正文清晰度、环境搭建难度和技能匹配度。

## 4. 本地命令

```powershell
$env:PYTHONPATH = "src"

python -m oss_mentor sync-candidates `
  --wave 1 `
  --repo matplotlib/matplotlib `
  --limit 3 `
  --allow-network `
  --allow-anonymous

python -m oss_mentor list-candidates --eligibility eligible
python -m oss_mentor list-candidates --newcomer-only
```

## 5. 下一版边界

v0.1 尚未判断评论中的非正式认领，也未计算正文清晰度、任务难度、预计工作量
和用户技能匹配。这些属于下一阶段的特征提取与排序，不应混入当前硬性资格门禁。
