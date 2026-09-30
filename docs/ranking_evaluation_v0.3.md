# OSS-Mentor 推荐算法离线评估 v0.3

## 概览

- 通道：`growth`
- 画像：`growth_python_crossplatform`
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
| `developer-task-match-v0.1` | 0.800 | 0.900 | 0.000 | 0.000 | 0.879 | 5 | 4 | 0.400 | 0.500 | 1 | 0.000 | 0.000 |
| `developer-task-match-v0.2` | 0.800 | 0.900 | 0.000 | 0.000 | 0.879 | 5 | 4 | 0.400 | 0.500 | 1 | 0.000 | 0.000 |
| `developer-task-match-v0.3` | 0.800 | 0.889 | 0.000 | 0.000 | 0.880 | 5 | 4 | 0.333 | 0.556 | 0 | 0.000 | 0.000 |

## Top 10 变化

- 新增：0
- 移出：1
- 排名移动：6

## 当前 Top 10

| 排名 | 任务 | 分数 | 标注适配 | 主要原因 |
|---:|---|---:|---:|---|
| 1 | `vercel/next.js#95745` | 75.68 | 3.00 | skill_coverage=0.91, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, skill_stretch, fresh_issue, active_repository, issue_clarity, growth_value |
| 2 | `matplotlib/matplotlib#8088` | 73.86 | 2.00 | skill_coverage=0.82, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, skill_stretch, fresh_issue, active_repository, issue_clarity, growth_value |
| 3 | `matplotlib/matplotlib#17479` | 73.86 | 1.00 | skill_coverage=0.82, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, skill_stretch, fresh_issue, active_repository, issue_clarity, growth_value |
| 4 | `scikit-learn/scikit-learn#22827` | 67.00 | 2.00 | skill_coverage=1.00, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, skill_stretch, fresh_issue, active_repository, issue_clarity, growth_value |
| 5 | `vercel/next.js#38863` | 72.10 | 2.00 | skill_coverage=0.87, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, skill_stretch, fresh_issue, active_repository, issue_clarity, growth_value |
| 6 | `vercel/next.js#41281` | 72.10 | 2.00 | skill_coverage=0.87, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, skill_stretch, fresh_issue, active_repository, issue_clarity, growth_value |
| 7 | `nodejs/undici#4143` | 67.30 | 3.00 | skill_coverage=0.87, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, skill_stretch, fresh_issue, active_repository, issue_clarity, growth_value |
| 8 | `nodejs/undici#4144` | 67.30 | 3.00 | skill_coverage=0.87, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, skill_stretch, fresh_issue, active_repository, issue_clarity, growth_value |
| 9 | `nodejs/undici#4287` | 67.30 | 3.00 | skill_coverage=0.87, preferred_language, preferred_task_type, language_match, task_type_match, skill_match, skill_stretch, fresh_issue, active_repository, issue_clarity, growth_value |

## 权重变化依据

- v0.3 makes candidate availability and declared operating systems hard filters.
- v0.3 scores repository activity, issue freshness and contribution guidance explicitly.
- v0.3 caps a repository at three Top-10 positions and rewards task-type coverage.

## 限制

- The report uses manually annotated samples and does not train a model.
- Small annotation sets can make diversity and precision metrics unstable.
- Feedback events are summarized separately and are not used for automatic reranking.
