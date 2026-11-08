# 首批真实模型历史数据补入

2026-10-25，已将此前独立采集的 pytest 历史数据补入 `data/oss_mentor.sqlite3` 和 `data/oss_mentor_web.sqlite3` 的 `midterm_dataset_*` 表。两库的导入状态均为 `completed`，按数据版本隔离。

| 数据 | 每份库新增数量 |
|---|---:|
| 仓库 | 1（pytest-dev/pytest） |
| 开发者 | 11 |
| 逻辑文件 | 30 |
| 可用代码版本 | 54 |
| 文件贡献关系 | 54 |
| 贡献行为 | 37 |
| 历史已关闭 Issue | 3 |
| Issue—文件关联 | 5 |

数据来自已有 `data/midterm/working/pytest_issue_links.sqlite3` 中的完成版本 `git-issue-history-a7c2417dde00cd9698c7`。通过现有导出器重新生成自包含快照，校验结果为 `source_verified`，`fixture_only=false`，版本为 `git-issue-history-a7c2417dde00cd9698c7-normalized-eac4f25dc409`。54 个代码版本全部为 available，内容、Git 对象、历史许可和关联来源按现有校验器验证。历史覆盖为 2021-12-26 至 2022-01-31（UTC）。

37 条行为的类型均为 contribution，未补造 star/fork 行为。5 条文件关联全部保留 `feature_role=outcome_only`，涉及历史 Issue #9471、#9488、#9478；这些来自解决 PR 的文件交集，应按可用时间用于结果监督。训练时仍需处理时点过滤和数据划分。

这批历史 Issue 与两份库当前候选身份的交集为 0，不能据此宣称已给一万多条候选补齐当前任务文件映射。它提供一个仓库的真实代码、作者和历史任务关系，可用于后续输入/图构建联调。接下来需要按实际候选仓库补采代码与贡献历史，并分别记录当前正文路径证据、历史解决证据和未确认项；完整 CODER 推理仍需要模型实现和训练权重。

## 验证与恢复

运行记录位于 `data/model_history_import_20261025_v1/`：

- `snapshot/`：自包含、来源已校验的输入包。
- `validation.json`：来源校验结果。
- `oss_mentor.before.sqlite3`、`oss_mentor_web.before.sqlite3`：两份业务库的 SQLite 一致性备份。
- `*.staging.sqlite3`：导入演练副本；同一快照重复导入验证通过。
- `import_report.json`：实际导入状态、实体计数、备份路径、业务表前后逐行散列及完整性结果。
- `final_readonly_check.json`：最终只读查询结果。
- `import_verified_history.py`：本次备份、演练、导入和比对的操作脚本，已有输出时拒绝覆盖，保留作复现依据。

两份库的 integrity_check 通过，外键错误为 0；所有非 `midterm_dataset_*` 表的行数和内容散列均保持一致，原候选、用户、画像和反馈未改变。

原有 DatasetStore 测试直接运行时，仓库合成 fixture 的 CRLF 换行造成内容校验和不一致。已确认各文件转为 LF 后均符合原散列；本次使用现有确定性生成器在本轮输出目录重建测试 fixture，再执行同一套 12 项测试，全部通过。业务导入使用的是另行验证的真实数据包，不依赖合成 fixture；仓库 fixture 和校验规则均未修改。

本次复用了离线数据，没有新增网络请求、模型下载或训练。
