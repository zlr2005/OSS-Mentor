# OSS-Mentor 推荐算法离线评估 v0.2

## 概览

- 通道：`growth`
- 画像：`growth_python_crossplatform`
- 当前评估版本：`developer-task-match-v0.2`
- 标注行数：33
- 标注任务数：33
- 候选任务数：48

## 指标对比

| 版本 | P@5 | P@10 | 关键技能不匹配率 | 平台不匹配率 | 基础技能覆盖率 | 任务类型多样性 | 仓库多样性 | 空结果率 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `developer-task-match-v0.1` | 1.000 | 1.000 | 0.125 | 0.000 | 0.806 | 5 | 3 | 0.000 |
| `developer-task-match-v0.2` | 1.000 | 1.000 | 0.000 | 0.000 | 0.839 | 3 | 3 | 0.000 |

## Top 10 变化

- 新增：0
- 移出：1
- 排名移动：6

## 当前 Top 10

| 排名 | 任务 | 分数 | 标注适配 | 主要原因 |
|---:|---|---:|---:|---|
| 1 | `vercel/next.js#38863` | 68.55 | 2.00 | skill_coverage=0.87, preferred_language, preferred_task_type, stretch_target=1, v0.2_weighting |
| 2 | `scikit-learn/scikit-learn#22827` | 61.80 | 2.00 | skill_coverage=1.00, preferred_language, preferred_task_type, stretch_target=1, v0.2_weighting |
| 3 | `nodejs/undici#4143` | 60.20 | 3.00 | skill_coverage=0.75, preferred_language, preferred_task_type, stretch_target=1, v0.2_weighting |
| 4 | `vercel/next.js#41281` | 68.55 | 2.00 | skill_coverage=0.87, preferred_language, preferred_task_type, stretch_target=1, v0.2_weighting |
| 5 | `nodejs/undici#4144` | 60.20 | 3.00 | skill_coverage=0.75, preferred_language, preferred_task_type, stretch_target=1, v0.2_weighting |
| 6 | `vercel/next.js#42846` | 68.55 | 2.00 | skill_coverage=0.87, preferred_language, preferred_task_type, stretch_target=1, v0.2_weighting |
| 7 | `nodejs/undici#4287` | 60.20 | 3.00 | skill_coverage=0.75, preferred_language, preferred_task_type, stretch_target=1, v0.2_weighting |

## 权重变化依据

- v0.2 raises the skill-coverage contribution to reduce critical skill misses.
- v0.2 keeps explicit platform requirements as hard filters.
- v0.2 applies repository-balanced top-k selection to reduce concentration.

## 限制

- The report uses manually annotated samples and does not train a model.
- Small annotation sets can make diversity and precision metrics unstable.
- Feedback events are summarized separately and are not used for automatic reranking.
