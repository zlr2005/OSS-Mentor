# OSS-Mentor 推荐算法离线评估 v0.3

## 概览

- 通道：`newcomer`
- 画像：`newcomer_python_linux`
- 当前评估版本：`developer-task-match-v0.3`
- 标注行数：33
- 标注任务数：33
- 标注员数：1
- 双人复标任务数：0
- 候选任务数：48

## 标注验收

- 至少 30 个任务：通过
- 至少 2 名标注员：未通过
- 至少 10 个双人复标任务：未通过
- 无开发期伪标注员：未通过
- 标注总体验收：未通过

## 指标对比

| 版本 | P@5 | P@10 | 关键技能不匹配率 | 平台不匹配率 | 技能覆盖率 | 类型数 | 仓库数 | 最大仓库占比 | 最大类型占比 | 仓库超限 | 失效泄漏率 | 空结果率 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `developer-task-match-v0.1` | 1.000 | 1.000 | 0.000 | 0.000 | 0.798 | 3 | 2 | 0.750 | 0.750 | 0 | 0.000 | 0.000 |
| `developer-task-match-v0.2` | 1.000 | 1.000 | 0.000 | 0.000 | 0.798 | 3 | 2 | 0.750 | 0.750 | 0 | 0.000 | 0.000 |
| `developer-task-match-v0.3` | 1.000 | 1.000 | 0.000 | 0.000 | 0.798 | 3 | 2 | 0.750 | 0.750 | 0 | 0.000 | 0.000 |

## Top 10 变化

- 新增：0
- 移出：0
- 排名移动：4

## 当前 Top 10

| 排名 | 任务 | 分数 | 标注适配 | 主要原因 |
|---:|---|---:|---:|---|
| 1 | `matplotlib/matplotlib#8088` | 76.95 | 2.00 | skill_coverage=0.82, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, fresh_issue, active_repository, newcomer_signal, issue_clarity |
| 2 | `matplotlib/matplotlib#17479` | 76.95 | 3.00 | skill_coverage=0.82, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, fresh_issue, active_repository, newcomer_signal, issue_clarity |
| 3 | `pytorch/ao#3637` | 73.33 | 3.00 | skill_coverage=0.78, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, fresh_issue, active_repository, newcomer_signal, issue_clarity |
| 4 | `matplotlib/matplotlib#31935` | 75.73 | 3.00 | skill_coverage=0.78, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, fresh_issue, active_repository, newcomer_signal, issue_clarity |

## 权重变化依据

- v0.3 makes candidate availability and declared operating systems hard filters.
- v0.3 scores repository activity, issue freshness and contribution guidance explicitly.
- v0.3 caps a repository at three Top-10 positions and rewards task-type coverage.

## 限制

- The report uses manually annotated samples and does not train a model.
- Small annotation sets can make diversity and precision metrics unstable.
- Feedback events are summarized separately and are not used for automatic reranking.
