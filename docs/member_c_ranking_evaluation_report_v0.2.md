# OSS-Mentor 成员 C 阶段报告：推荐算法与离线评估 v0.2

## 1. 总体说明

本阶段成员 C 负责的问题是：**系统应该把哪些开源 Issue 推荐给哪类开发者，以及推荐结果是否合理**。

OSS-Mentor 当前有两个推荐通道：

- 新手破冰：面向零贡献或首次贡献者，优先推荐低风险、说明清楚、有新人友好信号的任务。
- 进阶成长：面向已有一定经验的开发者，优先推荐略高于当前能力、能带来技能成长的任务。

本阶段已经完成从“候选任务数据”到“推荐排序”再到“离线评估报告”的闭环。换句话说，现在系统不仅能给出推荐，还能用一份标注集检查推荐结果是否符合预期。

## 2. 已完成工作

### 2.1 推荐算法版本升级

当前保留两个算法版本：

| 版本 | 说明 |
|---|---|
| `developer-task-match-v0.1` | 原有推荐规则，用作对照基线 |
| `developer-task-match-v0.2` | 本阶段新增版本，作为当前默认评估版本 |

v0.2 的主要改动是：

- 更重视开发者技能与任务要求之间的匹配程度。
- 保留平台要求的硬过滤，例如任务明确要求特定操作系统时不能错误推荐。
- 降低关键技能不匹配的风险。
- 在 Top 推荐中减少单一仓库过度集中的情况。

这些改动仍然是可解释规则，不是机器学习模型，也没有使用真实用户反馈自动训练。

### 2.2 标注集准备

新增标注文件：

```text
data/annotations/task_fit_v0.1.csv
```

这份标注集用来回答一个问题：**某个任务人工判断上是否适合推荐给新手或进阶开发者**。

当前标注集包含 33 条任务记录。字段包括：

| 字段 | 含义 |
|---|---|
| `repository` | GitHub 仓库名 |
| `issue_number` | Issue 编号 |
| `newcomer_fit` | 对新手通道的适合程度，0-3 |
| `growth_fit` | 对进阶通道的适合程度，0-3 |
| `code_difficulty` | 代码难度，0-3 |
| `setup_difficulty` | 环境难度，0-3 |
| `clarity` | Issue 描述清晰度，0-3 |
| `required_skills` | 需要的主要技能 |
| `critical_blocker` | 是否存在关键阻断项 |
| `annotation_reason` | 标注理由 |
| `annotator` | 标注来源 |

当前 `annotator = codex_pseudo`，表示这是一版规则辅助伪人工标注，适合开发期验证。正式验收时建议团队成员再抽样复核，尤其是至少 10 条双人复标。

### 2.3 离线评估能力

新增命令：

```powershell
python -m oss_mentor evaluate-ranking `
  --database data\oss_mentor_demo.sqlite3 `
  --track growth `
  --annotations data\annotations\task_fit_v0.1.csv `
  --profile growth_python_crossplatform `
  --output data\reports\ranking_evaluation_v0.2.json `
  --markdown-output docs\ranking_evaluation_v0.2.md
```

这个命令会：

1. 读取本地 SQLite 候选任务。
2. 读取开发者画像。
3. 运行 v0.2 推荐算法。
4. 用标注集判断推荐结果是否适合。
5. 生成 JSON 和 Markdown 两种评估报告。

报告文件：

```text
data/reports/ranking_evaluation_v0.2.json
docs/ranking_evaluation_v0.2.md
```

## 3. 当前评估结果

本次评估使用：

| 项目 | 值 |
|---|---|
| 推荐通道 | `growth` |
| 开发者画像 | `growth_python_crossplatform` |
| 当前算法版本 | `developer-task-match-v0.2` |
| 标注任务数 | 33 |
| 候选任务数 | 48 |
| 评估 warning | 0 |

`warning = 0` 表示推荐结果与标注集已经对齐，没有出现“推荐了但没标注”或“标注了但候选库里没有”的问题。

### 3.1 v0.1 与 v0.2 指标对比

| 指标 | v0.1 | v0.2 | 变化说明 |
|---|---:|---:|---|
| Precision@5 | 1.000 | 1.000 | Top 5 推荐均被标注为适合 |
| Precision@10 | 1.000 | 1.000 | Top 推荐整体命中稳定 |
| 关键技能不匹配率 | 0.125 | 0.000 | v0.2 消除了关键技能不匹配 |
| 平台不匹配率 | 0.000 | 0.000 | 两个版本都没有平台错配 |
| 基础技能覆盖率 | 0.806 | 0.839 | v0.2 技能匹配更好 |
| 任务类型多样性 | 5 | 3 | v0.2 更聚焦，但类型更少 |
| 仓库多样性 | 3 | 3 | 仓库覆盖保持不变 |
| 空推荐率 | 0.000 | 0.000 | 两个版本都能给出推荐 |

最重要的结论是：

> v0.2 在保持推荐命中率的同时，把关键技能不匹配率从 0.125 降低到 0，并把基础技能覆盖率从 0.806 提升到 0.839。

这说明 v0.2 的推荐结果更稳，不容易把技能要求不匹配的任务推荐给进阶开发者。

### 3.2 当前 Top 推荐

v0.2 当前推荐结果包括：

| 排名 | 任务 | 标注适配 | 说明 |
|---:|---|---:|---|
| 1 | `vercel/next.js#38863` | 2 | JavaScript bug 修复，适合有一定前端经验的开发者 |
| 2 | `scikit-learn/scikit-learn#22827` | 2 | Python 测试稳定性任务，适合进阶成长 |
| 3 | `nodejs/undici#4143` | 3 | HTTP/2 相关功能任务，成长价值较高 |
| 4 | `vercel/next.js#41281` | 2 | Next.js 错误处理相关 bug 修复 |
| 5 | `nodejs/undici#4144` | 3 | HTTP header 处理相关任务，偏进阶 |
| 6 | `vercel/next.js#42846` | 2 | TypeScript 类型相关 bug 修复 |
| 7 | `nodejs/undici#4287` | 3 | 底层 API 设计任务，适合进阶开发者 |

其中标注适配分含义为：

| 分值 | 含义 |
|---:|---|
| 0 | 不适合 |
| 1 | 勉强适合 |
| 2 | 比较适合 |
| 3 | 非常适合 |

当前 Top 推荐的适配分均为 2 或 3，因此在现有标注口径下推荐质量较好。

### 3.3 排名变化

从 v0.1 到 v0.2，主要变化包括：

- `scikit-learn/scikit-learn#22827` 从第 8 名上升到第 2 名。
- `nodejs/undici#4143` 从第 5 名上升到第 3 名。
- `vercel/next.js#95745` 被移出当前 Top 推荐。

这个变化符合 v0.2 的目标：更重视技能覆盖和可控成长，不只看任务本身的复杂度或成长价值。

## 4. 提供给成员 D 的接口与集成点

成员 D 负责系统集成、API 和页面展示时，可以使用以下交付内容。

### 4.1 推荐算法入口

代码位置：

```text
src/oss_mentor/matching.py
```

主要函数：

```python
rank_for_profile(profile, tasks, limit=20, match_version="developer-task-match-v0.2")
```

用途：给定开发者画像和候选任务，返回排序后的推荐任务。

当前支持的算法版本：

```text
developer-task-match-v0.1
developer-task-match-v0.2
```

### 4.2 离线评估入口

命令：

```powershell
python -m oss_mentor evaluate-ranking `
  --database data\oss_mentor_demo.sqlite3 `
  --track growth `
  --annotations data\annotations\task_fit_v0.1.csv `
  --profile growth_python_crossplatform `
  --output data\reports\ranking_evaluation_v0.2.json `
  --markdown-output docs\ranking_evaluation_v0.2.md
```

用途：生成推荐算法评估报告，供团队状态页、项目文档或阶段验收使用。

### 4.3 反馈统计入口

命令行：

```powershell
python -m oss_mentor feedback-summary --database data\oss_mentor_demo.sqlite3
```

API：

```http
GET /api/v1/feedback/summary
```

返回结构示例：

```json
{
  "summary": {
    "current": {
      "total": 0,
      "interested": 0,
      "not_suitable": 0,
      "started": 0,
      "completed": 0
    },
    "by_track": {
      "newcomer": {},
      "growth": {}
    },
    "transitions": {
      "interested_to_started": 0,
      "started_to_completed": 0
    }
  },
  "api_version": "v0.4"
}
```

注意：当前 demo 数据库没有真实用户点击反馈，所以反馈统计为 0 是正常现象。

### 4.4 OpenAPI 文档

接口说明已同步到：

```text
docs/openapi_v0.4.yaml
```

D 可以据此接系统状态页或反馈统计页面。

## 5. 当前限制

当前阶段结果能够证明离线评估链路已经打通，但还存在以下限制：

- 当前标注集为规则辅助伪人工标注，尚未完成真实人工复核。
- 双人复标数量为 0，因此还不能计算真实标注一致性。
- `growth` 与 `newcomer` 两个通道均已生成独立评估报告，但正式标注验收仍未通过。
- 标注样本数量为 33 条，适合阶段演示，但仍属于小样本。
- 用户反馈统计目前没有真实交互数据，因此不能用于自动调参或训练。

## 6. 下一步建议

建议后续按以下顺序推进：

1. 由团队成员抽样复核当前 33 条标注，至少完成 10 条双人复标。
2. 复跑 `growth` 与 `newcomer` 两份报告，确认 `annotation_acceptance.passed = true`。
3. 让成员 D 在系统状态页展示评估摘要和反馈统计。
4. 如果后续有真实用户反馈，再比较人工标注、推荐结果和用户反馈之间的一致性。

## 7. 阶段结论

成员 C 本阶段已经完成推荐算法与离线评估的开发期闭环：

- 推荐算法 v0.2 已实现。
- v0.1/v0.2 对比评估已实现。
- 标注集格式与标注指南已建立。
- 离线评估报告已生成。
- 反馈统计查询与 API 已提供给系统集成。

当前结果显示，v0.2 在 growth 通道上保持 Precision@5 和 Precision@10 为 1.000，同时将关键技能不匹配率降为 0，说明本阶段权重调整方向有效。

## 8. 下一阶段算法改进计划

下一阶段的目标不是直接引入复杂机器学习模型，而是在当前可解释规则基础上，把推荐算法从“能给出合理排序”推进到“能稳定兼顾适配度、成长价值、多样性和反馈诊断”的推荐系统。

### 8.1 总体方向

下一阶段建议形成新版本：

```text
developer-task-match-v0.3
```

v0.3 的定位是：

> 规则可解释 + 多目标排序 + 新手可行动性 + 进阶语义成长匹配 + 更完整离线评估。

也就是说，系统不仅要推荐“看起来匹配”的任务，还要避免推荐结果过窄、过难、过于集中，或者无法被新手真正开始。

### 8.2 评估体系增强

当前评估主要使用 Precision@5 和 Precision@10。下一阶段应保留这些指标，同时补充更细的离线评估指标。

建议新增：

| 指标 | 作用 |
|---|---|
| `nDCG@5` / `nDCG@10` | 判断高适配任务是否排在更靠前的位置 |
| `MRR` | 判断第一个高质量推荐出现得是否足够早 |
| `Coverage` | 判断推荐系统覆盖了多少候选任务，而不是只推荐少数任务 |
| `Repository Diversity` | 判断 Top 推荐是否过度集中在少数仓库 |
| `Task Type Diversity` | 判断推荐任务类型是否过于单一 |
| `Critical Blocker Rate` | 判断是否仍推荐存在关键阻断的任务 |

对应实现建议：

- 在 `ranking_evaluation.py` 中扩展指标计算函数。
- 在 `ranking_evaluation_v0.2.json` 的后续版本中增加 `ranking_quality`、`diversity_quality` 和 `blocker_quality` 三类指标。
- 持续同时生成 `newcomer` 和 `growth` 两个通道的评估报告。

### 8.3 标注集正式化

当前 33 条标注为 `codex_pseudo` 规则辅助伪人工标注，适合开发期验证。下一阶段应将其升级为团队正式标注集。

建议做法：

1. 保留当前 33 条作为初始样本。
2. 团队成员逐条复核 `newcomer_fit`、`growth_fit`、`critical_blocker` 和 `annotation_reason`。
3. 至少选择 10 条任务进行双人独立复标。
4. 在评估报告中输出一致性统计，例如大分歧样本数量和分歧率。

验收口径：

- 标注任务数不少于 30。
- 双人复标任务数不少于 10。
- 所有 Top 推荐任务必须在标注集中能找到对应记录。
- `warning_count` 应保持为 0。

### 8.4 v0.3 多目标排序

当前 v0.2 已提升技能覆盖并降低关键技能不匹配，但任务类型多样性从 5 降到 3，说明推荐结果更稳但略微变窄。

v0.3 建议把最终排序拆成多个目标：

```text
final_score =
  profile_fit_score
  + growth_value_score
  + actionability_score
  + diversity_bonus
  - concentration_penalty
  - blocker_penalty
```

各部分含义：

| 组成 | 含义 |
|---|---|
| `profile_fit_score` | 开发者技能、语言、任务偏好与任务要求的匹配程度 |
| `growth_value_score` | 任务是否能带来适度成长 |
| `actionability_score` | 任务是否足够清楚、可开始、低阻塞 |
| `diversity_bonus` | 鼓励仓库和任务类型适度多样 |
| `concentration_penalty` | 惩罚 Top 推荐中过度集中的仓库或任务类型 |
| `blocker_penalty` | 惩罚关键阻断项，例如平台不匹配或描述严重不足 |

初步规则建议：

- Top 10 中同一仓库最多保留 3 条高优先级任务。
- Top 10 中同一任务类型不应长期占据大多数位置。
- 分数接近时，优先选择描述更清楚、技能覆盖更高、维护状态更健康的任务。
- `critical_blocker = 1` 的任务不进入最终推荐。

### 8.5 新手通道增强

新手通道不能只依赖 `good first issue` 或 `help wanted` 标签。标签只能说明维护者可能欢迎贡献，不一定说明任务真的适合新手完成。

下一阶段建议为新手通道新增“可行动性评分”：

```text
actionability_score =
  clarity
  + has_reproduction_steps
  + has_acceptance_criteria
  + low_code_difficulty
  + low_setup_difficulty
  + newcomer_label_signal
  - discussion_complexity
```

重点关注：

- Issue 是否有明确目标。
- 是否有复现步骤或验收标准。
- 是否能定位到模块或文件。
- 是否有新人友好标签。
- 评论区是否已经变成长期设计讨论。
- 是否有维护者最近回应。

新手通道的目标应从“低难度”升级为“低风险且可开始”。

### 8.6 进阶通道增强

进阶通道的重点不是简单推荐更难的任务，而是推荐“略高于当前能力、但仍可完成”的任务。

下一阶段建议增加轻量语义匹配：

```text
semantic_overlap_score
```

可先使用关键词和任务领域标签，不必直接上大模型。

建议增加的画像字段：

```json
{
  "learning_goals": ["testing", "performance", "api design"],
  "avoid_task_types": ["documentation"],
  "avoid_domains": ["frontend build tooling"]
}
```

对应排序逻辑：

- 任务关键词命中 `learning_goals` 时加分。
- 任务类型或领域命中 `avoid_*` 时降分。
- 技能差距为 1 级左右的任务优先作为成长任务。
- 技能差距过大或主语言不匹配的任务应过滤或强降权。

### 8.7 反馈闭环升级

当前反馈统计只记录：

```text
interested
not_suitable
started
completed
```

下一阶段不建议直接用这些反馈训练模型，因为样本量会很小，容易过拟合。更稳妥的做法是做“反馈诊断”。

建议新增分析：

- 哪些仓库推荐很多，但 `started` 很少。
- 哪些任务类型经常被标为 `not_suitable`。
- 哪些任务从 `interested` 到 `started` 转化较高。
- 新手通道和进阶通道的反馈差异。
- v0.2/v0.3 推荐结果的反馈表现对比。

这些结果可以用于人工调权，而不是自动训练。

### 8.8 建议开发顺序

建议按以下优先级推进：

1. 正式化标注集：团队复核 33 条样本，并完成至少 10 条双人复标。
2. 扩展评估指标：新增 nDCG@K、MRR、Coverage 和多样性指标。
3. 持续维护 newcomer 与 growth 通道的对照评估报告。
4. 实现 `developer-task-match-v0.3`：加入可行动性评分、多目标排序和去集中惩罚。
5. 增强画像字段：支持 `learning_goals`、`avoid_task_types` 和 `avoid_domains`。
6. 扩展反馈统计报告：按仓库、任务类型和通道输出转化情况。

### 8.9 下一阶段预期交付物

建议下一阶段输出以下文件或能力：

| 交付物 | 说明 |
|---|---|
| `developer-task-match-v0.3` | 新一版多目标可解释推荐算法 |
| `data/annotations/task_fit_v0.2.csv` | 团队复核后的正式标注集 |
| `docs/ranking_evaluation_v0.3.md` | 包含更多指标的新评估报告 |
| `data/reports/ranking_evaluation_v0.3.json` | 机器可读评估结果 |
| `docs/feedback_diagnostics_v0.1.md` | 反馈诊断报告 |
| `GET /api/v1/feedback/summary` 扩展 | 支持按仓库、任务类型和通道聚合 |

### 8.10 下一阶段目标总结

下一阶段推荐算法的目标可以总结为：

> 从“规则排序可用”升级为“多目标推荐可靠”，在保持推荐准确率的同时，提高任务可行动性、技能成长价值、结果多样性和反馈可解释性。
