# 第二项：A → C 模型数据接入核查

日期：2026-11-01。只读检查及本地记录；没有提交、推送、发消息、下载模型或运行编码/训练。

## 结论

A 已交付的数据足以准备 C 下一阶段代码文件编码的输入，尚不能据此宣布正式模型训练数据就绪或 C 已签收。

需要修正此前过宽的缺项判断：完整 Issue—文件映射并非 C 当前画像 residual adapter 的直接输入；不能在 C 未确认训练目标之前，将“全量映射补采”列为 A 必须马上执行的工作。

## 使用的 C 契约及最新状态

远程 PR #24 已于 2026-11-01 合并，merge commit 为 `b337ee5cdb9c5f8e6e55d22a69e1ecf2a8141de6`；本次按其固定实现 Head `8b8f8656ae78ea1e808f9b507c3aa2ced21470f4` 读取代码和说明，没有更新本地 main。

- `models/code_encoder.py` 的 CodeDocument 只需 `file_version_key: str` 和 `content: str`。
- `CodeEmbeddingArtifact.encode` 还需 source_version、artifact_version、tokenizer 配置与 C 提供的实际 encoder。每个文件的 UTF-8 内容哈希写入 row_mapping。
- `docs/profile-coder-next-stage.md` 要求 A 冻结上述输入后再执行正式编码；当前计划 CodeBERT 固定 revision 为 `3b0952feddeffad0063f274080e3c23d75e7eb39`。
- 画像残差模块消费 B 的 confirmed 23 维特征、mask、画像版本及可空 developer_key，基础 Issue 向量保持冻结。它不直接读取 A 的贡献 JSONL 或完整 Issue—文件图。
- PR #24 有 B、D 的身份/画像契约 APPROVED 记录。这些是 B/C/D 契约验收，不等于 C 已验收 A 的最终真实数据包；本次 PR #25 discussion 查询为空。

远程依据：[PR #24](https://github.com/zlr2005/OSS-Mentor/pull/24)、[C 下一阶段约定](https://github.com/zlr2005/OSS-Mentor/blob/8b8f8656ae78ea1e808f9b507c3aa2ced21470f4/docs/profile-coder-next-stage.md)、[编码实现](https://github.com/zlr2005/OSS-Mentor/blob/8b8f8656ae78ea1e808f9b507c3aa2ced21470f4/CODER/src/coder_issue/models/code_encoder.py)。

## 本次全量实查

| 项目 | 结果 | 接入含义 |
|---|---:|---|
| 代码版本记录 | 3,865 | file_version_key 无重复 |
| 可用代码 | 3,715 | 逐份 SHA-256 与原 content_hash 一致，全部可严格 UTF-8 解码 |
| 缺失代码 | 150 | 保留缺失原因；不得填零向量冒充编码成功 |
| 可用内容独立哈希 | 3,490 | 相同内容可以缓存计算，但不能丢失各自 file_version_key 行映射 |
| 空内容 | 1 | C 当前编码器会记为 empty_content 失败，需保留失败映射 |
| 含 CRLF 的内容 | 19 | 应使用 read_bytes().decode('utf-8')，普通 read_text 会统一换行并可能改变输入哈希 |
| 超过默认 16×120 行上限 | 437 | C 默认最多取前 1,920 行；需在正式编码 manifest/覆盖说明中披露截断，不代表已经编码过 |
| 代码 snapshot_at 范围 | 2024-04-21 至 2026-10-25 UTC | 0 份早于旧训练截止 2021-01-01；不能混入旧冻结训练图或历史特征 |
| 开发者实体 | 1,325 | 全部使用 A 的 developer-… opaque key，不是 C 的 login |
| 开发者含 github_numeric_id | 1,298 | 账号关联信息存在，但没有 login 字段；不得把 numeric ID 或 opaque key 当 login |
| 两包仓库交叉核对 | 309/309 | GitHub numeric ID 和大小写规范仓库名一致，无冲突；内部 ID 仍不通用 |
| Issue—文件链接 | 0 | 当前包不能提供文件推荐到具体 Issue 的完整映射 |

机器结果：`tmp/a_c_input_audit_20261101/result.json`；仓库对照表：`tmp/a_c_input_audit_20261101/repository_crosswalk.json`。C 的固定契约和编码器源码也保存在该目录，用于复核。本次检查代码字节和引用兼容性，没有重新执行模型推理或验证完整训练数据。

## A 接下来应补的最小交付

1. **编码输入冻结清单和读取适配器**：保持原包不可变，明确 source_version、包 manifest SHA-256；用原始字节严格 UTF-8 解码生成 CodeDocument；保留全部 file_version_key、缺失/空文件原因、来源和内容哈希，不静默丢掉失败。C 确认是复用既有 B v0.2 作为 source，还是另起命名的代码输入版本。
2. **身份映射规则及验证记录**：A 的 opaque key / numeric ID 与 C 的规范 login 需要显式交叉表。抽查既有 evidence 投影，fields.author 已保留 id、login、type，因此应先离线抽取并核对账号 ID、来源和时间，不必先新增网络采集。尚未全量确认这些来源覆盖全部开发者；缺可靠 login 时保持未关联，不能猜测、按名字相似性拼接或把平台 UUID 当训练身份。是否补查当前 GitHub 身份需另行确定范围，并保留观测时间；当前 login 不自动证明历史 login。
3. **请 C 固定数据用途和时点**：这批新代码可用于当前代码贡献语义分析或新实验协议；不能当旧 2021 实验的历史可见输入。C 若需要严格文件级图、目录结构或 Issue 映射，应先列明输入 schema、预测时点和训练标签，再由 A 有界补采。
4. **正式接收验证**：C 在独立环境读取清单，运行自己的编码产物契约检查，记录接受/失败数量、source_version 与源码 SHA，并明确是否仍缺训练输入。未获得 C 的实际回复，不将第二项标为签收完成。

## 责任分界

- A：代码/来源与身份可追溯、读取适配、输入版本冻结及明确缺失项。
- B：将贡献/语义证据解释为可确认画像，提供 confirmed 23 维快照。
- C：实际 CodeBERT 编码、向量/训练映射、训练时点、adapter 权重和正式评估，确认是否需要全量 Issue—文件映射。
- D：鉴权身份从服务端传入，集成已确认画像与实际模型。

本记录为本地准备的需求清单，没有向 C 发送验收请求，也没有替 C 签收。
