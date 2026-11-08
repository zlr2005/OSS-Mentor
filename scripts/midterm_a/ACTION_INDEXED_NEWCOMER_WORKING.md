# 新手采集按动作对齐排序键（2026-10-22）

## 诊断

Gravitino 后 Gradio 的首条查询仍触发 Code 158 / TOO_MANY_ROWS；认证成功，未新增任务。
仅把失败仓库后置不能解决读取代价，日期拆分也不保证有效。
本地 `data/midterm/sources/teacher_clickhouse/20261015T161532Z/events_storage.json` 显示排序键为：
`toYYYYMM(created_at), type, action, org_id, repo_id, actor_id, platform`。
此前跨全部 action 的标签查询跳过了中间键 action，而日期不是上述排序键的一部分。
依据 ClickHouse 的前缀索引机制，这会削弱后续组织/仓库条件的裁剪效果；实际优化幅度仍待现场验证。
参考：[ClickHouse 索引裁剪说明](https://clickhouse.com/blog/index-based-pruning)。

## 本地修正

- 新布局 action_prewhere_v2 每条 SELECT 明确一个 action，保留月份/type/org/repo 边界和标签精确匹配。
- 从旧 checkpoint 的 5389 个待查日期范围合并出 343 个无重叠连续区间；不跨已完成日期缺口。
- 明确查询 18 个动作值，opened/labeled/reopened/unassigned/edited 优先；共 6174 个查询范围。
- 动作值是固定发现范围，不声称覆盖老师表所有自定义/非标准动作；不把未查询的动作算作已完成。
- 旧发现的 132 个身份原样带入，历史失败证据和旧 checkpoint 原样保留，由 continuation_of 串联。
- 新计划：`data/newcomer_discovery_20261022_action_v3/plan.json`；离线准备入口 prepare_action_indexed_continuation.py。
- 仍使用原单条 SQL 保护（100 万扫描行、128 MiB、15 秒）、串行及请求间隔、服务器错误停机。
- 老师库仅 SELECT / GET；没有远端写入、建索引、改表、改权限或配置。只在本地生成新计划与新输出目录。

32 项采集器测试与 1 项区间合并测试通过；全部待查区间逐动作精确覆盖、身份继承、SQL 固定模板和预算均校验通过。
终端现在逐条打印实际扫描行数、字节与耗时；只有运行成功后的统计能证明实际改善。

```powershell
pwsh -NoProfile -File scripts/midterm_a/run_newcomer_teacher_collection.ps1 -PlanPath data/newcomer_discovery_20261022_action_v3/plan.json -IgnoreRollingHourBudget -IgnoreRollingDayBudget -ExtendedRun
```

旧计划保持历史可复现性，本新计划启动时不传旧 ResumeDirectory；新输出目录保存后续自己的断点。
