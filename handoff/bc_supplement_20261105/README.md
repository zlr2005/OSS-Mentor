# B/C 补充数据交接 · 2026-11-05

目标分支：`zlr/midterm-a-data`。数据为待审核研究版本，`training_ready=false`。

- 392 条补充记录：391 条新增、1 条原已核实但未进入训练的恢复记录。
- 384 条有代码和同仓库对照；剩余 8 条有明确缺口。
- 275 份研究画像预览覆盖 59 人，并联接 384 条记录；不是正式冻结画像。
- 原 61 位低历史用户中 57 位达到数量目标，4 位仍不足。未取得真实曝光反馈或可靠难度真值。

## 本目录文件

- evidence.zip：约 48.5 MiB，2,728 个文件，保留仓库相对路径。
- file_manifest.json：压缩包内每个文件的 SHA-256；标记 evidence_subset=true。
- summary.json：包大小和 ZIP SHA-256。
- verification.json：在独立目录、HEAD 版本 src/config 与交接源码组合下的解包验收结果。
- upload_files.json：此次允许上传的精确白名单。

仅交付本批实际用到的代码、画像和证据。保留原始来源 manifest 便于追溯，但没有交付其中所有未使用文件，不能当成完整原始采集缓存。数据库、凭据和模型权重不在包内。重新全量采集或重组其他数据集需要另行提供其输入。

## B/C 使用

建议在该分支的新克隆中执行：

```powershell
python scripts/midterm_a/unpack_bc_supplement.py
python scripts/midterm_a/verify_sparse_expansion_handoff.py --version 6
```

解包器校验 ZIP 和全部文件，遇到不同版本的已有文件会停止，不覆盖它们。

解包后的主要入口：

- data/midterm/working/sparse_expansion_20261104_handoff_v6/
- data/midterm/working/sparse_review_profiles_20261105_v1/

B 审核画像证据、未知 mask 和提取规则后再冻结；C 审计时点策略、筛选困难对照并评估模型。画像仅有 8 种不同模型输入、平均观测 4.07/23 维，仍需改进区分度。提交日期、公开可见时间以及老师库时间语义的限制见 docs/sparse_data_completion_2026-11-05.md。
