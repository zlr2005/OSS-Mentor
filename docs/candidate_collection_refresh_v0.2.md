# 候选任务采集与刷新 v0.2

## 仓库活跃度

每次同步或刷新仓库元数据时，系统会记录 `pushed_at` 并计算
`maintenance_status`：

- 最近 180 天有仓库推送：`active`；
- 超过 180 天无仓库推送：`inactive`，候选任务退出匹配池；
- GitHub 未返回或返回非法 `pushed_at`：`unknown`，保留供人工复核。

该状态与 `is_archived`、`is_disabled` 分开保存，避免把长期停更误报为归档。
再次检测到近期推送后，仓库可恢复为 `active`。

候选池报告可以合并同步和刷新运行报告，展示 API 请求成本以及失败原因：

```powershell
python -m oss_mentor candidate-report `
  --sync-report data/reports/all_enabled_sync.json `
  --refresh-report data/reports/candidate_refresh_run.json `
  --output data/reports/candidate_pool_v0.3.json
```

本版本面向成员 A，使用现有 10 个启用仓库维护本地 SQLite 候选池。同步和刷新均串行执行；网络请求在事务外完成，每个仓库单独写入，单仓库失败不会回滚其他仓库。

## 安全约束

- 真实同步必须显式提供 `--allow-network`。
- 多仓库同步和全部刷新必须设置当前 PowerShell 会话内的只读 `GITHUB_TOKEN`。
- `--dry-run` 不创建网络客户端，也不发出请求。
- Token 不写入 `.env`、数据库或运行报告。
- JSON 运行报告不包含 Issue 正文、GitHub login 或其他非必要身份数据。
- `data/` 和 SQLite 文件由 `.gitignore` 排除；可提交的 Markdown 报告只有聚合数据。

```powershell
$env:PYTHONPATH = "src"
$env:GITHUB_TOKEN = "<fine-grained-read-only-token>"
```

Token 只需公共仓库 Metadata 与 Issues 读取权限。

## 批量同步

查看 10 个仓库、候选标签和最大请求量估算：

```powershell
python -m oss_mentor sync-candidates `
  --all-enabled `
  --limit-per-repo 20 `
  --dry-run
```

真实运行：

```powershell
python -m oss_mentor sync-candidates `
  --all-enabled `
  --limit-per-repo 20 `
  --output data/reports/all_enabled_sync.json `
  --allow-network
```

可重复使用 `--repo owner/name` 精确选择，也可用 `--wave 1` 或 `--wave 2`。`--all-enabled` 不可与 `--repo`、`--include-disabled` 同时使用。重复同步依赖 `(repository_id, issue_number)` 唯一键更新，不会生成重复候选。

如果 Ecosyste.ms 返回的单个候选已无法通过 GitHub API 获取（404/410），同步会将其记录为 `excluded / github_unavailable` 并继续处理当前仓库，不会因为一个过期候选放弃整仓库结果；运行报告通过 `unavailable_count` 和仓库级 `warnings` 汇总这类情况。

退出码：全部成功为 `0`，普通部分失败或主限额耗尽为 `1`，参数、安全检查或缺少 Token 为 `2`。

## 过期状态刷新

```powershell
python -m oss_mentor refresh-candidates `
  --all-enabled `
  --older-than-hours 24 `
  --limit 500 `
  --output data/reports/candidate_refresh_run.json `
  --allow-network
```

刷新前先检查仓库是否归档或禁用，再检查超过新鲜度阈值或从未验证的 Issue。关闭的 Issue 被排除；已分配、锁定、阻塞标签或关联开放 PR 的 Issue 暂时不可推荐；条件恢复后重新成为 `eligible`；404/410 记录为 `excluded / github_unavailable`。临时错误保留原状态，主限额耗尽时停止后续请求。

## 聚合报告

```powershell
python -m oss_mentor extract-features
python -m oss_mentor candidate-report `
  --output data/reports/candidate_pool_v0.2.json
```

该命令不访问网络，并同时更新 `docs/candidate_pool_report_v0.2.md`。报告包含仓库健康、候选状态、可推荐和新人信号数量、失效条件、仓库/语言/任务类型分布、验证新鲜度和数据质量警告。

若第一轮已验证可推荐任务不足 100，再把 `--limit-per-repo` 提高到 40；仍不足时才进入扩仓规划。
