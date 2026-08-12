OSS-Mentor B3 第四阶段：难度公式设计 v0.2

0. 文档放置位置

建议将上一阶段报告放到：

docs/b3_exploratory_difference_analysis_v0.1.md

本阶段设计文档建议放到：

docs/b3_difficulty_formula_design_v0.2.md

相关数据文件继续保持原位置：

data/reports/difficulty_diagnostics_v0.1.json
data/annotations/difficulty_calibration_ai_assisted_v0.1.json
data/reports/difficulty_calibration_current_predictions_v0.1.json

本阶段只做规则设计，不修改 task_features.py。

1. 当前真实调用链

根据当前 task_features.py，任务特征主流程是：

输入 record
  ├─ title
  ├─ body_text
  ├─ labels
  ├─ comment_count
  ├─ candidate_eligibility
  └─ primary_language
        ↓
extract_task_features(record)
        ↓
文本清晰度特征
  ├─ reproduction
  ├─ acceptance criteria
  ├─ expected behavior
  ├─ affected module hint
  └─ code block
        ↓
_classify_task_types(...)
        ↓
task_types
classification_evidence
performance auxiliary signal
        ↓
当前四维难度公式
        ↓
effort_bucket
        ↓
novice_fit_probability / newcomer_score
growth_value_score
        ↓
TaskFeatures
        ↓
infer_skill_requirements(record, features)
        ↓
语言、任务类型、平台技能要求

当前 TaskFeatures 对外包含：

estimated_code_difficulty
estimated_setup_difficulty
estimated_project_context_difficulty
estimated_collaboration_difficulty
estimated_effort_bucket
novice_fit_probability
newcomer_score
growth_value_score
feature_evidence
task_feature_version

匹配链路的可确认范围

本轮没有提供 src/oss_mentor/matching.py，因此不能精确声称：

matching 使用了哪些难度字段；

各字段权重是多少；

effort 是否直接参与排序；

newcomer/growth 如何进入最终排名。

当前能够确认的是：

难度会直接改变 novice_fit_probability、newcomer_score 和 growth_value_score；

Code 难度会改变 infer_skill_requirements() 生成的最低技能等级；

这些结果进入 TaskFeatures，通常会被后续持久化和排序流程消费；

第五阶段实现前必须补读 matching.py，但本阶段不得修改它。

2. 当前公式的精确问题清单

2.1 Code

当前顺序：

默认 1
纯 documentation → 0
feature/refactor → 至少 2
performance 或 core/architecture/api change → 3
newcomer label → 最多 1

规则类型：

documentation：硬赋值；

feature/refactor：max 升级；

performance/core/architecture/api change：硬赋值为 3；

newcomer：最后执行的硬上限，覆盖前面所有结果。

问题：

task type 近似直接决定 Code；

performance 信号无条件产生最高难度；

newcomer 标签覆盖技术证据；

testing、build tooling、bug fix 内部缺乏分层；

纯 documentation 被固定为 0，即使任务本质可能需要行为验证或代码调查；

没有保存 Code 评分的结构化证据。

2.2 Setup

当前顺序：

默认 1
纯 documentation → 0
全文出现 compile/native/toolchain/backend/
macOS/Windows/Linux/Docker/Kubernetes → 2

问题：

报告者环境和任务必需环境没有区分；

标题、正文、环境模板中的关键词权重相同；

没有 level 3；

一个偶然词即可覆盖 documentation 的 setup=0；

没有“明确要求多节点、GPU、native、多平台”这种强证据层级。

2.3 Project context

当前顺序：

默认 1
标签出现 core/architecture/api → 2
refactor 或 performance → 3
newcomer label → 最多 1

问题：

默认 1 导致 level 0 几乎无法出现；

refactor/performance 无条件进入 level 3；

API、跨模块、生命周期和核心架构没有分开；

newcomer 再次覆盖技术证据；

只检查标签，不充分使用标题和正文中的范围信息。

2.4 Collaboration

当前顺序：

comment_count < 3  → 0
comment_count < 10 → 1
其余              → 2
discussion/design 标签 → 至少 2

问题：

comment count 是主导变量；

评论多不等于设计协调复杂；

评论少的 RFC、API 设计、跨团队任务可能被低估；

level 3 永远不会出现；

discussion/design 只到 2，无法区分普通讨论和重大未决设计。

2.5 Effort

当前公式：

difficulty_sum =
  code + setup + context + collaboration

0–2  → under_2h
3–4  → half_day
5–6  → one_day
7–12 → multi_day

问题：

与四维等权求和完全耦合；

四个维度的语义不同，却被视为同等工时贡献；

collaboration 或 setup 的误判会直接改变工时；

不考虑任务范围、可执行性和信息质量；

roadmap/tracker 也会得到普通开发工时；

body 缺失仍输出确定工时；

两个相同总分但性质不同的任务必然得到同一工时。

3. 当前难度对下游的影响

3.1 Newcomer

当前：

novice =
  0.25
  + newcomer label 0.30
  + clarity 0.25
  + code 越低奖励 0.20
  - setup 惩罚 0.10
  - context 惩罚 0.10

因此：

Code 升高会降低 newcomer score；

Setup 升高会降低 newcomer score；

Context 升高会降低 newcomer score；

Collaboration 和 effort 不直接进入 newcomer 公式；

当前 newcomer 标签一边直接加分，一边又压低 Code/Context，形成重复奖励。

v0.2 应删除“newcomer 压低难度”的路径，但暂时保留 newcomer 分数公式，以隔离变化来源。

3.2 Growth

当前：

growth =
  clarity * 25%
  + code * 35%
  + context * 25%
  + task type 数量 * 15%

因此 Code 和 Context 的变化会明显改变 growth。

B3 不应同时修改 growth 权重，否则无法判断差异来自难度公式还是 growth 公式。

3.3 Skills

当前：

主语言最低等级：max(1, code_difficulty)；

task type 技能最低等级：

Code ≤ 1 → 1

Code ≥ 2 → 2；

平台技能根据标题/标签优先、正文次之推断。

因此 Code 改动会传播到技能要求。B3 不修改技能逻辑，但必须在新旧评估中统计技能等级变化。

3.4 Matching

由于本轮没有 matching.py，只确认存在间接影响：

difficulty
  → newcomer/growth
  → skill minimum level
  → 持久化任务特征
  → 后续匹配输入

第五阶段实现前需补读 matching，但不得修改 matching。

4. 现有 feature_evidence 是否能承载难度证据

可以。

当前 feature_evidence 是开放的 dict[str, Any]，已有：

task_type_evidence
task_type_scores
auxiliary_signals
rejected_task_type_evidence
title_length
body_length
has_code_block
newcomer_label_signal
comment_count
formula_version

现有测试要求 task type evidence 稳定、JSON 可序列化，但允许增加新的顶层键。

建议新增：

"difficulty_assessment": {
  "formula_version": "difficulty-rules-v0.2",
  "information_quality": {},
  "dimensions": {},
  "effort": {}
}

这不要求新增数据库列，也不要求迁移。

5. 候选方案 A：保守规则重构

5.1 定位

尽量保留现有结构，只替换明显不合理的硬覆盖。

特点：

改动较小；

仍采用顺序规则；

易于实现和回归；

解释性强；

但长期仍可能形成新的关键词硬编码。

5.2 默认值

维度

默认值

Code

1

Setup

1；纯文档且无需运行时为 0

Context

1；纯内容编辑可为 0

Collaboration

0

5.3 Code 规则

任务类型只作弱先验：

纯 documentation：初始 0；

bug/testing/build_tooling：初始 1；

feature/refactor：初始 1，不再自动为 2；

other：初始 1；非可执行任务单独标记。

升级：

明确局部逻辑、多个函数、单模块非平凡修改 → 2；

跨模块、公共 API、协议、并发、编译器、持久化一致性、分布式核心路径 → 3；

performance 只有与算法、索引、缓存一致性、分布式通信、编译执行等复杂证据共同出现时才能升级；

newcomer 不参与 Code。

降级：

明确纯文字、示例、README、单一配置说明 → 0；

明确单文件、单断言、局部重命名 → 保持 1。

5.4 Setup 规则

纯文档且无需运行 → 0；

标准仓库安装和测试 → 1；

明确必须使用特定平台、服务、数据库、浏览器后端、容器或集群复现 → 2；

明确需要多节点、GPU/ROCm/CUDA、native toolchain、多平台矩阵或高成本基础设施 → 3。

仅出现以下内容不升级：

Environment: Windows
Running on Linux
Docker version
Kubernetes version

必须同时出现“任务完成或复现依赖该环境”的语义证据。

5.5 Context 规则

纯文字或孤立配置，无需项目知识 → 0；

单模块、局部类或函数 → 1；

跨模块、公共 API、生命周期、序列化兼容、测试框架共享层 → 2；

核心架构、全局不变量、协议语义、编译器、分布式一致性、重大历史设计 → 3。

refactor/performance 不再自动为 3。

5.6 Collaboration 规则

范围明确、无未决方案 → 0；

普通澄清、需要一次维护者确认、评论较多但没有设计争议 → 1；

RFC、needs discussion、API design、多个可选方案、维护者协调 → 2；

跨团队、重大兼容性决策、明显争议、长期未决 RFC → 3。

comment count：

只能把 0 弱提升到 1；

不能单独产生 2 或 3；

评论阈值建议仅用于 evidence，不直接赋值。

5.7 信息不足策略

不增加新数据库字段，只在 evidence 中记录：

"information_quality": {
  "body_missing": true,
  "actionability": "unclear",
  "confidence": "low",
  "reasons": ["body_missing"]
}

约束：

body 缺失时，不能仅凭 task type 输出 Code/Context=3；

缺少范围证据时，effort 不得仅因类型或 performance 进入 multi_day；

tracker/roadmap 标记 actionability=non_actionable；

当前枚举没有 unknown/not_applicable，因此 effort 只能作为兼容性占位值，同时必须标记 applicable=false 或 confidence=low。

5.8 Effort

不再使用单纯求和，改为简单决策表：

under_2h：纯文字、单配置、单断言、明确微小改动；

half_day：局部、范围明确、标准环境，Code/Context 通常不超过 1；

one_day：非平凡单模块或有限跨文件任务，Code/Context 出现 2；

multi_day：明确跨模块、架构、分布式、复杂环境或多个强范围证据。

规则：

collaboration 单独升高不得直接产生 multi_day；

setup=2 单独不得直接产生 multi_day；

body missing 不得仅凭任务类型产生 multi_day；

non-actionable tracker 标记 effort 不适用。

5.9 风险

仍依赖顺序；

新规则可能继续受关键词影响；

冲突证据处理不够系统；

难以解释“为什么两个中等证据合起来升一级”。

5.10 最小测试矩阵

至少覆盖：

newcomer 不再压低 Code/Context；

performance 单独不再产生 3；

performance + distributed/algorithm evidence 可以产生 3；

环境模板中的 OS 名不升级；

明确 Kubernetes 集群复现升级到 2；

多节点/GPU/native 升到 3；

refactor 局部任务 Context=1；

跨模块 refactor Context=2；

core architecture Context=3；

评论数单独最多 Collaboration=1；

RFC/needs discussion 为 2；

跨团队重大设计为 3；

body missing 产生 low confidence；

tracker 标记 non-actionable；

effort 不再与四维总和一一对应。

6. 候选方案 B：证据加权 + 决策表

6.1 定位

先收集每个维度的结构化证据，再由统一聚合器决定等级。

不是训练模型，也不是黑盒加权，而是：

弱先验
+ 明确证据
+ 证据强度
+ 冲突优先级
+ 决策表

6.2 证据结构

每个证据建议统一为：

{
  "source": "label|title|body|derived",
  "rule_id": "difficulty.code.cross_module",
  "matched_value": "public API and two modules",
  "strength": "weak|medium|strong",
  "suggested_level": 2,
  "reason": "cross_module_public_api"
}

每个维度保存：

{
  "prior": 1,
  "level": 2,
  "confidence": "medium",
  "evidence": [],
  "conflicts": []
}

6.3 聚合原则

证据优先级：

明确任务要求/结构化标签
> 标题中的直接范围
> 正文中的明确实现或复现要求
> task type 弱先验
> comment count 等统计信号

更准确地说，来源优先级不能机械覆盖“强度”：

强证据优先于弱证据；

相同强度时，明确任务要求优先于泛化先验；

一个弱证据不能单独产生 level 3；

两个相互独立的 medium 证据可提升一级；

冲突时保留冲突记录并降低 confidence；

newcomer 永远不进入技术维度证据；

performance 本身只是一条 weak/medium 辅助证据。

6.4 各维度弱先验

Code

task type

prior

documentation-only

0

bug_fix

1

testing

1

build_tooling

1

feature

1

refactor

1

other

1 或不可执行标记

Setup

纯文档：0；

其余可执行开发任务：1。

Context

纯内容编辑：0；

其余可执行开发任务：1。

Collaboration

全部默认 0。

先验只能提供起点，不能作为最终硬判定。

6.5 四维决策表

Code

等级

证据要求

0

明确无代码修改：文档、文字、非代码示例

1

局部、明确、单文件/单函数/单断言/配置更新

2

非平凡逻辑、多个函数、单模块内复杂状态、有限跨文件

3

架构、并发、编译器、核心协议、分布式、全局兼容或大范围高风险

Level 3 必须至少有一条 strong 证据，不能由 task type 或 performance 单独产生。

Setup

等级

证据要求

0

不需要本地运行或特殊环境

1

标准仓库环境

2

特定平台、服务、容器、数据库、浏览器后端或复杂复现

3

多平台、native、GPU、分布式、多节点、高成本基础设施

环境模板中的系统名只记录为弱证据，不能单独升级。

Context

等级

证据要求

0

几乎不需要项目知识

1

局部模块、明确组件

2

跨模块、公共 API、生命周期、共享测试框架、兼容性

3

核心架构、全局不变量、协议、编译器、分布式语义或历史设计

Collaboration

等级

证据要求

0

范围明确、没有未决方案

1

普通澄清、维护者确认、一般 review

2

needs discussion、API design、RFC、多个方案或多维护者协调

3

跨团队重大设计、明显争议、兼容性决策、长期未决 RFC

comment count 只能提供 weak 证据，且最多帮助产生 level 1。

6.6 信息质量

统一计算：

high：
  正文充分 + 范围明确 + 至少两类独立证据

medium：
  一条明确强证据，或两条相互支持的中等证据

low：
  正文缺失、tracker、支持咨询、范围未定、
  证据冲突或只能依赖标题/标签

6.7 Effort 决策策略

先判断：

actionability
scope
implementation complexity
environment burden
uncertainty

不直接对四维求和。

Scope

micro
local
module
cross_module
system
non_actionable
unclear

决策

Effort

主要条件

under_2h

micro，明确文字/配置/断言，低环境负担

half_day

local，范围明确，标准环境

one_day

module 或有限跨文件，非平凡但边界明确

multi_day

cross_module/system、复杂环境、架构/分布式或重大未决设计

辅助约束：

Code=3 通常至少 one_day，但不自动 multi_day；

Setup=3 通常至少 one_day；

Context=3 且范围为 cross_module/system → multi_day；

Collaboration=3 只有在方案未决且影响实现范围时才提高 effort；

low confidence 时保存 provisional=true；

non_actionable 时保存 applicable=false，不得解释为真实开发工时。

6.8 优点

各维度可以共享统一证据结构；

不再依赖函数中的赋值顺序；

可保存冲突和置信度；

能解释为什么升级；

更容易扩展和测试；

适合后续人工复核。

6.9 风险

实现工作量高于方案 A；

规则目录需要严格控制，避免变成大量专用正则；

evidence 去重、排序和冲突处理需要更多测试；

需要防止“多个同义关键词”被误算成多条独立证据。

6.10 最小测试矩阵

除方案 A 的测试外，再增加：

单个弱证据不能升到 3；

两个独立 medium 证据可升级；

同一匹配的重复证据不重复计数；

冲突证据降低 confidence；

task type prior 不覆盖明确证据；

evidence 顺序稳定；

JSON 可序列化；

同输入重复运行完全一致；

difficulty evidence 不破坏原 task type evidence；

effort scope 与四维总分解耦；

相同四维、不同 scope 可产生不同 effort；

相同总和、不同 actionability 不得被视为同类任务。

7. 两套方案比较

维度

A 保守重构

B 证据加权/决策表

修改范围

小

中等

实现难度

低

中

可解释性

较强

强

冲突处理

较弱

明确

置信度

可补充

原生支持

防止硬覆盖

部分

更彻底

长期维护

一般

更好

测试成本

较低

较高

适合当前问题

能止血

能系统解决

8. 推荐方案

推荐：

采用方案 B 的结构，但使用方案 A 的保守证据集合。

也就是：

证据收集
→ 统一决策表
→ 结构化 evidence
→ 独立 effort

但第一版不要添加大量复杂关键词，只覆盖全量诊断已经证明的主要问题：

删除 newcomer 对 Code/Context 的硬上限；

删除 performance 对 Code/Context 的无条件 level 3；

删除 refactor 对 Context 的无条件 level 3；

将 setup 环境词改为“明确必需环境”证据；

将 comment count 降为 Collaboration 弱信号；

为 documentation/testing/build_tooling/refactor 建立内部层级；

增加 information quality、actionability、confidence；

effort 使用 scope 决策，不再四维等权求和。

这是最小但结构正确的 v0.2。

9. 推荐 evidence 输出契约

{
  "difficulty_assessment": {
    "formula_version": "difficulty-rules-v0.2",
    "information_quality": {
      "body_missing": false,
      "actionability": "actionable",
      "confidence": "medium",
      "reasons": []
    },
    "dimensions": {
      "code": {
        "prior": 1,
        "level": 2,
        "confidence": "medium",
        "evidence": [],
        "conflicts": []
      },
      "setup": {
        "prior": 1,
        "level": 1,
        "confidence": "medium",
        "evidence": [],
        "conflicts": []
      },
      "project_context": {
        "prior": 1,
        "level": 2,
        "confidence": "medium",
        "evidence": [],
        "conflicts": []
      },
      "collaboration": {
        "prior": 0,
        "level": 1,
        "confidence": "medium",
        "evidence": [],
        "conflicts": []
      }
    },
    "effort": {
      "bucket": "one_day",
      "scope": "module",
      "applicable": true,
      "provisional": false,
      "confidence": "medium",
      "evidence": []
    }
  }
}

稳定性要求：

evidence 按 source → rule_id → matched_value → suggested_level 排序；

去重键必须稳定；

不写入仓库名和 Issue 编号；

不覆盖现有 task type evidence；

所有 reason/rule_id 使用稳定常量；

无法判断时记录低置信度，不编造范围。

10. 与 newcomer、growth、skills、matching 的边界

本阶段允许

改四维难度计算；

改 effort 计算；

在 feature_evidence 中增加难度证据；

增加与难度直接相关的私有 helper 和规则常量；

增加测试。

本阶段不允许

修改 task type 识别；

修改 infer_skill_requirements()；

修改 newcomer/growth 公式；

修改 matching；

修改 CLI/API/Web；

新增数据库列或迁移；

针对具体仓库或 Issue 写规则。

传播处理

即使不修改下游公式，新的 Code/Setup/Context 会自然改变：

newcomer score；

growth score；

skill minimum level；

可能的匹配排序。

因此第五阶段验收必须记录传播结果，而不是只看四维分布。

11. 必须新增和保留的测试

11.1 必须保留

现有 B2 task type 识别测试全部保留，包括：

标签别名；

title/body 规则；

performance 作为辅助信号；

tracker 保持 other；

evidence 稳定；

skill requirements 使用公共任务类型；

CLI 与 CI、Docker 与 documentation 等消歧。

11.2 必须新增

Newcomer

newcomer label 不改变 Code；

newcomer label 不改变 Context；

newcomer 仍能提高 newcomer score。

Performance

performance 信号单独不能产生 Code=3；

performance 信号单独不能产生 Context=3；

明确分布式/编译器/核心算法证据可产生高难度。

Setup

Environment 区域中的 OS 名不自动升级；

标题明确平台专属 bug 可升级；

明确 Kubernetes 集群复现为 2；

多节点/GPU/native 为 3；

纯 documentation 不因正文引用 Linux 而升级。

Context

局部 refactor 为 1；

跨模块 refactor 为 2；

核心架构 refactor 为 3；

API consistency 与公共 API 任务至少为 2；

performance 本身不决定 Context。

Collaboration

20 条评论但无设计证据时最多为 1；

needs discussion/RFC/API design 为 2；

跨团队重大设计为 3；

评论少不能阻止强设计证据升级。

Information quality

body missing → low confidence；

support question → actionability unclear；

roadmap/tracker → non_actionable；

信息不足不能仅凭类型进入 multi_day。

Effort

effort 不再完全由四维总和决定；

相同总和、不同 scope 得到不同 bucket；

collaboration 单独升高不直接变 multi_day；

setup 单独升高不直接变 multi_day；

cross-module/system scope 可以 multi_day；

evidence 中 effort bucket 与输出字段一致。

Evidence

difficulty_assessment 存在且结构完整；

evidence 稳定、去重、可 JSON 序列化；

重复运行结果一致；

原 task_type_evidence 保持不变。

下游传播

newcomer/growth 公式本身不变；

Code 变化后 skill minimum level 按旧逻辑传播；

ineligible 任务的 newcomer/growth 仍为 0。

12. 新旧评估计划

12.1 数据

保留旧快照：

data/oss_mentor_task_features_v0.2_round3.sqlite3

新规则写入新的数据库副本，不覆盖旧数据。

建议新快照：

data/oss_mentor_difficulty_rules_v0.2.sqlite3

12.2 比较项目

四维分布

0/1/2/3 各等级数量；

均值；

未使用等级；

top difficulty tuple；

HHI/集中度。

规则异常

Context=1 占比；

setup body-only keyword；

collaboration=2 without design/discussion；

performance uniform hard-3；

newcomer cap；

documentation/testing/build tooling 固定模式；

body missing；

tracker/non-actionable；

effort 与四维求和一致率。

下游传播

newcomer score 变化；

growth score 变化；

skill minimum level 变化；

effort 跨档变化；

若获取 matching.py，再做排名变化检查。

36 条案例

在人工复核完成前：

只能计算与 AI 辅助评估的一致率和差异；

不声称准确率；

重点检查 12 条高风险案例。

人工复核完成后：

accept 使用 AI assessment；

revise 使用 revised annotation；

insufficient 排除主统计并单列。

12.3 验收原则

不建议为了好看强行规定每个比例，但至少满足：

newcomer 不再改变技术难度；

performance 不再统一产生 [3, *, 3, *]；

refactor 不再统一 Context=3；

comment count 单独不再产生 Collaboration=2；

setup body-only 偶然环境词显著减少；

Context 形成 0/1/2/3 的实际分层；

testing/build tooling/documentation 内部不再全部固定；

effort 与四维求和不再 100% 一致；

body missing 和 tracker 有明确低置信度/不可执行证据；

原 B2 测试全部通过；

不新增仓库或 Issue 专用规则。

13. 版本建议

当前 TASK_FEATURE_VERSION 已经是：

task-features-v0.2

B3 改变实际输出语义后，继续使用同一版本会造成数据来源混淆。

推荐二选一：

推荐

TASK_FEATURE_VERSION = "task-features-v0.3"
DIFFICULTY_FORMULA_VERSION = "difficulty-rules-v0.2"

负责人要求总体仍保持 v0.2 时

至少保留：

feature_evidence.difficulty_assessment.formula_version
  = "difficulty-rules-v0.2"

不能在没有版本区分的情况下覆盖旧快照。