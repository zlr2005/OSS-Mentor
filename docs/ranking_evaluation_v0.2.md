# OSS-Mentor 推荐算法离线评估 v0.2

本文件由 `python -m oss_mentor evaluate-ranking` 生成。当前仓库先提供报告位置和评估口径，真实评估应在团队完成 30-50 条人工标注后重新生成。

默认输出：

- JSON: `data/reports/ranking_evaluation_v0.2.json`
- Markdown: `docs/ranking_evaluation_v0.2.md`

评估范围包括 `Precision@5`、`Precision@10`、关键技能不匹配率、平台不匹配率、基础技能覆盖率、任务类型多样性、仓库多样性、推荐空结果率，以及 `developer-task-match-v0.1` 和 `developer-task-match-v0.2` 的 Top 10 变化。
