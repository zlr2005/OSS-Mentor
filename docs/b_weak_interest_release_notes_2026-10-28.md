# A → B：真实弱兴趣数据交付

本 Release 的 ZIP 附件包含可直接使用的 SQLite 数据库、SHA-256 元数据、逐账号覆盖清单和校验结果。源代码 ZIP/TAR 是 GitHub 自动生成的仓库快照，不包含本次数据库；请下载 `oss-mentor-b-weak-interest-20261028.zip` 及同名 `.zip.sha256`。

- dataset_version：`b-real-contributions-with-weak-8cc664dd7446b2d8`
- 数据库文件：解压后的 `b_weak_interest.sqlite3`
- 记录：star 92,852、fork 64,291、owned_repository 29,645，共 186,788 条。
- 对全部 1,276 个可核验 GitHub User 类型账号作采集尝试：1,272 个通过当前身份核验，1,257 个有数据，15 个确认公开列表为空，4 个当前身份端点返回 404。
- 分页：985 个账号两类列表均完整，286 个仍有后续页，1 个账号分页请求失败；覆盖清单保留这些状态，不宣称全部历史采完。
- Bot/Organization 22 个、未确认身份 27 个保留排除原因。type=User 不是真人或能力认证。
- 完整性、外键、逐条来源校验和采集/导入专项 14 项通过。
- B 固定 Head `f212b360d12beb6cf91bf8fa363fb3fe71c7fb6f` 全体 1,325 个身份适配通过：99,093 条弱兴趣语言证据，87,695 条因语言为空或不支持排除；原直接贡献证据 8,138 条保持不变。

B 请先校验 ZIP 的 SHA-256，再解压到新目录，读取指定 dataset_version，调用现有 `build_profile_evidence_from_a_dataset_store`。原 v0.2 与试采版本仍在同库，不能继续使用旧 dataset_version 期待看到本轮数据。使用新 manifest.created_at 作为本轮验证 as_of 即可。

fork/owned 的行为发生时间仍为 null，仅保留 observed_at；star 使用 starred_at。来源为公开 API 字段投影，仓库语言为观察时元数据。弱兴趣不代表代码能力、模型效果或概率校准。

请 B 核对来源引用、缺失/未知时间、分页限制、未确认身份排除和兴趣特征映射，并在反馈中记录本 Release tag、源代码 commit SHA、dataset_version 与校验命令结果。PR #26 的 ProfileEvidence 来源上下文 P2 仍需修复；本交付不代表 PR 验收通过或允许合并。

完整说明：仓库中的 `docs/b_weak_interest_all_accounts_2026-10-28.md`；解压目录中的 `HANDOFF.md`。
