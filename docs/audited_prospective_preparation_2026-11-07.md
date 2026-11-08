# 底座全量 X 审计、前瞻协议与学习曲线准备

本轮已完成可以立即执行的协议冻结、全量画像 X 审计、补证、准入/隔离清单、当前 Issue 预备采集及开发者分组抽样。**尚未完成干净底座重训、独立评估入组或实测学习曲线**；不能把计划、预备快照或采样清单描述为这些实验的完成结果。

## 1. 锁定研究方向与前瞻规则

正式使用 `data/midterm/working/prospective_protocol_20261107_v3/`，前两版在入组前被版本化取代，未覆盖。

- 研究比较：v6 正文与 v3，各使用固定的五个 seed。每个查询内分别对各 seed 的候选分数做 z 标准化（标准差下限1e-8），然后等权平均；同分按规范 issue_key 排序。该集成规则预先锁定，不声称已经取得独立集成收益。
- 主要指标：开发者等权 MRR；同时报告 outcome MRR、Recall@5/10/20、NDCG@5，以及无原训练 outcome、低画像覆盖、新仓库分组。
- 评估只在完整90天后执行一次。主差值开发者聚类95%区间下界需大于0；各组非劣容忍度预设为绝对MRR 0.02，区间下界不低于-0.02。缺失或无法形成有用区间的分组不允许据均值宣布晋级。容忍度是本研究的操作规则，不是通用统计标准。
- 408名预备用户中，40人没有原训练 outcome。名册按原 train/validation 身份确定，没有按未来贡献或排名筛选；这是时间独立设计，不是用户完全不重叠设计。
- 从 v3/v6 来源标签及175条新仓库来源记录构建4,559个已知用户–Issue排除对。只读取来源标签身份用于去重，未读取原有外部评价排名/指标或重新评分原 test。
- 冻结50个文件的SHA-256，包括当前研究权重、PCA、训练输入、底座文件和导入源码。干净重训改变产物时，必须在正式入组前生成新的模型锁定版本，不能用当前研究权重冒充已完成底座审计。

`protocol.json`、`LOCK.sha256`、`roster.json`、`excluded_user_issues.json` 均保留。研究状态为 `preregistered_preparation_only`。

## 2. 从三份选材扩到全部 X

本轮精确核对1461份冻结 train/validation 画像的原 profile_version 与全部 X：**5,499份不同文件、17,542条画像–文件引用**。内容SHA-256、Git blob、身份、登录名与事件截止检查没有失败。支持两种证据格式：完整缓存 payload 的canonical哈希，以及较早脱敏 projection；后者只核对其投影字段及记录来源，未把原payload哈希误当投影哈希。

复用已有证据，并对新增779个精确commit执行98批关联查询，全部成功。另执行65条PR公开事件只读查询，全部成功，无请求触及200行上限。

| 全部 X 公开性结果 | train | validation |
|---|---:|---:|
| 画像总数 | 1,193 | 268 |
| 至少一个文件获证 | 1,112 | 232 |
| 全部原选X文件获证 | **426** | **9** |

总计 **11,955/17,542条引用**获直接或复合证据；仍有 **1,567个不同commit**未决。复合证据仍是精确合并元数据与独立公开事件的历史推断，不是当时blob存档。未决不等于不公开。

这些是全部画像X的结果，不能与上轮“最多三份文件”通过率混用。还未覆盖历史Issue候选正文/状态、原预训练图/基础编码器全部训练来源的历史公开性，所以不能宣布整个底座完成无泄漏审计。

## 3. 准入、隔离与学习曲线

采取整份画像准入：全部原X文件获证才放行；任一文件未决，隔离整份画像。不会悄悄删除部分X后沿用原profile_version，也不修改原始数据。

- 435份画像通过、1,026份画像进入隔离清单。
- 对照实际冻结训练清单：通过画像覆盖 **868条训练 outcome、211名训练开发者**；验证只覆盖 **11条 outcome**。
- 已生成25%/50%/75%/100%四档×五seed，共20份嵌套开发者分组抽样清单，所有规模固定同一合格验证集合。
- 未执行这些20次训练，未生成假学习曲线数值。仅11条合格验证outcome，加上候选/图源审计未完成，当前不适合据此判断训练量是否充分。

在完成候选/图输入审计、新数据版本和用户–Issue监督去重后，每档必须重新初始化训练分支，不能冻结用全量旧训练集学到的RC1/v2/v3再称为全模型学习曲线。独立前瞻集不得参与这些采样、PCA或参数选择。

清单位于 `data/midterm/working/audited_curve_20261107_v1/`：`admitted_profiles.json`、`quarantined_profiles.json`、`curve_assignments.json`、`fixed_validation.json` 和 `plan.json`。

## 4. 独立后续评估的预备快照

按全部X中六种语言路径分别出现最多的两个仓库确定12个仓库，不用标签或排名筛选仓库。每仓库取当前创建时间倒序的50条公开open Issue，共 **600条**；冻结标题、正文、标签、状态、创建/更新时间、来源响应和哈希。

这是有限仓库、有限候选的**pilot预备快照**，没有完整生态覆盖保证，也不表示全部候选已通过产品资格过滤。正式预测锁定前需刷新，届时按所有用户的固定准入候选池排序，不能在看到结果后补候选。

首次查询因误用Issue字段isLocked失败；已改为locked并在全新v2目录重新采集。12仓库均成功，失败版本留存，未并入数据。

**当前不入组、不评分、不开始90天计时。** 下一步须先完成干净模型版本和当前用户画像，再冻结预测及输入哈希，记实际 `prediction_lock_at`，由此计算90天截止。若将来确实在2026-11-07锁定，最早才可能于2027-02-05成熟；本轮尚未锁定预测，不能把这个假设日期当实际完成日期。

`check_prospective_readiness.py` 已确认协议和50个锁定文件完整、408人名册和600条快照存在；明确返回 `evaluation_ready=false`，原因包括底座审计/重训未完成、当前画像与预测未锁定、90天未完成。

## 5. 检查与复现

22项测试通过，覆盖原证据检查、脱敏格式精确SHA、整份画像准入、重复文件拒绝、嵌套开发者抽样和90天严格边界。相关新脚本编译检查通过。未修改公共API、原数据、发布模型，也未创建自动化或向用户/开发者发送消息。

关键产物还包括：

- `data/midterm/working/full_x_materials_20261107_v1/`：全部X材料、画像引用和零失败清单。
- `data/midterm/working/full_x_composite_20261107_v2/`：最终逐引用公开证据和汇总。
- `data/midterm/working/full_x_pr_links_20261107_v1/`：779个新增commit关联元数据。
- `data/midterm/working/full_x_pr_events_20261107_v1/`：65条公开事件查询和返回。
- `data/midterm/working/prospective_candidates_20261107_v2/`：600条预备快照及readiness。

```powershell
python -m pytest tests/midterm_a/test_audited_prospective.py tests/midterm_a/test_v5_publication.py tests/midterm_a/test_v5_pr_publication.py tests/midterm_a/test_v5_patch_supply.py -q -p no:cacheprovider --basetemp tmp/prospective_tests_NEW
python scripts/midterm_a/check_prospective_readiness.py --protocol data/midterm/working/prospective_protocol_20261107_v3 --candidates data/midterm/working/prospective_candidates_20261107_v2
python scripts/midterm_a/build_audited_curve_plan.py --root . --materials data/midterm/working/full_x_materials_20261107_v1 --proofs data/midterm/working/full_x_composite_20261107_v2 --output data/midterm/working/audited_curve_NEW
```

接续工作优先级已具体化：补足合格验证覆盖；审计历史Issue候选/图输入；建立干净数据版本并重新训练；之后刷新当前画像与候选并冻结前瞻预测。未来90天结果必须等待真实时间和结果证据，不能用当前快照或未解决状态代填。
