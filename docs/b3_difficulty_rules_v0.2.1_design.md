OSS-Mentor B3 difficulty-rules-v0.2.1 定向小修设计

阶段：B3-E2分支：feat/difficulty-features-v0.2日期：2026-08-10状态：仅完成设计，尚未修改任何源代码目标版本：difficulty-rules-v0.2.1

1. Background

当前 B3 已完成 difficulty-rules-v0.2 的工程实现、task-features-v0.3 数据刷新、608 条 eligible 任务诊断、36 条固定校准案例比较，以及从中筛选出的 25 条重点高风险案例的项目成员复核。

当前代码版本为：

TASK_FEATURE_VERSION = "task-features-v0.3"

DIFFICULTY_FORMULA_VERSION = "difficulty-rules-v0.2"

当前难度链路已经从旧版“task type / performance / newcomer 硬规则”重构为：

information quality -> dimension evidence -> evidence aggregation -> effort scope/actionability decision

v0.2 已经解决了若干结构性问题：

performance 不再自动产生 Code=3 / Project Context=3；

good first issue / newcomer 不再压低技术难度；

reported environment 与 required environment 已开始区分；

comment count 仅作为 Collaboration 弱信号；

task type 只保留弱先验；

roadmap / tracker / dependency dashboard 可识别为 non-actionable；

body missing / unclear 不允许 high confidence；

level 3 需要 strong evidence；

Effort 不再直接使用四维总分映射。

因此 v0.2.1 的目标不是重写公式，而是修补 v0.2 在“复杂证据召回”和“Effort 决策”上的不足。

2. Human-review findings

2.1 项目成员复核结果

25 条重点案例已完成项目成员复核：

old_rules_more_reasonable：11

new_rules_more_reasonable：3

both_unreasonable：4

insufficient_information：7

其中 18 条具有完整人工修订值，可进行定向一致性比较。

2.2 18 条可评分重点案例上的一致性信号

指标

旧规则

v0.2 新规则

四维 exact agreement

24/72（33.3%）

27/72（37.5%）

四维 MAD

0.931

0.847

Effort exact agreement

6/18（33.3%）

2/18（11.1%）

Effort MAD

0.889

1.556

Effort 低于项目成员复核

11/18

16/18

v0.2 的四维总体一致性略有改善，但结构性问题明显：

Code：12/18 低于项目成员复核；

Project Context：16/18 低于项目成员复核；

Effort：16/18 低于项目成员复核；

Setup 与 Collaboration 不应整体上调。

2.3 608 条 eligible 诊断中的分布信号

这些数据不是准确率，只用于观察规则覆盖结构：

Code：563/608 为 level 1；513/608 没有 material difficulty evidence；

Project Context：562/608 为 level 1；526/608 没有 material difficulty evidence；

Effort scope：

local：313

unclear：245

module：21

system：3

cross_module：0

non_actionable：10

micro：16

这说明当前问题不是“level 3 阈值过高”这么简单，而是大量真实复杂表达没有进入现有 evidence vocabulary，导致维度长期停留在 prior=1，Effort 随后自然停在 half_day 或 unclear -> half_day。

3. Methodological limitations

本轮项目成员复核必须保持以下方法边界：

复核表同时展示旧规则、新规则和 AI 辅助初评；

复核过程中使用了 AI 辅助解释；

因此不是独立盲评；

不是人工金标准；

25 条是从 36 条中有意筛出的高风险子集；

不能代表全部 608 条 eligible 任务；

本设计只能表述为：

“与项目成员复核结果的一致性变化”；

“高风险子集上的工程校准信号”；

不能表述为：

“准确率提升”；

“模型精度提高”；

“v0.2.1 已证明优于 v0.2”。

因此 v0.2.1 的目标是减少已观察到的系统性低估，而不是对 18 条案例做逐条拟合。

4. Root-cause analysis

4.1 当前 v0.2 的共同根因

根因 A：复杂 evidence 依赖少数精确短语

当前 Code strong evidence 主要识别：

core architecture

distributed state

all-gather

fsdp

tensor parallelism

compiler semantics

query execution engine

storage engine

segment reader

但真实 Issue 常使用不同表达：

“reading whole segment”

“recompilations in torch.compile”

“Boruvka algorithm is not implemented”

“remote.processExternalLabels uses 50% heap”

“snap-to-spacing”

“FileNotFound wrapped by AttemptsExceededException”

“same tuple syntax has two interpretations”

这些内容语义复杂，但没有命中现有 exact vocabulary。

根因 B：缺少组合证据

当前聚合规则是：

strong evidence -> 采用最高 strong level；

否则 medium evidence -> 最高到 level 2；

weak evidence 不会组合升级；

performance 仅提供 weak level-2 suggestion。

因此：

performance + benchmark + explicit bottleneck + multi-path impact

不会自然组合成更高 Code。

同样：

API Design + backward compatibility + multiple failed heuristics

也不会自动形成 Project Context=3。

v0.2.1 应优先在 collector 中生成“derived composite strong evidence”，而不是把 _aggregate_difficulty_dimension() 改成“多个 medium 自动叠加为 3”。

这样可以继续保留：

level 3 必须有明确 strong evidence

并避免全局阈值膨胀。

根因 C：Context collector 没有充分利用标签作为辅助上下文

例如：

module: dynamic shapes

API Design

distributed

Deprecate

Needs Investigation

这些标签单独不能决定难度，但可以作为组合证据的一部分。

v0.2.1 应允许：

弱领域标签 + 明确正文行为 + 明确影响范围

生成 medium/strong derived evidence。

不能让单个 API、RFC、core、performance 标签直接判高。

根因 D：actionability 识别过于保守

以下真实模板在 v0.2 中仍可能进入 unclear：

Describe the solution you'd like

What did you do / What did you expect / What did you see

Minified repro

Steps

Create test -> Run test -> Add support

具有明确规模阈值和性能症状的 bug report

一旦 actionability=unclear，_infer_effort_scope() 会直接返回 unclear，不再根据复杂维度推导真实 scope。

根因 E：Effort 只看 scope + 少量 level 调整，缺少 validation burden

当前 Effort 大致为：

micro -> under_2h

local -> half_day

module -> one_day

cross_module/system -> multi_day

unclear -> half_day

non_actionable -> compatibility placeholder

补充规则仅包括：

setup=3 时有限抬升；

code=3 时至少 one_day；

context=3 + cross_module/system -> multi_day。

因此以下工作不会被充分体现：

性能 profiling + 修复 + benchmark；

多设备/多浏览器回归；

算法正确性验证；

API compatibility regression；

编译器多算子回归；

分布式性能验证；

根因未知情况下的定位成本。

4.2 15 条重点失败案例逐条分析

案例

v0.2 为什么判低

缺失证据 / 维度

可泛化性

误伤风险与防护

建议强度

apache/pinot #6970

只有 performance weak signal；正文写“read whole segment / read from bottom”，未命中 segment reader / query execution engine

query/storage traversal + 大规模扫描差异 + 明确 traversal change；Code / Context / Effort

高

“segment/query”单词不能触发；必须同时出现扫描/读取行为、明显性能差异和具体执行路径

Code medium=2；Context medium=2；validation heavy

excalidraw #7237

“lag / 4000–5000 elements / scroll / pen lag”没有命中 state machine / core engine

交互性能 at scale + rendering/state bottleneck + 跨设备验证；Code / Context / Effort

高

performance 标签不能单独触发；要求具体规模、交互症状和 profiling/优化对象

Code medium=2；Context medium=2；validation heavy

excalidraw #11273

alignment guides / snap-to-spacing / moving elements 未被视为复杂交互逻辑

real-time geometry + drag state + snapping + visual feedback；Code / Context / Effort

高

“alignment”单词不足；需要实时移动/定位 + 几何/间距/吸附多个行为

Code medium=2；Context medium=2

kubernetes #82440

cluster 只提升 Setup；performance 仍为 weak，未识别“CPU 随 replicas 线性增长”的 runtime hot path

system runtime hot path + scale-dependent overhead + probe lifecycle；Code / Context / Effort

高

“Kubernetes”本身不能触发；要求可复现 scaling + repeated runtime operation + system resource impact

derived strong=3 for Code/Context

pandas #62022

Deprecate / Needs Discussion 只影响 task type / collaboration；Context 不读取完整 API policy 组合

public API deprecation + compatibility cycle + unresolved policy；Context / Effort

高

单独 deprecate 不等于 level3；需 public API target + compatibility/deprecation policy + discussion

Context strong=3；Collab保持2

pandas #65326

API Design 标签未进入 Context；同一 tuple 的语义歧义与 getitem/setitem 多路径未被识别

API semantic ambiguity + multiple execution paths + backward compatibility + failed heuristics；Code / Context / Collaboration / Effort

高

单独 API Design 不能判3；需行为歧义 + 多路径 + compatibility/alternative evidence

Code strong=3；Context strong=3；Collab strong=3

prometheus #9107

Btrfs/Podman 被当作环境描述；either ... or 还可能错误触发 Collaboration=2

filesystem-specific reproduction + allocation semantics + root-cause investigation；Setup / Code / Context / Effort；并修复 Collab false positive

高

必须有明确 reproduce/run/storage volume 关系；普通“环境=Linux”不能提升

Setup medium=2；Code/Context medium=2；Collab expected down

prometheus #10431

pprof / heap / remote.processExternalLabels / agent mode 未进入 memory/lifecycle evidence

profiling + memory lifecycle + subsystem-specific bottleneck；Code / Context / Setup / Effort

高

“memory”单词不足；要求 profiling evidence + named subsystem/function + compare/validation

Code/Context medium=2；Setup medium=2 only if deployment is part of validation

pytorch #135859

torch.compile 不等于当前 regex 的 compiler semantics；dynamic shapes 在标签中未参与组合

compile/recompile + multiple ops + dynamic-shape/compiler module + shared path；Code / Context / Effort

高

“compile”单独不足；需 recompilation/graph/guard/dynamic shape + 多算子或共享路径

Code/Context derived strong=3

quarkus #42510

Code 已到2，但 Context 只识别窄的 shared test framework 文本；rollout 未进入 collaboration

classloader lifecycle + test framework integration + extension-wide rollout；Context / Collaboration / Effort

高

“classloader”或“test”单独不足；需 lifecycle/leak + framework integration + cross-extension rollout

Context strong=3；Collab medium=2

sklearn #27441

documentation-only strong 0 过早覆盖 Context/Setup；行为差异和统计语义没有被识别

behavior validation + conditional/interventional semantics + reproducible discrepancy；Setup / Context / Collaboration / Effort；Code继续0

中高

不能让普通文档都升高；需复现/expected-vs-actual + domain semantic discrepancy

Code保持0；Context medium=2；Setup=1；Collab=2

sklearn #31503

performance weak；Boruvka algorithm、25s vs 5min 没有算法实现 evidence

missing algorithm implementation + benchmark gap + algorithm correctness/performance；Code / Context / Effort

高

算法名称本身不足；需 explicit missing/implement algorithm + benchmark gap

Code strong=3；Context medium=2；validation heavy

sklearn #31554

多指标 batch/weighted average 被当普通 feature；数学可加性/等价性未被捕捉

multi-metric aggregation semantics + parallel/batch design + correctness；Code / Context / Collaboration / Effort

高

“batch”不能判3；需多个 metric family + aggregation/equivalence + performance/parallel requirement

Code/Context derived strong=3；Collab medium=2

apache/pinot #16231

retry、exception wrapping、404/500 mapping 没进入 nontrivial path evidence

exception propagation + retry policy + REST status mapping across layers；Code / Context / Effort

高

单个 exception/404 不足；要求 exception + retry + API mapping / multi-layer path

Code/Context medium=2

apache/pinot #16584

RFC 只让 Collaboration=2；invalidation 只让 Context=2；两层 cache architecture 没形成 strong composite

broker/server multi-layer cache + versioned key + invalidation + correctness + observability；Code / Context / Collaboration / Effort

高

RFC / cache 单独绝不能判3；需多层架构 + correctness/versioning/invalidation + broad implementation surface

Code/Context/Collab derived strong=3

5. Code rule changes

5.1 设计原则

v0.2.1 不改变“level 3 需要 strong evidence”的聚合契约。

不建议修改：

_aggregate_difficulty_dimension()

的核心 strong / medium / weak 层级逻辑。

应在 _collect_code_difficulty_evidence() 中增加组合证据，必要时通过新的私有 helper 生成 derived strong evidence。

5.2 Code Level 1

保持现有语义：

局部 bug；

单函数 / 单配置；

局部 rename；

单一 assertion；

普通测试补充；

明确低风险修改。

task type 仍不能直接把 feature/refactor 提升到2。

5.3 Code Level 2：medium evidence

以下组合可以产生 medium=2：

C2-A：执行 / 存储 traversal

满足至少两类证据：

执行对象：

query / scan / index / segment / storage / reader / traversal；

明确修改行为：

reverse/read from bottom/change scan strategy/avoid full scan/change traversal；

有可验证行为：

latency / scanned rows / benchmark / correctness。

避免：

只出现 “query”；

只出现 “segment”；

只出现 performance 标签。

C2-B：profiling / memory investigation

要求：

profiling evidence + concrete subsystem/function + memory/CPU behavior

例如：

pprof + named function + heap；

profiler + allocation hotspot；

lifecycle leak + specific framework integration。

C2-C：复杂交互状态

要求：

实时操作 + 几何/状态变化 + 多个交互行为

例如：

drag/move/position + snap/alignment/spacing；

viewport rendering + interaction latency；

state transition + interactive feedback。

C2-D：exception / retry propagation

要求：

exception/error class + retry behavior + API/status/layer mapping

例如：

FileNotFound -> retry -> wrapped exception -> HTTP status。

C2-E：shared framework / lifecycle integration

要求：

framework integration + lifecycle/leak/reload/state + regression behavior

但如果同时达到 system-wide rollout，Context 可以升3，Code仍可保持2。

5.4 Code Level 3：derived strong evidence

C3-A：compiler multi-path core behavior

至少满足：

compiler/compile/graph/guard/dynamic shape/decomposition/tracing 领域信号；

recompilation / codegen / graph cache / guard behavior；

多算子、多路径或共享根因。

单独 compile 不能触发。

C3-B：distributed core implementation

保留 v0.2 已有：

FSDP

tensor parallelism

all-gather

collective communication

multi-node coordination

但 strong=3 必须同时存在：

明确实现/修改行为

而不是仅在背景中提到分布式。

C3-C：algorithm implementation + validation

要求：

明确缺失/新增/替换算法；

有算法级行为或性能差距；

需要 benchmark / correctness verification / parallel strategy。

Performance + 算法名不能直接触发。

C3-D：API semantic ambiguity / core behavior semantics

要求：

同一 API/语法存在多个解释或行为；

影响多个执行路径；

存在兼容性或历史行为约束；

简单 heuristic 已知不可行或有多个方案。

C3-E：multi-layer system architecture

要求至少三类：

多层 / 多 subsystem；

invalidation / versioning / lifecycle；

correctness / consistency；

pluggable backend / tracing / observability；

system-level cache/execution architecture。

5.5 标签的作用

以下标签只能作为 supporting evidence：

API Design

module: dynamic shapes

distributed

Performance

Needs Investigation

以下标签不作为技术难度证据：

good first issue

help wanted

Hard

Hard 可以保留到 evidence 中作为人工项目标签参考，但不得单独改变 Code。

5.6 evidence 输出约束

新的组合规则仍输出现有 difficulty evidence schema：

dimension

source

rule_id

matched_value

strength

suggested_level

reason

建议新增 rule_id，例如：

difficulty.code.composite.execution_traversal

difficulty.code.composite.profiled_subsystem

difficulty.code.composite.interactive_geometry

difficulty.code.composite.exception_retry_path

difficulty.code.composite.compiler_multi_path

difficulty.code.composite.algorithm_implementation

difficulty.code.composite.api_semantic_ambiguity

difficulty.code.composite.multi_layer_architecture

不新增对外字段。

6. Project Context rule changes

6.1 Level 1

保持：

只需理解局部模块；

单一 class / method；

局部 helper；

无公共兼容性和生命周期要求。

6.2 Level 2：medium evidence

以下情况建议 level2：

X2-A：subsystem execution context

query/storage/index execution path；

memory subsystem；

file allocation/storage semantics；

retry/error path；

internal test infrastructure。

要求至少有明确目标 subsystem，不因项目名提升。

X2-B：public API / limited compatibility

public API contract；

public type/interface；

有限 compatibility concern；

单一 deprecation step，但没有重大语义争议。

X2-C：domain semantic validation

对于 documentation-only：

如果存在：

reproducible behavior discrepancy；

expected vs actual；

需要理解统计/算法/协议语义才能正确写文档；

则 Project Context 可以为2，即使 Code=0。

这用于保护 sklearn #27441 类型案例，不能让普通 README typo 升高。

X2-D：cross-cutting QA / shared component

跨场景 fixture；

shared test utilities；

property testing；

endpoint/shared API testing；

但范围清晰且已有边界时保持2，不直接3。

6.3 Level 3：derived strong evidence

X3-A：public API semantic policy

要求：

public API target + deprecation/backward compatibility + unresolved behavior/policy

不是单独 API 或 Deprecate。

适用：

pandas #62022。

X3-B：API semantic ambiguity

要求：

相同调用/语法存在多个解释；

get/set/read/write 或其他多路径行为不一致；

compatibility risk；

multiple failed heuristics / design alternatives。

适用：

pandas #65326。

X3-C：compiler / execution stack internals

要求：

compiler domain + concrete internal mechanism + multi-path/shared behavior

适用：

torch.compile 多算子 recompilation。

X3-D：distributed training / communication semantics

保留 v0.2 当前强证据：

FSDP

tensor parallelism

collective communication

distributed state

但仍要求其为任务实现核心，而非背景说明。

X3-E：system framework lifecycle

要求组合：

lifecycle/classloader/reload/global state；

shared testing/runtime framework；

system-wide or extension-wide rollout。

适用：

Quarkus classloader leak defense。

X3-F：multi-layer RFC architecture

要求：

RFC/proposal 只是弱/中信号；

同时必须有多个系统层；

correctness/versioning/invalidation/lifecycle；

明确 broad architecture surface。

适用：

Pinot Broker + Server cache。

X3-G：multi-family mathematical/API semantics

要求：

多个 metric/algorithm family；

aggregation/equivalence correctness；

API or execution strategy；

方案并非简单逐批平均。

适用：

sklearn batch metrics。

6.4 为什么不修改 aggregation

不要采用：

“两个 medium evidence = level3”

这种全局规则。

原因：

容易把普通 API + 普通 performance 误升3；

容易让评论/环境/标签间接叠加；

不利于解释。

应由 collector 对“明确组合”生成一个 derived strong evidence，再交给现有 aggregator。

7. Effort rule changes

7.1 当前问题

当前 Effort 的主要问题不是 bucket 边界，而是：

Code / Context 低估导致 scope=local；

actionability=unclear 时直接 scope=unclear；

unclear 固定基础 bucket=half_day；

validation burden 不进入决策；

code=3 只保证最低 one_day；

根因未知的 profiling 成本没有体现；

documentation semantic verification 被当成 micro content change。

7.2 不使用四维总分

v0.2.1 继续禁止：

Code + Setup + Context + Collaboration -> bucket

Effort 应由五组因素联合决定：

scope

actionability

technical_complexity

validation_burden

uncertainty

7.3 technical_complexity

建议在 Effort 内部派生，不新增外部字段。

low

Code <=1；

Context <=1；

没有 material strong evidence。

medium

Code=2 或 Context=2；

或有多个 medium technical evidence。

strong

Code=3；

或 Context=3；

且对应 strong evidence 可追溯。

technical_complexity 不能由 Collaboration 或 comment count 提升。

7.4 validation_burden

建议新增私有 helper：

_infer_validation_burden(...)

返回：

none

light

heavy

并在 Effort evidence 中记录原因。

none

文案 / rename / local config；

无运行验证。

light

单元测试；

单一 regression test；

本地有限验证；

一个明确环境的简单复现。

heavy

至少满足一种：

profiling + before/after benchmark；

大规模 performance benchmark；

多设备/多浏览器矩阵；

distributed / multi-device validation；

compatibility regression across multiple execution paths；

algorithm correctness across datasets/metric families；

cross-extension/framework regression；

system/resource scaling verification。

“performance”单词本身不是 heavy validation。

7.5 uncertainty

建议区分：

bounded uncertainty

根因未完全确定；

但症状、复现、目标行为和验证方式明确。

这类任务仍可评分，通常增加 Effort 风险。

例如：

Prometheus memory profiling；

Pinot scan performance。

unbounded uncertainty

body missing；

support question；

是否实施都未决定；

scope/acceptance boundary 未形成；

tracker/dashboard。

这类不应给真实 Effort。

7.6 actionability 与 Effort applicability

non_actionable

保持：

applicable=false

provisional=true

confidence=low

外部 compatibility bucket 继续只是占位，不代表工时。

unclear

v0.2.1 建议细化：

如果 reason 包含：

body_missing

support_question

actionable_scope_not_explicit

且没有 concrete implementation scope：

applicable=false

provisional=true

confidence=low

由于 TaskFeatures.estimated_effort_bucket 仍要求合法 bucket，为避免 schema 变化，可以继续保留兼容 bucket，但 applicable=false 必须是解释该值的权威字段。

design_pending

建议：

provisional=true

如果 implementation boundary 明确：applicable=true

如果只是“Should we ...?”、方案未选且 migration/compatibility 范围未知：

applicable=false

confidence low/medium

RFC 已有明确 architecture scope 时仍可给 provisional effort。

7.7 新 Effort 决策表

条件

推荐 bucket

non-applicable

compatibility bucket；不得解释为真实工时

micro + actionable + no validation

under_2h

local + low technical + none/light validation

half_day

local + medium technical 或明确 Setup=2 + bounded scope

one_day

module + low/medium technical + light validation + scope明确

one_day

module + heavy validation

multi_day

module + strong technical + bounded-but-uncertain root cause

multi_day

cross_module

multi_day

system

multi_day

documentation-only + Code=0 + Context=0

under_2h

documentation-only + semantic validation + Context>=2

one_day

bounded design_pending + Context=3，但实现范围小

至少 one_day

multi-layer RFC / compiler / distributed / algorithm implementation

multi_day

7.8 为什么以下任务不再落到 half_day

性能 profiling + 修复 + benchmark

因为：

technical >= medium；

validation=heavy；

即使 scope=module，也进入 multi_day。

核心 API 语义调整

因为：

Context strong；

compatibility regression heavy；

通常 system/module + strong -> multi_day。

compiler / torch.compile

因为：

Code/Context strong；

多路径回归；

multi_day。

核心算法性能优化

因为：

algorithm implementation strong；

benchmark/correctness heavy；

multi_day。

跨模块测试框架集成

如果涉及 lifecycle + framework + rollout：

Context strong；

cross-extension validation heavy；

multi_day。

RFC 级功能

只有“RFC”不够；若是 multi-layer architecture + correctness/invalidation：

system scope；

multi_day。

分布式训练路径

Code/Context strong；

distributed validation heavy；

multi_day。

8. Setup / Collaboration protected behavior

8.1 Setup：只做定向补强

Setup 不做全局上调。

必须保留

Environment: Windows 只算 reported environment；

Linux/macOS/Docker/Kubernetes 单独出现不能提高；

documentation-only 不需要运行时仍保持0。

新增 Setup medium=2

S2-A：filesystem/container reproduction

要求：

明确 reproduce/run；

特定 filesystem/storage behavior；

container/volume 是问题复现条件的一部分。

适用：

Btrfs + Podman volume。

S2-B：deployment profiling requirement

要求：

profiler / benchmark；

明确在 deployed service/cluster 环境比较；

该部署环境是验证问题的一部分，不只是模板信息。

新增/补强 Setup strong=3

S3-A：distributed validation required

要求：

FSDP / tensor parallel / collective / all-gather+run/test/benchmark/speedup/validation

由任务本身要求多设备/分布式验证，而不是从项目名称推断。

S3-B：GPU / CUDA / ROCm

继续保留 v0.2 明确 requirement 规则。

8.2 Setup 有意接受的“不一致”

以下案例如果文本不能证明特殊环境为任务必要条件，不为追求人工一致性强行提高：

Pinot #6970；

torch.compile #135859；

Quarkus #42510。

这属于防过拟合约束。

8.3 Collaboration：不按评论数扩大

必须保留

comment_count 只能 weak；

40 条评论不能直接 >1；

Needs Discussion / API Design / RFC 可以2；

breaking cross-team decision 才能3。

修复 C-bug：either ... or 过宽

当前 either ... or 可能把普通说明文字误判成 multiple design options。

v0.2.1 应要求附近同时出现：

approach

design

strategy

behavior

option

implementation

等设计词。

新增 Collaboration medium=2

COL2-A：rollout strategy

要求：

opt-in / opt-out；

phased rollout；

extension owners / multiple components；

adoption policy。

COL2-B：unresolved semantic remedy

要求：

明确有两个以上修复方向；

需要维护者决定行为语义；

不是普通“either A or B”描述。

COL2-C：distributed implementation design alternatives

例如：

two tensor subclass designs；

existing abstraction may or may not be reusable；

已有明确 implementation choice 未定。

新增 Collaboration strong=3

COL3-A：API semantic compatibility decision

要求：

API Design；

backward compatibility / behavior ambiguity；

多个方案；

选择会影响现有用户语义。

COL3-B：multi-layer RFC architecture review

要求：

RFC draft；

broad architecture surface；

target/review 未定；

correctness / lifecycle / configuration policy。

RFC 单词仍不能直接3。

9. Insufficient-information handling

7 条 insufficient_information 必须单独处理，不尝试用 v0.2.1 “补成正确难度”。

9.1 eslint #17733 Dependency Dashboard

保持：

actionability=non_actionable

confidence=low

effort.applicable=false

effort.provisional=true

其 compatibility bucket 不代表执行全部依赖升级的工时。

9.2 nodejs/undici #4122

当前问题是：

标题是 Should ...?

正文仍在讨论是否 hash；

collision、migration、compatibility 均未定。

建议新增：

unresolved_design_choice

reason。

如果没有 concrete implementation boundary：

actionability=design_pending

effort.applicable=false

provisional=true

不能根据“cache/hash”关键词强行评分。

9.3 pytorch/ao #2147 roadmap/tracker

保持：

non_actionable；

low confidence；

effort not applicable。

不能把整个 roadmap 的 Code=3 / Context=3 解释为单个推荐任务难度。

9.4 body_missing 四条

excalidraw #1007

excalidraw #5301

nodejs/undici #3276

pytorch/ao #2298

保持：

confidence=low；

provisional=true。

v0.2.1 建议把：

body_missing + no concrete implementation boundary

的 effort 标记为：

applicable=false

而不是把 half_day 当成真实工作量。

9.5 support/question 类型

对于：

“Why ...?”

“How should ...?”

“Is this expected?”

无正文咨询式标题

在无明确 feature/bug acceptance scope 时：

actionability=unclear

effort.applicable=false

low confidence

不能为了覆盖率将其转换成 feature 难度。

10. Positive-case protection

10.1 wagtail/wagtail #14318

v0.2：

Code=2

Setup=1

Context=1

Collaboration=2

Effort=one_day

项目成员：

Code=2

Setup=1

Context=2

Collaboration=2

Effort=one_day

v0.2 正确部分：

测试任务没有因 testing 被固定为1；

property testing / shared testing work 能到 Code=2；

RFC/Review 能到 Collaboration=2；

bounded scope 没有被粗暴判成 multi_day。

v0.2.1 只能：

Context expected_up 到2；

必须保护：

Code 不升3；

Effort 不因“RFC + property test”自动变 multi_day。

保护规则：

RFC 是辅助信号；若正文明确拆出 bounded subtask、存在 acceptance criteria / explicit estimate / time-box，则不得仅因上层 RFC 将 scope 变 system。

10.2 pytorch/ao #988

v0.2：

Code=3

Context=3

这说明：

good first issue 已经不能压低技术难度；

tensor parallelism strong evidence 能正确提升；

performance 并不是唯一触发来源。

v0.2.1 必须保护 Code/Context=3 的方向。

预期只补：

Setup expected_up（明确 distributed test）

Effort expected_up（分布式 validation burden）

10.3 pytorch/ao #1224

v0.2 已正确识别：

Code=3

Context=3

Effort=multi_day

必须保护：

FSDP/all-gather strong evidence；

performance 不负责触发3；

newcomer 不压低难度。

v0.2.1 仅允许：

Setup expected_up；

Collaboration 在明确设计 alternatives 时 expected_up；

Effort expected_same。

11. Rule design table

维度

v0.2问题

v0.2.1拟新增/调整证据

证据强度

触发条件

防误判条件

对应人工案例

Code

performance weak 后仍停1

execution/storage traversal composite

medium -> 2

scan/read/traversal + concrete change + benchmark/behavior

query/segment 单词不能触发

Pinot #6970

Code

交互任务未识别

realtime geometry/interaction composite

medium -> 2

move/drag + alignment/snap/spacing + realtime behavior

alignment 单词不足

Excalidraw #11273

Code

性能卡顿未识别

scaled interactive profiling composite

medium -> 2

concrete scale + lag/latency + interactive bottleneck

performance 标签不足

Excalidraw #7237

Code

runtime hot path 停1

system scaling runtime composite

strong -> 3

repeated runtime op + scale with concurrency/replicas + system resource effect

Kubernetes/CPU 单词不足

Kubernetes #82440

Code

compiler自然语言未匹配

compiler multi-path composite

strong -> 3

compile/graph/guard/dynamic shape + recompile + multiple ops/path

compile 单词不足

PyTorch #135859

Code

算法性能未识别

algorithm implementation + benchmark

strong -> 3

explicit implement/missing algorithm + large benchmark/correctness work

algorithm name / Performance 不足

sklearn #31503

Code

数学 batch feature 停1

multi-family aggregation correctness

strong -> 3

multiple metric families + aggregation equivalence + parallel/batch behavior

batch 单词不足

sklearn #31554

Code

retry bug停1

exception/retry/API-path composite

medium -> 2

exception + retry + wrapping/status mapping

单个 HTTP/exception 不足

Pinot #16231

Code

RFC cache停1

multi-layer architecture composite

strong -> 3

multiple layers + invalidation/versioning + correctness/ops

RFC/cache 单词不足

Pinot #16584

Context

API deprecation停1

public API policy composite

strong -> 3

public API target + deprecation/compatibility + discussion/policy

Deprecate/API 单词不足

pandas #62022

Context

API ambiguity停1

semantic ambiguity composite

strong -> 3

same syntax multiple interpretations + multi-path behavior + compatibility

API Design label不足

pandas #65326

Context

profiler/storage任务停1

subsystem semantic context

medium -> 2

named subsystem + behavior semantics

component name不足

Prometheus #9107/#10431

Context

compiler停1

compiler/execution internals composite

strong -> 3

compiler mechanism + shared/multi-op behavior

dynamic shapes label单独不足

PyTorch #135859

Context

test framework停1

lifecycle + shared framework + rollout

strong -> 3

lifecycle/leak + framework integration + broad rollout

testing/classloader单词不足

Quarkus #42510

Context

文档被固定0

semantic validation context

medium -> 2

documentation + reproducible behavior discrepancy + domain semantics

普通文档仍0

sklearn #27441

Context

算法实现停1

algorithm semantic context

medium -> 2

algorithm implementation + correctness/performance understanding

Performance 不足

sklearn #31503

Context

batch metrics停1

multi-family math/API semantics

strong -> 3

multiple metrics + equivalence/aggregation + API strategy

metrics 单词不足

sklearn #31554

Context

RFC只到2

multi-layer architecture context

strong -> 3

RFC + multiple subsystems + correctness/versioning/lifecycle

RFC单独最多2

Pinot #16584

Setup

reported env不应升

保持现有 reported-only weak

weak -> 1

environment template only

永不当requirement

全局保护

Setup

Btrfs/Podman复现漏掉

filesystem/container required reproduction

medium -> 2

reproduce/run + fs/storage + container/volume dependency

只写环境名不触发

Prometheus #9107

Setup

distributed validation漏掉

collective validation requirement

strong -> 3

FSDP/TP/all-gather + test/benchmark/validation

分布式背景描述不足

PyTorch AO #988/#1224

Collaboration

either-or误报

收紧 multiple-options pattern

medium -> 2

design/strategy/approach vocabulary around alternatives

普通 either...or 不触发

Prometheus #9107

Collaboration

rollout漏掉

phased rollout / adoption policy

medium -> 2

opt-in/opt-out/phased rollout/component owners

release 单词不足

Quarkus #42510

Collaboration

API/RFC复杂案例只2

semantic compatibility / architecture review composite

strong -> 3

API ambiguity + compatibility + alternatives，或 RFC multi-layer + review pending

API/RFC 单独不触发3

pandas #65326；Pinot #16584

Effort

local/module低估

validation burden

derived

profiling/benchmark/distributed/compatibility/algorithm regression

performance 单词不足

多个案例

Effort

unclear固定half_day

non-applicable unclear cases

derived

body missing/support/unbounded design

bounded repro bug仍可评分

7 insufficient cases

Information

真实模板常被unclear

扩充 concrete-action templates

medium

expected/actual、minified repro、solution wanted、implementation steps

不因长正文自动actionable

Excalidraw/Prometheus/PyTorch

Information

Should we...未区分设计问题

unresolved_design_choice

medium

interrogative design choice + tradeoff + no chosen implementation

should return 等明确bug不触发

Undici #4122

12. Expected-direction replay

说明：

不伪造 v0.2.1 精确输出；

仅给出设计期预期方向；

顺序为 Code / Setup / Context / Collaboration / Effort；

intentionally_unchanged 表示即使与项目成员值有差异，也不为了拟合单例增加规则。

Task

v0.2

项目成员复核

Code

Setup

Context

Collaboration

Effort

设计理由

apache/pinot #6970

1/1/1/1/half_day

2/2/2/1/multi_day

expected_up

intentionally_unchanged

expected_up

expected_same

expected_up

traversal + benchmark 可泛化；数据规模不等于 Setup requirement

excalidraw #7237

1/1/1/1/half_day

2/1/2/1/multi_day

expected_up

expected_same

expected_up

expected_same

expected_up

scaled interaction performance + heavy validation

excalidraw #11273

1/1/1/0/half_day

2/1/2/1/multi_day

expected_up

expected_same

expected_up

intentionally_unchanged

expected_up

real-time geometry/drag/snap；不为普通 review 全局抬 Collaboration

kubernetes #82440

1/2/1/1/half_day

3/2/3/2/multi_day

expected_up

expected_same

expected_up

intentionally_unchanged

expected_up

runtime scaling strong；评论量不能代替设计证据

pandas #62022

1/1/1/2/half_day

1/1/3/2/one_day

expected_same

expected_same

expected_up

expected_same

expected_up

public API deprecation + compatibility policy

pandas #65326

1/1/1/2/half_day

3/1/3/3/multi_day

expected_up

expected_same

expected_up

expected_up

expected_up

semantic ambiguity + multi-path + compatibility

prometheus #9107

1/1/1/2/half_day

2/2/2/1/multi_day

expected_up

expected_up

expected_up

expected_down

expected_up

fs/container reproduction；修复 either-or false positive

prometheus #10431

1/1/1/1/half_day

2/2/2/1/multi_day

expected_up

expected_up

expected_up

expected_same

expected_up

pprof + deployed comparison + subsystem bottleneck

pytorch #135859

1/1/1/1/half_day

3/2/3/1/multi_day

expected_up

intentionally_unchanged

expected_up

expected_same

expected_up

compiler multi-op composite；不推断未明确 special env

quarkus #42510

2/1/1/0/half_day

2/2/3/2/multi_day

expected_same

intentionally_unchanged

expected_up

expected_up

expected_up

lifecycle + shared framework + rollout；不把测试框架本身当特殊Setup

sklearn #27441

0/0/0/1/under_2h

0/1/2/2/one_day

expected_same

expected_up

expected_up

expected_up

expected_up

documentation 保持 Code0，但需行为/统计语义验证

sklearn #31503

1/1/1/1/half_day

3/1/2/1/multi_day

expected_up

expected_same

expected_up

expected_same

expected_up

missing algorithm + benchmark gap

sklearn #31554

1/1/1/1/half_day

3/1/3/2/multi_day

expected_up

expected_same

expected_up

expected_up

expected_up

multi-metric aggregation correctness + design

apache/pinot #16231

1/1/1/1/half_day

2/1/2/1/one_day

expected_up

expected_same

expected_up

expected_same

expected_up

exception/retry/status multi-layer，但范围明确

apache/pinot #16584

1/2/2/2/one_day

3/2/3/3/multi_day

expected_up

expected_same

expected_up

expected_up

expected_up

multi-layer cache RFC + correctness/versioning

wagtail #14318

2/1/1/2/one_day

2/1/2/2/one_day

expected_same

expected_same

expected_up

expected_same

expected_same

bounded RFC subtask；保护 one_day

pytorch/ao #988

3/1/3/1/one_day

3/3/3/1/multi_day

expected_same

expected_up

expected_same

expected_same

expected_up

保留 TP strong evidence；补 distributed validation

pytorch/ao #1224

3/1/3/1/multi_day

3/3/3/2/multi_day

expected_same

expected_up

expected_same

expected_up

expected_same

保留 FSDP strong evidence；补 validation/design alternatives

13. Regression test plan

本节只设计测试，不写测试代码。

A. Code 复杂任务提升

A1 query traversal -> Code 2

输入特征：

performance bug；

明确 full scan / read from bottom / traversal；

有扫描量或 latency 对照。

预期：

Code=2；

不到3；

evidence 包含 execution traversal medium。

必要性：

保护 Pinot #6970 类型任务，同时防止普通 query issue 自动3。

A2 compiler multi-op -> Code 3

输入特征：

torch.compile/compiler；

multiple ops；

extra recompilation；

graph/guard/dynamic-shape supporting signal。

预期：

Code=3；

strong composite evidence。

必要性：

保护复杂编译器路径召回。

A3 algorithm + benchmark -> Code 3

输入：

explicit missing algorithm；

benchmark gap；

implement/optimize behavior。

预期：

Code=3。

反例：

只有 Performance + algorithm name -> 不自动3。

A4 realtime geometry -> Code 2

输入：

drag/move；

alignment/snap/spacing；

realtime feedback。

预期：

Code=2。

B. Context 复杂任务提升

B1 public API deprecation policy -> Context 3

输入：

public API target；

deprecation；

compatibility；

needs discussion。

预期：

Context=3；

Code 仍可=1。

B2 public API contract only -> Context 2

输入：

单一 public API contract；

无 breaking semantic ambiguity。

预期：

Context=2，不到3。

B3 API semantic ambiguity -> Context 3

输入：

same syntax two interpretations；

get/set multiple paths；

compatibility；

alternatives。

预期：

Context=3；

strong evidence。

B4 documentation semantic verification -> Context 2

输入：

documentation task；

reproducible expected/actual discrepancy；

domain semantics required。

预期：

Code=0；

Context=2。

C. Effort 不再系统性停在 half_day

C1 profiling + benchmark -> multi_day

输入：

Code=2 / Context=2 evidence；

pprof/benchmark；

before/after validation。

预期：

Effort=multi_day；

validation burden=heavy。

C2 bounded module bug -> one_day

输入：

retry + exception + API status；

scope明确；

regression test有限。

预期：

Code=2；

Context=2；

Effort=one_day。

必要性：

防止所有 Code2/Context2 被粗暴改成 multi_day。

C3 bounded QA subtask -> one_day

输入：

fixture/property testing；

RFC child task；

acceptance criteria；

explicit bounded scope/time-box。

预期：

Effort=one_day。

保护 Wagtail positive case。

D. performance 不能自动变3

输入：

PERF: Reduce temporary allocation in local helper

无 distributed/compiler/algorithm/core evidence。

预期：

Code<3；

Context<3。

E. newcomer 不能降低 difficulty

同一任务分别：

无 newcomer label；

加 good first issue。

预期四维完全相同。

F. reported environment 不能自动提高 Setup

输入：

Environment: Windows 11

普通 local bug。

预期：

Setup=1。

对照：

only reproducible on macOS platform-specific backend

Setup=2。

G. comments 不能自动提高 Collaboration

输入：

local bug；

comment_count=40；

无 design evidence。

预期：

Collaboration<=1。

H. roadmap/tracker 保持 non-actionable

输入：

tracker label；

多 milestones / child PR。

预期：

actionability=non_actionable

effort.applicable=false

provisional=true

low confidence。

I. body missing 保持 low confidence 且 Effort 不可解释

输入：

feature title；

body empty。

预期：

body_missing=true；

confidence=low；

actionability=unclear；

effort.applicable=false；

provisional=true。

bucket 只作为 compatibility value。

J. 正向案例保护

J1 bounded cross-cutting QA

预期：

Code=2；

Context=2；

Collaboration=2；

Effort=one_day；

不因 RFC 自动3/multi_day。

J2 Tensor Parallel implementation

输入：

tensor parallel；

explicit test/run；

missing ops；

good first issue。

预期：

Code=3；

Context=3；

Setup=3（若明确 distributed validation）；

newcomer label 不降低任何技术维度。

J3 FSDP low-bit all-gather

输入：

FSDP/all-gather；

quantize/dequantize；

benchmark/speedup；

two implementation alternatives。

预期：

Code=3；

Context=3；

Setup=3；

Collaboration=2；

Effort=multi_day。

K. Collaboration false-positive guard

输入：

普通正文出现 either filesystem or application issue

无 design/approach/strategy。

预期：

不生成 multiple_unresolved_options medium evidence；

comment count 单独最多1。

L. unresolved design applicability

输入：

标题 Should cache keys be hashed?

正文列出 collision/compat tradeoffs；

无选择、无 acceptance criteria。

预期：

actionability=design_pending 或明确 unresolved-design reason；

effort.applicable=false；

provisional=true。

14. Versioning plan

14.1 TASK_FEATURE_VERSION

建议：

TASK_FEATURE_VERSION 保持：

task-features-v0.3

原因：

TaskFeatures dataclass 不变；

对外字段不变；

feature_evidence["difficulty_assessment"] schema 不变；

task type schema 不变；

skill requirement schema 不变；

effort bucket enum 不变；

只调整 difficulty formula 的内部规则和 applicability 语义。

因此无需把整个 task feature schema 升到 v0.4。

14.2 DIFFICULTY_FORMULA_VERSION

实施 v0.2.1 时建议修改为：

DIFFICULTY_FORMULA_VERSION = "difficulty-rules-v0.2.1"

原因：

Code / Context evidence 规则发生变化；

Effort decision 发生变化；

information/actionability 的解释将定向增强；

相同 TaskFeatures schema 下，difficulty 输出语义发生变化。

14.3 本轮

本轮不实际修改任何版本常量。

15. Explicit non-goals

v0.2.1 明确不做：

不修改 B2 task type rules；

不修改 infer_skill_requirements()；

不修改 matching；

不修改 newcomer / novice 公式；

不修改 growth 公式；

不修改 SQLite schema；

不创建 migration；

不修改 CLI / API / Web；

不引入机器学习模型；

不根据 repository 名硬编码；

不把 AI 辅助初评当 gold label；

不追求 18 条人工案例逐条 exact fit；

不把 performance / API / RFC / core 单关键词变成 hard rule；

不让 comment count 重新主导 Collaboration；

不让 reported environment 重新主导 Setup；

不把 good first issue 作为技术难度下调信号；

不重新设计 B4 skill extraction；

本轮不刷新数据库、不重新跑 608 diagnostics。

16. Implementation checklist

下一阶段进入代码实现前，应按以下顺序执行。

16.1 优先修改 / 新增的内部逻辑

预计修改：

_assess_information_quality()

增加真实 Issue template/actionability 识别；

增加 unresolved design choice；

不改变 body missing / tracker 低置信度保护。

_collect_code_difficulty_evidence()

增加 medium composite；

增加 derived strong composite；

使用 source-aware title/body/label supporting signals；

不修改 task type classification。

_collect_context_difficulty_evidence()

增加 API policy / semantic ambiguity / compiler / framework lifecycle / algorithm / RFC composite。

_collect_setup_difficulty_evidence()

仅增加 filesystem-required reproduction；

distributed validation requirement；

deployment profiling requirement；

reported environment 规则保持。

_collect_collaboration_difficulty_evidence()

收紧 either ... or；

增加 rollout / semantic design / multi-layer RFC composite；

comment_count weak 规则保持。

_infer_effort_scope()

不再让“可行动但模板未识别”的复杂案例都落入 unclear；

保持 scope 不是四维总分。

_estimate_effort()

引入 technical complexity；

引入 validation burden；

引入 non-applicable unclear / unresolved design handling。

建议新增私有 helper：

_infer_validation_burden(...)

_has_bounded_implementation_scope(...)

_add_composite_difficulty_evidence(...)（如实现可复用）

若必要，建立少量 domain-agnostic signal helper。

16.2 尽量不修改

_aggregate_difficulty_dimension()

原则上保持不变。

只有实现时确认现有 evidence conflict / dedup 结构无法承载组合证据，才允许做最小修改。

16.3 source 精度清理

当前 Code collector 第一轮 regex 使用 combined = title + body 却标记 source="title"。

v0.2.1 实现时建议顺手改为：

title rule 只匹配 context.title；

body rule 只匹配 context.semantic_body；

composite evidence 使用 source="derived"。

这是 explainability 修正，不应用来人为改变 level。

16.4 实现后但在数据库刷新前

下一阶段实现完成后先：

跑 difficulty 单元测试；

跑现有 B2/B3 相关回归；

跑全部 263+ tests；

用固定 25 条人工复核案例做静态 replay；

检查：

performance-only 不变3；

newcomer 不压低；

reported env 不升级；

comments 不升级；

tracker/non-actionable contract；

body-missing contract；

positive cases 不回退。

通过后才允许：

创建新的 v0.2.1 数据库；

重新生成 608 条 diagnostics。

17. Go / No-Go recommendation

17.1 是否值得做 v0.2.1

GO。

理由不是“新规则准确率低”，而是：

在定向高风险人工复核子集中，Project Context 有 16/18 低于项目成员复核；

Effort 有 16/18 低于项目成员复核；

现有 608 条诊断显示 Code / Context 大量停留在 level1 且没有 material evidence；

根因可以追溯到 evidence vocabulary、组合证据和 Effort validation burden，而不是 v0.2 整体架构失败；

v0.2 已有三条 positive case 证明其“证据化 + no automatic hard-3”方向应保留。

因此最合理的动作是定向小修，不是推翻重写。

17.2 最需要修改的 3 处规则

第一优先：Project Context composite evidence

补足：

public API policy；

semantic ambiguity；

compiler internals；

lifecycle/shared framework；

distributed semantics；

multi-layer RFC；

algorithm/math semantics。

第二优先：Effort validation burden + applicability

补足：

profiling/benchmark；

distributed validation；

compatibility regression；

algorithm correctness；

uncertainty；

body-missing/support/unbounded design 不再解释为真实 half_day。

第三优先：Code complex composite evidence

补足：

compiler multi-path；

algorithm implementation；

system runtime scaling；

API semantic behavior；

execution/storage traversal；

realtime interaction；

exception/retry path。

17.3 必须保护的 v0.2 行为

必须继续成立：

performance signal alone 不会 Code=3；

performance signal alone 不会 Context=3；

newcomer/good first issue 不改变四维；

reported environment 不提高 Setup；

required environment 才提高 Setup；

comments 不能把 Collaboration 推到2/3；

RFC/API/core 单词不能单独判3；

task type 仅 weak prior；

body missing / unclear 不允许 high confidence；

roadmap/tracker/dashboard non-actionable；

non-actionable effort applicable=false；

level 3 必须有 strong evidence；

difficulty evidence 稳定、去重、JSON serializable；

B2 task type evidence 不被 B3 改写。

17.4 下一阶段预计修改的函数

主要：

_assess_information_quality()

_collect_code_difficulty_evidence()

_collect_setup_difficulty_evidence()（定向）

_collect_context_difficulty_evidence()

_collect_collaboration_difficulty_evidence()（定向）

_infer_effort_scope()

_estimate_effort()

预计新增：

_infer_validation_burden()

一个或多个 composite evidence helper。

原则上不修改：

_classify_task_types()

_aggregate_difficulty_dimension() 核心聚合策略

infer_skill_requirements()

newcomer/growth

matching

persistence / CLI / API / Web。

17.5 最终结论

本轮仅完成 difficulty-rules-v0.2.1 设计，尚未修改任何源代码。