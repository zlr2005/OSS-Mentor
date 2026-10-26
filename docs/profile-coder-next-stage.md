# 增强画像接入 CODER：C 与 B 的下一阶段约定

状态：接口与受控推理路径已实现；画像 adapter 尚无正式训练权重。30～50 对合成试标只用于校准标注指南和验证管线，不作为效果结论。

## 1. 融合位置

23 维确认后画像在 CODER 用户塔末端、Developer–Issue 点积之前融合：

```text
u_base = known_history_user | training_user_population_mean
p = MLP([values * observed_mask, observed_mask])
u_profile = u_base + residual_scale * p
score = dot(u_profile, issue_embedding)
```

现有 CODER、Issue 向量、MiniLM、结构图和 Repository 分支保持冻结。全未知画像显式产生零残差，严格退化为 `u_base`。平台硬过滤仍发生在模型排序之前。

`locked_mask` 和 `evidence_refs` 只参与校验、审计和解释，不进入模型张量。`current_interests` 与 23 维中的三个兴趣位交叉校验，不重复计权。

实现：

- `CODER/src/coder_issue/models/profile.py`：画像契约校验、tensor 化、残差 adapter 及版本化保存/加载；
- `CoderIssueScorer.score_paired()`：在同一候选集合上输出 original/profile 原始分、排名和差值所需身份信息；
- 只有显式配置与基础模型版本匹配的 adapter bundle 时，普通评分才消费增强画像。

## 2. B 保持的字段契约

| 字段 | 约束 |
|---|---|
| `schema_version` | `enhanced-profile-export-v0.1` |
| `profile_state` | 正式评分只接受 `confirmed` |
| `profile_key` / `profile_version` | 非空、稳定、可追溯；`profile_key` 是平台画像 UUID |
| `developer_key` | 可空；仅允许经过服务端验证、可对齐训练映射的 GitHub login，不能复制 `profile_key` |
| `feature_schema_version` | 必须与 adapter manifest 完全一致 |
| `feature_names` | 23 项，名称和顺序必须与 adapter manifest 完全一致 |
| `values` | 载入为 `float32`，有限值，当前范围 `[0,1]` |
| `observed_mask` / `locked_mask` | 各 23 个 JSON boolean |
| `evidence_refs` | 23 个字符串数组 |
| `current_interests` | 与被观察且值大于零的兴趣维度一致 |
| `as_of` | UTC；不得晚于推荐或评估时点 |

未知值必须是 `values[i]=0.0` 且 `observed_mask[i]=false`。不得用 `null`、`NaN`、均值填充或低能力零分替代。

### 身份字段语义

当前 `developer_mapping.parquet` 的 `developer_key` 来自 GFI-Bot
`resolved_issue.resolver`，规范化规则为 `strip().casefold()`。真实映射中的值是 GitHub
login 形式，不是平台画像 UUID。平台当前也从已认证用户的 `github_login` 向评分器传入
该字段。

因此三类身份必须分离：

- `user_id`：登录鉴权与数据归属；
- `profile_key`：平台画像 UUID；
- `developer_key`：可空、经过验证并能与训练映射对齐的 GitHub/训练开发者标识。

没有可靠身份关联时应输出 `developer_key=null` 并显式走冷启动。服务端可维护
`profile_key -> developer_key` 关联，但关联必须记录验证状态、来源、验证时间和规范化版本；
更稳妥的主归属是 `user_id -> verified GitHub identity`，画像只引用该身份。模型侧拒绝
`developer_key == profile_key`，也拒绝画像内身份与鉴权调用参数不一致。

## 3. `coder_original` 与 `coder_profile`

首轮实验使用同一个冻结基础模型和同一个画像 adapter bundle：

- 相同 checkpoint、Issue 向量、候选池、候选顺序、硬过滤、数据切分和随机种子；
- `coder_original` 使用 `u_base`；
- `coder_profile` 只增加画像残差；
- 不分别训练两套完整 CODER，不在测试集选择 adapter 或融合系数；
- 每条记录保存原始点积分、两个 rank、rank delta、coverage state 和画像版本。

## 4. 统一 CodeBERT 编码产物

`CodeEmbeddingArtifact` 生成：

```text
<artifact-version>/
├── embeddings.npy
├── row_mapping.jsonl
├── failures.jsonl
├── encoder_manifest.json
└── _SUCCESS
```

`row_mapping.jsonl` 为每个输入保留 `file_version_key`、`content_sha256`、`status` 和可空的 `embedding_row`。失败文件不会获得伪造零向量。manifest 固定 encoder/revision、tokenizer、分段、token/segment pooling、维度、dtype、成功/失败数及三个内容文件 SHA-256；`_SUCCESS` 固定 manifest SHA-256。

当前计划 checkpoint 为 `microsoft/codebert-base@3b0952feddeffad0063f274080e3c23d75e7eb39`。正式编码在 A 冻结 `file_version_key + content` 的 Issue Release 后运行；当前实现和测试不下载模型。

## 5. MiniLM 与 CodeBERT 的表述

当前正式 `coder-full-search8-v1` 使用冻结的 `all-MiniLM-L6-v2` 384 维 Issue 文本向量。CodeBERT 是下一阶段代码文件/贡献语义编码及受控对照，不能把当前结果改称 CodeBERT 复现。

本项目改变了原论文的推荐对象、数据集和行为定义，因此统一表述为 CODER-inspired Developer–Issue/Repository 推荐；接入 CodeBERT 后仍不是原论文同数据集严格复现。

## 6. 人工标注与模型评估

试标使用 `config/profile_issue_fit_annotation_v0.1.json`。30～50 对全部由两人独立、盲法标注；标注页面不得展示模型版本、分数和排名。试标只检查指南可理解性、无法判断比例、分歧原因和加权一致性。

模型评估额外记录：代码提交、配置哈希、基础模型/checkpoint、adapter 版本、画像产物及 SHA-256、编码 manifest 及 SHA-256、候选池版本和大小、已知/冷启动状态、Issue coverage、original/profile 原始分和 rank、人工标签、双标一致性与裁决状态。
