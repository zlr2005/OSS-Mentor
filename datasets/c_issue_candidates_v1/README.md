# A → C：最终候选数据快照

本目录取自完整业务候选库，只导出 repository、task_candidate、task_skill_requirement；不含用户、会话、反馈或数据库凭据。
JSONL 按 500 行分片，字段与三张现有业务表一致，保留原 ID、特征版本、原始准入原因、警告、核验和到期时间。
这是供任务特征/推荐算法联调的冻结样本，不承诺过期任务当前仍可推荐。stored eligible 不是当前可推荐数；线上使用前必须重新检查状态及有效期。
历史宽准入信息在 feature_evidence_json 和 warnings_json 等原字段中，缺失/other 不应伪造匹配加分。

```powershell
python datasets/c_issue_candidates_v1/dataset.py
python datasets/c_issue_candidates_v1/dataset.py --database data/c_candidate_delivery.sqlite3
```

独立脚本仅需 Python 标准库；新建数据库保留候选相关三表及索引，不含 API/用户等运行时表，不应直接当作完整服务数据库启动。
也可导入 dataset.read_table(root, 'task_candidate') 按原字段读取；table_files 和 table_counts 见 manifest。
309 个仓库可经 repository.github_repository_id/full_name 与 B 的 repository.source_repository_id/full_name 对齐；不能把两个数据集内部 repository_id/key 直接等同。
未包含 CODER 模型、权重、向量、评分真值或完整 Issue—文件映射。
