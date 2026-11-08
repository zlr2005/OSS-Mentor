# CODER 资源核查及第一批真实数据交接

核查日期：2026-10-15；负责人 A；分支 `zlr/midterm-a-data`。
状态：来源核查与 GitHub 补采小闭环通过，团队验收待进行。

## 官方资源的实际情况

本次固定 [CODER 官方提交 d5f88cf](https://github.com/Ahren09/CODER/tree/d5f88cf76520e0ab0863f4f01f8f7dd37521d589)，
完整树只有 6 个文件，Python 文件为下载辅助脚本和目录检查工具；未发现训练实现、处理后数据或 LICENSE。
GitHub repository API 的 license 为 null。未将上游辅助代码复制到本项目实现中。

[官方 README](https://github.com/Ahren09/CODER/blob/d5f88cf76520e0ab0863f4f01f8f7dd37521d589/README.md)
列出 5 个项目行为压缩包，说明贡献关系需要从仓库 commit 历史获取；发布数据与论文协议也存在差异。
README 指向的 [Google Drive 目录](https://drive.google.com/drive/folders/1-dehBnSc9kBcEKwL9SroEqNMVoLOvU1F)
本次实测 HTTP 404，未取得压缩包。此结果不证明文件永久删除或所有环境均无法访问。

机器可读核查：`config/midterm_datasets/coder_resource_audit_v0.1.json`。
原始证据缓存：`data/midterm/sources/coder/audit_v0.1/`。

离线重新生成核查摘要：

```powershell
$env:PYTHONPATH = "src"
python scripts/midterm_a/audit_coder_resources.py --source-dir data/midterm/sources/coder/audit_v0.1 --output config/midterm_datasets/coder_resource_audit_v0.1.json
```

需重新查询网络时加 --allow-network。新上游文件需要重新人工审阅；不凭旧检查宣称实现完整。

## 本次实际接入

从既有试点仓库 [pytest-dev/pytest](https://github.com/pytest-dev/pytest) 补采：

- 时间截止：2022-02-01T00:00:00Z；默认分支历史的 3 个 commit。
- 每个 commit 最多选 2 个 Python 修改文件。本批实际得到 2 条文件贡献，来自同一 commit/作者。
- 3 棵完整历史目录树、2 份可用代码、历史 MIT 许可和 11 个原始 API 响应均已保存。
- 作者、提交者分别记录；身份关联来自 API 的 author 数字 ID，不推测邮箱相似性。
- 文件内容来自相应 SHA 的 Git blob，并验证 Git 对象 SHA-1 及内容 SHA-256。

三次提交：

```text
9d2ffe207bfea9a8b57452bafb9961aba67edf7a
3aef0b9de6ed071580a63b441d749162c71037de
4b1d9092a8b0efaae0028b939f997ee6588c63cf
```

实际代码为第一条提交中的 `src/_pytest/doctest.py` 与 `testing/io/test_terminalwriter.py`。
空白的 Issue/关联数据保持为空，不编造推荐任务或解决标签。

输入、数据库、导出路径与运行命令见同目录 README 的“第三步”。
逐文件大小、校验和、实体数量、来源与许可均在各包 manifest 中；原始响应属于本地大产物，不进 Git。

## 交接给 B/C/D

B：贡献归属限于被修改文件，不把整仓库当作作者能力；画像解释由 B 完成。

C：这批是 GitHub 历史补采，不是已确认的 CODER 原始实验仓库。
3 个 commit/2 条文件边只用于真实输入接入检查，不足以评价模型。
目录结构和代码在各自 commit SHA 固定；作者时间、commit 时间、观察时间需要按含义使用。
没有历史 star、负样本、冻结划分或 Issue 映射。模型与论文复现状态仍需 C 独立审计。

D：本地独立运行库已初始化并导入本批真实数据，合成样本库与其分开。
未修改公共连接、契约、网页或路由，网页消费接入尚未完成。
新 source schema 是 A 的草案，需按 S0 流程审核，不能自行标为已集成。

人工接收方验收建议：校验导出包 → 抽查贡献对应原始 commit/文件/blob →
在另一数据库导入 → 再次导入确认批次/计数不变 → 对齐时间与缺失含义。
