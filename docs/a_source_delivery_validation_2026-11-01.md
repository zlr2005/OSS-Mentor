# A 源码整理验证结果（本地，未提交）

本次完成第一项的本地整理：以 main `18fd830a9cc343d70ef4a5e54204bcf4b58750a3` 为依赖基线，明确加入 38 个 A 采集模块、入口、测试及交付说明文件，得到 243 个有 SHA-256 记录的文件。

包位置：`tmp/a_source_delivery_20261101_v1/`；独立源码位于其 `checkout/`。详细使用方式见 `docs/a_source_delivery_2026-11-01.md`。

## 实际验证

运行环境：Windows / Python 3.13.9（Anaconda）。声明支持 Python 3.11+，本次未另行运行 3.11。

| 检查 | 结果 |
|---|---|
| 包内文件字节、大小和 SHA-256 | 243 个文件全部通过 |
| 论文解析、Git/GitHub 历史、身份/改名、来源篡改、Issue 关联、B 数据导出与 011 导入、流水线 CLI | 69/69 通过 |
| 既有 snapshot、DatasetStore、GitHubClient、RawStore 回归 | 31/31 通过 |
| SQLite migration check | 13 个迁移通过 |
| 公共 JSON contract fixture check | 6 个 fixture 通过 |
| 相对 main 的差异补丁 | git apply --check 在此前隔离导出的 main 上通过，未实际应用 |
| collect_github_history / collect_git_history / import_gfibot 的 --help | 在包内源码环境运行通过 |

所有测试使用包内 main 基线与明确覆盖文件，未借用当前本地 API、UI、排序、画像或候选刷新改动。当前本地 GitHubClient 的 HTTPException 重试修改没有纳入本包；现有采集流水线及所列回归在 main 原客户端上通过。

机器结果：`tmp/a_source_delivery_20261101_v1/verification.json`；详细输出：`verification-1.log` 至 `verification-4.log`。`source-manifest.json` 的 prepared 状态表示打包阶段，不覆盖 verification.json 的最终 `source_delivery_verified` 结果。

## 本次没有执行的事项

- 没有暂存、提交、推送、开 PR 或联系队友。
- 没有发起真实数据采集，没有下载模型或论文大文件，没有训练模型。
- 没有修改业务数据库或混入本地候选 012/013 迁移。
- 原始 API 缓存、Git 对象库、论文归档和私有凭据没有打包；真实大批次复现仍需提供这些外部输入。
- 本次只完成源码整理与离线验证，不代表 C 数据签收、线上库存刷新或全组验收已经完成。

## 待正式交付时

直接按 `source-manifest.json` 的 overlay_files 审阅待交付文件，检查固定 main 的差异。不要使用 git add . 或混入当前工作区所有改动。GitHub 正式交付留待用户后续指示。

源码包自身带 main 的依赖快照，因此本地查看和测试无需修改当前分支。打包目录没有 .git；若在其中执行 git status，会向上找到原仓库，不能把它当作独立 Git checkout 操作。
