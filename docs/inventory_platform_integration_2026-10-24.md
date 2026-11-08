# Issue 库存接入与刷新

本轮把 `data/candidate_inventory_20261024_v1/issue_inventory.sqlite3` 的 10,361 条记录转换为现有 `repository / task_candidate / task_skill_requirement` 结构。原始快照保持只读；默认库 `data/oss_mentor.sqlite3` 和网页库 `data/oss_mentor_web.sqlite3` 都已合入。已有任务 ID、用户、画像、会话和反馈保留，导入前有 SQLite 一致性备份。

验收数量会随有效期和后台刷新变化，最新一次验收结果在 `data/platform_inventory_20261024_v1/report.md` 和 `validation.json`。业务库还包含原有记录，业务库总行数不等于本次库存的 10,361 条。

## 特征与准入

- 任务类型、代码/环境/上下文/协作难度、预计工时、技能要求复用 `task_features.py` 的规则，保存版本、命中证据、置信度和缺失字段。
- 难度及技能是规则推断，不是实际构建或人工验收结果；推荐页面和接口注明估计性质。
- `other` 类型、范围不清、设计未定、缺少技能要求的记录留待复核；字段生成完成不等于批准推荐。
- 新手轨道需要明确入门标签；仅有 `help wanted` 或 `contribution welcome` 不足以放行。
- 当前库存九种主语言均可在首页和画像页选择并填写技能：Python、JavaScript、TypeScript、Java、Go、Rust、C、C++、Groovy。

## 有效期与一致性

`verification_expires_at` 保存原截止时间。有效期取“GitHub 核验后 24 小时”和原截止时间的较早者。缺失、非法、来自未来或已经到期的核验时间不能推荐。导入、重复导入、特征重算都不续期。

`candidate_freshness.py` 和 `task_readiness.py` 对齐数据库候选、规则排名和推荐详情的准入；状态接口的 `matchable_count / newcomer_count` 只统计当前满足条件的记录。`stored_eligible_newcomer_count` 是历史规则资格计数，不是当前可推荐的新手数量。

每次成功状态刷新会同步重算特征和技能。刷新检查当前仓库、Issue、完整时间线、讨论中的疑似认领、引用 PR 的当前状态，以及正文/评论中的 PR 链接。认领疑似文本和提交引用转待复核，不把历史文本直接当作当前所有权。单条时间线上限 20 页、额外 PR 引用上限 30 个；超限或读取失败保持未确认，不直接放行。关闭、分配、锁定、开放 PR 等阻止推荐。

导入以 GitHub 全局 ID 与仓库/编号共同校验；冲突会回滚，不能用旧快照恢复较新的关闭或占用状态。同一仓库/编号的旧记录若缺全局 ID，可以补齐而保留任务 ID。

## 运行

在项目根目录打开 PowerShell：

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python -m oss_mentor serve-api --database data/oss_mentor_web.sqlite3 --use-gh-auth
```

或运行原有 `scripts/run_local_web.ps1`。`scripts/run_github_profile.ps1` 也会在本机安装了 gh 时选择已有 gh 凭据。凭据只保留在进程内，不写入库存、报告或日志。已有 `GITHUB_TOKEN` 时优先使用该运行时凭据。

服务器运行期间默认每 300 秒最多刷新 20 条已经通过初筛且临近到期的记录，提前一小时检查。没有凭据时禁用自动刷新；可以用 `--refresh-interval-seconds 0` 主动禁用，或用 `--refresh-limit` 调整批量。停止服务器即停止后台线程。限流后退避，失败不延长旧证据。

`GET /api/v1/status` 的 `automatic_refresh` 返回启用状态、上次时间、成功/失败数量和安全错误摘要。公开自定义推荐使用 `POST /api/v1/recommendations/custom`；登录用户仍沿用已有 OAuth 配置与 `/api/v1/me/recommendations`。

历史未复核记录不会被后台线程直接放行。要显式推进历史队列，可分批执行：

```powershell
python -m oss_mentor refresh-candidates --from-database `
  --database data/oss_mentor_web.sqlite3 --limit 20 --dry-run

python -m oss_mentor refresh-candidates --from-database `
  --database data/oss_mentor_web.sqlite3 --limit 20 --allow-network --use-gh-auth `
  --output data/reports/inventory_refresh.json
```

普通刷新在当前库记录中挑选，不受早期 pilot 仓库清单限制。真实验收中的首批 3 条刷新使用 25 次 GitHub GET，失败 0 次；运行报告在 `live_refresh.json`。

## 导入与复现

完整转换在本地执行，不需要联网：

```powershell
python -m oss_mentor import-inventory `
  --snapshot data/candidate_inventory_20261024_v1/issue_inventory.sqlite3 `
  --database data/new_platform.sqlite3 `
  --evidence data/growth_projects_20261023_v1/complete_evidence.json `
  --evidence data/newcomer_refresh_20261023_v1/complete_evidence.json `
  --evidence data/inventory_screening_20261023_v1/complete_evidence.json `
  --evidence data/inventory_screening_20261023_v5/complete_evidence.json `
  --evidence data/inventory_screening_20261024_v1/complete_evidence.json `
  --output data/reports/inventory_import.json
```

已经验收的转换副本可复用，避免重复提取所有正文特征：

```powershell
python -m oss_mentor import-inventory `
  --snapshot data/candidate_inventory_20261024_v1/issue_inventory.sqlite3 `
  --prepared-database data/platform_inventory_20261024_v1/staging.sqlite3 `
  --database data/oss_mentor_web.sqlite3 `
  --output data/reports/inventory_import.json
```

复用要求原快照 SHA256、记录覆盖和当前特征版本一致；任务与仓库 ID 会重新映射，不复制副本中的用户数据。每次对已有库导入前都会备份；重复导入跳过相同或较新观测。`candidate_inventory_import` 保存来源散列和导入摘要。

## 验收依据

`tests/test_inventory_integration.py` 覆盖重复导入、短截止时间、非法/未来核验、历史记录保留、较新负面状态保护、身份冲突回滚、缺失 ID 补齐、跨库 ID 映射、特征随刷新重算、失败不续期、提前刷新、新手标签与九语言兼容。

最终完整 Python 回归 720 项通过；画像 JavaScript 14 项通过。实际本地 HTTP 请求逐一验证九种语言都能取得推荐，以及难度、技能、核验时间和有效期字段。两份 SQLite 完整性检查通过，非候选业务表和导入前备份逐行比对保持一致，原采集快照散列不变。

17 个进阶项目路径仍是独立项目池；这次没有把项目伪装成 Issue，也没有完成独立项目卡片接口。
