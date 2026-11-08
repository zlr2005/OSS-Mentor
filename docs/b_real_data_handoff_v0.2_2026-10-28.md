# A → B：真实数据接入优化 v0.2

本版针对 B 已完成 synthetic-a-v0.1 → snapshot/011 → ProfileEvidence 联调的现状，补齐真实批次的读取与 SQLite 桥接。不修改 B 的画像逻辑或 ProfileEvidence 类型，不新增画像评估迁移。

推荐目录：`datasets/b_real_contributions_v0.2/`；dataset_version=`b-real-contributions-v0.2-20261028`。v0.1 保留；v0.2 manifest 记录父版本和父 manifest SHA-256，schema 如实保持 b-profile-evidence-v1。

## 本地验收结果

独立校验 delivery_verified；Python 隔离模式（-I）下，两次以 chunk-size 500 和 777 导入均 completed 25255/25255，同一 batch_id，实体数量一致。SQLite integrity_check=ok，外键错误为 0。12 项接入测试通过，包括幂等、失败检查点恢复、版本冲突、导入前校验、无关表保留和质量统计篡改拒绝。

10,473 个 v0.1 数据/证据/许可资产的字节哈希保持不变；仅新增兼容工具、索引及更新说明/统计。当前 manifest SHA-256 为 `790e73a1b050ae9055a187e8fa944bc483d1170a92670f3fc0b78a105de1d044`。详细本机验收记录在 data/b_delivery_v02_validation_20261028/acceptance.json，数据库不随 Git 提交。

## 相比 v0.1 的变化

- 真实记录和代码不变：309 仓库、3,865 条贡献、3,865 个代码版本（3,715 可用、150 缺失）、1,325 个规范身份 key。
- reader.py 按旧 snapshot 的九组实体返回 records；提供 identity_evidence_location 兼容别名，保留全部 evidence_locations；read_code 校验代码，缺失时返回 None。
- import_011.py 配合原字节 011 SQL 导入既有 midterm_dataset_* 表，支持检查点、幂等、失败恢复和版本不可变。内容与证据字节存于 midterm_dataset_asset。
- developer_contribution_index.jsonl 提供每个身份的贡献 key、仓库 key、可用/缺失代码计数；1,133 个身份有选中文件贡献，192 个只有抽样元数据，不能凭后者推断文件贡献画像。
- 明确提供空 issues、issue_file_links、weak_interest_behaviors 表，不混入旧合成样本或 outcome-only 结果。
- 校验器核对所有表均列入校验和清单，并复算关键质量统计。

## B 可直接运行

从仓库根目录，只需 Python 3.11+ 标准库：

```powershell
python datasets/b_real_contributions_v0.2/verify.py
python datasets/b_real_contributions_v0.2/import_011.py --database data/midterm/b_real_v02.sqlite3 --chunk-size 500
python datasets/b_real_contributions_v0.2/import_011.py --database data/midterm/b_real_v02.sqlite3 --chunk-size 777
```

校验预期 delivery_verified。两次导入返回同一 batch_id、completed、checkpoint=total_items 和相同计数；真实批次不再是合成 fixture 的 19/19。

本工具仅创建/写入已有 011 表，不操作画像、问卷、推荐或用户表，不接入老师数据库，不需要新的迁移编号。

## 接入边界

旧 DatasetStore.import_snapshot 针对原始快照 schema，本目录不能伪装成旧 source schema。请改用本包 import_011.py 导入 normalized 数据，再复用 B 已有 011/payload_json 读取路径。数据库注册行保持真实 normalized schema；如果 B 入口写死旧 schema，还需 B 明确接受本版。

也可调用 reader.load_records(root) 获取 (manifest, records)，再接 B 的 ProfileEvidence 适配器。reader 不输出技能评分或画像结论。B 的实现不在本地，本轮不宣称 B 侧端到端验收通过。

event_at 为 committer 时间，author_at 和 observed_at 单独保留；未确认身份不升级，缺代码不填补。账号 verified 可能包含机器人，不是实名或能力认证。source_location 指向明确标记的来源投影，完整原始 API 归档在 A 本地；许可文本和代码保留原字节。

## 尚未交付的部分

本批是所有候选仓库的有界贡献样本，不是全历史或所有目标用户的画像数据。star、fork、拥有仓库等真实弱兴趣行为尚未采集；2,218 条 project_behaviors 全部为 contribution。

B 旧反馈中的共享测试已改为发现 db/sqlite/*.sql，同时保留业务字段断言，不再写死到 010。本地已有 012_candidate_lifecycle.sql，B 计划的 012_midterm_profile_assessment.sql 编号仍需 B/D 协调；本版不重命名已应用迁移。

## 手动提交命令

本轮不代为提交或推送，不生成压缩包。只需提交 v0.2 目录、最新说明和共享测试修复，无需提交 v0.1 或本机数据库：

```powershell
git add -- datasets/b_real_contributions_v0.2 docs/b_real_data_handoff_v0.2_2026-10-28.md tests/test_candidate_refresh_report.py
git diff --cached --stat
git diff --cached --check
git commit -m "Improve B real data handoff with resumable 011 import"
git push -u origin zlr/midterm-a-data
git rev-parse HEAD
```

推送后请 B 用该 commit 重新运行 ProfileEvidence 侧回归，重点检查 schema 入口、未确认身份、缺代码和证据投影的解释。
