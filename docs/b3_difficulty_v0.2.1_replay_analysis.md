OSS-Mentor B3 difficulty-rules-v0.2.1 固定案例 Replay 分析

阶段：B3-E4 第一阶段公式：difficulty-rules-v0.2.1TaskFeatures：task-features-v0.3结论：GO_TO_FULL_DIAGNOSTICS本轮未刷新数据库，未运行 608 条全量 diagnostics。

1. 方法与输入固定

本轮严格使用 data/annotations/difficulty_member_review_v0.2.json 中保存的 25 条固定 snapshot，不访问 GitHub，不获取当前 Issue 内容。25 条全部成功 replay，重算后的 task types 与固定 snapshot 25/25 一致，每条 difficulty_assessment.formula_version 均为 difficulty-rules-v0.2.1。

人工复核属于“项目成员人工复核（AI辅助）”，不是独立盲评或金标准；18 条可评分案例是高风险筛选子集。因此下面所有数值只描述与项目成员复核结果的一致性变化，不解释为准确率或总体性能。

2. 18 条可评分案例：v0.2 vs v0.2.1

指标

v0.2

v0.2.1

四维 exact

27/72 (37.5%)

52/72 (72.2%)

四维 MAD

0.847

0.347

Effort exact

2/18 (11.1%)

12/18 (66.7%)

Effort MAD

1.556

0.556

Effort 低于项目成员复核

16/18

6/18

Effort 相差 ≥2 档

12/18

4/18

2.1 分维度

维度

v0.2 exact

v0.2.1 exact

v0.2 MAD

v0.2.1 MAD

v0.2.1低于人工

v0.2.1高于人工

Code

6/18

15/18

1.000

0.278

3/18

0/18

Setup

10/18

14/18

0.556

0.222

4/18

0/18

Project Context

2/18

11/18

1.278

0.556

7/18

0/18

Collaboration

9/18

12/18

0.556

0.333

6/18

0/18

3. 主要变化

Code

exact：6/18 → 15/18。

MAD：1.000 → 0.278。

低于项目成员复核：12/18 → 3/18。

v0.2 → v0.2.1：上升 9，下降 0，不变 9。

Code 的系统性低估在该重点子集上明显减少，且没有出现 Code 高于项目成员复核的案例。

Project Context

exact：2/18 → 11/18。

MAD：1.278 → 0.556。

低于项目成员复核：16/18 → 7/18。

v0.2 → v0.2.1：上升 9，下降 0，不变 9。

Context 仍有残余低估，但已经从本轮最严重问题之一显著收敛。

Effort

exact：2/18 → 12/18。

MAD：1.556 → 0.556。

低于项目成员复核：16/18 → 6/18。

相差 ≥2 档：12/18 → 4/18。

v0.2 → v0.2.1：上升 11，下降 0，不变 7。

Effort 的 half_day 低估问题在固定重点子集上明显减少。

Setup / Collaboration

Setup：上升 5、下降 1、不变 12；v0.2.1 在18条中 0 条高于项目成员复核。Collaboration：上升 2、下降 1、不变 15；同样 0 条高于项目成员复核。

因此本 replay 没有发现 Setup 或 Collaboration 大面积意外上涨。

4. hard-3 检查

维度

v0.2 level3数量

v0.2.1 level3数量

v0.2.1 高于人工数量

Code

2

6

0

Project Context

2

6

0

level3 触发明显增加，这是设计预期的复杂证据召回；但在18条可评分重点案例中没有任何 Code/Context 预测高于项目成员复核，因此当前没有观察到新的系统性 hard-3 过触发信号。是否在608条总体上过触发，必须通过下一阶段全量 diagnostics 判断。

5. 设计期 Expected Direction 回放

90 个“任务×维度”方向判断中：

matched_expected_direction：65

intentionally_unchanged：5

unexpected_change：20

18 个任务中，6 个五个维度全部按设计方向变化；12 个为 partially_matched。这里的“方向匹配”不是精确评分，也不能解释为准确率。

Task

Dimension

v0.2

v0.2.1

Member Review

Expected Direction

Actual Direction

Match Design?

apache/pinot #6970

code

1

2

2

expected_up

up

matched_expected_direction

apache/pinot #6970

setup

1

1

2

intentionally_unchanged

same

intentionally_unchanged

apache/pinot #6970

project_context

1

2

2

expected_up

up

matched_expected_direction

apache/pinot #6970

collaboration

1

1

1

expected_same

same

matched_expected_direction

apache/pinot #6970

effort

half_day

half_day

multi_day

expected_up

same

unexpected_change

excalidraw/excalidraw #7237

code

1

2

2

expected_up

up

matched_expected_direction

excalidraw/excalidraw #7237

setup

1

1

1

expected_same

same

matched_expected_direction

excalidraw/excalidraw #7237

project_context

1

1

2

expected_up

same

unexpected_change

excalidraw/excalidraw #7237

collaboration

1

1

1

expected_same

same

matched_expected_direction

excalidraw/excalidraw #7237

effort

half_day

multi_day

multi_day

expected_up

up

matched_expected_direction

excalidraw/excalidraw #11273

code

1

2

2

expected_up

up

matched_expected_direction

excalidraw/excalidraw #11273

setup

1

1

1

expected_same

same

matched_expected_direction

excalidraw/excalidraw #11273

project_context

1

1

2

expected_up

same

unexpected_change

excalidraw/excalidraw #11273

collaboration

0

0

1

intentionally_unchanged

same

intentionally_unchanged

excalidraw/excalidraw #11273

effort

half_day

one_day

multi_day

expected_up

up

matched_expected_direction

kubernetes/kubernetes #82440

code

1

1

3

expected_up

same

unexpected_change

kubernetes/kubernetes #82440

setup

2

2

2

expected_same

same

matched_expected_direction

kubernetes/kubernetes #82440

project_context

1

1

3

expected_up

same

unexpected_change

kubernetes/kubernetes #82440

collaboration

1

1

2

intentionally_unchanged

same

intentionally_unchanged

kubernetes/kubernetes #82440

effort

half_day

half_day

multi_day

expected_up

same

unexpected_change

pandas-dev/pandas #62022

code

1

1

1

expected_same

same

matched_expected_direction

pandas-dev/pandas #62022

setup

1

1

1

expected_same

same

matched_expected_direction

pandas-dev/pandas #62022

project_context

1

1

3

expected_up

same

unexpected_change

pandas-dev/pandas #62022

collaboration

2

2

2

expected_same

same

matched_expected_direction

pandas-dev/pandas #62022

effort

half_day

half_day

one_day

expected_up

same

unexpected_change

pandas-dev/pandas #65326

code

1

3

3

expected_up

up

matched_expected_direction

pandas-dev/pandas #65326

setup

1

1

1

expected_same

same

matched_expected_direction

pandas-dev/pandas #65326

project_context

1

3

3

expected_up

up

matched_expected_direction

pandas-dev/pandas #65326

collaboration

2

2

3

expected_up

same

unexpected_change

pandas-dev/pandas #65326

effort

half_day

multi_day

multi_day

expected_up

up

matched_expected_direction

prometheus/prometheus #9107

code

1

1

2

expected_up

same

unexpected_change

prometheus/prometheus #9107

setup

1

2

2

expected_up

up

matched_expected_direction

prometheus/prometheus #9107

project_context

1

1

2

expected_up

same

unexpected_change

prometheus/prometheus #9107

collaboration

2

1

1

expected_down

down

matched_expected_direction

prometheus/prometheus #9107

effort

half_day

half_day

multi_day

expected_up

same

unexpected_change

prometheus/prometheus #10431

code

1

2

2

expected_up

up

matched_expected_direction

prometheus/prometheus #10431

setup

1

2

2

expected_up

up

matched_expected_direction

prometheus/prometheus #10431

project_context

1

1

2

expected_up

same

unexpected_change

prometheus/prometheus #10431

collaboration

1

1

1

expected_same

same

matched_expected_direction

prometheus/prometheus #10431

effort

half_day

multi_day

multi_day

expected_up

up

matched_expected_direction

pytorch/pytorch #135859

code

1

3

3

expected_up

up

matched_expected_direction

pytorch/pytorch #135859

setup

1

1

2

intentionally_unchanged

same

intentionally_unchanged

pytorch/pytorch #135859

project_context

1

3

3

expected_up

up

matched_expected_direction

pytorch/pytorch #135859

collaboration

1

1

1

expected_same

same

matched_expected_direction

pytorch/pytorch #135859

effort

half_day

multi_day

multi_day

expected_up

up

matched_expected_direction

quarkusio/quarkus #42510

code

2

2

2

expected_same

same

matched_expected_direction

quarkusio/quarkus #42510

setup

1

1

2

intentionally_unchanged

same

intentionally_unchanged

quarkusio/quarkus #42510

project_context

1

3

3

expected_up

up

matched_expected_direction

quarkusio/quarkus #42510

collaboration

0

2

2

expected_up

up

matched_expected_direction

quarkusio/quarkus #42510

effort

half_day

multi_day

multi_day

expected_up

up

matched_expected_direction

scikit-learn/scikit-learn #27441

code

0

0

0

expected_same

same

matched_expected_direction

scikit-learn/scikit-learn #27441

setup

0

1

1

expected_up

up

matched_expected_direction

scikit-learn/scikit-learn #27441

project_context

0

2

2

expected_up

up

matched_expected_direction

scikit-learn/scikit-learn #27441

collaboration

1

1

2

expected_up

same

unexpected_change

scikit-learn/scikit-learn #27441

effort

under_2h

one_day

one_day

expected_up

up

matched_expected_direction

scikit-learn/scikit-learn #31503

code

1

3

3

expected_up

up

matched_expected_direction

scikit-learn/scikit-learn #31503

setup

1

1

1

expected_same

same

matched_expected_direction

scikit-learn/scikit-learn #31503

project_context

1

2

2

expected_up

up

matched_expected_direction

scikit-learn/scikit-learn #31503

collaboration

1

1

1

expected_same

same

matched_expected_direction

scikit-learn/scikit-learn #31503

effort

half_day

multi_day

multi_day

expected_up

up

matched_expected_direction

scikit-learn/scikit-learn #31554

code

1

1

3

expected_up

same

unexpected_change

scikit-learn/scikit-learn #31554

setup

1

1

1

expected_same

same

matched_expected_direction

scikit-learn/scikit-learn #31554

project_context

1

1

3

expected_up

same

unexpected_change

scikit-learn/scikit-learn #31554

collaboration

1

1

2

expected_up

same

unexpected_change

scikit-learn/scikit-learn #31554

effort

half_day

half_day

multi_day

expected_up

same

unexpected_change

apache/pinot #16231

code

1

2

2

expected_up

up

matched_expected_direction

apache/pinot #16231

setup

1

1

1

expected_same

same

matched_expected_direction

apache/pinot #16231

project_context

1

2

2

expected_up

up

matched_expected_direction

apache/pinot #16231

collaboration

1

1

1

expected_same

same

matched_expected_direction

apache/pinot #16231

effort

half_day

one_day

one_day

expected_up

up

matched_expected_direction

apache/pinot #16584

code

1

3

3

expected_up

up

matched_expected_direction

apache/pinot #16584

setup

2

1

2

expected_same

down

unexpected_change

apache/pinot #16584

project_context

2

3

3

expected_up

up

matched_expected_direction

apache/pinot #16584

collaboration

2

3

3

expected_up

up

matched_expected_direction

apache/pinot #16584

effort

one_day

multi_day

multi_day

expected_up

up

matched_expected_direction

wagtail/wagtail #14318

code

2

2

2

expected_same

same

matched_expected_direction

wagtail/wagtail #14318

setup

1

1

1

expected_same

same

matched_expected_direction

wagtail/wagtail #14318

project_context

1

2

2

expected_up

up

matched_expected_direction

wagtail/wagtail #14318

collaboration

2

2

2

expected_same

same

matched_expected_direction

wagtail/wagtail #14318

effort

one_day

one_day

one_day

expected_same

same

matched_expected_direction

pytorch/ao #988

code

3

3

3

expected_same

same

matched_expected_direction

pytorch/ao #988

setup

1

3

3

expected_up

up

matched_expected_direction

pytorch/ao #988

project_context

3

3

3

expected_same

same

matched_expected_direction

pytorch/ao #988

collaboration

1

1

1

expected_same

same

matched_expected_direction

pytorch/ao #988

effort

one_day

multi_day

multi_day

expected_up

up

matched_expected_direction

pytorch/ao #1224

code

3

3

3

expected_same

same

matched_expected_direction

pytorch/ao #1224

setup

1

3

3

expected_up

up

matched_expected_direction

pytorch/ao #1224

project_context

3

3

3

expected_same

same

matched_expected_direction

pytorch/ao #1224

collaboration

1

1

2

expected_up

same

unexpected_change

pytorch/ao #1224

effort

multi_day

multi_day

multi_day

expected_same

same

matched_expected_direction

6. Positive-case protection

Task

Code

Setup

Context

Collaboration

Effort

Protection

wagtail/wagtail #14318

2

1

2

2

one_day

PASS

pytorch/ao #988

3

3

3

1

multi_day

PASS

pytorch/ao #1224

3

3

3

1

multi_day

PASS

结论：3/3 positive cases protected。

Wagtail #14318：Code=2，Context提升到2，Collaboration=2，Effort仍为 one_day；没有被 RFC 规则推成 Code3 / multi_day。

PyTorch AO #988：Code=3、Context=3 保持，Setup提升到3，Effort提升到 multi_day。

PyTorch AO #1224：Code=3、Context=3、Effort=multi_day 保持，Setup提升到3。

7. 7 条 insufficient-information 保护

Task

Category

Confidence

Actionability

Effort applicable

Provisional

Result

eslint/eslint #17733

non_actionable

low

non_actionable

false

true

PASS

nodejs/undici #4122

unresolved_design_or_question

medium

design_pending

false

true

PASS

pytorch/ao #2147

non_actionable

low

non_actionable

false

true

PASS

excalidraw/excalidraw #1007

body_missing

low

unclear

false

true

PASS

excalidraw/excalidraw #5301

body_missing

low

unclear

false

true

PASS

nodejs/undici #3276

body_missing

low

unclear

false

true

PASS

pytorch/ao #2298

body_missing

low

unclear

false

true

PASS

结论：7/7 protected。

尤其是4条 body-missing 记录，v0.2 诊断中虽然已经是 low confidence，但 effort 仍是 applicable；v0.2.1 replay 中均变为 applicable=false + provisional=true，因此兼容 bucket 不再被解释成可靠真实工时。

8. Unexpected / residual changes

kubernetes/kubernetes #82440：Code/Context/Effort remain unchanged despite expected-up. The v0.2.1 evidence vocabulary does not yet recognize the probe-execution scaling pattern as a runtime-hot-path composite; heavy validation alone does not raise effort when scope remains local and technical_complexity remains low. 处理建议：Do not patch from this single case before full diagnostics; track as a vocabulary gap and inspect prevalence in 608-task diagnostics.

pandas-dev/pandas #62022：Context/Effort remain unchanged despite expected-up. The body snapshot is extremely short and does not explicitly contain public-API/backward-compatibility wording; labels carry Deprecate + Needs Discussion but current Context composites intentionally avoid using those labels alone as strong evidence. 处理建议：Keep conservative behavior until full diagnostics show whether label+target combinations can be generalized without over-triggering.

scikit-learn/scikit-learn #31554：Code/Context/Collaboration/Effort remain unchanged despite expected-up. Current composite patterns do not match the specific mathematical aggregation language in the snapshot strongly enough; performance remains auxiliary only. 处理建议：Track as a residual semantic-vocabulary gap; evaluate prevalence before another patch.

apache/pinot #16584：Setup moved down 2 -> 1 although the design expected same. v0.2.1 correctly refuses to infer special setup from RFC architecture text without an explicit required runtime/validation environment. 处理建议：Accept as intentional conservative behavior rather than patching toward the member score.

仍有若干明显低估未解决，最突出的是 Kubernetes #82440、pandas #62022、sklearn #31554。它们值得在608条全量 diagnostics 中建立触发队列检查，但当前不建议立即再做一次针对性 patch，因为：

replay 已经显著改善主要系统性低估；

18条中所有维度和 Effort 均没有出现“高于项目成员复核”的系统性信号；

positive cases 与 insufficient cases 全部保护；

继续针对三个案例加词表，过拟合风险高于先看全量分布的收益。

9. Go / No-Go

GO_TO_FULL_DIAGNOSTICS

满足进入全量 diagnostics 的理由：

25/25 replay 成功；

固定 task types 25/25 无漂移；

Code / Context / Effort 与项目成员复核的一致性方向明显改善；

Setup / Collaboration 没有大面积过度上涨；

scoreable subset 中没有任何 v0.2.1 维度或 Effort 高于项目成员复核；

3/3 positive cases 保护；

7/7 insufficient cases 保护；

当前残余问题更适合通过608条全量分布确认是否具有泛化性，再决定是否补 v0.2.2。

下一阶段应刷新新的 v0.2.1 数据库并重新生成608条全量 diagnostics，同时保留 v0.2 数据库作为 baseline。不要覆盖旧库。

本轮只完成固定案例replay，尚未刷新数据库，也尚未运行608条全量diagnostics。