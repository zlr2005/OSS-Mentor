# OSS-Mentor 任务适配人工标注指南 v0.1

## 目标

本标注集用于评估推荐排序是否把合适任务排给合适通道，不用于训练模型。每条记录以 `repository + issue_number` 定位一个候选任务，至少标注 30 条，建议 30-50 条；其中至少 10 条由两名成员独立复标，用于检查一致性。

## 字段

| 字段 | 说明 |
|---|---|
| `repository` | GitHub 仓库全名，例如 `matplotlib/matplotlib` |
| `issue_number` | Issue 编号 |
| `newcomer_fit` | 对新手破冰通道的适配度，0-3 |
| `growth_fit` | 对进阶成长通道的适配度，0-3 |
| `code_difficulty` | 代码难度，0-3 |
| `setup_difficulty` | 环境难度，0-3 |
| `clarity` | 描述清晰度，0-3 |
| `required_skills` | 逗号分隔技能，如 `Python, testing`；平台要求写为 `platform:macos` |
| `critical_blocker` | 是否存在关键阻断项，填 `0/1` 或 `false/true` |
| `annotation_reason` | 一句话解释主要判断依据 |
| `annotator` | 标注员代号，不写真实身份信息 |

## 评分口径

适配度：

| 分值 | 含义 |
|---:|---|
| 0 | 不适合该通道，或存在明显关键阻断 |
| 1 | 勉强适合，需要较多额外支持 |
| 2 | 比较适合，可推荐 |
| 3 | 非常适合，应优先推荐 |

难度和清晰度：

| 分值 | 难度字段含义 | 清晰度字段含义 |
|---:|---|---|
| 0 | 几乎无代码/环境门槛 | 描述缺失或不可判断 |
| 1 | 低门槛，适合首次尝试 | 基本清楚 |
| 2 | 中等门槛，需要一定上下文 | 较清楚，有关键线索 |
| 3 | 高门槛或跨模块复杂任务 | 非常清楚，有复现/验收/模块线索 |

## 关键阻断项

任一情况可标记 `critical_blocker=1`：

- Issue 已关闭、已分配、已有活跃 PR，或维护状态不可贡献。
- 明确平台要求与目标画像不匹配，例如只支持 Windows 但画像是 macOS。
- 缺少关键主语言能力，且任务无法作为轻量学习任务处理。
- Issue 描述过少，无法判断具体贡献路径。
- 涉及大范围架构、长期设计讨论或高风险发布流程。

## 一致性检查

双人复标的 10 条任务不需要强行一致。离线评估会统计同一任务在目标通道上的标注差异；若两名标注员评分差距大于 1，应在报告中列为需要讨论的样本，并回看 `annotation_reason`。

## 文件

默认标注文件：

```text
data/annotations/task_fit_v0.1.csv
```

评估命令：

```powershell
$env:PYTHONPATH = "src"
python -m oss_mentor evaluate-ranking `
  --track newcomer `
  --annotations data/annotations/task_fit_v0.1.csv

python -m oss_mentor evaluate-ranking `
  --track growth `
  --annotations data/annotations/task_fit_v0.1.csv
```
