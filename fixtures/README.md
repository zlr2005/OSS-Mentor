# OSS-Mentor 演示数据

`oss_mentor_demo.sqlite3` 是从本地完整数据库导出的固定、小型、脱敏演示快照，供队员在不访问外部 API 时运行界面、排序和匹配流程。

快照仅保留：

- 每种已有编程语言最多 8 个可推荐候选；
- 候选任务的公开元数据、预计算特征与技能要求；
- `profile_source = 'demo'` 的匿名演示画像。

导出过程会移除全部推荐反馈及反馈事件，并清空 Issue 正文、作者关联、GitHub 数字 Issue ID 和采集警告。数据库不包含 Token、Cookie、浏览器 Profile、原始 API 响应或真实用户画像。

## 重新生成

先按项目 README 生成 `data/oss_mentor.sqlite3`，再运行：

```powershell
python scripts/export_demo_fixture.py
```

可通过 `--source`、`--output` 和 `--per-language` 调整来源、目标与样本上限。更新快照后应运行测试，并确认数据库中不存在反馈和非 demo 画像。

## 使用

先把固定快照复制到被 Git 忽略的运行目录，再通过 `--database` 指向副本。这样既不会覆盖本地完整数据库，也不会因网页反馈修改仓库中的固定快照：

```powershell
Copy-Item fixtures/oss_mentor_demo.sqlite3 data/oss_mentor_demo.sqlite3
$env:PYTHONPATH = "src"
python -m oss_mentor serve-api --database data/oss_mentor_demo.sqlite3
```

运行副本中的反馈仅供本地演示，不应作为需要长期保留的数据。
