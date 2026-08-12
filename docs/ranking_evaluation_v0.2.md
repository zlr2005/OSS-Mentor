# OSS-Mentor 推荐算法离线评估 v0.2

## 概览

- 通道：`growth`
- 画像：`growth_python_crossplatform`
- 当前评估版本：`developer-task-match-v0.2`
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

| 版本 | P@5 | P@10 | 关键技能不匹配率 | 平台不匹配率 | 基础技能覆盖率 | 任务类型多样性 | 仓库多样性 | 空结果率 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `developer-task-match-v0.1` | 1.000 | 1.000 | 0.700 | 0.000 | 0.661 | 7 | 4 | 0.000 |
| `developer-task-match-v0.2` | 1.000 | 1.000 | 0.000 | 0.000 | 0.904 | 3 | 1 | 0.000 |

## Top 10 变化

- 新增：0
- 移出：7
- 排名移动：3

## 当前 Top 10

| 排名 | 任务 | 分数 | 标注适配 | 主要原因 |
|---:|---|---:|---:|---|
| 1 | `vercel/next.js#42846` | 82.34 | 2.00 | skill_coverage=0.89, preferred_language, preferred_task_type, stretch_target=1, v0.2_weighting |
| 2 | `vercel/next.js#41281` | 81.44 | 2.00 | skill_coverage=0.91, preferred_language, preferred_task_type, stretch_target=1, v0.2_weighting |
| 3 | `vercel/next.js#38863` | 80.61 | 2.00 | skill_coverage=0.91, preferred_language, preferred_task_type, stretch_target=1, v0.2_weighting |

## 权重变化依据

- v0.2 raises the skill-coverage contribution to reduce critical skill misses.
- v0.2 keeps explicit platform requirements as hard filters.
- v0.2 applies repository-balanced top-k selection to reduce concentration.

## 限制

- The report uses manually annotated samples and does not train a model.
- Small annotation sets can make diversity and precision metrics unstable.
- Feedback events are summarized separately and are not used for automatic reranking.
