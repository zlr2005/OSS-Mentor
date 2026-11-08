# 2026-11-04 低历史开发者证据加深

本批覆盖 60 位开发者各一个真实训练窗口，截止前贡献列表最多取两页、每页 5 个 commit。原工作包为 advanced_adapter_backfill_2024_2026_v1，新增内容位于独立补充包 advanced_adapter_depth_20261104_v1。

## 结果

- 全部 127 人的证据覆盖写入 collection_plan.json；66 位仍只有 1～2 条可用训练 outcome，逐人缺口写入 account_gaps.jsonl。
- 本批为其中 60 人补采贡献窗口，另外 6 人保留在下一批缺口中。
- 新增 420 条去重文件贡献，涉及 288 个 commit，均有数字账号、作者日期、提交日期、Git blob 和 SHA-256 证据。
- 保留 295 个时间回放通过的候选快照，其中 118 个 issue/as_of 键为父包没有的新增候选快照。
- 排除已知正例后得到 280 个可用比较项；45 个窗口拥有 5 个可用同仓库对照。
- 采集、离线缓存重放和补充包清单校验通过。时间、身份、Git blob 损坏拒收边界测试 2 项通过，贡献字段包含父采集器的全部字段。

## B/C 使用方式

- historical_contributions.jsonl 为增量贡献，按 github_numeric_id、as_of、repository、commit_sha、path 联接，使用 content_sha256 检查字节。API 引用和代码字节相对补充包根目录解析；identity_evidence 沿用项目根目录相对路径。
- 合并贡献时保留原窗口证据和本批证据，再按截止时间过滤，去重后重建画像。未知维度继续保持 observed_mask=false；本包没有生成新的冻结画像。
- historical_controls.jsonl 为候选历史快照；comparison_groups.jsonl 已按开发者排除其已知正例。C 可联接同账号、同截止点和同仓库的正例，继续按基础 CODER 分数筛选困难候选。
- 本批对照来自最多 30 条候选的有界检索；窗口不足 5 个时保留真实数量。检索第二页满 5 个 commit 的窗口仍可能有更早贡献，不能视为完整历史。
- 贡献时间已检查；首次公开可见时间尚未独立核实。资料仍待 B 合并冻结、C 训练输入审计。

## 平台真实曝光

现有服务已保存 recommendation_run、profile_hash、candidate_hash 和排名。现有反馈入口支持 interested、not_suitable、started、completed。还需要前端实际展示事件及其时间、与 run_id 的联接、放弃原因等信息；生成推荐批次不能直接计为真实曝光。本批交付历史证据和候选，真实曝光数据仍依赖平台实际使用。
