# B 交付 · 用 v3 历史贡献生成 Adapter 画像

数据版本为 `temporal_xy_2024_2026_v3`，GitHub 交付提交为 `eb35864e`。B 的交付目标是给每个「GitHub 数字 ID × UTC 截止点」生成一份可回放的历史画像，供 C 训练离线 Adapter。B 可以立即开始画像生成，C 可以同时实现数据读取和训练流程。正式训练使用双方验收通过的冻结画像。

[GitHub 交付目录](https://github.com/zlr2005/OSS-Mentor/tree/eb35864e/datasets/temporal_xy_2024_2026_v3) · [现有画像与 CODER 契约](https://github.com/zlr2005/OSS-Mentor/blob/main/docs/profile-coder-next-stage.md) · [C 的交付说明](handoff_C_temporal_xy_v3.md)

## 1. 输入和关联键

| 文件 | B 的用法 |
|---|---|
| `train.jsonl`、`validation.jsonl`、`test.jsonl` | 每行是一个开发者截止点组。读取 `github_numeric_id`、`cutoff_at`、`x_contribution_keys` 和 `split` |
| `file_contributions.jsonl` | 按 `repository, commit_sha, path` 查找贡献，读取作者、`event_at`、内容哈希和 `sources` |
| `summary.json` | 核对切分与规模。训练 1,193 组，验证 268 组，测试 169 组 |
| `source_manifest.json` | 核对两个来源的 manifest SHA-256，按 `source_roots` 解析各条来源的相对路径 |

GitHub 分支提供索引和元数据。读取代码字节、证据或 Issue 历史文本需要团队内部提供相同版本的来源。`ab` 指向 `datasets/b_real_contributions_v0.2`，`backfill` 指向 `data/midterm/working/advanced_adapter_backfill_2024_2026_v1`。本地 `data/midterm/working/merged_ab_history_2024_2026_v3` 是生成公开索引时使用的合并包。跨机器可重映射来源根目录，文件内容及清单哈希保持一致。

## 2. 从 X 生成画像

1. 以 `(github_numeric_id, cutoff_at)` 作为画像联接键，UTC 时间解析后比较。B 需要覆盖三个集合的 1,630 组，并保留原 split。一个开发者可以有多个季度画像，一个季度画像可以服务该组的多个 Y。
2. 对每个 `x_contribution_keys` 三元组查表，确认贡献的 `github_numeric_id` 与组一致、`event_at < cutoff_at`、`content_available=true`。按三元组去重，多个来源和窗口作为证据保留。解析 `sources` 中相应 `content_location`，按 `source` 选择根目录，按 SHA-256 核验内容字节。无法解析的记录写入失败清单，记录该组缺失了哪些 X。
3. 把合格记录转换成 B 的贡献证据输入，复用 `src/oss_mentor/profiling/evidence.py` 的 `interpret_github_contributions` 与现有画像融合规则。首轮可以基于现有代码、测试、文档和贡献路径规则生成基线画像。需要语义证据时消费 C 提供的同版本 CodeBERT 缓存，按文件内容哈希和编码配置关联。以固定规则覆盖全部集合，凡是需要从数据拟合的统计量只在训练期拟合。
4. 按 `config/midterm_profile/questionnaire_v0.1.json` 的 `feature_names` 顺序输出 23 维。每个维度都保留证据引用和解释。未知维度使用 `values[i]=0.0` 与 `observed_mask[i]=false`，已观察的零分使用 `observed_mask[i]=true`。历史问卷、兴趣和锁定位只使用对应截止点时可验证的记录，缺失项保持未知。路径代理特征的含义按 B 的规则说明写清。
5. X 构建只使用截止点前的输入。Y、关闭事件、目标 PR、候选对照、截止点后的问卷或仓库信息留在相应的标签或审计流程。现有 `profile_previews.jsonl` 可用作联调参考，正式画像按合并后的 v3 X 重算。v3 使用 commit 发生时间，首次公开可用时间尚未逐条核实。输出需保留该时间口径，不能把采集观测时间回填成历史公开时间。

## 3. 给 C 的冻结产物

建议输出 `profiles.jsonl`、`profile_manifest.json` 和 `profile_failures.jsonl`，具体离线 schema 名称由 B、C 对齐。每个画像至少包含以下字段。

| 字段 | 约束 |
|---|---|
| `github_numeric_id`, `as_of`, `split` | `as_of` 对应 X/Y 行的 `cutoff_at`，数字 ID 与截止点联合唯一 |
| `profile_key`, `profile_version` | 稳定标识与可复现版本，版本绑定截止点、X、规则和编码版本 |
| `profile_state` | 审核后为离线 `research_frozen`。线上评分按既有 `confirmed` 契约另行接入 |
| `feature_schema_version`, `feature_names` | 与 C 的 Adapter manifest 完全一致，名称和顺序均固定 |
| `values`, `observed_mask`, `locked_mask`, `evidence_refs` | 各 23 项，数值有限且范围符合现有契约。证据与审计字段留给追溯 |
| `developer_key` | 仅在与 C 的训练映射可靠对齐时填写。无法映射时为 `null`，供 C 走冷启动 |

清单记录 v3 来源和输入哈希、B 规则提交或版本、可选 CodeBERT 编码版本、按 split 交付及失败数量、各维覆盖率、全未知画像数。C 用数字 ID 与截止点联接离线样本，线上身份和训练映射按现有接口核验。

验收时检查 23 维顺序、重复联接键、证据归属及 `event_at < as_of`。抽查同一账号的不同季度，较晚的贡献只能出现在它发生之后的截止点画像里。给 C 先发字段契约和一小批标明预览状态的画像用于联调，审核完成后再提供冻结版本与哈希。
