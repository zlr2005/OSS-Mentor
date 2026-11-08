# Advanced Adapter：小样本续采与 B 侧回放检查

2026-11-02。仅本地工作版本，未冻结、提交、推送或发送团队消息。

## 本轮结果

在第一轮满足季度时间窗口的 5 条记录上，复用 B 的现有贡献解释与特征融合规则，生成历史 23 维画像预览。5 份画像涉及 2 位开发者，因此不能据此判断正式训练数据已经充分。

首轮每个 commit 只取一个 Python 文件，得到的 5 份模型输入完全相同，仅观察到源码修改和开源经历。为检查采样偏差，本轮对这些固定历史 commit 增加一个测试/文档文件，优先测试、其次文档；共补到 6 个通过 Git blob 哈希验证且不超过 256 KiB 的文件版本，4 个大文件保留排除记录。

补采后的画像有 4 种不同输入，每份观察到 2～5 维。实际可观察维度为源码修改、开源经历、文档、测试设计和测试。剩余维度保持未知，没有倒填当前问卷或兴趣；现有规则会将部分 changelog 解释为文档贡献，应请 B 复核这种代理特征的适用性。

## 历史 Issue 内容

10 个正样本/对照 Issue 的标题按当前标题和返回完整的改名事件列表回放。后续通过已恢复登录的只读 GraphQL 查询，确认全部 10 条正文的最后编辑时间为空或早于对应截止点，历史正文已核实；历史标签尚未恢复。

公开页面的结构化记录没有暴露 `lastEditedAt` 或 `userContentEdits`，不能据此认定正文从未编辑。GitHub 官方提供这些 [GraphQL 字段](https://docs.github.com/en/graphql/reference/issues)。用户已完成官方设备授权，确认本机账号 zlr2005 登录正常；沙箱内状态检查的网络失败不表示凭据仍失效。全部查询仅为读取，没有修改 GitHub 仓库数据。

工作目录已准备只读 GraphQL 查询与响应导入器：明确最后编辑时间为空或不晚于截止时间时可接收正文；截止后编辑的正文继续保持缺失，需要从版本历史或老师库当时的事件快照恢复。字段缺失和明确的 JSON null 分开处理。恢复标题与正文也不自动等于 C 已接受候选内容格式。

## 交接状态

- A：历史贡献、固定文件、结果关系、时间窗口和历史标题/正文已提供小样本；候选格式及历史标签仍需与 C 明确。
- B：现有规则可复用，画像预览已生成；仍需审核规则并确认正式历史冻结导出。
- C：本轮未生成向量或训练。当前 Python 没有 torch，未运行官方画像自检，不宣称 C 已验收。

画像使用独立 `advanced-pilot-temporal-profile-v0.1` 和 `research_preview`，与训练 `research_frozen`、线上 `confirmed` 区分；正式训练就绪数仍为 0。

## 本地入口

- 第一轮：`data/midterm/working/advanced_adapter_pilot_20261102/`
- 第二轮：`data/midterm/working/advanced_adapter_pilot_20261102_stage2/`
- 第二轮报告：上述目录 `REPORT.md`。
- 工具：`scripts/midterm_a/advance_adapter_pilot_samples.py`。

```powershell
python scripts/midterm_a/advance_adapter_pilot_samples.py
python scripts/midterm_a/advance_adapter_pilot_samples.py --verify
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests/midterm_a -p test_advanced_adapter_pilot_samples.py -v
python -m unittest discover -s tests/midterm_b -p test_github_evidence.py -v
```

6 项新增时间/正文边界测试及 7 项 B 原有贡献解释测试通过。第二轮清单校验覆盖文件哈希、B 规则来源、画像哈希、未知 mask、证据截止时间与固定内容引用；第一轮数据保持不变。
