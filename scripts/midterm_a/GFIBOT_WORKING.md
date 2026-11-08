# GFI-Bot 论文历史解决记录工作库

本批保持 working / frozen=false。它是论文来源的独立暂存库，未接入线上推荐，
不是原版 CODER 数据，也不替代此前的 pytest 用户—文件贡献数据。

## 本次实测（2026-10-15）

| 项目 | 数量 |
|---|---:|
| 完整读取的来源文档 / 去重后的历史 Issue | 159,919 / 159,919 |
| 实际包含仓库 | 99 |
| 解决者来源 login 身份 | 18,864 |
| PR 类型解决记录 | 113,327 |
| commit 类型解决记录 | 46,592 |
| `resolver_commit_num=0` 的项目新手标签 | 17,147 |
| 精确重复 / 拒绝 / 缺失解决者 / 缺失解决时间 | 均为 0 |
| 本批文件贡献边 / 当前开放候选 | 均为 0 |

这些是实际下载包的统计；不把论文报告的 100 个项目代替实测 99 个。
来源创建时间范围为 2001-05-05 至 2022-06-17，解决时间为 2010-02-09 至 2022-06-18。
有 41 条来源创建时间早于 2008 年，例子来自 phpmyadmin 历史任务，可能涉及平台迁移；
本轮保留来源时间并单独列出，未将其改写为 GitHub 事件时间，后续时间划分需核查。

工作库：`data/midterm/working/gfibot_resolved.sqlite3`（约 183 MB）。
导入报告：`data/midterm/validation/a/gfibot_resolved_working.json`；
独立 SQL 质量检查：`data/midterm/validation/a/gfibot_resolved_quality.json`。
完整成员 ZIP CRC、gzip 校验和 SQLite integrity_check 均通过；独立 SQL 检查无错误特征角色记录。
新增 8 项测试通过，A 专属全套 72 项测试通过。
本包不包含 `pytest-dev/pytest`，与此前 3 个 pytest Issue 的重叠为 0；
后续应在论文包实际仓库中选取代码补采对象，不能假设现有 pytest 代码可以直接关联这批 Issue。

## 来源与范围

- 来源：[GFI-Bot Zenodo 6837711](https://zenodo.org/records/6837711)，版本 0.0.3。
- 作者：Hao He、Haonan Su、Wenxin Xiao、Runzhi He、Minghui Zhou，2022。
- 数据许可：CC BY 4.0；作者程序的 GPL v3 许可与数据许可分开记录。本模块自行实现读取，未复制作者训练程序。
- 本轮选取 `dump/gfibot/resolved_issue.bson.gz` 完整成员，记录历史 Issue、解决者、解决时间及 PR/commit。
- 未下载完整 2.46 GB ZIP，因而不能宣称验证了整包 MD5；本轮验证成员的 ZIP CRC、完整 gzip 校验、BSON 边界，并保存 SHA-256。
- `dataset.bson.gz` 任务特征成员尚未导入。解决记录本身没有完整 Issue 标题和正文。

## 运行

需要 PowerShell 7、Python，以及 PyMongo 提供的 `bson` 读取器；当前 Python 环境已有 PyMongo。
不需要启动 MongoDB 服务，不执行来源中的程序或 pickle，不修改项目公共依赖。

在仓库根目录运行：

```powershell
& scripts/midterm_a/download_gfibot.ps1
$env:PYTHONPATH = 'src'
python scripts/midterm_a/import_gfibot.py --database data/midterm/working/gfibot_resolved.sqlite3 --report data/midterm/validation/a/gfibot_resolved_working.json
```

下载器验证 HTTP 206、Content-Range、每块长度，重跑复用完整分块。
导入器要求新输出路径，不覆盖已有工作库。中途失败库标记 failed，不能作为已完成数据消费；
重新导入需指定新库路径。下载断点恢复与数据库断点恢复是两件事，本脚本只支持前者。

原始来源位于 `data/midterm/sources/gfibot/6837711/`，保留官方元数据、ZIP 尾部、范围分块与提取后的 gzip。
原始大文件和 SQLite 库受现有 `.gitignore` 排除。不要把它们提交到 Git。

## 数据边界

- `paper_source.status=completed` 才可读取；最终汇总包含 lifecycle/frozen、来源和许可。
- `paper_resolution` 按大小写归一后的仓库名和 Issue 编号去重。精确重复计数；冲突直接失败，不能静默覆盖。
- 每行保存原文档 ID、未压缩 BSON 偏移/长度、SHA-256，原始事件仍保存在 gzip 内，可回查。
- `resolver_key` 为 GFI-Bot 来源命名空间中的 login，不能当作已确认的 GitHub 数字 ID 或直接与自采身份合并。
- 解决者、解决时间、PR/commit、`resolver_commit_num` 均为 `outcome_only`，不能当作推荐时已知特征。
- 历史结果按作者数据记录，未逐条实时重验；未知 PR/commit 类型保持 unknown。
- 没有新增文件贡献边、当前开放候选、训练划分或模型。Issue 数量增加不等于完整训练数据目标已经完成。
- RecGFI、GFI-Bot、PFIRec 有共同来源，后续合并时必须跨来源按 Issue 去重，并检查时间泄漏。

## 后续

取得任务特征/正文数据，按 Issue 与历史时点关联；核查与 pytest 等自采数据的重叠。
在确认来源身份、标签定义和特征时间后，再由 C 生成实验输入；用户未要求前不冻结。
