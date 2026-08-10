OSS-Mentor B3 难度重点人工复核清单 v0.2

1. 使用说明

本清单从固定 36 条案例中筛选高风险差异案例。AI 结果仅为辅助初评，不是独立人工标注，也不是金标准。项目成员应依据 Issue 证据独立判断，不要因为某一列来自 AI 就默认接受。

本轮只填写“人工复核”区域，不修改任务证据、旧规则预测、新规则预测或 AI 辅助初评。

允许的 decision：

old_rules_more_reasonable

new_rules_more_reasonable

both_unreasonable

insufficient_information

含义：

old_rules_more_reasonable：旧规则相对更合理；

new_rules_more_reasonable：新规则相对更合理；

both_unreasonable：两套规则都需要修订；

insufficient_information：Issue 信息不足，不能可靠判断。

本 v0.2 清单使用上述四类 decision，与 v0.1 指南中的旧 decision 约定不同；本轮填写以本文件为准。

2. 筛选结果

固定案例总数：36

条件命中次数（去重前）：42

最终重点任务（去重后）：25

合并的重复命中：17

优先级

筛选条件

命中任务数

1

effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上

15

2

high_confidence_farther：AI 辅助初评为 high confidence，且新规则比旧规则更远

6

3

code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上

12

4

performance_code3_context3：performance signal 下新规则仍为 Code=3 且 Project Context=3

3

5

body_missing：Issue 正文缺失

4

6

non_actionable：新规则 actionability=non_actionable

2

排序规则：先按最高命中优先级，再按 repository、issue_number 排序。

3. 复核任务

01. [P1：Effort 跨两档差异] apache/pinot #6970

标题： Increase the latency of a query which has TIME as sorted column when ORDER BY TIME DESC is applied

链接： https://github.com/apache/pinot/issues/6970

task_candidate_id： 1460

sample_groups： performance_newcomer、bug_fix

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Code 1 vs 3；Project Context 1 vs 3。

Issue 证据摘要

Labels：help wanted

Task types：bug_fix

Comment count：4

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>Hi,
There is usecase, where need to Increase the latency of a query which has ATIME as sorted column in a pinot table when ORDER BY ATIME DESC limit 100 is applied.

I&#x27;ve a select query which has ORDER BY ATIME ASC limit 100 is applied , then the the time taken for query is barely 15ms, (total rows - 450M, segments- 290, numOfDocsScanned - 318), where as for ORDER BY ATIME DESC limit 100 is taking around (timetaken -200ms, numDocsScanned - 5744955), which is very high, as it is reading whole segment.

Please provide a support reading the segment from bottom in this case, which can reduce the timetaken and numOfDocsScanned...

Thanks
Akram Syed</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

3

Setup

1

1

2

Project Context

1

1

3

Collaboration

1

1

1

Effort

half_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：5

新规则与 AI 四维总绝对差：5

新旧规则四维变化总绝对值：0

旧规则与 AI Effort 档位差：2

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：medium

理由：目标是改变按降序读取排序列时的段扫描策略，涉及查询执行、段读取方向和性能验证，不是简单参数调整；需要具有代表性的数据规模验证收益与正确性。

证据：

ASC 查询扫描约 318 条文档，而 DESC 查询扫描约 574 万条文档

建议支持从 segment 底部读取以减少扫描量

问题直接涉及 Pinot 的查询执行与 segment 存储访问

不确定性：

未说明现有索引和 segment reader 是否已支持反向遍历

未提供预期修改范围及兼容性约束

人工复核（由项目成员填写）

复核状态：reviewed

决策：
both_unreasonable
修订后的四维：

Code：2

Setup：2

Project Context：2

Collaboration：1

修订后的 Effort：multi_day

复核说明：任务涉及查询执行和segment扫描性能优化，需要定位读取策略并进行性能验证，当前规则对代码、上下文和工作量估计偏低。

复核人：白淑静

日期：26/8/10

02. [P1：Effort 跨两档差异] eslint/eslint #17733

标题： Dependency Dashboard

链接： https://github.com/eslint/eslint/issues/17733

task_candidate_id： 294

sample_groups： build_tooling_control

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=multi_day，AI辅助初评=under_2h，相差3档。

P6 non_actionable：新规则 actionability=non_actionable。具体：诊断报告 actionability=non_actionable。

Issue 证据摘要

Labels：无

Task types：build_tooling

Comment count：27

Actionability：non_actionable

Information confidence：low

<details>
<summary>展开 body_excerpt</summary>

<pre>This issue lists Renovate updates and detected dependencies. Read the [Dependency Dashboard]([link] docs to learn more.&lt;br&gt;[View this repository on the Mend.io Web Portal]([link]

## Config Migration Needed

- [ ] Select this checkbox to let Renovate create an automated Config Migration PR.

## Pending Approval

The following branches are pending approval. To create them, click on a checkbox below.

- [ ] chore: update babel (major) (@babel/core, @babel/preset-env, babel-loader)
- [ ] chore: update dependency @11ty/eleventy-fetch to v5
- [ ] chore: update dependency @11ty/eleventy-img to v6
- [ ] chore: update dependency @11ty/eleventy-navigation to v1
- [ ] chore: update dependency @11ty/eleventy-plugin-rss to v3
- [ ] chore: update dependency @cypress/webpack-preprocessor to v7
- [ ] chore: update dependency @types/node to v24
- [ ] chore: update dependency algoliasearch to v5
- [ ] chore: update dependency chai to v6
- [ ] chore: update dependency cheerio to v1
- [ ] chore: update dependency cross-env to v10
- [ ] chore: update dependency cssnano to v8
- [ ] chore: update dependency cypress to v15
- [ ] chore: update dependency ejs to v6
- [ ] chore: update dependency eslint-plugin-yml to v3
- [ ] chore: update dependency github-slugger to v2
- [ ] chore: update dependency glob to v13
- [ ] chore: update dependency globals to v17
- [ ] chore: update dependency got to v15
- [ ] chore: update dependency imagemin-cli to v8
- [ ] chore: update dependency js-yaml to v5
- [ ] chore: update dependency lint-staged to v17
- [ ] chore: update dependency luxon to v3
- [ ] chore: update dependency marked to v18
- [ ] chore: update dependency node-polyfill-webpack-plugin to v4
- [ ] chore: update dependency npm-run-all2 to v9
- [ ] chore: update dependency postcss-cli to v11
- [ ] chore: update dependency sinon to v22
- [ ] chore: update dependency stylelint to v17
- [ ] chore: update dependency stylelint-config-standard to v40
- [ ] chore: update dependency stylelint-config-standard-scss to v17
- [ ] chore: update dependency webpack-cli to v7
- [ ] fix: update dependency ajv to v8
- [ ] fix: update dependency escape-string-regexp to v5
- [ ] fix: update dependency eslint-plugin-jsdoc to v63
- [ ] fix: update dependency eslint-plugin-n to v18
- [ ] fix: update dependency eslint-plugin-unicorn to v71
- [ ] fix: update dependency file-entry-cache to v11
- [ ] fix: update dependency find-up to v8
- [ ] fix: update dependency ignore to v7
- [ ] 🔐 **Create all pending approval PRs at once** 🔐

## Pending Status Checks

The following updates await pending status checks. To force their creation now, click on a checkbox below.

- [ ] chore: update dependency prettier to v3.9.5
- [ ] chore: update actions/setup-node action to v7
- [ ] chore: update dependency typescript to v7

## PR Closed (Blocked)

The following updates are blocked by an existing closed PR. To recreate the PR, click on a checkbox below.

- [ ] [chore: update dependency fs-teardown to ^0.3.0](../pull/17731)

## Detected Dependencies

[details omitted]

[details omitted]

[details omitted]

[details omitted]

[details omitted]

[details omitted]

[details omitted]

[details omitted]

[details omitted]

[details omitted]

&lt;/blockquote&gt;
&lt;/details&gt;

[details omitted]

[details omitted]

[details omitted]

[details omitted]

[details omitted]

[details omitted]

&lt;/blockquote&gt;
&lt;/details&gt;

[details omitted]

&lt;/blockquote&gt;
&lt;/details&gt;

---

- [ ] Check this box to trigger a request for Renovate to run again on this repository</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

0

Setup

1

1

0

Project Context

1

1

0

Collaboration

2

1

1

Effort

one_day

multi_day

under_2h

差异摘要

旧规则与 AI 四维总绝对差：4

新规则与 AI 四维总绝对差：3

新旧规则四维变化总绝对值：1

旧规则与 AI Effort 档位差：2

新规则与 AI Effort 档位差：3

AI 辅助初评

置信度：low

理由：该记录是 Renovate 自动维护的依赖仪表板，不是一个边界明确的单项开发任务。评分仅反映对该记录进行分诊或选择某个更新的成本，不能代表执行全部依赖升级的工作量。

证据：

正文由 Renovate 自动列出大量待审批依赖更新

包含触发自动 PR 的复选框，而非单一验收标准

不同依赖升级的风险和范围彼此独立

不确定性：

若将整个仪表板视为任务，工作量不可由单一 effort bucket 表示

项目成员应考虑将本条标记为 insufficient，而不是接受精确难度

人工复核（由项目成员填写）

复核状态：reviewed

决策：insufficient_information

修订后的四维：

Code：

Setup：

Project Context：

Collaboration：

修订后的 Effort：

复核说明：该Issue是Renovate自动维护的Dependency Dashboard，包含大量相互独立的依赖升级，不是边界明确的单项开发任务，因此不适合给出统一难度和工作量。

复核人：白淑静

日期：26/8/10

03. [P1：Effort 跨两档差异] excalidraw/excalidraw #7237

标题： Lag with many elements

链接： https://github.com/excalidraw/excalidraw/issues/7237

task_candidate_id： 557

sample_groups： performance_non_newcomer、bug_fix

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

Issue 证据摘要

Labels：performance ⚡️

Task types：bug_fix

Comment count：11

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>I&#x27;m having an issue with excalidraw in obsidian, firefox, chrome on laptop, tablet, phone and desktop pc with 4090 and 7950x3d. My current notes have about 4-5000 characters/elements each. and by the time i get to this point, it is almost impossible to even scroll, let alone write more notes. Is there a more efficient way to implement loading the note somehow? only loading what&#x27;s in the view? If i zoom in a lot, it gets a little better, but its impossible to even write one word without the pen lagging out. I&#x27;m assuming the rest of the file is effecting the current view significantly. Due to this i may have to switch over to another solution :(</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

3

1

2

Setup

1

1

2

Project Context

3

1

2

Collaboration

2

1

1

Effort

multi_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：4

新规则与 AI 四维总绝对差：3

新旧规则四维变化总绝对值：5

旧规则与 AI Effort 档位差：0

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：medium

理由：大画布包含数千元素时的严重卡顿需要性能剖析、定位渲染或数据结构瓶颈，并在多浏览器和设备上验证；不应仅因 performance 标签自动判为架构级最高难度。

证据：

问题在约 4000–5000 个字符/元素时出现严重滚动和输入延迟

现象跨 Firefox、Chrome、移动端和桌面端

用户推测视口外元素仍影响当前视图

不确定性：

未提供可复现文件或性能 trace

根因可能位于渲染、协作层、状态更新或插件环境

人工复核（由项目成员填写）

复核状态：reviewed

决策：old_rules_more_reasonable

修订后的四维：

Code：2

Setup：1

Project Context：2

Collaboration：1

修订后的 Effort：multi_day

复核说明：需要对大画布性能进行复现、分析和优化，预计涉及渲染或状态更新逻辑。旧规则虽然部分维度偏高，但新规则的half_day和低难度明显低估任务复杂度。

复核人：白淑静

日期：26/8/10

04. [P1：Effort 跨两档差异] excalidraw/excalidraw #11273

标题： Enhancement: Add alignment guides for consistent spacing between elements

链接： https://github.com/excalidraw/excalidraw/issues/11273

task_candidate_id： 556

sample_groups： performance_non_newcomer

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

Issue 证据摘要

Labels：无

Task types：feature

Comment count：1

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>**Describe the solution you&#x27;d like**
Introduce visual alignment guides or spacing indicators when moving or positioning elements on the canvas.

For example:

* Show equal spacing indicators between objects
* Display temporary guide lines when elements are aligned or evenly distributed
* Snap-to-spacing behavior for consistent layouts

---

**Describe alternatives you&#x27;ve considered**

* Manually adjusting positions by eye
* Using existing alignment tools (which don’t provide real-time spacing feedback)

---

**Additional context**
Currently, maintaining consistent spacing between elements requires manual effort and can be imprecise.

Adding alignment guides would:

* Improve layout precision
* Speed up design workflows
* Enhance overall user experience, especially for structured diagrams and UI mockups

---</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

3

1

2

Setup

2

1

1

Project Context

3

1

2

Collaboration

0

0

1

Effort

multi_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：4

新规则与 AI 四维总绝对差：3

新旧规则四维变化总绝对值：5

旧规则与 AI Effort 档位差：0

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：medium

理由：实时对齐线、等间距提示和吸附行为涉及画布几何计算、拖拽状态、视觉反馈和交互一致性，通常需要多个模块配合。

证据：

需求包含对齐线、等间距指示和 snap-to-spacing

功能需要在移动或定位元素时实时计算

涉及画布交互和 UX 反馈

不确定性：

未定义吸附阈值、性能目标和与现有对齐工具的关系

可能需要设计确认后才能准确估算

人工复核（由项目成员填写）

复核状态：reviewed

决策：old_rules_more_reasonable
修订后的四维：

Code：2

Setup：1

Project Context：2

Collaboration：1

修订后的 Effort：multi_day

复核说明：该功能需要实时几何计算、拖拽状态处理、辅助线显示和吸附行为，属于非平凡画布交互功能。新规则的half_day和1级代码难度偏低。

复核人：白淑静

日期：26/8/10

05. [P1：Effort 跨两档差异] kubernetes/kubernetes #82440

标题： High system load/CPU utilization with trivial liveness and readiness exec probes

链接： https://github.com/kubernetes/kubernetes/issues/82440

task_candidate_id： 710

sample_groups： setup_body_keyword、bug_fix

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

P2 high_confidence_farther：AI 辅助初评为 high confidence，且新规则比旧规则更远。具体：四维总绝对差变化=+1，Effort档位差变化=+1。

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Code 1 vs 3；Project Context 1 vs 3。

Issue 证据摘要

Labels：help wanted、kind/bug、priority/backlog、sig/node、triage/accepted

Task types：bug_fix

Comment count：94

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>**What happened**:
Using trivial probes significantly contributes to CPU/system load.

**What you expected to happen**:
Trivial probes should be performant

**How to reproduce it (as minimally and precisely as possible)**:

1. Create a cluster (minimal reproduction done on an m5.large ec2 instance, creating a single node cluster via kubeadm with a deployment specifying a 2 pod replica scheduled on the single node. Reproduction steps are available for more complex setups)
2. Measure system load/CPU utilization
3. Deploy the following deployment

[code block omitted]

4. Remeasure system load/CPU utilization

**Anything else we need to know?**:
Included is a screenshot. The beginning shows the node in &quot;steady state&quot; without the deployment running. This is at about 10% utilization. The first blip is deploying a similar deployment with no probes specified, still at 10%. The second blip is deploying a similar deployment but with just the livenessprobe specified, this is about 17%. The third blip is deploying the provided deployment with both liveness and readiness probes specified, this is about 34%.

![probe-cpu-util]([link]

Further testing shows that CPU utilization scales linearly with pod replica size.

**Environment**:
- Kubernetes version (use kubectl version): 1.15.3
- Cloud provider or hardware configuration: AWS (m5.large)
- OS (e.g: cat /etc/os-release): Ubuntu 18.04.3
- Kernel (e.g. uname -a): 5.0.0-25-generic
- Install tools: kubeadm 1.15.3
- Network plugin and version (if this is a network-related bug): VPC CNI v1.5.3 (reproduced with kuberouter as well)
- Others: Docker 18.09.2</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

3

Setup

2

2

3

Project Context

1

1

3

Collaboration

2

1

2

Effort

one_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：5

新规则与 AI 四维总绝对差：6

新旧规则四维变化总绝对值：1

旧规则与 AI Effort 档位差：1

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：high

理由：问题涉及 kubelet 探针执行造成的系统级 CPU 开销，需要搭建集群、进行性能剖析并理解探针生命周期和容器运行时交互，属于高风险核心路径优化。

证据：

复现需要 kubeadm 集群、节点、多个 Pod 和 liveness/readiness probes

CPU 利用率随副本数线性上升

环境涉及 AWS、Linux、kubeadm、Docker 和网络插件

不确定性：

需要确认问题在现代版本和其他容器运行时上是否仍存在

修复可能跨 kubelet、exec probing 和 runtime 接口

人工复核（由项目成员填写）

复核状态：reviewed

决策：old_rules_more_reasonable
修订后的四维：

Code：3

Setup：2

Project Context：3

Collaboration：2

修订后的 Effort：multi_day

复核说明：问题位于Kubernetes节点探针执行的核心性能路径，需要搭建集群复现、进行性能分析，并理解kubelet与容器运行时交互。新旧规则均低估技术难度，但旧规则在Setup、Collaboration和Effort上更接近人工判断。

复核人：白淑静

日期：26/8/10

06. [P1：Effort 跨两档差异] nodejs/undici #4122

标题： Should SqliteCacheStore (and perhaps other cache stores) be hashing headers before storing?

链接： https://github.com/nodejs/undici/issues/4122

task_candidate_id： 1183

sample_groups： performance_non_newcomer

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

Issue 证据摘要

Labels：bug

Task types：bug_fix

Comment count：8

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>Currently SqliteCacheStore writes plaintext headers to disk.

Would it be better to hash cache keys? This might have other advantages, such as faster lookup and lower storage size, but would also introduce risk of clashes.

# Repro

Adapted from test/cache-interceptor/sqlite-cache-store-tests.js.

[code block omitted]</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

3

1

2

Setup

1

1

1

Project Context

3

1

2

Collaboration

1

1

2

Effort

multi_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：3

新规则与 AI 四维总绝对差：3

新旧规则四维变化总绝对值：4

旧规则与 AI Effort 档位差：0

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：medium

理由：是否对缓存键哈希属于数据模型与安全/性能设计选择，需要处理碰撞、兼容旧数据、存储迁移和测试；当前 Issue 仍以问题形式提出。

证据：

当前 SqliteCacheStore 将明文 headers 写入磁盘

提议哈希 cache keys，同时承认碰撞风险

标题使用 Should，说明方案尚未定稿

不确定性：

未明确安全威胁模型和向后兼容要求

可能只需文档说明，也可能需要存储格式迁移

人工复核（由项目成员填写）

复核状态：reviewed

决策：insufficient_information

修订后的四维：

Code：

Setup：

Project Context：

Collaboration：

修订后的 Effort：

复核说明：当前Issue仍处于设计讨论阶段，只提出是否应该对cache key进行hash，尚未确定碰撞处理、兼容策略和实际修改范围，因此无法可靠给出实现难度和工作量。

复核人：白淑静

日期：26/8/10

07. [P1：Effort 跨两档差异] pandas-dev/pandas #62022

标题： DEPR: convert_dtypes keywords

链接： https://github.com/pandas-dev/pandas/issues/62022

task_candidate_id： 316

sample_groups： refactor_high_context

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Project Context 1 vs 3。

Issue 证据摘要

Labels：Deprecate、Dtype Conversions、Needs Discussion

Task types：refactor

Comment count：5

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>Looking at the keywords for convert_dtypes I&#x27;m wondering if users actually want it for anything other than dtype_backend?</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

2

1

2

Setup

2

1

1

Project Context

3

1

3

Collaboration

2

2

3

Effort

multi_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：2

新规则与 AI 四维总绝对差：4

新旧规则四维变化总绝对值：4

旧规则与 AI Effort 档位差：0

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：medium

理由：公开 API 关键字弃用需要理解用户行为、兼容周期和文档迁移，并经过设计讨论；实际代码修改可能不大，但决策与发布影响较高。

证据：

标签包含 Deprecate 与 Needs Discussion

问题涉及 convert_dtypes 的公开关键字

正文仍在询问用户是否需要除 dtype_backend 外的参数

不确定性：

尚未形成明确弃用范围和替代方案

最终实现可能被拆成讨论、弃用警告、文档和后续删除多个阶段

人工复核（由项目成员填写）

复核状态： reviewed

决策：both_unreasonable

修订后的四维：

Code：1

Setup：1

Project Context：3

Collaboration：2

修订后的 Effort：one_day

复核说明：实际代码修改可能较简单，但任务涉及pandas公开API参数弃用、兼容周期和用户行为，需要较高项目上下文并经过讨论。旧规则对代码和环境偏高，新规则对项目上下文明显偏低。

复核人：白淑静

日期：26/8/10

08. [P1：Effort 跨两档差异] pandas-dev/pandas #65326

标题： API/BUG: .loc tuple ambiguity with MultiIndex when nlevels == ndim

链接： https://github.com/pandas-dev/pandas/issues/65326

task_candidate_id： 115

sample_groups： context_broad_label

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

P2 high_confidence_farther：AI 辅助初评为 high confidence，且新规则比旧规则更远。具体：四维总绝对差变化=+1，Effort档位差变化=+1。

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Code 1 vs 3；Project Context 1 vs 3。

Issue 证据摘要

Labels：API Design、Indexing、MultiIndex

Task types：bug_fix

Comment count：2

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>I [the human] asked Claude to post the following:

## Summary

When a DataFrame has a MultiIndex with nlevels == ndim (the most common case: a 2-level MI on a 2D DataFrame), tuple keys passed to .loc are fundamentally ambiguous between two interpretations:

1. **MI row key**: df.loc[(a, b)] means &quot;the row with MultiIndex key (a, b)&quot;
2. **Multi-axis indexing**: df.loc[a, b] means &quot;row a, column b&quot;

Python produces the same tuple (a, b) for both spellings. The current code resolves this ambiguity differently depending on context — getitem vs setitem, existing vs missing key, scalar vs slice — leading to a cluster of related bugs where the same syntax silently produces different results.

## Related issues

### Primary — same root ambiguity

| Issue | Status | Problem |
|-------|--------|---------|
| #14969 | Open | df.loc[0, 0] returns a DataFrame or a Series depending on the dtype of the inner MI level — identical syntax, different interpretation |
| #16396 | Open | df.loc[1, 2] uses the MI interpretation, but df.loc[:1, 2] and df.loc[:, 2] switch to multi-axis (column 2) — incoherent |
| #27248 | Open | df.loc[(1, 2019)] = [3, 4] fails on setitem-with-expansion because the MI key (1, 2019) is misinterpreted as (row=1, col=2019) |
| #42603 | Open | df.loc[&quot;foo&quot;, 0] silently returns different things depending on whether 0 exists in MI level 1 — proposes an ambiguity error |
| #19110 | Open | df.loc[existing_row, new_col] = val adds a column instead of a (partial) row — missing-label priority is inconsistent with present-label priority |
| #17024 | Open | df.loc[&#x27;all&#x27;] = [5, 6] on a MI DataFrame flattens the MI to tupled strings |

### See also

| Issue | Status | Notes |
|-------|--------|-------|
| #39775 | Open | KeyError semantics for partially-missing MI keys in slices — adjacent problem about what should happen when MI keys don&#x27;t exist |
| #16018 | Closed | cannot reindex from duplicate axis on MI expansion (fixed in 1.3) |
| #22247 | Closed | MI expansion with NaN level copies wrong values (fixed) |

## Current behavior

### The resolution rule depends on context

The current code applies different heuristics depending on the operation and key type:

**getitem with scalar tuple** (_getitem_lowerdim → _handle_lowerdim_multi_index_axis0):
- Try MI key via obj.xs(tup). If found → return row.
- If KeyError and ndim &lt; len(tup) &lt;= nlevels → re-raise (MI interpretation).
- If KeyError and len(tup) == nlevels == ndim → raise IndexingError, which is suppressed by the caller so the per-axis loop handles it as multi-axis.

This means getitem **silently switches interpretation** when a MI key is missing:

[code block omitted]

**getitem with slices** (_getitem_tuple_same_dim):
- Always multi-axis. df.loc[:1, 2] treats 2 as a column, even though df.loc[1, 2] treats it as MI level 1 (#16396).

**setitem with scalar tuple** (_get_setitem_indexer):
- Try MI key via ax.get_loc(key). If found → overwrite row.
- If KeyError → suppress, fall through to _convert_tuple (always multi-axis). This is why #27248 fails — the missing MI key is reinterpreted as (row, new_column).

### The result depends on dtype

Because the fallback to multi-axis only triggers when the MI lookup fails, and whether it finds a match depends on what values are in the MI levels, the **same syntax gives different results depending on dtype** (#14969):

[code block omitted]

## Cases that already work

The ambiguity only exists when nlevels == ndim. These cases work correctly:

- **Series with MI** (ndim=1): any tuple with len &gt; 1 exceeds ndim, so _convert_tuple raises IndexingError(&quot;Too many indexers&quot;) and the fallthrough correctly handles MI keys.
- **3+-level MI on DataFrame** (nlevels &gt; ndim=2): same mechanism — _convert_tuple rejects the tuple and the fallthrough handles it.

## Why heuristics don&#x27;t work

I explored several heuristic approaches to disambiguate setitem when nlevels == ndim:

1. **Check if last element is an existing column**: Fails for …</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

3

Setup

1

1

1

Project Context

2

1

3

Collaboration

2

2

3

Effort

one_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：4

新规则与 AI 四维总绝对差：5

新旧规则四维变化总绝对值：1

旧规则与 AI Effort 档位差：1

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：high

理由：该问题是 .loc 与 MultiIndex 的核心 API 歧义，涉及 getitem/setitem、缺失键、切片和向后兼容性；正文明确说明简单启发式会失败，需要 API 设计决策。

证据：

同一 tuple 语法可被解释为 MultiIndex 行键或多轴索引

行为在 getitem、setitem、存在键与缺失键之间不一致

列出多个历史 Issue，说明影响范围和兼容性复杂

不确定性：

最终方案可能选择报歧义错误、改变优先级或引入新 API

任何行为修改都可能影响大量现有用户代码

人工复核（由项目成员填写）

复核状态：reviewed

决策：old_rules_more_reasonable

修订后的四维：

Code：3

Setup：1

Project Context：3

Collaboration：3

修订后的 Effort：multi_day

复核说明：该问题涉及pandas核心.loc与MultiIndex索引语义，影响getitem/setitem、缺失键、切片和向后兼容性，并需要API设计决策。新旧规则均有低估，但旧规则相对更接近实际复杂度。

复核人：白淑静

日期：26/8/10

09. [P1：Effort 跨两档差异] prometheus/prometheus #9107

标题： Potentially wasted space when storing chunk files on Btrfs

链接： https://github.com/prometheus/prometheus/issues/9107

task_candidate_id： 725

sample_groups： performance_newcomer

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

Issue 证据摘要

Labels：component/documentation、help wanted

Task types：bug_fix

Comment count：15

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>**What did you do?**

Ran Prometheus in a container using Podman and a Btrfs volume for storage.

$ podman run --name prometheus_test --net host -v prometheus_test:/prometheus:Z -v /home/$USER/config.yml:/prometheus/prometheus.yml prometheus

**What did you expect to see?**

A lower disk usage than 256M for every chunk file.

**What did you see instead? Under which circumstances?**

A disk usage of precisely 256M for every chunk file (as reported by compsize).

[code block omitted]

**Environment**

Running

* System information:

[code block omitted]

* Prometheus version:

[code block omitted]

* Prometheus configuration file:

[code block omitted]

* Logs:

[code block omitted]

**Misc comments/thoughts/suspicions**

Could this be an issue with too aggressive fallocate calls? However, I think if that is the case, compsize should report some of the 256M as pre-allocated, which it does not. I would do a bit more digging in the code but I&#x27;m not too well versed in either Go or this project.</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

2

Setup

2

1

3

Project Context

1

1

2

Collaboration

2

2

2

Effort

one_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：3

新规则与 AI 四维总绝对差：4

新旧规则四维变化总绝对值：1

旧规则与 AI Effort 档位差：1

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：medium

理由：问题需要在 Btrfs、Podman 和 Prometheus TSDB chunk 文件分配行为下复现，并判断 fallocate、文件系统计量与实际磁盘占用；环境成本高于普通本地测试。

证据：

复现依赖 Podman、Btrfs volume 与 Prometheus 存储

每个 chunk 文件报告固定 256M 占用

Issue 对 fallocate 只是猜测，根因尚未确定

不确定性：

可能是文件系统显示语义或文档问题，而非 Prometheus 缺陷

未确认其他文件系统是否存在相同现象

人工复核（由项目成员填写）

复核状态：reviewed

决策：old_rules_more_reasonable

修订后的四维：

Code：2

Setup：2

Project Context：2

Collaboration：1

修订后的 Effort：multi_day

复核说明：问题需要在Btrfs、Podman和Prometheus存储环境下复现，并分析chunk文件分配和fallocate行为。根因尚未确定，因此代码难度不宜直接判为最高级，但新规则对环境、上下文和工作量明显低估。

复核人：白淑静

日期：26/8/10

10. [P1：Effort 跨两档差异] prometheus/prometheus #10431

标题： Prometheus agent mode using more heap memory than regular mode.

链接： https://github.com/prometheus/prometheus/issues/10431

task_candidate_id： 742

sample_groups： performance_newcomer

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Code 1 vs 3；Project Context 1 vs 3。

Issue 证据摘要

Labels：help wanted、kind/more-info-needed

Task types：bug_fix

Comment count：27

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>**What did you do?**
Configured the prometheus in agent mode.
**What did you expect to see?**
Expect to see less memory foot print compared to regular mode.
**What did you see instead? Under which circumstances?**
Rather we see agent mode using more memory than that of regular mode. After further debugging with go pprof we see remote.processExternalLabels is using more than 50% of the heap, which is not the case in regular mode.

[code block omitted]

Below the heap foot print of the same for non agent mode for the exact same environment.

[code block omitted]

**Environment**
Kubernetes
* System information:
Linux 3.10.0-1160.53.1.el7.x86_64 x86_64

* Prometheus version:
prometheus, version 2.33.4 (branch: HEAD, revision: 83032011a5d3e6102624fe58241a374a7201fee8)
build user: root@d13bf69e7be8
build date: 20220222-16:51:28
go version: go1.17.7
platform: linux/amd64
* Alertmanager version:

insert output of alertmanager --version here (if relevant to the issue)

* Prometheus configuration file:

[code block omitted]

* Alertmanager configuration file:

[code block omitted]

* Logs:

[code block omitted]</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

3

Setup

2

1

3

Project Context

1

1

3

Collaboration

2

1

2

Effort

one_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：5

新规则与 AI 四维总绝对差：7

新旧规则四维变化总绝对值：2

旧规则与 AI Effort 档位差：1

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：medium

理由：agent mode 内存异常需要 Kubernetes 环境、pprof 分析和 remote write 生命周期知识，且标签显示仍需更多信息，根因与修复范围不确定。

证据：

pprof 指向 remote.processExternalLabels 占用超过 50% heap

需要对比 agent mode 与 regular mode 的同环境内存

复现环境包含 Kubernetes、Linux 与特定 Prometheus 版本

不确定性：

标签 kind/more-info-needed 表明信息仍不充分

问题可能已受版本、配置或数据规模影响

人工复核（由项目成员填写）

复核状态：reviewed

决策：old_rules_more_reasonable

修订后的四维：

Code：2

Setup：2

Project Context：2

Collaboration：1

修订后的 Effort：multi_day

复核说明：
问题已有pprof证据指向remote.processExternalLabels，但仍需要在Kubernetes环境下分析agent mode与regular mode的内存差异并完成性能回归验证。新规则的half_day明显低估工作量。
复核人：白淑静

日期：26/8/10

11. [P1：Effort 跨两档差异] pytorch/pytorch #135859

标题： bmm, topk, cholesky, linalg.norm, max with out variants set causing recompilations in torch.compile

链接： https://github.com/pytorch/pytorch/issues/135859

task_candidate_id： 984

sample_groups： performance_newcomer

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

P2 high_confidence_farther：AI 辅助初评为 high confidence，且新规则比旧规则更远。具体：四维总绝对差变化=+2，Effort档位差变化=+1。

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Code 1 vs 3；Project Context 1 vs 3。

Issue 证据摘要

Labels：good first issue、module: dynamic shapes、oncall: pt2、PT2-Bug-Bash、triaged

Task types：bug_fix

Comment count：11

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>### 🐛 Describe the bug

Out variants of following ops are causing extra recompilations (in 3rd iteration) in torch.compile when compared to not using out variant,
torch.bmm
torch.topk
torch.cholesky
torch.linalg.norm
torch.max

### Error logs

[code block omitted]

### Minified repro

torch.topk

[code block omitted]

torch.bmm

[code block omitted]

torch.cholesky

[code block omitted]

torch.linalg.norm

[code block omitted]

### Versions

[code block omitted]

cc @chauhang @penguinwu @ezyang @bobrenjc93 @aditvenk @laithsakka</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

3

Setup

2

1

2

Project Context

1

1

3

Collaboration

2

1

2

Effort

one_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：4

新规则与 AI 四维总绝对差：6

新旧规则四维变化总绝对值：2

旧规则与 AI Effort 档位差：1

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：high

理由：torch.compile 多个 out variant 在第三次迭代触发额外重编译，涉及 dynamic shapes、图缓存或 guard 逻辑；需要跨多个算子验证修复且影响编译器核心路径。

证据：

问题同时影响 bmm、topk、cholesky、linalg.norm 和 max

标签指向 dynamic shapes 与 PT2

提供多个最小复现，说明并非单一算子实现问题

不确定性：

根因可能位于共同的 out variant 处理或多个独立 decomposition

可能需要 GPU 环境验证部分算子

人工复核（由项目成员填写）

复核状态：reviewed

决策：old_rules_more_reasonable

修订后的四维：

Code：3

Setup：2

Project Context：3

Collaboration：1

修订后的 Effort：multi_day

复核说明：问题影响多个算子并涉及torch.compile、dynamic shapes及可能的guard/图缓存逻辑，
属于编译核心路径问题，需要跨算子验证。新规则的Code=1和half_day明显偏低，
旧规则虽然仍低估技术难度，但相对更接近。

复核人：白淑静

日期：26/8/10

12. [P1：Effort 跨两档差异] quarkusio/quarkus #42510

标题： Regression testing & defense against classloader leaks

链接： https://github.com/quarkusio/quarkus/issues/42510

task_candidate_id： 581

sample_groups： performance_newcomer

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Project Context 1 vs 3。

Issue 证据摘要

Labels：help wanted、kind/enhancement、triage/qe?

Task types：bug_fix、feature、testing

Comment count：1

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>### Description

# Goal: reduce future regressions in the area of memory leaks, with a special focus on classloader leaks

In its very nature of reloading the whole app, including many 3rd party dependencies of which we might not know the design in great detail, we&#x27;ve found that maintaining extensions, especially for dev-mode, to not leak their classloaders is challenging.
Many leaks have been resolved recently, but we need a way to ensure we keep improving over potentially regressing again when people lower their guard on this topic.

# How
Memory leaks are actually testable; such a test is not even tricky but it requires a commitment from our side: I&#x27;m wondering if we can proof the approach on a tactical selection of extensions - immediately getting some benefits already - but eventually to make it zero-impact for any extension maintainers; this is described in more detail below.

Originally I prototyped the general idea of regression tests against classloader leaks in the Hibernate ORM project, about a year ago; I&#x27;ve left it there to mature for some time as I wasn&#x27;t sure the timeout-approach wouldn&#x27;t be potentially too fragile, and flaky CI is unacceptable to me; but it seems actually very robust and reliable: we&#x27;ve had no problems with it and it&#x27;s been a long time now.

My Hibernate ORM utilities can be found here, and needless to say I&#x27;m granting permission to copy them into Quarkus and adapt as necessary, including adapt the license to ASL2.

Example usage:
- [link]

Testing the test utility to make sure it actually works, but also to see how it&#x27;s supposed to be used:
- [link]
- [link]
- [link]

Core logic to test for leaks of just any type (can spot a leak of any object, not just classloaders):
- [link]

Making it nicer to use specifically for ClassLoader leaks, as it&#x27;s a bit of a tricky special case:
- [link]
## Enforce it globally?

I&#x27;m assuming it needs adapting to be integrated within Quarkus; wondering if there&#x27;s a smart way to integrate it in such a way that all extensions are inherently tested this way? Perhaps it could be integrated in the testing framework, and call into these helpers to ensure that after any testsuite is run, a classloader leak would be spotted?

Perhaps I&#x27;m dreaming, as I&#x27;m not too familiar with our testing frameworks - but I think it would be really great if somebody could think of such an integration.

## Rollout Suggestions

I&#x27;m confident that many extensions still have classloader leaks we&#x27;re not aware of.

Enforcing such a global check on all extensions would make it much harder to introduce such a check, so I&#x27;d suggest initially needing an opt-in, so that various extension owners can be encouraged to fix whatever they need fixing at their own pace, and eventually switch to opt-out. This would allow us to benefit from the regression tests in short time, and roll it out gradually.</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

2

3

Setup

1

1

2

Project Context

1

1

3

Collaboration

0

0

3

Effort

half_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：8

新规则与 AI 四维总绝对差：7

新旧规则四维变化总绝对值：1

旧规则与 AI Effort 档位差：2

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：high

理由：任务要把 classloader leak 检测集成到 Quarkus 测试框架并逐步覆盖扩展，涉及生命周期、第三方依赖、测试稳定性和全局 rollout 策略，范围明显跨模块。

证据：

目标是防止 dev-mode 与扩展中的 classloader 泄漏回归

提议将检测能力集成到测试框架并逐步从 opt-in 走向更广覆盖

需要避免引入 flaky CI，并参考 Hibernate ORM 中的既有工具

不确定性：

Issue 同时包含原型验证和全局 rollout，可能需要拆分

不同扩展可能存在独立泄漏修复工作

人工复核（由项目成员填写）

复核状态：reviewed

决策：both_unreasonable

修订后的四维：

Code：2

Setup：2

Project Context：3

Collaboration：2

修订后的 Effort：multi_day

复核说明：任务涉及ClassLoader生命周期、测试框架、第三方扩展和CI稳定性，并包含逐步推广策略。
新规则虽然将Code提升到2，但仍严重低估Context、协作和工作量；旧规则同样明显低估。

复核人：白淑静

日期：26/8/10

13. [P1：Effort 跨两档差异] scikit-learn/scikit-learn #27441

标题： partial_dependence() with method recursion computes conditional partial dependence for trees

链接： https://github.com/scikit-learn/scikit-learn/issues/27441

task_candidate_id： 237

sample_groups： documentation_only、code_zero_high_effort

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=under_2h，AI辅助初评=one_day，相差2档。

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Project Context 0 vs 2。

Issue 证据摘要

Labels：Documentation、help wanted

Task types：documentation

Comment count：14

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>### Describe the bug

For the case of correlated predictors (clearly highly common) the sklearn.inspection.partial_dependence() function gives different answers for method = &quot;recursion&quot; and method = &quot;brute&quot;, see my [post]([link] for elaborate examples.

I do not believe that this is intentional and should be fixed. Alternatively, it should be communicated clearly in the documentation that (i) the two methods are not equivalent for tree based algorithms, and (ii) that method = &quot;recursion&quot; actually computes the **conditional** $E[f(x_S,X_C)|X_S=x_s]$ instead of the (desired) **interventional** $E[f(x_S,X_C)| \mathbf{do}(X_S=x_s)]$

### Steps/Code to Reproduce

[code block omitted]

### Expected Results

We would like the two methods to yield the same pdp values, so the last line should yield

array([[True, True]])

### Actual Results

Instead they are different. The print statements yield
_X1 brute (interventional): [[0.6 0.4]]
X1 recursion (conditional): [[0.42 0.22]]_
and the last line yields
array([[False, False]])

### Versions

[code block omitted]</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

0

0

0

Setup

2

0

1

Project Context

1

0

2

Collaboration

2

1

2

Effort

one_day

under_2h

one_day

差异摘要

旧规则与 AI 四维总绝对差：2

新规则与 AI 四维总绝对差：4

新旧规则四维变化总绝对值：4

旧规则与 AI Effort 档位差：0

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：medium

理由：当前分类为文档任务，但需要理解 recursion 与 brute partial dependence 的统计语义，并明确相关预测变量下的差异；代码量可能为零，概念核验和文档表述并不简单。

证据：

用户指出 recursion 与 brute 在相关特征下计算不同量

正文区分 conditional 与 interventional partial dependence

Issue 明确提出至少需要在文档中说明两种方法不等价

不确定性：

维护者可能选择修改算法而非仅更新文档

需要统计与树模型专家确认数学语义

人工复核（由项目成员填写）

复核状态：reviewed

决策：old_rules_more_reasonable

修订后的四维：

Code：0

Setup：1

Project Context：2

Collaboration：2

修订后的 Effort：one_day

复核说明：当前可按文档任务处理，因此不一定需要修改代码，但需要理解partial dependence的统计语义、
运行示例验证并经过专家或维护者确认。新规则的Context=0和under_2h明显低估概念核验成本。

复核人：白淑静

日期：26/8/10

14. [P1：Effort 跨两档差异] scikit-learn/scikit-learn #31503

标题： HDBSCAN performance issues compared to original hdbscan implementation (likely because Boruvka algorithm is not implemented)

链接： https://github.com/scikit-learn/scikit-learn/issues/31503

task_candidate_id： 224

sample_groups： performance_newcomer

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

P2 high_confidence_farther：AI 辅助初评为 high confidence，且新规则比旧规则更远。具体：四维总绝对差变化=+1，Effort档位差变化=+1。

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Code 1 vs 3；Project Context 1 vs 3。

Issue 证据摘要

Labels：Hard、help wanted、New Feature、Performance

Task types：bug_fix、feature

Comment count：4

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>### Describe the bug

When switching from Sklearn HDBSCAN implementation to original one from hdbscan library, I&#x27;ve notice that Sklearn&#x27;s implementation has much worse implementation. I&#x27;ve tried investigating different parameters but it doesn&#x27;t seem to have an effect on the performance.

I&#x27;ve created synthetic benchmark using make_blobs function. And those are my results:

CPU: Ryzen 5 1600, 12 Threads@3.6Ghz*
RAM: 32GB DDR4

[code block omitted]

![Image]([link]

* Tested out on Google Collab with similar results

### Steps/Code to Reproduce

I am starting both algorithms with n_jobs=-1 to rule out the difference that may occure because of default setting of core_dist_n_jobs=4 in hdbscan

[code block omitted]

### Expected Results

Similar performance between algorithms from Sklearn and hdbscan library

### Actual Results

Sklearn implementation of HDBSCAN gets much worse performance than original library. For example when testing much bigger dataset, i.e.

[code block omitted]

hdbscan library performs fit in 25s on my hardware, while Sklearn needs 5 minutes to perform clustering.

### Versions

[code block omitted]</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

3

Setup

2

1

2

Project Context

1

1

3

Collaboration

1

1

2

Effort

one_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：5

新规则与 AI 四维总绝对差：6

新旧规则四维变化总绝对值：1

旧规则与 AI Effort 档位差：1

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：high

理由：性能差距可能来自缺少 Boruvka 算法，实现高性能聚类算法需要算法设计、并行化、内存行为和大规模基准验证，属于核心算法功能。

证据：

scikit-learn HDBSCAN 在大数据集上约 5 分钟，对比实现约 25 秒

Issue 推测缺少 Boruvka 算法

标签包含 Hard、New Feature 与 Performance

不确定性：

性能差距可能还受参数、并行策略或实现细节影响

是否完整实现 Boruvka 需要维护者设计确认

人工复核（由项目成员填写）

复核状态：reviewed

决策：both_unreasonable


修订后的四维：

Code：3

Setup：1

Project Context：2

Collaboration：1

修订后的 Effort：multi_day

复核说明：性能差距可能涉及HDBSCAN核心算法实现甚至Boruvka算法，需要算法理解和大规模benchmark。
普通本地CPU即可测试，所以Setup不必很高；但两套规则都把Code和Context严重低估。

复核人：白淑静

日期：26/8/10

15. [P1：Effort 跨两档差异] scikit-learn/scikit-learn #31554

标题： Allow batch based metrics calculation of sklearn.metrics

链接： https://github.com/scikit-learn/scikit-learn/issues/31554

task_candidate_id： 223

sample_groups： performance_newcomer

命中复核原因

P1 effort_gap_two_or_more：新规则 Effort 与 AI 辅助初评相差两档以上。具体：新规则=half_day，AI辅助初评=multi_day，相差2档。

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Code 1 vs 3；Project Context 1 vs 3。

Issue 证据摘要

Labels：help wanted、module:metrics、Needs Investigation、Performance

Task types：feature

Comment count：22

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>### Describe the workflow you want to enable

I have a lot of data and need to calculate metrics such as accuracy_score, jaccard_score, f1_score, recall, precision etc.

### Describe your proposed solution

When I try to calculate these it can literally take days, so i created a small solution which can batch and avg in the end, or for the weighted metrics it can do a weighted avg of each, this accelerated the calculation to just a couple of minutes, because I have a 32 core CPU. I&#x27;m willing to contribute with the proper guidance as I&#x27;m unfamiliar with the codebase, but I think many people can benefit from this. I&#x27;m unsure if there is already a work around of this present in the codebase, but if there is one do let me know, thanks a lot.

### Describe alternatives you&#x27;ve considered, if relevant

### Additional context</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

3

Setup

2

1

2

Project Context

1

1

3

Collaboration

2

1

3

Effort

one_day

half_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：5

新规则与 AI 四维总绝对差：7

新旧规则四维变化总绝对值：2

旧规则与 AI Effort 档位差：1

新规则与 AI Effort 档位差：2

AI 辅助初评

置信度：medium

理由：批量并行计算 metrics 涉及不同指标的可加性、加权聚合正确性、API 设计和并行执行策略；并非所有指标都能通过分批平均获得等价结果。

证据：

需求覆盖 accuracy、Jaccard、F1、recall、precision 等多类指标

提出批处理与加权平均以利用 32 核 CPU

标签包含 Needs Investigation 与 Performance

不确定性：

部分指标的批次聚合可能数学上不等价

现有并行或流式方案是否可复用尚未确认

人工复核（由项目成员填写）

复核状态： reviewed

决策：old_rules_more_reasonable
修订后的四维：

Code：3

Setup：1

Project Context：3

Collaboration：2

修订后的 Effort：multi_day
复核说明：批量metrics并非简单并行化，需要保证多种指标分批聚合后的数学正确性，并涉及API设计和并行策略。
新规则的Code=1、Context=1和half_day明显偏低；旧规则虽然也低估，但相对更接近。

复核人：白淑静

日期：26/8/10

16. [P2：高置信度案例变远] apache/pinot #16231

标题： Avoid retries for FileNotFound exception in /segments controller API

链接： https://github.com/apache/pinot/issues/16231

task_candidate_id： 1351

sample_groups： context_broad_label、bug_fix

命中复核原因

P2 high_confidence_farther：AI 辅助初评为 high confidence，且新规则比旧规则更远。具体：四维总绝对差变化=+1，Effort档位差变化=+1。

Issue 证据摘要

Labels：bug、rest-api

Task types：bug_fix

Comment count：3

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>For an incorrect segment download path, the /segments API failed with 500 http code - causing confusion as to whether the pinot controllers are broken. On debugging logs, this seems to be due to FileNotFound exception which got wrapped by AttemptsExceededException - delaying debugging process.

Proposed Improvements -

- Controller API - /segments should return a 404 error in case of such failures
- Retry should not be performed in case of FileNotFound exception

Error -
Caused by: org.apache.pinot.common.exception.HttpErrorStatusException: Got error status code: 500 (Internal Server Error) with reason: &quot;Exception while uploading segment: Operation failed after 3 attempts&quot; while sending request: [link] to controller:

Stacktrace for the API -
`
org.apache.pinot.spi.utils.retry.AttemptsExceededException: Operation failed after 3 attempts at org.apache.pinot.spi.utils.retry.BaseRetryPolicy.attempt(BaseRetryPolicy.java:65) at org.apache.pinot.common.utils.fetcher.BaseSegmentFetcher.fetchSegmentToLocal(BaseSegmentFetcher.java:74) at org.apache.pinot.common.utils.fetcher.SegmentFetcherFactory.fetchSegmentToLocal(SegmentFetcherFactory.java:124) at org.apache.pinot.common.utils.fetcher.SegmentFetcherFactory.fetchSegmentToLocal(SegmentFetcherFactory.java:132) at org.apache.pinot.controller.api.resources.PinotSegmentUploadDownloadRestletResource.downloadSegmentFileFromURI(PinotSegmentUploadDownloadRestletResource.java:461) at org.apache.pinot.controller.api.resources.PinotSegmentUploadDownloadRestletResource.uploadSegment(PinotSegmentUploadDownloadRestletResource.java:277) at org.apache.pinot.controller.api.resources.PinotSegmentUploadDownloadRestletResource.uploadSegmentAsJson(PinotSegmentUploadDownloadRestletResource.java:511)
`

Cause -
`
java.io.FileNotFoundException: /viewfs: at org.apache.hadoop.fs.viewfs.InodeTree.resolve(InodeTree.java:402) at org.apache.hadoop.fs.viewfs.ViewFileSystem.getFileStatus(ViewFileSystem.java:378) at org.apache.hadoop.fs.FileUtil.copy(FileUtil.java:340) at org.apache.hadoop.fs.FileUtil.copy(FileUtil.java:292) at org.apache.hadoop.fs.FileSystem.copyToLocalFile(FileSystem.java:2067) at org.apache.hadoop.fs.FileSystem.copyToLocalFile(FileSystem.java:2036) at org.apache.hadoop.fs.FileSystem.copyToLocalFile(FileSystem.java:2012) at com.uber.uPinot.filesystem.HDFSFileSystem.copyToLocalFile(HDFSFileSystem.java:335) at org.apache.pinot.spi.filesystem.NoClosePinotFS.copyToLocalFile(NoClosePinotFS.java:98) at org.apache.pinot.common.utils.fetcher.PinotFSSegmentFetcher.fetchSegmentToLocalWithoutRetry(PinotFSSegmentFetcher.java:31) at org.apache.pinot.common.utils.fetcher.BaseSegmentFetcher.lambda$fetchSegmentToLocal$0(BaseSegmentFetcher.java:76) at org.apache.pinot.spi.utils.retry.BaseRetryPolicy.attempt(BaseRetryPolicy.java:58) at org.apache.pinot.common.utils.fetcher.BaseSegmentFetcher.fetchSegmentToLocal(BaseSegmentFetcher.java:74) at org.apache.pinot.common.utils.fetcher.SegmentFetcherFactory.fetchSegmentToLocal(SegmentFetcherFactory.java:124) at org.apache.pinot.common.utils.fetcher.SegmentFetcherFactory.fetchSegmentToLocal(SegmentFetcherFactory.java:132) at org.apache.pinot.controller.api.resources.PinotSegmentUploadDownloadRestletResource.downloadSegmentFileFromURI(PinotSegmentUploadDownloadRestletResource.java:461) at org.apache.pinot.controller.api.resources.PinotSegmentUploadDownloadRestletResource.uploadSegment(PinotSegmentUploadDownloadRestletResource.java:277) at org.apache.pinot.controller.api.resources.PinotSegmentUploadDownloadRestletResource.uploadSegmentAsJson(PinotSegmentUploadDownloadRestletResource.java:511)
`</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

2

Setup

1

1

2

Project Context

2

1

2

Collaboration

1

1

1

Effort

one_day

half_day

one_day

差异摘要

旧规则与 AI 四维总绝对差：2

新规则与 AI 四维总绝对差：3

新旧规则四维变化总绝对值：1

旧规则与 AI Effort 档位差：0

新规则与 AI Effort 档位差：1

AI 辅助初评

置信度：high

理由：任务要求在 segment 上传/下载路径中区分 FileNotFound 与通用重试失败，并调整 HTTP 状态与重试策略，涉及多层异常传播但范围较明确。

证据：

明确要求 FileNotFound 时返回 404，而不是包装成 500

明确要求 FileNotFound 不再执行重试

堆栈跨越文件系统、segment fetcher、retry policy 与 REST resource

不确定性：

需要确认其他不可重试异常是否应采用相同策略

可能需要兼容不同文件系统实现

人工复核（由项目成员填写）

复核状态：reviewed

决策：old_rules_more_reasonable

修订后的四维：

Code：2

Setup：1

Project Context：2

Collaboration：1

修订后的 Effort：one_day

复核说明：任务涉及segment下载路径中的多层异常传播，需要区分FileNotFound与通用重试失败，并调整HTTP状态码和重试策略。修改目标和范围较明确，旧规则的Context和one_day更接近，新规则half_day及Context=1略有低估。

复核人：白淑静

日期：26/8/10

17. [P2：高置信度案例变远] apache/pinot #16584

标题： [Feature] Broker and Server Segment Query Cache

链接： https://github.com/apache/pinot/issues/16584

task_candidate_id： 1324

sample_groups： performance_non_newcomer、feature

命中复核原因

P2 high_confidence_farther：AI 辅助初评为 high confidence，且新规则比旧规则更远。具体：四维总绝对差变化=+1，Effort档位差变化=+1。

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Code 1 vs 3。

Issue 证据摘要

Labels：PEP-Request

Task types：feature

Comment count：2

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre># RFC: Broker and Server Segment Query Cache for Apache Pinot

Status: Draft 0.1
Authors: Xiang Fu, Hongkun Xu
Created: 2025-08-13
Target Release: TBD
Discussion Slack Channel: [(link after posting to dev@pinot.apache.org)]([link]
RFC Doc: [link]

## Abstract

This RFC proposes a two-layer query caching feature for Apache Pinot:
1. **Broker Result Cache** – caches fully merged query results at the broker layer.
2. **Server Segment Result Cache** – caches per-segment partial results (aggregations, group-by tables, and selection blocks) at the server layer.

The design emphasizes correctness on immutable/sealed segments, pluggability of cache backends, precise invalidation, and rich observability. It draws on patterns from Apache Druid (broker + historical caches), StarRocks (backend caches), and ClickHouse (final result cache + block caches) while fitting Pinot’s execution model.

## Background &amp; Motivation

Pinot executes every query end-to-end even when the inputs and the plan are unchanged. In dashboard scenarios, identical queries recur at 5–60s intervals and create avoidable CPU and IO load. Immutable segments (offline and sealed realtime) are natural candidates for result reuse.

Goals of this RFC:

- Reduce p95/p99 latency and cluster cost for repetitive workloads.
- Provide predictable correctness via strong versioning and conservative defaults.
- Offer a pluggable SPI and configuration knobs to match diverse deployments.

&gt; Non-goals for Phase 1: ORDER BY/top-k caching, semantic/approximate matching, mutable/upsert caching.

## Terminology

&lt;img width=&quot;730&quot; height=&quot;644&quot; alt=&quot;Image&quot; src=&quot;[link] /&gt;

- Segment: Pinot data shard (offline or realtime). Realtime may be consuming or sealed.
- CRC/Version: Segment-level versioning/epoch used to detect content changes.
- Broker Response: Final BrokerResponseNative after merge/trim.
- Partial Result: Per-segment contribution (agg array, group-by map, or selection rows) before broker merge.
- FullResultCache: caches fully merged query results at the broker layer
- PartitalSegmentCache: caches per-segment partial results

## Requirements

### 1. Functional

- Exact-match caching of final results (broker) and partial results (server) for supported operators.
- Deterministic, canonical keys covering query + data versions + relevant options.
- Automatic invalidation on segment/schema/table-config changes and server/broker lifecycle events.
- Manual invalidation APIs for operators and SREs.
- Per-query escape hatches (disable flags) for debugging.

### 2. Correctness &amp; Safety

- Enabled by default only for: offline and sealed realtime segments.
- Disabled by default for: consuming realtime segments and upsert tables.
- Staleness bounded by TTL but correctness enforced by versioned keys.

### 3. Performance

- Weighted LRU with size in bytes, not entry count.
- Optional compression for value payloads.

### 4. Operability

- Detailed metrics (hits/misses/bytes/latency).
- Tracing spans and cache decision annotations.
- Configurable per-table overrides.

## High-Level Architecture

Two orthogonal caches:

### 1 Broker Result Cache

- Placement: Broker (query entry/exit).
- Key: Canonical SQL + normalized options + routing table version + participating segment epochs + schema epoch.
- Value: Serialized BrokerResponseNative (optionally compressed).
- Lookup Path: Check before dispatch → on hit, return; on miss, execute and store.
- Invalidation: Broker listens to ExternalView/segment metadata and schema/config change events to purge affected keys.

### 2 Server Segment Result Cache

- Placement: Server, per-segment, around the operator execution.
- Key Composition:

Key = HASH(
tableNameWithType,
segmentName,
segmentCrcOrEpoch,
planSignature, // canonical operator tree
projectionSchemaSig, // columns+types used
queryOptionsSig, // null handling, response format, group trim thresholds, etc.
starTreeSig, // star-tree id/version used by planner
timeRangeConstraintSig, // broker pruning …</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

3

1

3

Setup

2

2

3

Project Context

3

2

3

Collaboration

0

2

3

Effort

multi_day

one_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：4

新规则与 AI 四维总绝对差：5

新旧规则四维变化总绝对值：5

旧规则与 AI Effort 档位差：0

新规则与 AI Effort 档位差：1

AI 辅助初评

置信度：high

理由：这是跨 Broker、Server、缓存后端、失效机制和可观测性的 RFC 级功能，涉及分布式正确性、缓存键设计、生命周期事件及性能评测，显著超出单一任务范围。

证据：

提出 Broker 结果缓存与 Server segment 结果缓存两层架构

要求处理版本化键、精确失效、压缩、指标和 tracing

正文标记为 RFC Draft，目标发布版本尚未确定

不确定性：

Issue 可能需要进一步拆分为多个可执行子任务

最终实现范围取决于 RFC 评审结论

人工复核（由项目成员填写）

复核状态:reviewed

决策：old_rules_more_reasonable

修订后的四维：

Code：3

Setup：2

Project Context：3

Collaboration：3

修订后的 Effort：multi_day

复核说明：该Issue是跨Broker、Server、缓存失效、版本化键和可观测性的RFC级功能，
涉及分布式正确性和架构设计。旧规则虽然Collaboration=0明显错误，
但Code、Context和Effort更接近；新规则整体低估实现范围。

复核人：白淑静

日期：26/8/10

18. [P3：Code/Context 跨两级差异] wagtail/wagtail #14318

标题： Write API: QA fixtures and property testing

链接： https://github.com/wagtail/wagtail/issues/14318

task_candidate_id： 130

sample_groups： context_broad_label

命中复核原因

P3 code_or_context_gap_two_or_more：新规则 Code 或 Project Context 与 AI 辅助初评相差两级以上。具体：Project Context 1 vs 3。

Issue 证据摘要

Labels：component:API、DX、status:Needs Review、type:Enhancement

Task types：feature、testing

Comment count：2

Actionability：未从关键队列提取

Information confidence：未从关键队列提取

<details>
<summary>展开 body_excerpt</summary>

<pre>Part of [RFC 115]([link] tracked in #14295.

## Problem statement

[RFC 115 §QA capabilities]([link] defines cross-cutting QA mechanisms. The critical-path pieces (permissions-matrix harness, audit-log assertion helpers, OpenAPI snapshot tooling) moved to #14296. Two RFC items remain unowned: **more advanced fixtures** — extending wagtail/test/ with content shapes broad enough to exercise the API end-to-end (e.g. custom log models, which existing test apps cover only thinly) — and **automated test case generation** via schema-driven property tests/fuzzing.

## Proposed solution

- [ ] Fixture expansion in wagtail/test/: review gaps against the API tickets&#x27; needs (custom log models, deeper translated trees, collection permission scenarios); extend testapp rather than adding apps where possible.
- [ ] Property tests / fuzzing (best-effort, RFC marks it &quot;if possible&quot;): generate request payloads from the exported JSON Schemas (e.g. hypothesis + hypothesis-jsonschema); assert no 500s, and RFC 7807 conformance on rejection. Time-box; document findings either way.

## Acceptance criteria / definition of done

- Fixture additions documented per existing test-app conventions, and consumed by at least one endpoint ticket&#x27;s tests.
- Property-test outcome documented: running in CI (possibly non-blocking) or a written conclusion on why not.

Suggested review steps:

1. Run the property tests locally overnight against the v3 branch; triage anything found.
2. Check runtests.py time impact stays acceptable.

## Additional context

- RFC 115: [QA capabilities — Core]([link]
- Property tests need #14296&#x27;s schema export; fixture work has no blocker.

## Working on this

Assignee TBC. Effort: not in the RFC effort matrix — estimate S–M (~8–12h).</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

2

2

3

Setup

1

1

2

Project Context

2

1

3

Collaboration

0

2

2

Effort

one_day

one_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：5

新规则与 AI 四维总绝对差：4

新旧规则四维变化总绝对值：3

旧规则与 AI Effort 档位差：1

新规则与 AI Effort 档位差：1

AI 辅助初评

置信度：high

理由：任务同时包括扩展跨场景 QA fixtures 与基于 JSON Schema 的 property testing/fuzzing，需要理解 Write API、权限、审计、翻译树和 CI 约束，且与 RFC 和多个 endpoint ticket 联动。

证据：

任务是 RFC 115 的一部分并由其他跟踪 Issue 协调

要求扩展测试 fixtures 并尝试 schema-driven property tests/fuzzing

验收标准要求至少被一个 endpoint ticket 使用并记录 CI 结果

不确定性：

property testing 被标记为 best-effort，最终范围可能缩减

可能需要新增 hypothesis 相关测试依赖或 CI 配置

人工复核（由项目成员填写）

复核状态：reviewed

决策：new_rules_more_reasonable

修订后的四维：

Code：2

Setup：1

Project Context：2

Collaboration：2

修订后的 Effort：one_day

复核说明：任务范围相对明确，主要是扩展QA fixtures并尝试schema-driven property testing，
需要理解相关API和RFC背景，但Issue自身估计约8-12小时。新规则整体更接近合理工程量级。

复核人：白淑静

日期：26/8/10

19. [P4：Performance 双维最高难度] pytorch/ao #988

标题： Tensor Parallelism Support for AffineQuantizedTensor

链接： https://github.com/pytorch/ao/issues/988

task_candidate_id： 884

sample_groups： performance_newcomer

命中复核原因

P4 performance_code3_context3：performance signal 下新规则仍为 Code=3 且 Project Context=3。具体：performance signal 且新规则 Code=3、Project Context=3。

Issue 证据摘要

Labels：good first issue、triaged

Task types：feature、testing

Comment count：5

Actionability：unclear

Information confidence：low

<details>
<summary>展开 body_excerpt</summary>

<pre>Recently we landed [link] to support tensor parallelism for int8 weight only quantization, another example: [link]

now we can support tensor parallelism for other types of quantization as well.

* [x] float8 weight only @jainapurva - #1003
* [x] float8 dynamic activation @jainapurva - #1078
* [ ] uintx weight only @melvinebenezer
* [x] int4 weight only quant - @jerryzh168 #1120
* [x] int8 dynamic act + int8 weight - @jainapurva [link]
* [ ] fpx -

# Steps
## 1. Create test
Since we don&#x27;t have many tests today, we can optimize for readability for now, so we can copy paste the test cases to a [link] instead of inheriting from these test cases

For new tests you can follow [link] to create your own test case

## 2. Run the test

python test/dtypes/test_affine_quantized_tensor_parallel.py

## 3. Add support for missing ops until test passes
We&#x27;d expect people to add some slicing ops etc. to the corresponding TensorImpl tensor subclass</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

3

3

Setup

1

1

3

Project Context

1

3

3

Collaboration

1

1

2

Effort

half_day

one_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：7

新规则与 AI 四维总绝对差：3

新旧规则四维变化总绝对值：4

旧规则与 AI Effort 档位差：2

新规则与 AI Effort 档位差：1

AI 辅助初评

置信度：high

理由：任务要求为多种量化 tensor 类型补齐 tensor parallel 支持，创建测试并实现缺失算子，涉及分布式、量化 tensor subclass 和 GPU 测试环境。

证据：

需要为 float8、uintx、int4 等量化类型扩展 tensor parallelism

步骤包含新增测试并逐个补齐 TensorImpl 缺失操作

验证涉及专用 quantization 与 tensor parallel 测试

不确定性：

不同量化类型可能应拆成多个独立子任务

所需 GPU 拓扑和 CI 资源未说明

人工复核（由项目成员填写）

复核状态：reviewed

决策：new_rules_more_reasonable
修订后的四维：

Code：3

Setup：3

Project Context：3

Collaboration：1

修订后的 Effort：multi_day

复核说明：任务涉及量化tensor、Tensor Parallel和缺失算子实现，需要专用分布式/GPU测试环境。
新规则正确识别了Code和Context的高难度，但仍低估Setup和Effort；相比旧规则明显更合理。
复核人：白淑静

日期：26/8/10

20. [P4：Performance 双维最高难度] pytorch/ao #1224

标题： [QAT] Low-bit FSDP all-gather for QAT

链接： https://github.com/pytorch/ao/issues/1224

task_candidate_id： 891

sample_groups： performance_newcomer、feature

命中复核原因

P4 performance_code3_context3：performance signal 下新规则仍为 Code=3 且 Project Context=3。具体：performance signal 且新规则 Code=3、Project Context=3。

Issue 证据摘要

Labels：distributed、good first issue、module: qat

Task types：feature

Comment count：4

Actionability：actionable

Information confidence：medium

<details>
<summary>展开 body_excerpt</summary>

<pre>Had this idea and discussed briefly with @andrewor14.

Conceptually the current QAT + FSDP looks like this

- sharded FP32 weight -&gt; all-gather in BF16 -&gt; fake quantize

However, we can do low-bit all-gather, since the weight can be quantized before all-gather

- sharded FP32 weight -&gt; (real) quantize -&gt; all-gather in low-bit -&gt; dequantize

In terms of perf, basically we are comparing between (ignoring potential fusion surrounding this)
1. BF16 all-gather + fake quantize
2. (Real) quantize (1/NGPU) + Low-bit all-gather + Dequant

This might be a small perf win, especially when distributed comm is bottleneck. Might be useful for QAT recipes in torchtune.

This is probably a low priority, so just leave it here if anyone is interested to implement. Need to quantify the speedup, if any.

In terms of implementation, we can follow float8 design ([link]
- A tensor subclass to hold original weight + use FSDP2 all-gather extension: possibly extend this [link]
- Another tensor subclass to hold quantized weight. If AQT has basic support for backward, maybe we can use AQT directly. Otherwise, need to have another subclass.</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

3

3

Setup

1

1

3

Project Context

1

3

3

Collaboration

1

1

2

Effort

half_day

multi_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：7

新规则与 AI 四维总绝对差：3

新旧规则四维变化总绝对值：4

旧规则与 AI Effort 档位差：2

新规则与 AI Effort 档位差：0

AI 辅助初评

置信度：high

理由：低位宽 FSDP all-gather 涉及量化、反向传播、tensor subclass、FSDP2 扩展与分布式性能验证，是核心分布式训练路径的非平凡设计与实现。

证据：

需要在 all-gather 前量化并在通信后反量化

建议使用 tensor subclass 与 FSDP2 all-gather extension

需要量化 speedup，并处理 AQT backward 支持

不确定性：

Issue 自身称性能收益尚需量化

具体实现取决于现有 float8 设计可复用程度

人工复核（由项目成员填写）

复核状态：reviewed

决策：new_rules_more_reasonable

修订后的四维：

Code：3

Setup：3

Project Context：3

Collaboration：2

修订后的 Effort：multi_day

复核说明：任务位于量化和FSDP分布式训练核心路径，需要实现低位宽通信、tensor subclass、
backward兼容并进行多GPU性能验证。新规则对Code、Context和Effort的判断基本合理，
主要低估了Setup和一定的设计协作成本。

复核人：白淑静

日期：26/8/10

21. [P4：Performance 双维最高难度] pytorch/ao #2147

标题： [roadmap/tracker] Low precision MoE training

链接： https://github.com/pytorch/ao/issues/2147

task_candidate_id： 962

sample_groups： other_multi_day、roadmap_tracker、performance

命中复核原因

P4 performance_code3_context3：performance signal 下新规则仍为 Code=3 且 Project Context=3。具体：performance signal 且新规则 Code=3、Project Context=3。

P6 non_actionable：新规则 actionability=non_actionable。具体：诊断报告 actionability=non_actionable。

Issue 证据摘要

Labels：tracker、triaged

Task types：other

Comment count：4

Actionability：non_actionable

Information confidence：low

<details>
<summary>展开 body_excerpt</summary>

<pre>Creating this issue as a roadmap/tracker for enabling float8 training for MoEs with token-choice routing. Both core requirements as well as ideas for additional performance optimizations are included.

**UPDATE** 07/22/2025: revised priorities to reflect shifting focus to prioritize mxfp8

**This is not an exhaustive list, but highlights some primary milestones / requirements**

## Compute
- [x] mxpf8
- [x] mxfp8 scaled grouped gemm
- [X] 2d-3d gemm for output and dX (#2848)
- [x] 2d-2d gemm for dW ([link]
- [x] torchao differentiable _scaled_grouped_mm support for mxpf8 recipe for dynamic quant before grouped GEMMs
- [x] triton kernels for per token group scale conversion to blocked swizzled format
- [x] for 2d inputs with groups along M (#2886)
- [x] for 3d expert weights (#2894)
- [x] for 2d in puts with groups along K ([link]
- [ ] fp8 rowwise
- [x] Add torch._scaled_grouped_mm kernel in core
- [link] (by @ngimel)
- [x] Add differentiable scaled grouped mm with dynamic float8 rowwise quant in torchao
- [link]
- [x] Add custom kernels in torchao for performing per-group scaling on device, to avoid host-device sync
- [link]
- [link]
- [ ] Faster inductor codegen kernels for dynamic quant of 3d tensors along dim1: [link]
- [x] alternatively, handwritten triton kernel faster than torch.compile for this ([link]
- [ ] this also needs to be faster [link]
- [ ] fp8 blockwise
- [ ] quant primitives
- [ ] Explore DeepGEMM for fp8 blockwise grouped GEMM
- [ ] triton kernels to do scaling per group without d2h sync

## Communication
I looked at traces and validated &quot;all to all dispatch and shuffle -&gt; grouped gemm -&gt; all to all combine and unshuffle&quot; are all sequentially dependent, so in theory faster/low precision comms should improve performance. There is some overlap with the shared expert computation, but it is not 100% overlap, so there is room for optimization. This will be especially important if/when &quot;all to all&quot; spans multiple nodes, where inter-node network bandwidth is lower than the intra-node NVLink bandwidth.

- [ ] mxfp8
- [x] 1d on device all_to_all_v comms kernel (differentiable; with dynamic quant) [link]
- [ ] NVSHMEM put/get primitives instead of tl.load/tl.store to support inter-node a2a
- [ ] float8 blockwise (P0)

## Torchao UX
- [X] Add tensor subclass (ScaledGroupedMMTensor) with an op override for torch.aten._grouped_mm =&gt; runs differentiable scaled grouped mm
- [link]
- [X] Add one line model conversion API, should recursively swap nn.Parameter data tensors of the expert weights with ScaledGroupedMMTensor.
- [link]
- [X] support configurable recipe (fp8 blockwise/rowwise, mxpf8)

## Compile support
- [x] Compile support for torch._grouped_mm
- done by @bdhirsh in [link]
- [X] Differentiable _scaled_grouped_mm can compile with fullgraph=True
- [X] E2E compilation of each TranformerBlock in torchtitan after MoE conversion via tensor subclass approach (fullgraph=False)
- [ ] E2E compilation of each TranformerBlock in torchtitan after MoE conversion via tensor subclass approach (fullgraph=True)

## Distributed support
- [x] Composability with FSDP2 (will likely need something like [this]([link] for the new tensor subclass)
- [x] mxfp8 (P0)
- [ ] float8 blockwise (P0)
- [x] float8 rowwise (P1) [link]
- [ ] Composability with TP
- [x] mxfp8 (P0)
- [ ] float8 blockwise (P0)
- [x] float8 rowwise (P1) [link]
- [ ] Composability with FSDP + TP
- [x] mxfp8 (P0)
- [ ] float8 blockwise (P0)
- [x] float8 rowwise (P1) [link]
- [ ] Composability with dp2ep as implemented here: [link]
- [x] mxfp8 (P0)
- [ ] float8 blockwise (P0)
- [x] float8 rowwise (P1) [link]</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

3

3

3

Setup

2

1

3

Project Context

3

3

3

Collaboration

1

1

3

Effort

multi_day

multi_day

multi_day

差异摘要

旧规则与 AI 四维总绝对差：3

新规则与 AI 四维总绝对差：4

新旧规则四维变化总绝对值：1

旧规则与 AI Effort 档位差：0

新规则与 AI Effort 档位差：0

AI 辅助初评

置信度：low

理由：若把整份 roadmap 视为一个任务，它横跨 kernel、通信、compile、FSDP/TP 与多节点训练，显然是长期多任务计划；但该 Issue 不应被当作可直接领取的单一开发任务。

证据：

标题和标签明确为 roadmap/tracker

正文包含大量已完成和未完成里程碑

范围横跨计算、通信、编译和多种分布式组合

不确定性：

不存在单一明确的验收边界

项目成员应优先将本条标记为 insufficient，并要求拆分后再评估

人工复核（由项目成员填写）

复核状态: reviewed

决策：insufficient_information

修订后的四维：

Code：

Setup：

Project Context：

Collaboration：

修订后的 Effort：

复核说明：该Issue明确是roadmap/tracker，包含大量已完成和未完成里程碑，
范围横跨kernel、通信、compile和多种分布式训练组合，不是单一可执行任务，
因此不适合给出统一难度和Effort。

复核人：白淑静

日期：26/8/10

22. [P5：正文缺失] excalidraw/excalidraw #1007

标题： We should have a different hint when drawing multisegments lines on mobile

链接： https://github.com/excalidraw/excalidraw/issues/1007

task_candidate_id： 535

sample_groups： body_missing、feature、newcomer

命中复核原因

P5 body_missing：Issue 正文缺失。具体：诊断报告确认 body_text 缺失或为空。

Issue 证据摘要

Labels：enhancement、good first issue

Task types：feature

Comment count：8

Actionability：unclear

Information confidence：low

<details>
<summary>展开 body_excerpt</summary>

<pre>（正文缺失）</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

1

Setup

1

1

1

Project Context

1

1

1

Collaboration

1

1

1

Effort

half_day

half_day

half_day

差异摘要

旧规则与 AI 四维总绝对差：0

新规则与 AI 四维总绝对差：0

新旧规则四维变化总绝对值：0

旧规则与 AI Effort 档位差：0

新规则与 AI Effort 档位差：0

AI 辅助初评

置信度：low

理由：标题暗示是移动端绘制多段线时的提示文案或交互调整，可能是局部 UI 修改；但正文缺失，无法确认实际触发条件和设计要求。

证据：

标题只描述移动端绘制 multisegment lines 时需要不同提示

标签为 enhancement

正文为空

不确定性：

不知道提示内容、交互状态和测试要求

可能涉及移动端输入状态机，实际复杂度可能更高

人工复核（由项目成员填写）

复核状态： reviewed

决策：insufficient_information

修订后的四维：

Code：

Setup：

Project Context：

Collaboration：
修订后的 Effort：
复核说明：正文缺失，仅凭标题无法判断是简单提示文案调整还是涉及移动端绘图状态和交互逻辑，
无法可靠估计技术难度和工作量。

复核人：白淑静

日期：26/8/10

23. [P5：正文缺失] excalidraw/excalidraw #5301

标题： Feature Request: Import GIF pictures

链接： https://github.com/excalidraw/excalidraw/issues/5301

task_candidate_id： 549

sample_groups： body_missing、feature

命中复核原因

P5 body_missing：Issue 正文缺失。具体：诊断报告确认 body_text 缺失或为空。

Issue 证据摘要

Labels：无

Task types：feature

Comment count：15

Actionability：unclear

Information confidence：low

<details>
<summary>展开 body_excerpt</summary>

<pre>（正文缺失）</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

2

1

2

Setup

1

1

1

Project Context

1

1

2

Collaboration

2

1

1

Effort

one_day

half_day

one_day

差异摘要

旧规则与 AI 四维总绝对差：2

新规则与 AI 四维总绝对差：2

新旧规则四维变化总绝对值：2

旧规则与 AI Effort 档位差：0

新规则与 AI Effort 档位差：1

AI 辅助初评

置信度：low

理由：导入 GIF 可能涉及文件解析、资源存储、渲染和导出兼容性，通常不是简单文案改动；但正文缺失，无法判断是静态首帧支持还是完整动画支持。

证据：

标题提出导入 GIF 图片功能

正文为空

任务属于 feature

不确定性：

未说明是否需要保留动画、透明度和导出行为

未说明浏览器兼容性和文件大小限制

人工复核（由项目成员填写）

复核状态：reviewed

决策：insufficient_information

修订后的四维：

Code：

Setup：

Project Context：

Collaboration：

修订后的 Effort：

复核说明：正文缺失，无法确认GIF导入是仅支持静态首帧还是需要完整动画、渲染和导出兼容，
不同范围的复杂度差异很大，因此暂不评分。

复核人：白淑静

日期：26/8/10

24. [P5：正文缺失] nodejs/undici #3276

标题： interceptors: move signal handling to interceptor

链接： https://github.com/nodejs/undici/issues/3276

task_candidate_id： 1085

sample_groups： body_missing、refactor、newcomer

命中复核原因

P5 body_missing：Issue 正文缺失。具体：诊断报告确认 body_text 缺失或为空。

Issue 证据摘要

Labels：good first issue、interceptors

Task types：refactor

Comment count：8

Actionability：unclear

Information confidence：low

<details>
<summary>展开 body_excerpt</summary>

<pre>（正文缺失）</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

1

1

2

Setup

1

1

1

Project Context

1

1

2

Collaboration

1

1

1

Effort

half_day

half_day

one_day

差异摘要

旧规则与 AI 四维总绝对差：2

新规则与 AI 四维总绝对差：2

新旧规则四维变化总绝对值：0

旧规则与 AI Effort 档位差：1

新规则与 AI Effort 档位差：1

AI 辅助初评

置信度：low

理由：将 signal handling 移到 interceptor 可能涉及请求取消生命周期和多个 interceptor 的职责边界；标题暗示重构，但正文缺失，无法确认范围。

证据：

标题明确要求移动 signal handling 到 interceptor

标签指向 interceptors 模块

正文为空

不确定性：

无法判断涉及哪些 interceptor、兼容性和测试

good first issue 标签不能作为技术难度依据

人工复核（由项目成员填写）

复核状态：reviewed

决策：insufficient_information

修订后的四维：

Code：

Setup：

Project Context：

Collaboration：

修订后的 Effort：

复核说明：标题只能确认这是signal handling相关重构，但正文缺失，
无法判断涉及哪些interceptor、请求取消生命周期、兼容性及测试范围，因此无法可靠评分。

复核人：白淑静

日期：26/8/10

25. [P5：正文缺失] pytorch/ao #2298

标题： Use Int8WeightOnlyConfig  to quant wan2.1 model, and export to onnx file, Why the onnx weights in my disk  are fp32 precision?

链接： https://github.com/pytorch/ao/issues/2298

task_candidate_id： 981

sample_groups： body_missing、classification_boundary

命中复核原因

P5 body_missing：Issue 正文缺失。具体：诊断报告确认 body_text 缺失或为空。

Issue 证据摘要

Labels：无

Task types：feature

Comment count：1

Actionability：unclear

Information confidence：low

<details>
<summary>展开 body_excerpt</summary>

<pre>（正文缺失）</pre>

</details>

三方结果对照

维度

旧规则

新规则

AI辅助初评

Code

2

1

0

Setup

1

1

1

Project Context

1

1

1

Collaboration

0

0

1

Effort

half_day

half_day

under_2h

差异摘要

旧规则与 AI 四维总绝对差：3

新规则与 AI 四维总绝对差：2

新旧规则四维变化总绝对值：1

旧规则与 AI Effort 档位差：1

新规则与 AI Effort 档位差：1

AI 辅助初评

置信度：low

理由：当前只有咨询式标题，没有正文或可执行验收标准，更像使用问题而不是已确认的 feature；不应推断需要代码修改。

证据：

标题询问 ONNX 导出后权重为何仍为 fp32

正文为空且没有标签

未提供最小复现、预期行为或目标改动

不确定性：

可能是配置使用错误、导出限制、文档缺失或真实功能缺口

在补充信息前无法可靠估计开发难度

人工复核（由项目成员填写）

复核状态：reviewed

决策：insufficient_information

修订后的四维：

Code：

Setup：

Project Context：

Collaboration：

修订后的 Effort：

复核说明：当前只有咨询式标题，没有正文、最小复现或明确验收目标，
可能属于配置问题、导出限制、文档问题或真实功能缺口，无法可靠判断是否需要代码修改。

复核人：白淑静

日期：26/8/10