# B/C 交接 · temporal_xy_2024_2026_v6

本轮继续执行《数据扩充.md》的历史补采要求，保留 2024—2026 年范围和原训练、验证、测试边界。独立生成 v6，不覆盖 v5。状态仍为 working / training_ready=false。

## 本轮补充

- 对 v5 剩余 61 位低历史用户复用可追溯的关联 Issue 检索证据，排除父包及四个补充包已核查 PR 后，没有可新增核查的 PR。空结果和搜索分页证据保存在 advanced_adapter_targeted_20261104_v5。该结论仅限现有查询、时间窗口和有界检索，不表示这些用户没有其他真实贡献。
- 继续从现有目标账号的历史索引中核查 1,000 条未处理 PR，得到 203 条新增核实训练 outcome，覆盖 34 位开发者、56 个目标 Issue 仓库。
- advanced_adapter_targeted_20261104_v6 包含 374 个历史候选快照、309 条窗口关联贡献和 60 个画像预览，201 条 outcome 在包内有画像与可用对照。贡献记录含复用证据，不应将 309 全部称作独立新增文件。
- 补充包逐项校验通过：1,615 个文件、203 条 outcome、309 条贡献、60 个画像和 201 条串联样本。实际关闭事件、数字身份、Git blob、时间窗口和未知 mask 均检查。
- 修复采集器无新增 outcome 时缺少空表导致中断的问题；上述空包已完整通过验证。

## v6 合并结果

| 项目 | v5 | v6 |
|---|---:|---:|
| 核实 outcome | 4,641 | 4,844 |
| 可排序 outcome | 4,212 | 4,413 |
| X/Y 开发者 | 527 | 528 |
| 去重文件贡献 | 12,416 | 12,483 |
| 原低历史组不足 3 条训练结果人数 | 61 | 61 |
| 可排序新仓库测试结果 | 191 | 191 |

| 切分 | 用户截止点组 | outcome 标签 | 可排序 outcome | 同仓库比较项 |
|---|---:|---:|---:|---:|
| train | 1567 | 3706 | 3592 | 11594 |
| validation | 271 | 391 | 391 | 1165 |
| test | 227 | 477 | 430 | 1448 |

所有 v5 的 4,371 条 X/Y outcome 标签均保留，净增 203 条训练标签。合并冲突为 0。270 条核实结果因缺少截止前代码未进入 X/Y；114 条训练标签及 47 条测试标签缺少可用对照，未进入排序文件。

## 文件入口

- data/midterm/working/advanced_adapter_targeted_20261104_v6：本轮独立证据包。
- data/midterm/working/merged_ab_history_2024_2026_v6：合并贡献、结果、历史对照及多来源清单。
- data/midterm/working/temporal_xy_2024_2026_v6：训练/验证/测试 X/Y、ranking 文件、audit.json、coverage.json、low_history_gaps.jsonl 和 new_repository_test_ready.jsonl。
- data/midterm/working/historical_candidate_features_20261104_v3：按新证据重新生成的候选特征，估计字段保留规则来源。

候选特征共 11,412 个快照，其中 7,490 个有任务类型证据、9,190 个有采样文件语言证据；代码难度、环境难度的受支持规则估计分别为 202、742 个，显式技术要求证据为 26 个。事实字段未知时仍保留 null。

合并清单中的 outcome_5 指向 advanced_adapter_targeted_20261104_v6；来源编号是清单内局部别名，不等于采集包版本号。证据文件应依 source_roots 和行内来源字段解析。

## 仍缺少什么

核实 outcome 已在 4,500～6,000 目标范围内；可排序结果距 4,500 还差 87 条，X/Y 开发者距 600 还差 72 人。原低历史组 61 人仍不足 3 条训练结果，需要不同检索范围或其他真实关闭证据，不能用普通 PR 或画像贡献顶替。

同仓库比较项尚未按基础 CODER 分数筛选为困难对照。B 仍需基于 v6 X 重建并冻结画像；C 需核对基础 checkpoint 的仓库映射、多正例去重、固定候选采样和正式评估。本轮未运行 C 模型训练。

新仓库定义为不在 v6 训练 X/Y 的 755 个仓库中，191 条测试结果覆盖 152 个仓库；不能直接宣称基础 checkpoint 从未见过它们。贡献采用 commit 时间，首次公开可见时间未独立验证；2026 Q4 随访仍不完整。

可靠主语言、技术栈、难度事实字段和平台真实曝光/反馈仍待补充；规则推断继续与事实区分。历史补采未产生真实曝光。

## 验证与复现

已运行补充包完整 verify、merge_ab_history.py --version 6、build_temporal_xy.py --version 6、verify_temporal_xy_handoff.py --version 6 和 report_temporal_coverage.py --version 6。合并来源使用 v5 的两批 depth 和四批 outcome，并新增本轮 v6 outcome 包。

采集器、合并补充和选样相关 15 项单元测试通过；额外检查 v5 标签保留、净增 203 条及最终 X/Y 包文件哈希。
