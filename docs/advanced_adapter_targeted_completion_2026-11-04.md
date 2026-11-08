# 2026-11-04 低历史训练与新仓库测试补充

本轮完成两个独立补充包、测试仓库新颖性复查和历史候选特征导出。原 v4 输入作为本轮选样参照，后续需由 B/C 建立新数据版本。

## 新增结果

- 新增训练 outcome 8 条，覆盖 2 位开发者。跨仓库关闭关系按实际 Issue 仓库归属，保留源 PR 仓库及实际 ClosedEvent 证据。
- 原低历史 127 人中，按 v4 标签与本轮具备画像和对照的训练补充合并后，仍有 61 人不足 3 条训练结果。现有未关联 Issue 的 PR 试采收益较低，排除原因已保存。
- 新增严格新仓库 Q3 测试 outcome 270 条，覆盖 233 个仓库，其中 175 条同时具备画像和候选对照。
- 新仓库定义为未出现在 v4 训练 X/Y、补充训练标签及训练期可见补充贡献中的仓库；基础 checkpoint 的仓库映射仍需 C 复核。
- 两个补充包逐条时间、数字账号、实际关闭事件、Git blob 和文件哈希校验通过。技术性历史读取失败为 0；无法证明的身份、时间、正文和贡献继续排除。

## 新测试数据如何使用

汇总目录为 data/midterm/working/advanced_adapter_targeted_20261104_handoff。

- training_outcomes.jsonl 为 8 条训练补充。
- new_repository_test_outcomes.jsonl 为 270 条严格新仓库测试结果。
- new_repository_test_ready.jsonl 为 175 条同时具备画像和候选对照的结果。
- 每条记录通过 source_root 选择完整证据包，按 outcome_id 联接对应 joined_samples.jsonl、profile_previews.jsonl 和 historical_controls.jsonl。
- source_manifest.json 绑定来源清单及 v4 训练参照哈希，outcome_exclusions.jsonl 保留排除原因。

这些记录属于新增评估补充集。C 需要版本化候选采样并用新冻结画像评估，再比较模型表现。

## 历史结构化候选字段

导出目录为 data/midterm/working/historical_candidate_features_20261104_v1。

- 共 9519 个历史候选快照，使用原 content_sha256 联接。
- 6334 个候选有任务类型规则证据；task_types 明确标记为历史正文规则推断，附规则版本、源码哈希和匹配证据。其余可能是规则回退类型，需与证据覆盖区分。
- 8027 个候选有关联仓库的截止前采样文件语言证据，保留文件键。该证据描述采样贡献，不能替代仓库主语言或 Issue 必需语言。
- primary_language、technology_stack、code_difficulty、setup_difficulty、历史 labels 及 available_until 缺少可靠证据时均为 null，列入 unknown_fields。
- repository、created_at、available_from、内容哈希和特征版本均已提供。

## B/C 下一步

B 合并新训练结果及历史贡献后重建画像，按数字账号和截止点去重，审核冻结。新测试集的预览画像仍是 research_preview，不能直接视作正式冻结输入。

C 复核实际基础 checkpoint 的仓库映射和时间来源，按 Issue key 构建多正例标签，筛选同仓库困难对照并记录候选集合哈希。175 条可联接测试记录尚未完成 C 模型运行，不能作为效果提升结论。

后续仍需补 61 位历史不足用户的真实结果、可靠的语言与难度字段，以及平台真实曝光和明确反馈。真实反馈需要实际使用产生。

## 复现入口

- scripts/midterm_a/collect_targeted_adapter.py 保存选样和补采逻辑，两个补充包内保存实际 collector_source、请求及分页证据。
- scripts/midterm_a/export_historical_candidate_features.py 从历史正文复用现有特征规则，导出推断状态与未知字段。
- 新增关闭事件、新仓库归属以及训练期贡献影响仓库新颖性的边界测试通过；贡献时间与作者、损坏 blob 拒收测试通过。
