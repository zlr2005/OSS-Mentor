# 给 B：补采数据、研究画像及冻结审核说明

## 1. 接收范围和已确认的上传状态

仓库：`https://github.com/zlr2005/OSS-Mentor`，分支：`zlr/midterm-a-data`。

2026-11-05 已通过远端引用核实该分支提交为 `d08326f29065d68e129aa2f0505e3ff47143b3d4`。本次白名单 20 个文件均存在且内容核对通过；少数文本只存在 Git 换行转换差异。`handoff/bc_supplement_20261105/evidence.zip` 的 SHA-256 为：

`bda10dc0c4fbe59823982adcd188539020c60104c3a9d2cfe602780ed2429838`

ZIP 大小 50,903,023 字节（约 48.5 MiB），含 2,728 个文件；解包后约 209 MiB。拉取整个分支即已包含 ZIP，不需要再单独索取相同压缩包。

当前交付是待审核研究数据，所有 `training_ready` 保持 false。不要把本次交接称为用户确认画像、正式冻结画像或模型效果提升。

## 2. 首次接收步骤

建议使用独立的新克隆，不要在存有其他版本采集数据的目录覆盖解包。Python 3.11+；本批解包和验收脚本仅使用标准库及仓库已有代码，不需要老师库密码、GitHub Token 或模型权重。

```powershell
git clone --single-branch --branch zlr/midterm-a-data https://github.com/zlr2005/OSS-Mentor.git OSS-Mentor-B-supplement
cd OSS-Mentor-B-supplement
git switch -c codex/b-supplement-review-20261105 d08326f29065d68e129aa2f0505e3ff47143b3d4

python scripts/midterm_a/unpack_bc_supplement.py
python scripts/midterm_a/verify_sparse_expansion_handoff.py --version 6
```

解包应报告 `Verified and unpacked 2728 evidence files`。验收应报告 `verified_outcomes=392`、`verified_history_control_joins=384`、来源哈希和证据重放通过。

若提示不同版本已有文件、ZIP 哈希错误或原始证据缺失，应保留报错并检查目录和版本，不要改 manifest、删除校验条件或覆盖旧数据来通过检查。

## 3. B 应先阅读哪些文件

以下路径均相对仓库根目录。

| 目录/文件 | 用途 |
|---|---|
| `handoff/bc_supplement_20261105/README.md` | 交付范围、解包方式和使用限制 |
| `data/midterm/working/sparse_review_profiles_20261105_v1/profile_previews.jsonl` | 本次重建的研究画像 |
| 同目录 `profile_input_index.jsonl` | 每个用户/截止点实际使用的贡献记录与证据路径 |
| 同目录 `profile_evidence_batches.jsonl` | 现有 B 规则产生的证据解释 |
| 同目录 `joined_samples.jsonl` | 结果、候选与画像版本/hash 的联接 |
| 同目录 `manifest.json`、`validation.json` | B 规则指纹、输入来源及检查结果 |
| `data/midterm/working/sparse_expansion_20261104_handoff_v6/outcome_index.jsonl` | 结果身份、时间、Issue 正文及来源 |
| 同目录 `account_coverage.jsonl`、`remaining_gaps.jsonl` | 逐人结果覆盖与仍不足的人 |
| 同目录 `evidence_gaps.jsonl`、`candidate_rejections.jsonl` | 8 条未配齐输入及候选排除原因 |

本批有 392 条补充记录：391 条此前未进入主核实集合的新增结果，1 条原已核实但未进入训练 X/Y、此次恢复的记录。其中 384 条具备代码与可用对照；275 个不同用户/截止点画像覆盖 59 位开发者。同一画像可对应多条 outcome，275、384、392 是不同口径。

## 4. 正确联接与证据定位

- 账号优先使用 `github_numeric_id`；不要只用可更名、大小写不稳定的 login。
- 画像使用 `(github_numeric_id, as_of)` 联接。结果中的对应字段叫 `profile_as_of`。
- `outcome_id` 标识 PR 与 Issue 的结果关系；同一 Issue 可能有多条结果关系，不应重复当成不同任务能力证据。
- 联接表携带 `profile_key`、`profile_version`、`profile_hash`。C 应使用审核后的精确版本，不取该用户“当前最新画像”替代历史画像。
- 输入记录的 `evidence_location` 是项目相对路径，指向实际来源证据。文件版本由仓库、commit、文件路径定位。
- `source_root` 是该结果表所在的包；若同时存在 `evidence_source_root`，原始 API 证据应相对后者解析。
- 复用贡献的来源可能在 `reused_contribution_keys` 或合并表的 `sources` 中。不要把 API 证据文件、实际源码文件和来源根目录混为一项。

ZIP 是本批实际使用证据的子集，原路径保留，原来源 manifest 也保留以便核对指纹。原 manifest 中列出的其他未使用文件可能没有交付。它不保证包含旧基线的全部源码或能重跑所有历史采集任务。

## 5. B 的审核工作顺序

### 第一步：接收验收和时间审计

先保存接收提交、ZIP 哈希、解包/验收输出，确认能打开源码与证据。针对每个画像确认输入属于本人、输入时间严格早于截止点，没有混入目标 PR 后产生的代码、未来贡献或当前问卷。

本批同时存在季度截止点和 PR 创建前一秒的截止点，且包含 2024 年以前的补充结果。不能把它们改成统一的旧季度时间来消除差异。B/C 应共同决定可准入的时间协议和范围，并给出接受/排除清单。

提交时间已检查，但个别 commit 首次公开可见时间未独立证明；部分代码来自截止前创建的未合并 PR，其后续新增 commit 的公开可见时间尤其需要审核。无法确认时保留限制或排除，不把时间代理当成已经完成的公开性证明。

### 第二步：审核 23 维证据语义

预览沿用现有 `src/oss_mentor/profiling/evidence.py`、`assessment.py` 和 `config/midterm_profile/questionnaire_v0.1.json`。维度顺序以 `feature_names` 为准，不自行重排向量。

每个值应能追溯到个人、文件版本、行为和规则。PR 数量、文件数量或语言出现次数不能直接解释成能力更强；仓库生态不等于用户熟练程度，也不等于某个 Issue 必需语言。

必须保留：没有证据时 `value=0`、`observed_mask=false`。不能用均值、最终画像、推测的问卷或人为用户确认补齐历史缺失维度。

### 第三步：改进有证据支持的画像提取

275 个画像目前平均只观测 4.07/23 维，模型输入组合只有 8 种，区分度不足。优先结合实际源码和变更证据审核测试、复现、调试、文档、改动范围等信息是否可被现有规则正确识别。不能仅为增加不同向量而人为赋值。

原始规则和改进规则应版本化，分别报告观测覆盖、未知比例和输入组合数量。增加观测维度也不等于推荐效果改善，后者由 C 的同协议评估确认。

`build_sparse_review_profiles.py` 是此次预览的构建参考，当前输出目录固定。开发新规则时应先改成新的版本化输出目录或接入 B 的现有流程，不在交付原目录直接覆盖重跑。

### 第四步：向 C 交付审核后的版本

建议独立输出正式画像包及审核报告，至少包含：

1. 采用的输入清单、时间协议、接受/排除原因及版本。
2. 每个用户/截止点的正式画像，保留 `feature_names`、值、observed/locked mask、schema、版本、哈希和证据引用。
3. 从本次预览到正式画像的映射及 384 条样本联接清单；排除项单独列出。
4. 观测覆盖、重复向量比例、各用户时间点覆盖和规则变更说明。
5. 画像文件、规则、配置和证据的指纹，以及可复现的导出/校验方式。

这些是建议的交接内容，正式字段和冻结状态以 B/C 已有契约为准。不能只把 `research_preview` 改名或把 `training_ready` 改成 true 就视为完成审核。

## 6. B 不应承诺的内容

原 61 位低历史用户中 57 位达到数量目标是相对采集侧 v6 缺口清单的候选统计；C 还需针对自己的实际训练集重算。当前仍不足的 4 人为 auroratwinkle、blickly、dmihai、sdelicata，合计还缺 5 个不同 Issue 结果。

384 条完整记录之外，5 条缺合格同仓库对照、3 条缺可靠的早期代码。保留原始结果和排除原因，不据此生成负反馈或伪造代码。

本次未取得真实推荐曝光/明确反馈或可靠难度真值。老师库的质量、严重程度等分析字段不能自动替代任务难度。老师库抽查时间与 GitHub 相差 8 小时，历史语言元数据的版本时间语义也待核实，不能直接填入历史已观测特征。

## 7. B 完成后的验收标准

每条准入样本能在精确截止点找到正式画像；值、mask 和维度顺序符合契约；输入可追溯且未越界；新规则有版本和对比报告；C 能按固定哈希联接并复现。剩余无法证明的项明确排除，不要求用假数据把所有计数填满。
