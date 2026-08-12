OSS-Mentor B3 项目成员复核说明 v0.1

1. 方法定位

本文件配套：

difficulty_calibration_ai_assisted_v0.1.json

当前流程是：

AI 辅助初评 + 项目成员人工复核

AI 初评不是独立人工盲标，也不是人工金标准。它只是帮助项目成员更快识别旧难度规则可能存在的高估、低估和信息不足问题。只有项目成员完成复核后，结果才能用于后续公式设计。

本批次共有 36 条任务。AI 置信度分布：

high：13

medium：15

low：8

2. 每条记录需要填写什么

每条记录已经包含：

annotation_input：原始任务信息，禁止修改；

ai_assessment：AI 初评，只作为建议；

member_review：项目成员需要填写的复核结果。

项目成员只修改 member_review，不要修改 annotation_input 和 ai_assessment。

3. 三种复核决定

accept

当项目成员认为 AI 的四维难度、effort、理由和不确定性总体合理时填写：

"member_review": {
  "review_status": "completed",
  "reviewer": "成员姓名或团队代号",
  "decision": "accept",
  "revised_annotation": null,
  "review_comment": "已核对任务描述，接受 AI 初评。"
}

accept 不表示评分绝对正确，只表示在现有 Issue 信息下，项目成员认为它足以作为校准参考。

revise

当项目成员认为部分评分需要修改时填写：

"member_review": {
  "review_status": "completed",
  "reviewer": "成员姓名或团队代号",
  "decision": "revise",
  "revised_annotation": {
    "code_difficulty": 2,
    "setup_difficulty": 1,
    "project_context_difficulty": 2,
    "collaboration_difficulty": 1,
    "effort_bucket": "one_day",
    "annotation_confidence": "medium",
    "rationale": "说明修改原因",
    "evidence": [
      "列出支持修改判断的任务证据"
    ],
    "uncertainties": [
      "列出仍无法确认的部分"
    ]
  },
  "review_comment": "说明具体修改了哪些字段，以及为什么。"
}

revise 时必须完整填写 revised_annotation，不能只写被修改的单个字段。

insufficient

当 Issue 缺少正文、属于 roadmap/tracker、支持咨询、方案尚未确定，或者现有信息无法支持可靠估计时填写：

"member_review": {
  "review_status": "completed",
  "reviewer": "成员姓名或团队代号",
  "decision": "insufficient",
  "revised_annotation": null,
  "review_comment": "现有 Issue 信息不足，不能作为可靠难度校准样本。"
}

insufficient 是有效结论，不要为了凑齐数据强行给分。

4. 复核时重点检查

项目成员应逐条确认：

good first issue 是否被错误当作低技术难度；

performance 是否被无条件判成最高难度；

评论数是否被误当作协作难度；

Linux、Windows、Docker、Kubernetes 等词是否真的代表必须搭建特殊环境；

roadmap、tracker、discussion 是否属于可直接执行的单项任务；

正文缺失时是否出现了过度推断；

effort 是否是独立判断，而不是四维机械求和；

AI 是否编造了 Issue 中没有提供的实现范围。

5. 建议复核安排

36 条全部至少由 1 名项目成员复核；

以下高风险类别建议由第 2 名成员再复核：

body_missing

roadmap_tracker

performance_newcomer

performance_non_newcomer

documentation_only

context_broad_label

setup_body_keyword

两名成员有分歧时，保留分歧说明，不必强行制造唯一答案；

完成后另存为：difficulty_calibration_reviewed_v0.1.json

6. 完成条件

复核集完成时应满足：

36 条记录全部 review_status = "completed"；

decision 只能是 accept、revise 或 insufficient；

revise 必须包含完整 revised_annotation；

accept 和 insufficient 的 revised_annotation 必须为 null；

reviewer 与 review_comment 不得为空；

annotation_input_sha256 不变；

不得把 AI 初评描述为人工金标准。

7. 项目报告推荐表述

本项目采用 AI 辅助案例评估与项目成员复核相结合的方法。AI 根据 Issue 标题、标签、任务类型、正文摘录和讨论信息生成初始难度建议；项目成员逐条选择接受、修订或标记为信息不足。该复核集用于识别规则的系统性偏差，不被视为开源任务真实工时的绝对金标准。