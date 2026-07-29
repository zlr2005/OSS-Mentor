# OSS-Mentor 推荐算法离线评估 v0.2

## 概览

- 通道：`newcomer`
- 画像：`newcomer_python_linux`
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
| `developer-task-match-v0.1` | 1.000 | 1.000 | 0.000 | 0.000 | 0.875 | 1 | 2 | 0.000 |
| `developer-task-match-v0.2` | 1.000 | 1.000 | 0.000 | 0.000 | 0.875 | 1 | 2 | 0.000 |

## Top 10 变化

- 新增：0
- 移出：0
- 排名移动：2

## 当前 Top 10

| 排名 | 任务 | 分数 | 标注适配 | 主要原因 |
|---:|---|---:|---:|---|
| 1 | `matplotlib/matplotlib#8088` | 90.57 | 2.00 | skill_coverage=0.87, preferred_language, preferred_task_type, newcomer_label_required, v0.2_weighting |
| 2 | `pytorch/ao#3637` | 90.57 | 3.00 | skill_coverage=0.87, preferred_language, preferred_task_type, newcomer_label_required, v0.2_weighting |
| 3 | `matplotlib/matplotlib#17479` | 90.57 | 3.00 | skill_coverage=0.87, preferred_language, preferred_task_type, newcomer_label_required, v0.2_weighting |
| 4 | `matplotlib/matplotlib#31935` | 89.52 | 3.00 | skill_coverage=0.87, preferred_language, preferred_task_type, newcomer_label_required, v0.2_weighting |

## 权重变化依据

- v0.2 raises the skill-coverage contribution to reduce critical skill misses.
- v0.2 keeps explicit platform requirements as hard filters.
- v0.2 applies repository-balanced top-k selection to reduce concentration.

## 限制

- The report uses manually annotated samples and does not train a model.
- Small annotation sets can make diversity and precision metrics unstable.
- Feedback events are summarized separately and are not used for automatic reranking.
