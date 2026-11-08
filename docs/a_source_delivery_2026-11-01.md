# A 采集与来源校验源码交付（本地待提交）

本项只整理采集流水线代码。未执行 git add、commit、push，未修改业务数据库、网页、公共 API 或模型。正式数据包沿用已交付版本，不重复打包。

## 交付内容

| 能力 | 实现与入口 |
|---|---|
| GitHub 历史补采、固定 commit 代码、许可与目录证明 | datasets/github_history.py、code_languages.py、path_tree.py；collect_github_history.py |
| 本地 Git 对象历史、作者/提交者、改名 | datasets/git_history.py、git_objects.py；collect_git_history.py |
| 身份和路径稳定 key | datasets/identity_map.py；由 Git 历史采集器调用，未知身份不推断合并 |
| API/对象来源、代码字节与哈希核对 | datasets/source_validation.py、git_source_validation.py；通过 snapshot.validate_snapshot 调用 |
| 已解决论文记录解析与独立库导入 | datasets/gfibot.py；import_gfibot.py |
| Issue—PR—commit—文件证据补充 | datasets/issue_links.py、connector_cache.py；enrich_issue_links.py、import_connector_reads.py |
| B 真实贡献聚合、来源投影与 011 接入工具生成 | datasets/b_delivery.py；export_b_delivery.py、upgrade_b_delivery.py、verify_b_delivery.py、b_delivery_reader.py、import_b_delivery_011.py |
| 有界批次采集与失败仓库修复 | collect_b_real_pilot.py、collect_b_candidate_repositories.py、repair_b_repository_coverage.py |

以上路径分别位于 `src/oss_mentor/datasets/` 和 `scripts/midterm_a/`。已有 main 的 normalization、snapshot、DatasetStore、011、fixture、弱兴趣工具作为依赖一并保留。

## 环境与离线验收

- Python 3.11+，仅使用标准库；无需安装 PyTorch、Transformers 或 BSON 第三方包。
- 本地 Git 历史采集额外需要 Git CLI 和已有对象库；准备交付包也需要 Git CLI。
- 数据存储使用 SQLite。离线单元测试使用合成证据，不请求 GitHub，不需要凭据。
- 本次固定基线为 main `18fd830a9cc343d70ef4a5e54204bcf4b58750a3`。

从原仓库根目录执行（output 必须是尚不存在的新目录）：

```powershell
python scripts/midterm_a/prepare_source_delivery.py --output tmp/a_source_delivery_20261101_v1
python scripts/midterm_a/verify_source_delivery.py tmp/a_source_delivery_20261101_v1
```

交付目录包含：

- `checkout/`：固定 main 源码依赖，加上明确列出的 A 源码和测试；不读取本地其他未提交业务改动。
- `source-manifest.json`：基线 SHA、覆盖文件清单、逐文件 SHA-256、加入/修改状态。
- `a-source-changes.patch`：相对基线的文本差异，用于本地审阅；不自动应用到原仓库。
- `verification.json`、`verification-*.log`：独立目录内的实际测试结果。manifest 的 prepared 状态仅代表已打包，以 verification 为验收结果。

接收人可将整个目录复制到另一位置，然后使用 checkout 内的校验脚本：

```powershell
python <交付目录>/checkout/scripts/midterm_a/verify_source_delivery.py <交付目录>
```

验证器先核对打包源码字节，再运行采集/来源/身份/关联/导入测试、相关既有测试、迁移与契约检查。它只执行上述固定离线检查，不执行批量采集脚本。日志与测试临时数据库均写入交付目录。

## 复现真实数据的方法与输入边界

进入 `checkout/` 后，先在当前 PowerShell 设置 `$env:PYTHONPATH='src'`。每个 CLI 的准确参数以 `python scripts/midterm_a/<脚本>.py --help` 为准。

1. GitHub 小批历史：collect_github_history.py 指定 repo、until、source-dir、output、commits、files-per-commit。默认只重放已有来源缓存；新输出目录不得已存在。显式 `--allow-network` 才允许该入口补请求。
2. 本地 Git 历史：collect_git_history.py 还需要 git-dir、anchor、identity-map、working-dir。Git 对象与 API 缓存属于外部输入，代码包不会自动下载它们。
3. 论文数据：import_gfibot.py 需要已有的 GFI-Bot 来源目录，包含其校验所需归档/元数据；传入全新的 database/report 路径。这里导入的是历史解决记录，不会成为当前开放候选，也不等于原版 CODER benchmark。
4. 快照验证：`python scripts/midterm_a/validate_snapshot.py <快照目录>`；真实来源和固定代码字节一并核对。
5. 011 导入/重试：`python scripts/midterm_a/dataset_pipeline.py --database <独立新库> import <快照目录> --chunk-size 100`。重复执行可核对幂等与检查点；不要用业务运行库做验收。
6. 最终 B/C 数据包继续使用各自目录的 verify/reader/import 工具；原始快照、B normalized 包与弱兴趣数据库是不同 schema，不应混用入口。

有界批次采集器和修复器属于高级手动入口，可能读取本机 `gh` 的登录凭据并发起真实 GET；不能因有 `--help` 就把它们当作默认离线运行入口。本次没有执行这些采集。原始来源缓存、论文归档、身份映射运行文件和历史对象库不在源码包内；没有这些输入时可以完整运行离线测试，但不能声称重放了真实大批次。

## 整理边界与已知限制

- 没有纳入本地 API/UI、推荐排序或画像业务修改，也没有引入本地 012_candidate_lifecycle/013_candidate_identity_lookup，避免与 main 的画像迁移混合。
- 当前库存刷新、教师库 SQL 计划及过程性筛选脚本不在本次最小源码交付范围；这些属于后续库存/来源交接，不影响本包核心采集依赖。
- 原始贡献只归属于实际修改文件；未确认作者、缺失代码、许可证据不足、未知时间均保留边界。
- Issue 关联功能有离线测试与小批历史记录，不代表最终贡献包已具备完整映射，更不代表模型训练完成。
- 本包只完成第一项的“源码可审阅、可独立验证”准备；接收方签收与 GitHub 正式交付尚未进行。
