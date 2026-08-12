库
/
b3_exploratory_difference_analysis_v0.1.md


OSS-Mentor B3 探索性难度差异分析 v0.1
方法边界
本报告比较：

608 条 eligible 任务的全量只读诊断；

36 条固定案例的当前规则预测；

36 条 AI 辅助初评。

AI 辅助初评不是人工金标准，因此下文只使用“一致率、平均绝对差异、当前更高、当前更低”等描述，不使用“准确率”或“真实误差”。

1. 文件完整性
AI 辅助评估：36 条；

当前预测：36 条；

task_candidate_id + repository + issue_number 完全对齐；

两份文件排序一致；

无重复、无缺失；

annotation_input_sha256 与文件内容一致；

36 条 AI assessment 的四维、effort、confidence、rationale、evidence、uncertainties 均完整；

member_review 仍为 36 条 pending；

两份案例文件均来自 oss_mentor_task_features_v0.2_round3.sqlite3；

全量诊断主范围为 608 条 eligible，另有 264 条 newcomer eligible。

2. 总体探索性差异
维度	完全一致	一致率	平均绝对差异	当前更高	当前更低	相差≥2级	当前均值→AI均值
Code	10/36	27.8%	1.06	6	20	12	1.39→2.00
Setup	16/36	44.4%	0.61	7	13	2	1.50→1.72
Project context	10/36	27.8%	1.00	5	21	10	1.50→2.17
Collaboration	10/36	27.8%	0.89	10	16	4	1.25→1.58
Effort：

完全一致：15/36（41.7%）；

相邻档差异：15/36；

跨两档以上：6/36；

当前更高：6；

当前更低：15；

平均绝对档位差：0.75。

3. 按样本组差异
下表四维列为平均绝对差异：

样本组	n	Code	Setup	Context	Collaboration	Effort一致	Effort方向
body_missing	4	0.75	0.00	0.50	0.50	2/4	当前更高 1，当前更低 1
documentation_only	2	0.00	1.00	0.50	0.50	1/2	当前更高 1，当前更低 0
other_multi_day	1	0.00	1.00	0.00	2.00	1/1	当前更高 0，当前更低 0
performance_newcomer	11	1.64	0.91	1.73	0.82	1/11	当前更高 0，当前更低 10
setup_body_keyword	4	1.00	0.50	1.00	0.50	2/4	当前更高 1，当前更低 1
context_broad_label	4	1.25	0.50	0.50	1.00	2/4	当前更高 0，当前更低 2
performance_non_newcomer	4	0.75	0.75	0.75	1.50	4/4	当前更高 0，当前更低 0
testing_control	2	1.00	0.00	1.00	1.00	1/2	当前更高 0，当前更低 1
build_tooling_control	2	0.50	0.50	0.50	1.00	0/2	当前更高 2，当前更低 0
refactor_high_context	2	1.00	0.50	1.00	1.00	1/2	当前更高 1，当前更低 0
4. 四维相差两级以上的案例
apache/pinot #6970：Code 1→3（Δ-2）；Project context 1→3（Δ-2）。AI confidence=medium。

apache/pinot #16584：Collaboration 0→3（Δ-3）。AI confidence=high。

kubernetes/kubernetes #82440：Code 1→3（Δ-2）；Project context 1→3（Δ-2）。AI confidence=high。

nodejs/undici #5466：Code 3→1（Δ+2）；Project context 3→1（Δ+2）。AI confidence=high。

pandas-dev/pandas #65326：Code 1→3（Δ-2）。AI confidence=high。

prometheus/prometheus #10431：Code 1→3（Δ-2）；Project context 1→3（Δ-2）。AI confidence=medium。

pytorch/ao #988：Code 1→3（Δ-2）；Setup 1→3（Δ-2）；Project context 1→3（Δ-2）。AI confidence=high。

pytorch/ao #1224：Code 1→3（Δ-2）；Setup 1→3（Δ-2）；Project context 1→3（Δ-2）。AI confidence=high。

pytorch/ao #2147：Collaboration 1→3（Δ-2）。AI confidence=low。

pytorch/ao #2298：Code 2→0（Δ+2）。AI confidence=low。

pytorch/pytorch #135859：Code 1→3（Δ-2）；Project context 1→3（Δ-2）。AI confidence=high。

quarkusio/quarkus #42510：Code 1→3（Δ-2）；Project context 1→3（Δ-2）；Collaboration 0→3（Δ-3）。AI confidence=high。

scikit-learn/scikit-learn #31503：Code 1→3（Δ-2）；Project context 1→3（Δ-2）。AI confidence=high。

scikit-learn/scikit-learn #31554：Code 1→3（Δ-2）；Project context 1→3（Δ-2）。AI confidence=medium。

wagtail/wagtail #14318：Collaboration 0→2（Δ-2）。AI confidence=high。

5. Effort 跨两档以上的案例
apache/pinot #6970：half_day → multi_day（Δ-2）。AI confidence=medium。

eslint/eslint #17733：one_day → under_2h（Δ+2）。AI confidence=low。

nodejs/undici #5466：multi_day → half_day（Δ+2）。AI confidence=high。

pytorch/ao #988：half_day → multi_day（Δ-2）。AI confidence=high。

pytorch/ao #1224：half_day → multi_day（Δ-2）。AI confidence=high。

quarkusio/quarkus #42510：half_day → multi_day（Δ-2）。AI confidence=high。

6. 全量数据客观证明的问题
Project context 高度退化：556/608（91.45%）为 1，等级 0 完全未使用；只有 10 条为 2、42 条为 3。

Setup 对关键词规则依赖过强：256/608 为 setup=2；194 条进入 setup keyword escalation 队列；169 条仅凭正文关键词进入 setup=2，占全部 setup=2 的 66.02%。

Collaboration 的有效分层不足：等级 3 完全未使用；170 条 collaboration=2 中有 157 条没有 discussion/design 信号，占 92.35%；comment_count 阈值队列覆盖全部 608 条。

Performance 与 newcomer 存在硬规则冲突：264 条进入 newcomer cap；30 条非 newcomer performance 被统一输出为 code=3、context=3、multi_day；另有 11 条 performance+newcomer 未进入该 escalation。

任务类型内部分层不足：testing-only 15 条全部 code=1/context=1；build-tooling-only 21 条全部 code=1/context=1；documentation-only 28 条全部 code=0/context=1。

Effort 与四维机械耦合：608/608 与当前四维求和映射完全一致；B2 后有 99 条 effort 改变，其中 22 条跨两档以上，说明上游单维或任务类型变化会被直接放大。

缺少正文仍输出确定评分：4 条 body missing 全部得到完整四维和 effort，没有“不足以判断”的输出状态。

非单项任务也被映射到确定 effort：存在 1 条 other + multi_day 的 roadmap/tracker 案例。

7. AI 案例支持、但仍待成员复核的问题
performance+newcomer 组 11 条中，当前规则在 Code 上 10 条更低、Context 上 11 条更低、Effort 上 10 条更低；但这只能说明与 AI 评估存在稳定分歧。

performance 非 newcomer 组 4 条中，当前 Code 和 Context 均有 3 条高于 AI 评估，但四条 Effort 都一致为 multi_day；因此更可能是 hard-3 维度过粗，而不是 multi_day 一定错误。

Setup keyword 组四条的 setup 均值恰好一致，且出现“一条当前更高、一条当前更低、两条一致”；这不支持“所有关键词触发都高估”，只支持单一关键词缺乏判别力。

Collaboration 在高评论常规任务上可能偏高，在低评论 RFC、设计或跨团队任务上又可能偏低；comment_count 不宜作为主导证据。

Testing 和 build tooling 的固定 code/context=1 与 AI 评估出现分层差异，但 build tooling 两条均为低置信度，需人工确认。

Refactor 的 context=3 硬升级可能过粗；两个案例一个明显偏高、一个基本合理。

Body missing 的主要问题不是统一高估或低估，而是模型没有表达“不确定”。

8. 当前不应直接修改的规则
不应依据 AI 案例把所有 performance 任务统一调低或调高。

不应取消纯 documentation 的 code=0；两个 documentation-only 案例的 AI 评估同样为 code=0。

不应认定所有 setup 关键词都是误触发。

不应完全删除 comment_count；它可保留为弱辅助信号，但不能单独决定 collaboration。

不应把正文缺失统一映射成低难度；正确处理方向是降低确定性或标记信息不足。

不应按 36 条案例重新调 effort 阈值，因为 AI 评估尚未完成人工复核。

不应修改 B2 任务类型、技能要求或 matching。

不应增加仓库名、Issue 编号或案例专用关键词。

9. B3 新公式的最小设计约束
四个维度继续保持 0～3 的明确语义。

Task type 只能提供先验，不能直接固定完整难度。

Newcomer/good-first-issue 与技术难度解耦，不能作为硬上限。

Performance 不得无条件把 Code、Context 提升到 3。

Setup 只使用“完成或复现任务所必需的环境证据”，区分描述性环境信息与真实环境要求。

Context 必须允许 0、1、2、3 全部分层，并明确跨模块/API/架构证据。

Collaboration 优先依据未决设计、RFC、争议、多维护者或跨团队证据；评论数只作弱辅助。

Documentation、testing、build tooling、refactor 均允许内部难度分层。

对正文缺失、支持咨询、roadmap/tracker 和范围未定任务建立信息不足策略。

Effort 独立考虑任务范围、可执行性与不确定性，不能只做四维等权求和。

规则必须可解释、顺序稳定、证据结构化。

所有规则保持通用，不依赖仓库或 Issue 身份。

10. 最需要人工复核的 12 条案例
apache/pinot #6970：performance + newcomer，当前规则明显偏低。

quarkusio/quarkus #42510：performance + newcomer，且低评论数却可能需要高协作。

excalidraw/excalidraw #7237：performance 非 newcomer，当前 hard-3 可能偏高。

nodejs/undici #5466：refactor hard context escalation 可能严重偏高。

excalidraw/excalidraw #9281：环境关键词可能造成 setup 偏高。

kubernetes/kubernetes #82440：同属环境关键词组，但 AI 反而认为当前整体偏低。

pandas-dev/pandas #65326：公共 API/索引语义，context 与协作证据复杂。

eslint/eslint #17733：自动 Dependency Dashboard 是否属于可执行任务。

apache/pinot #13263：testing 任务是否被固定为 code/context=1。

scikit-learn/scikit-learn #17140：documentation-only 与 bug 语义边界。

pytorch/ao #2147：roadmap/tracker 不应被当作普通单项任务。

pytorch/ao #2298：正文缺失且可能是支持咨询，不宜给确定难度。