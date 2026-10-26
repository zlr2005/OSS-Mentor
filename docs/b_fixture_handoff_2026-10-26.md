# A → B：小型联调 fixture 交付

版本：`synthetic-a-v0.1`；schema：`midterm-a-draft-v1`；`fixture_only=true`。

这是专门构造的合成测试数据，用于 B 联调读取、身份处理、代码缺失处理和画像输入流程。它不是从真实 GitHub 库随机抽出的样本，不能支持真实开发者能力、兴趣或模型效果结论。真实贡献小样本与后续弱兴趣采集另行交付。

## GitHub 中的交付文件

- `fixtures/midterm/a/snapshot_v0.1/`：manifest、records 和自包含内容文件。
- `fixtures/midterm/a/.gitattributes`：保持 fixture 原始字节，防止 Windows 换行转换破坏 SHA-256。
- `db/sqlite/011_midterm_datasets.sql`：数据版本、实体、贡献、代码、行为、导入检查点等增量表。
- `src/oss_mentor/datasets/{__init__,snapshot,normalization}.py` 和 `src/oss_mentor/storage/datasets.py`：校验、归一化、导入导出。
- `scripts/midterm_a/{build_fixture,validate_snapshot,dataset_pipeline,verify_b_fixture}.py`：生成、检查和联调入口。
- `tests/midterm_a/test_snapshot.py`、`test_dataset_store.py`：校验、异常、重复和恢复验收。

提交后使用该次 commit SHA 固定交付版本；本说明不把本地分支名当作已经推送的证明。当前准备分支为 `zlr/midterm-a-data`。

## 数据与字段对应

| B 输入 | records.json 成员 | 数量与关键字段 |
|---|---|---|
| ContributionRecord | contributions | 2；developer_key、repository_key、file_key、file_version_key、commit_sha、event_at、observed_at、behavior_type、author_association_status、evidence_location |
| CodeDocument | code_documents | 2；path、language、commit_sha、content_hash、content_location、availability、exclusion_reason |
| ProjectBehaviorRecord | project_behaviors | 1 条合成 star；developer_key、repository_key、behavior_type、event_at、event_time_unknown、observed_at、source |
| 身份与文件改名 | developers / file_aliases | 2 个开发者（1 verified、1 unconfirmed）；1 条改名 |

另有 1 个仓库、2 个逻辑文件、2 个 Issue、2 条 Issue—文件关联。manifest 时间范围为 2026-09-01 至 2026-10-14 UTC。

统计口径：开发者身份确认率 1/2；贡献作者确认率 1/2；代码缺失率 1/2；弱兴趣行为事件时间未知率 1/1。这些比例都是故意构造的测试覆盖，不代表真实数据质量。逐文件 SHA-256 在 manifest 中；`verify_b_fixture.py` 另输出 manifest 自身 SHA-256 及上述统计。

## B 的运行步骤

从本次交付 commit 的仓库根目录运行，无需额外 Python 包、网络或凭据。建议 Python 3.11 或更新版本。

```powershell
$env:PYTHONPATH = 'src'
python scripts/midterm_a/verify_b_fixture.py
python scripts/midterm_a/dataset_pipeline.py --database data/midterm/b_fixture.sqlite3 import fixtures/midterm/a/snapshot_v0.1 --chunk-size 2
python scripts/midterm_a/dataset_pipeline.py --database data/midterm/b_fixture.sqlite3 import fixtures/midterm/a/snapshot_v0.1 --chunk-size 5
python scripts/midterm_a/dataset_pipeline.py --database data/midterm/b_fixture.sqlite3 status synthetic-a-v0.1
```

预期校验 `status=fixture_verified`；导入 `status=completed`，检查点 19/19。两次导入应返回同一批次且不增加实体。19 是文件加实体的导入工作项数，不是贡献数。

B 可以通过 `load_snapshot(Path(...))` 读取三元组 `(manifest, records, contents)`，或从 `midterm_dataset_*` 表读取 `payload_json`。消费数据库前先确认该版本导入状态为 completed。

## 六类验收场景

| 场景 | 验收方式 |
|---|---|
| 已确认身份 | dev-a / author_association_status=verified（合成状态） |
| 未确认身份 | dev-unknown 保持独立，不依据姓名或邮箱强行合并 |
| 文件改名 | file_aliases 保持同一 file_key，记录 old_path/new_path 和 commit |
| 缺代码 | version-missing 标为 missing，保留排除原因，不制造内容 |
| 部分失败 | test_failure_rolls_back_chunk_and_resumes_after_reopening 注入 SQLite 触发器失败，验证批内回滚、检查点和重开恢复 |
| 重复导入 | test_repeating_import_is_idempotent、test_exact_duplicate_edges_are_removed 验证幂等及精确关系去重 |

失败和重复场景通过确定性测试运行，不需要把损坏数据混入有效主快照。

```powershell
New-Item -ItemType Directory -Force data/b_fixture_test_tmp | Out-Null
$env:TEMP = (Resolve-Path data/b_fixture_test_tmp).Path
$env:TMP = $env:TEMP
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests/midterm_a -p test_snapshot.py -v
python -m unittest discover -s tests/midterm_a -p test_dataset_store.py -v
```

共 21 项测试（9 项快照检查、12 项存储与迁移检查）。未知 star 时间保持 null，不能用 observed_at 代替；outcome_only 文件关联不能作为预测时已知证据。

2026-10-26 本地验收通过：fixture 校验、21 项测试、两次不同 chunk-size 的同批次幂等导入（19/19）。另用 HEAD 干净代码副本加本说明列出的交付文件，重新通过 fixture 校验和全部 21 项测试，确认不依赖工作区其他未提交修改。

## 本次范围与后续

本次先交付合成 fixture、011 迁移及读取/导入验收链路。真实 GitHub 贡献和代码覆盖扩展、真实 star/fork/拥有仓库行为及其质量统计尚待后续数据交付。B 负责解释画像结论，无需重复实现采集器。
