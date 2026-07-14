# 任务特征与双通道排序 v0.1

## 1. 定位

本版本是可解释的任务侧排序基线，不是机器学习模型。它只使用推荐时可获得的
Issue 标题、正文、标签、评论数和候选资格，避免使用 PR 最终结果造成数据泄漏。

流水线先执行硬性资格门禁，再计算软特征和排序分。被排除或暂不可推荐的任务，
两个通道的分数均为 0。

## 2. 提取字段

| 特征 | 规则摘要 |
|---|---|
| `has_reproduction_steps` | 检测复现步骤、最小示例等结构 |
| `has_acceptance_criteria` | 检测验收标准、Definition of Done、未完成清单 |
| `has_expected_behavior` | 检测预期行为或预期结果 |
| `has_affected_module_hint` | 检测代码路径、文件名、模块或后端提示 |
| `task_types` | bug、文档、测试、重构、性能、构建工具、功能等，可多选 |
| `text_clarity_score` | 根据标题、正文长度、结构化信息和代码块计算 `0–100` |
| 四类难度 | 代码、环境、项目上下文、协作，范围 `0–3` |
| `estimated_effort_bucket` | `under_2h`、`half_day`、`one_day`、`multi_day` |

每条记录还保存 `feature_evidence_json`，包括正文长度、评论数、是否含代码块、
是否命中新人标签和公式版本，便于解释与调试。

## 3. 新手通道

新手分综合以下因素：

- 新人标签信号；
- 正文清晰度；
- 较低的代码难度；
- 较低的环境和项目上下文难度。

`newcomer_score = novice_fit_probability × 100`。当前查询会要求任务同时满足：

1. `candidate_eligibility = eligible`；
2. `newcomer_label_signal = true`。

这是有意采用的保守策略，避免把没有任何新人证据的普通任务直接推荐给零贡献者。

## 4. 进阶通道

成长价值分综合：

- 正文清晰度；
- 代码难度；
- 项目上下文难度；
- 任务类型的复合程度。

它偏好“可执行、信息清楚，并且能扩展技能边界”的任务。v0.1 仍是任务侧分数，
尚未结合某个开发者已有技能，因此不能称为完整的个性化成长推荐。

## 5. 真实样本结果

在 8 个已同步真实候选中，资格门禁后只剩
`matplotlib/matplotlib#31936`：

| 指标 | 结果 |
|---|---:|
| 新手分 | 75.83 |
| 新手适配概率 | 0.758 |
| 成长价值分 | 52.50 |
| 正文清晰度 | 70.00 |
| 代码难度 | 1 |
| 环境难度 | 2 |
| 项目上下文难度 | 1 |
| 协作难度 | 1 |
| 预计工作量 | `one_day` |
| 任务类型 | bug、构建工具、测试 |

该结果说明任务具备新人标签和较完整正文，但 macOS 后端带来一定环境成本。
它可以进入新手候选池，最终是否推荐还需结合用户操作系统、Python 技能和 GUI
后端经验。

## 6. 命令

```powershell
$env:PYTHONPATH = "src"
python -m oss_mentor extract-features
python -m oss_mentor rank-candidates --track newcomer --limit 10
python -m oss_mentor rank-candidates --track growth --limit 10
```

## 7. v0.1 限制

- 关键词规则可能产生误判，需要人工标注样本校准；
- 工作量只是分桶估计，不代表真实完成时间；
- 新手标签是弱信号，不是质量保证；
- 尚未结合开发者画像、技能证据和设备环境；
- 尚未识别评论中的非正式认领。

下一步应建立开发者技能向量和任务技能要求之间的可解释匹配，分别计算“能力匹配”
与“技能跨度”，再与本文件的任务侧分数融合。
