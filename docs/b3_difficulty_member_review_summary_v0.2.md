OSS-Mentor B3 项目成员复核汇总 v0.2

1. 方法边界

本轮完成了 25 条重点案例的项目成员复核。复核表同时展示了旧规则、新规则和 AI 辅助初评，且复核过程中使用了 AI 辅助解释，因此本结果应表述为项目成员人工复核（AI辅助），不是独立盲评，也不是金标准。

25 条案例来自固定 36 条校准案例中的高风险子集，因此下面的数值只用于规则校准和工程决策，不能代表全部 608 条 eligible 任务，也不能用于声称准确率提高。

2. 复核结果

已复核：25

可进行数值比较：18

信息不足/任务粒度不适合评分：7

old_rules_more_reasonable：11

new_rules_more_reasonable：3

both_unreasonable：4

insufficient_information：7

3. 旧规则 / 新规则与项目成员复核的一致性

仅比较 18 条具有完整人工修订值的案例。

指标

旧规则

新规则

四维 exact agreement

24/72 (33.3%)

27/72 (37.5%)

四维平均绝对差

0.931

0.847

Effort exact agreement

6/18 (33.3%)

2/18 (11.1%)

Effort 平均档位差

0.889

1.556

Effort 低于人工复核

11/18

16/18

Effort 相差≥2档

4/18

12/18

四维分别比较

维度

旧规则 exact

新规则 exact

旧规则 MAD

新规则 MAD

新规则低于人工

Code

3/18

6/18

1.222

1.000

12/18

Setup

9/18

10/18

0.611

0.556

8/18

Project Context

4/18

2/18

1.111

1.278

16/18

Collaboration

8/18

9/18

0.778

0.556

8/18

4. 工程结论

新规则在四维总体一致性上相对旧规则有小幅变化：exact agreement 从 33.3%变为 37.5%，MAD 从 0.931变为 0.847。

但这个总体变化掩盖了明显的结构性问题：

Project Context 仍明显偏低：新规则有16/18条低于项目成员复核值。

Code 在复杂案例上仍偏低：新规则有12/18条低于项目成员复核值。

Effort 是最需要修正的问题：新规则只有2/18 条与项目成员复核一致，并有 16/18条低于项目成员复核值。

Setup 与 Collaboration 不宜整体上调；应只针对明确证据增强。

insufficient_information 的 7 条记录不应被强行转成精确难度，应继续保留 non-actionable / unclear / provisional 语义。

5. 下一步建议

B3 不建议直接关闭。

下一步应进入 difficulty-rules-v0.2.1 的定向小修设计，而不是推翻 v0.2 重写。

小修优先处理：

复杂性能 / 编译器 / 核心 API / 分布式 / 算法任务的 Code 强证据；

跨模块、公共 API、兼容性、RFC、核心执行路径的 Project Context 强证据；

Effort 的 scope + actionability + technical-complexity 联合决策，避免大量复杂任务停在 half_day；

保留 v0.2 已修好的约束：performance 不自动=3、reported environment 不自动提高 Setup、评论数不自动提高 Collaboration；

信息不足、roadmap/tracker、non-actionable 任务继续单独处理，不强行评分。