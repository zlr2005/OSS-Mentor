OSS-Mentor B3 第五阶段：difficulty-rules-v0.2 实现计划

状态：仅完成修改前设计；尚未修改任何源代码。

1. 当前版本与精确修改位置

当前版本

TASK_FEATURE_VERSION = "task-features-v0.2"

位置：src/oss_mentor/task_features.py:13

建议实施时改为：

TASK_FEATURE_VERSION = "task-features-v0.3"
DIFFICULTY_FORMULA_VERSION = "difficulty-rules-v0.2"

TASK_FEATURE_VERSION 用于区分整个 TaskFeatures 输出语义；DIFFICULTY_FORMULA_VERSION 只标识本次难度公式。

当前四维与 effort 代码位置

extract_task_features() 位于：

src/oss_mentor/task_features.py:1354-1502

其中：

逻辑

当前行号

task type 分类调用

1397-1401

Code

1404-1416

Setup

1418-1432

Project context

1434-1440

Collaboration

1442-1445

四维求和与 Effort

1447-1457

newcomer/novice

1459-1468

growth

1469-1475

feature_evidence

1477-1485

TaskFeatures 构造

1486-1502

计划只替换 1404-1457 的旧难度和 effort 计算，并扩展 1477-1485 的 evidence。任务类型分类逻辑 1195-1351 不改。

2. 当前真实调用链

record
  ↓
extract_task_features()
  ├─ 提取 clarity/reproduction/acceptance/expected/module hint
  ├─ _classify_task_types()
  │    ├─ task_types
  │    ├─ classification_evidence
  │    └─ has_performance_signal
  ├─ 当前四维难度
  ├─ 当前 effort
  ├─ newcomer/novice
  ├─ growth
  └─ TaskFeatures
        ↓
infer_skill_requirements()
        ↓
数据库持久化/推荐匹配

第五阶段拟改为：

_classify_task_types()
        ↓
_build_difficulty_context()
        ↓
_assess_information_quality()
        ↓
_collect_*_difficulty_evidence()
        ↓
_aggregate_difficulty_dimension()
        ↓
_estimate_effort()
        ↓
四维 + effort + difficulty_assessment evidence
        ↓
现有 newcomer/growth 公式（不改）

3. 当前规则精确还原

Code

默认 1
documentation-only → 0
feature/refactor → 至少 2
performance 或标签 core/architecture/api change → 3
newcomer label → 最后压到最多 1

规则性质：

documentation：硬赋值；

feature/refactor：max 升级；

performance/core/architecture/api change：硬赋值；

newcomer：最后执行的硬上限，会覆盖前面结果。

Setup

默认 1
documentation-only → 0
全文出现 compile/native/toolchain/backend/
macOS/Windows/Linux/Docker/Kubernetes → 2

报告者环境与完成任务所需环境没有区分。

Project context

默认 1
标签含 core/architecture/api → 2
refactor 或 performance → 3
newcomer label → 最后压到最多 1

Collaboration

comment_count < 3  → 0
comment_count < 10 → 1
comment_count ≥ 10 → 2
discussion/design 标签 → 至少 2

Effort

sum = code + setup + context + collaboration

sum ≤ 2 → under_2h
sum ≤ 4 → half_day
sum ≤ 6 → one_day
其他    → multi_day

4. 下游依赖

infer_skill_requirements

位置：

src/oss_mentor/task_features.py:1505-1552

Code 的直接影响：

主语言 minimum_level = clamp(code, 1, 3)
task type minimum_level:
  code ≤ 1 → 1
  code ≥ 2 → 2

本阶段不修改此函数，但新 Code 会自然传播到技能要求。

newcomer/growth

位置：

newcomer/novice：1459-1468
growth：1469-1475

直接依赖：

newcomer：Code、Setup、Context；

growth：Code、Context。

本阶段不修改公式，只改变输入难度。这样可以隔离 B3 的影响。

matching

matching.py 显示难度不只影响排序，还直接影响候选任务是否可匹配：

estimated_code_difficulty > profile.max_code_difficulty → 过滤
estimated_setup_difficulty > profile.max_setup_difficulty → 过滤

同时：

requirements 的最低等级会影响 skill gap；

newcomer_score 或 growth_value_score 参与最终 match score；

newcomer track 仍要求 newcomer label。

因此 B3 新规则可能改变：

推荐任务可用数量；

被 Code/Setup 上限过滤的任务；

技能缺口；

newcomer/growth 排名。

本阶段不修改 matching.py，但最终评估必须增加匹配可用性与排名变化检查。

5. 当前 feature_evidence 结构

当前顶层字段：

title_length
body_length
has_code_block
newcomer_label_signal
comment_count
formula_version
task_type_evidence
task_type_scores
auxiliary_signals
rejected_task_type_evidence

其中 formula_version 当前直接等于 TASK_FEATURE_VERSION，无法单独标识难度公式。

计划新增，不覆盖原字段：

difficulty_assessment:
  formula_version
  information_quality
  dimensions
  effort

6. 计划新增的常量

建议放在 TASK_FEATURE_VERSION 和现有任务类型常量附近：

DIFFICULTY_FORMULA_VERSION = "difficulty-rules-v0.2"

_DIFFICULTY_SOURCE_ORDER = {
    "label": 0,
    "title": 1,
    "body": 2,
    "derived": 3,
}

_DIFFICULTY_STRENGTH_ORDER = {
    "weak": 0,
    "medium": 1,
    "strong": 2,
}

_DIFFICULTY_DIMENSIONS = (
    "code",
    "setup",
    "project_context",
    "collaboration",
)

_ACTIONABILITY_VALUES = {
    "actionable",
    "design_pending",
    "unclear",
    "non_actionable",
}

_EFFORT_SCOPE_ORDER = {
    "micro": 0,
    "local": 1,
    "module": 2,
    "cross_module": 3,
    "system": 4,
    "unclear": 5,
    "non_actionable": 6,
}

不把仓库名、Issue 编号放入任何常量或规则。

7. 计划新增的内部上下文

建议新增内部 dataclass，避免向多个 helper 传递十几个松散参数：

@dataclass(frozen=True, slots=True)
class _DifficultyContext:
    title: str
    body: str
    semantic_body: str
    labels: tuple[str, ...]
    normalized_labels: tuple[str, ...]
    task_types: tuple[str, ...]
    performance_signal: bool
    comment_count: int
    has_reproduction_steps: bool
    has_acceptance_criteria: bool
    has_expected_behavior: bool
    has_affected_module_hint: bool

构造接口：

def _build_difficulty_context(
    *,
    title: str,
    body: str,
    labels: list[str],
    task_types: tuple[str, ...],
    performance_signal: bool,
    comment_count: int,
    has_reproduction_steps: bool,
    has_acceptance_criteria: bool,
    has_expected_behavior: bool,
    has_affected_module_hint: bool,
) -> _DifficultyContext:
    ...

该对象只保存已有输入的规范化结果，不保存 repository 或 issue number。

8. 通用 evidence helper 接口

排序键

def _difficulty_evidence_key(
    item: dict[str, Any],
) -> tuple[Any, ...]:
    ...

排序顺序：

source
rule_id
matched_value
strength
suggested_level
reason

增加证据

def _add_difficulty_evidence(
    bucket: list[dict[str, Any]],
    *,
    dimension: str,
    source: str,
    rule_id: str,
    matched_value: str,
    strength: str,
    suggested_level: int,
    reason: str,
) -> None:
    ...

约束：

dimension 必须是四个合法维度之一；

source、strength 必须来自固定枚举；

suggested_level 必须在 0～3；

通过稳定键去重；

输出只含 JSON 可序列化值；

最终排序稳定。

单条证据格式：

{
  "dimension": "project_context",
  "source": "title",
  "rule_id": "difficulty.context.public_api",
  "matched_value": "public API",
  "strength": "medium",
  "suggested_level": 2,
  "reason": "public_api_contract"
}

9. 信息质量与 actionability helper

接口：

def _assess_information_quality(
    context: _DifficultyContext,
) -> dict[str, Any]:
    ...

输出：

{
  "body_missing": false,
  "actionability": "actionable",
  "confidence": "medium",
  "reasons": []
}

通用判定：

non_actionable

明确：

roadmap；

tracker；

dependency dashboard；

umbrella milestone list；

仅用于追踪多个子任务的 issue。

unclear

明确：

正文缺失；

support question；

“why/how/what do I miss”类咨询；

只有现象但没有可执行目标；

任务范围无法由现有文本判断。

design_pending

明确：

RFC；

needs discussion；

API design；

proposal 状态；

多个方案尚待选择。

actionable

具备明确问题、期望行为、修复目标、验收条件或具体实现任务。

置信度：

high：正文完整、范围明确、至少两类相互支持证据；

medium：存在一条明确强证据或两条中等证据；

low：正文缺失、非可执行、范围未定或证据冲突。

10. 任务类型弱先验

接口：

def _difficulty_priors(
    task_types: tuple[str, ...],
    information_quality: dict[str, Any],
) -> dict[str, int]:
    ...

决策表：

情况

Code

Setup

Context

Collaboration

documentation-only

0

0

0

0

其他可执行任务

1

1

1

0

other + non_actionable

1

1

1

0

other + unclear

1

1

1

0

注意：

feature、refactor 不再自动 Code=2；

refactor 不再自动 Context=3；

performance 不是公开 task type，不提供硬先验；

newcomer/good first issue 完全不进入技术难度先验。

11. 四个证据收集 helper

Code

def _collect_code_difficulty_evidence(
    context: _DifficultyContext,
    information_quality: dict[str, Any],
) -> list[dict[str, Any]]:
    ...

保守规则：

建议等级

通用证据

0

明确纯文字、README、文档说明、无代码修改

1

单文件、单函数、局部断言、配置、重命名、明确低风险修复

2

非平凡逻辑、多个函数、单模块复杂状态、有限跨文件、集成测试稳定性

3

核心架构、并发、编译器、协议、分布式一致性、核心存储/查询路径、大范围兼容性

约束：

performance_signal 只产生 weak 证据，不能单独升到 2 或 3；

feature/refactor/testing/build_tooling 只提供 prior；

Code=3 必须至少有一条 strong 技术范围证据；

newcomer 标签不参与。

建议 rule id：

difficulty.code.no_code.documentation
difficulty.code.local_change
difficulty.code.nontrivial_logic
difficulty.code.integration_test_state
difficulty.code.cross_module
difficulty.code.core_architecture
difficulty.code.concurrent_or_distributed
difficulty.code.compiler_or_protocol
difficulty.code.performance_auxiliary

Setup

def _collect_setup_difficulty_evidence(
    context: _DifficultyContext,
    information_quality: dict[str, Any],
) -> list[dict[str, Any]]:
    ...

决策表：

等级

证据

0

无需本地运行或特殊环境

1

标准仓库安装和测试

2

明确要求特定平台、服务、容器、数据库、浏览器后端、单节点集群

3

多节点、GPU/CUDA/ROCm、native toolchain、多平台矩阵、高成本基础设施

重要消歧：

“Environment: Windows/Linux/macOS”
“Docker version”
“Kubernetes version”

只说明报告者环境时：

strength=weak
suggested_level=1
reason=reported_environment_only

不能升级。

必须存在“复现或完成任务依赖该环境”的动作语义，才建议 2 或 3。

建议 rule id：

difficulty.setup.standard_repository
difficulty.setup.reported_environment_only
difficulty.setup.platform_required
difficulty.setup.service_required
difficulty.setup.container_or_cluster_required
difficulty.setup.native_toolchain_required
difficulty.setup.gpu_required
difficulty.setup.multinode_required

Project context

def _collect_context_difficulty_evidence(
    context: _DifficultyContext,
    information_quality: dict[str, Any],
) -> list[dict[str, Any]]:
    ...

决策表：

等级

证据

0

纯内容修改，不需要项目知识

1

单模块、明确组件或局部路径

2

跨模块、公共 API、生命周期、共享测试框架、兼容性

3

核心架构、全局不变量、协议语义、编译器、分布式状态、历史设计

约束：

refactor 本身不升级；

performance 本身不升级；

“API”只能在明确指向公共契约/兼容性时建议 2；

普通 REST endpoint 名称不能自动当作核心 API 设计；

Context=3 必须有 strong 范围证据。

建议 rule id：

difficulty.context.no_project_context
difficulty.context.local_module
difficulty.context.cross_module
difficulty.context.public_api
difficulty.context.lifecycle_or_compatibility
difficulty.context.shared_framework
difficulty.context.core_architecture
difficulty.context.protocol_semantics
difficulty.context.distributed_state
difficulty.context.compiler_semantics

Collaboration

def _collect_collaboration_difficulty_evidence(
    context: _DifficultyContext,
    information_quality: dict[str, Any],
) -> list[dict[str, Any]]:
    ...

决策表：

等级

证据

0

范围明确，无未决方案

1

普通澄清、维护者确认、一般 review

2

RFC、needs discussion、API design、多个方案、多维护者协调

3

跨团队重大设计、明显争议、破坏性兼容决策、长期未决 RFC

comment count：

<3：不产生难度证据；

3～9：可记录 weak level 1；

≥10：仍只能记录 weak level 1；

不能单独产生 level 2/3。

建议 rule id：

difficulty.collaboration.comment_volume
difficulty.collaboration.ordinary_review
difficulty.collaboration.needs_discussion
difficulty.collaboration.api_design
difficulty.collaboration.rfc_or_proposal
difficulty.collaboration.multiple_options
difficulty.collaboration.cross_team
difficulty.collaboration.breaking_change_decision
difficulty.collaboration.long_running_dispute

12. 维度聚合 helper

接口：

def _aggregate_difficulty_dimension(
    *,
    dimension: str,
    prior: int,
    evidence: list[dict[str, Any]],
    information_quality: dict[str, Any],
) -> dict[str, Any]:
    ...

输出：

{
  "prior": 1,
  "level": 2,
  "confidence": "medium",
  "evidence": [],
  "conflicts": []
}

聚合顺序：

稳定去重和排序；

查找 strong 证据；

没有 strong 时查找 medium；

weak 只作辅助，不能单独把 Code/Setup/Context 升到 2 或 3；

Collaboration 的 weak 证据最多把 0 升到 1；

Level 3 必须至少有一条 strong 证据；

明确 no-code strong 证据与 implementation strong 证据同时出现时记录冲突；

建议等级差至少 2 的强/中证据同时出现时记录冲突；

evidence 冲突或 information quality low 时，最终 confidence 不能为 high；

newcomer 标签不在 evidence 中。

建议 confidence：

high：
  strong 证据 + 正文完整 + 无冲突

medium：
  medium 证据，或 strong 证据但存在轻微不确定性

low：
  body missing / non_actionable / unclear / 强冲突

13. 总体难度入口

接口：

def _assess_difficulty(
    context: _DifficultyContext,
) -> tuple[
    int,
    int,
    int,
    int,
    str,
    dict[str, Any],
]:
    ...

返回顺序：

code
setup
project_context
collaboration
effort_bucket
difficulty_assessment

该 helper：

计算 information quality；

计算 priors；

收集四维 evidence；

聚合四维；

估计 effort；

构造完整 evidence。

14. Effort helper 与决策表

建议拆成两个 helper：

def _infer_effort_scope(
    *,
    context: _DifficultyContext,
    information_quality: dict[str, Any],
    dimensions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ...

def _estimate_effort(
    *,
    context: _DifficultyContext,
    information_quality: dict[str, Any],
    dimensions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ...

输出：

{
  "bucket": "one_day",
  "scope": "module",
  "applicable": true,
  "provisional": false,
  "confidence": "medium",
  "evidence": []
}

Scope

micro
local
module
cross_module
system
unclear
non_actionable

Bucket 决策

Scope

默认 Bucket

micro

under_2h

local

half_day

module

one_day

cross_module

multi_day

system

multi_day

unclear

half_day，provisional=true，confidence=low

non_actionable

兼容性占位 bucket；applicable=false，provisional=true，confidence=low

修正条件：

Setup=3 可将 local/module 上调一档；

Code=3 但范围明确局部时至少 one_day，不强制 multi_day；

Context=3 且 scope 为 cross_module/system → multi_day；

Collaboration=3 只有在未决设计会改变实现范围时才影响 bucket；

Setup 或 Collaboration 单维升高不能单独产生 multi_day；

body missing 不能仅凭 task type 或 performance 得到 multi_day；

performance_signal 不直接决定 bucket。

非可执行任务的兼容性缺口

当前外部字段只有四个 bucket，没有 unknown/not_applicable。又不能修改 API/Web/数据库契约，因此 v0.2 需要：

estimated_effort_bucket：保留四值兼容字段
difficulty_assessment.effort.applicable：真实语义

对于 tracker/roadmap：

applicable=false
provisional=true
confidence=low

后续诊断和校准必须排除 applicable=false 的记录，不能把兼容 bucket 当成真实开发工时。

具体占位 bucket 在写代码前建议固定为单一常量，并在测试中明确；不得根据仓库或案例选择。

15. extract_task_features 精确替换方案

保留不动

1354-1403：
输入、clarity、task type 分类

1459-1475：
eligible、newcomer、growth 公式

1486-1502：
TaskFeatures 返回结构

替换

删除当前：

1404-1457

改为概念调用：

difficulty_context = _build_difficulty_context(...)
(
    code_difficulty,
    setup_difficulty,
    context_difficulty,
    collaboration_difficulty,
    effort,
    difficulty_assessment,
) = _assess_difficulty(difficulty_context)

扩展 evidence

当前：

"formula_version": TASK_FEATURE_VERSION,

建议保留兼容字段，并新增：

"formula_version": TASK_FEATURE_VERSION,
"difficulty_assessment": difficulty_assessment,

difficulty_assessment.formula_version 使用：

difficulty-rules-v0.2

16. 现有 difficulty 相关测试

当前 tests/test_task_features.py 没有完整的难度测试矩阵。

现有相关测试只有：

test_clear_first_contribution_bug_scores_for_newcomer

检查 newcomer score；

不检查四维和 effort。

test_ineligible_candidate_gets_zero_track_scores

检查 ineligible 的 newcomer/growth 为 0；

同时断言旧规则下 Code=3。

test_title_platform_takes_priority_over_body_comparison

检查技能平台来源；

不检查 Setup。

多个 performance 测试

只保护 task type 与 auxiliary signal；

不保护难度。

test_evidence_is_structured_stable_and_json_serializable

保护 task type evidence 稳定；

尚未检查 difficulty evidence。

test_skill_requirement_logic_uses_public_task_types_unchanged

保护 skill requirement 任务类型边界。

需要特别注意：

B2 task type 测试必须原样保留；

test_ineligible_candidate_gets_zero_track_scores 可以继续测试 zero track score，但其 Code 断言必须符合新证据规则，而不能依赖“performance 一律 3”。

17. 计划新增的测试名称

Newcomer

test_newcomer_label_does_not_change_difficulty_dimensions
test_newcomer_label_still_increases_newcomer_score

Performance

test_performance_signal_alone_does_not_force_code_three
test_performance_signal_alone_does_not_force_context_three
test_performance_with_strong_distributed_evidence_can_be_high_difficulty
test_performance_tracker_is_non_actionable_and_low_confidence

Setup

test_reported_operating_system_does_not_raise_setup
test_platform_required_by_reproduction_raises_setup
test_container_or_single_cluster_requirement_is_setup_two
test_gpu_or_multinode_requirement_is_setup_three
test_documentation_reference_to_linux_keeps_setup_zero

Project context

test_local_refactor_keeps_context_one
test_cross_module_refactor_has_context_two
test_core_architecture_change_has_context_three
test_public_api_contract_has_context_two
test_performance_signal_does_not_set_context_level

Collaboration

test_comment_count_alone_cannot_raise_collaboration_above_one
test_needs_discussion_has_collaboration_two
test_rfc_api_design_has_collaboration_two
test_cross_team_breaking_decision_has_collaboration_three
test_low_comment_count_does_not_block_design_evidence

Task type 内部分层

test_documentation_only_can_remain_code_zero
test_documentation_with_runtime_validation_can_have_code_one
test_testing_local_assertion_can_be_code_one
test_flaky_integration_test_can_have_code_two
test_build_tooling_config_change_can_be_code_one
test_native_toolchain_change_can_have_higher_setup_and_code
test_refactor_task_type_is_only_a_prior

Information quality

test_missing_body_has_low_information_confidence
test_support_question_has_unclear_actionability
test_roadmap_tracker_is_non_actionable
test_design_proposal_is_design_pending
test_missing_body_does_not_infer_multi_day_from_task_type

Effort

test_effort_is_not_legacy_four_dimension_sum
test_same_difficulty_sum_can_have_different_effort_scope
test_collaboration_alone_does_not_force_multi_day
test_setup_alone_does_not_force_multi_day
test_cross_module_scope_can_be_multi_day
test_non_actionable_effort_is_marked_not_applicable
test_effort_evidence_bucket_matches_output_field

Evidence

test_difficulty_evidence_is_stable_deduplicated_and_json_serializable
test_difficulty_evidence_contains_all_four_dimensions
test_difficulty_level_three_requires_strong_evidence
test_conflicting_evidence_reduces_confidence
test_task_type_evidence_is_unchanged_by_difficulty_assessment
test_repeated_extraction_produces_identical_difficulty_evidence

Downstream boundary

test_infer_skill_requirements_uses_new_code_level_without_logic_change
test_ineligible_candidate_still_gets_zero_track_scores

18. 不修改范围确认

本阶段明确不修改：

src/oss_mentor/developer_profiles.py
ALLOWED_TASK_TYPES
B2 task type 正则与分类规则
infer_skill_requirements()
newcomer/novice 公式
growth 公式
src/oss_mentor/matching.py
src/oss_mentor/sqlite_store.py
CLI
API
Web
数据库 schema/migrations

允许修改：

src/oss_mentor/task_features.py
tests/test_task_features.py
scripts/export_difficulty_diagnostics.py（核心实现完成后）
相关 docs 与测试日志

19. 分阶段实现顺序

第五阶段 A：核心 helper 与专项测试

修改：

task_features.py
test_task_features.py

完成：

版本常量；

context；

information quality；

evidence helper；

四维 collectors；

aggregator；

effort；

difficulty_assessment；

专项测试。

先运行：

python -m unittest tests.test_task_features -v

第五阶段 B：完整回归

运行：

python -m unittest discover -s tests -v

确认 B2 task type、数据质量、CLI、Web 等测试无回归。

第五阶段 C：诊断脚本 v0.2

更新诊断逻辑：

原 expected_effort_bucket() 改名为 legacy comparison；

不再把与四维求和不一致视为错误；

读取 difficulty_assessment；

增加：

actionability 分布；

confidence 分布；

applicable=false 数量；

strong/medium/weak evidence 分布；

newcomer 技术难度差异；

performance hard-3 数量；

setup reported-environment-only；

comment-only collaboration；

task type 内部分层；

effort scope 分布。

输出：

data/reports/difficulty_diagnostics_v0.2.json

第五阶段 D：新数据库副本与评估

不得覆盖：

data/oss_mentor_task_features_v0.2_round3.sqlite3

建议新副本：

data/oss_mentor_difficulty_rules_v0.2.sqlite3

然后比较：

608条四维分布；

effort；

information quality；

newcomer/growth；

skill requirements；

matching 可用数量；

36条 AI 辅助案例探索性差异。

人工复核仍延后，不能声称准确率提高。

20. 实现前唯一需要固定的契约决策

由于不修改 API/Web/数据库枚举，non_actionable 任务仍必须填一个四值 effort bucket。

建议在真正写代码前固定以下策略：

bucket：统一兼容占位值
applicable=false
provisional=true
confidence=low

评估、诊断和报告一律以 applicable=false 为准，不把占位 bucket 当作真实开发工作量。

除这一兼容策略外，当前实现方案已具备进入代码阶段的条件。