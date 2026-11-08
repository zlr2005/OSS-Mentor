# 2026-11-04 低历史开发者证据加深交接

两批已覆盖全部 66 位低历史开发者，各选择一个真实训练截止点窗口。第二批补齐 6 人，新增 49 条文件贡献、30 个候选快照和 29 个可用比较项。两包的文件校验清单均已复核通过。

## 合计

- 66 位开发者、66 个画像窗口。
- 469 条新增文件贡献，涉及 316 个 commit。
- 325 个去重候选快照，过滤该开发者已知正例后有 309 个可用比较项。
- 50 个窗口有 5 个可用同仓库对照。

## 读取

汇总目录为 data/midterm/working/advanced_adapter_depth_20261104_handoff。

- contribution_index.jsonl 保留原贡献字段，并添加 source_root。内容字节及 API 证据引用相对该 source_root 解析；source_root 相对项目根目录。
- candidate_index.jsonl 按 issue_key、as_of 去重；历史正文由 content_sha256 标识。
- comparison_groups.jsonl 按 github_numeric_id、as_of、repository 联接正例；comparison_issue_keys 已排除该开发者已知正例。
- account_gaps.jsonl 保留每人的可用训练 outcome 数和时间点覆盖。加深贡献证据不代表新增已完成 Issue，原有 1～2 条 outcome 的缺口仍需真实结果补充。
- source_manifest.json 保存两个证据包根目录和原清单哈希，交付时需携带对应完整证据包。

## 后续联接

B 按账号和截止点将本包贡献与父包合并，按仓库、commit、文件去重后重建画像。所有输入须早于画像截止点；无证据的维度保留未知 mask。C 按同一账号和时点联接候选，使用固定基础模型继续筛选困难对照。

本轮每个窗口最多回溯 10 个 commit，每个 commit 依照现有规则最多保留代码、测试、文档各一个文件。满页窗口可能仍有更早历史。候选来自最多 30 条 Issue 的有界检索，尚未经过 CODER 困难程度筛选。提交时间已核验，首次公开可见时间尚未独立核实。证据仍待 B 合并冻结、C 审核。

真实曝光仍需平台实际展示事件和反馈联接，历史补采不产生真实曝光记录。
