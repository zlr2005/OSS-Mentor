# 62 个未解决候选仓库的针对性补采

原批次 `data/b_all_repos_20261026_v1/` 已完成 247/309 个仓库；本轮独立目录为 `data/b_repair_62_20261027_v1/`，不覆盖原 jobs.json 或快照。原缓存 c/ 可追加新的成功响应，用于断点复用；原始失败原因另存于新队列 original_failure。manifest 保存原 jobs 的 SHA-256、原成功数量和采集截止时间。

原问题：45 个仓库没有 GitHub 标准 SPDX 声明；8 个在最近 10 次提交中未选中源代码；6 个递归目录树被截断；3 个根目录许可文件名未匹配。

github-history-v3 的修复：

- 许可声明 null/NOASSERTION 明确保留，不再作为唯一采集阻断。仍收集并校验固定 commit 根目录的许可文本。该做法不宣称法律授权、历史 SPDX 分类或可重新发布代码。
- 许可名大小写无关，匹配 LICENSE、LICENCE、COPYING 等前缀。搜索根目录、选中文件祖先目录，并在根目录无许可时检查最多 20 个顶层包目录。当前选择一个匹配的历史文本，并不声称收全多许可证条件。
- 扫描截止时间前最近 100 个 commit，在其首 100 条变更路径中选择最多 10 次含已识别源文件的提交，每提交最多 2 文件。未找到代码时保留真实缺口。
- 不再要求大仓库的递归完整树。对每条选中路径读取非递归根树和子树，逐级核对 SHA；导出 schema=selected-git-paths-v1 的内部路径证明，保留所有真实 API 响应，不伪造完整树或 upstream truncated=false。
- 来源校验器重新验证根树、子树边、最终文件条目、blob 和内容一致性，仍检查作者身份及提交时点。只声明 selected_paths_only 覆盖。

本地 `report.md` 为可读逐仓库进度；`progress.json` 给出本轮成功数、与原 247 个合计的覆盖数及新增实体；`jobs.json` 保存逐条结果和失败原因；`runner_state.json` 与 `network_state.json` 给出运行状态。快照在 s/ 下，以仓库名散列缩短路径。全量结果应按原批次加本轮成功快照去重汇总，不能只看旧报告。

程序使用已有 gh 登录，只读 GitHub，不提交或推送，不生成压缩包，不更改推荐业务库。每仓库失败后再尝试一轮，持续失败明确保留 unresolved。18 项 GitHub history 测试已通过，包括未知 SPDX、嵌套路径、大小写及包内许可名、扩大窗口和篡改路径证明的拒绝检查。

恢复命令（先确认该目录无其他采集进程）：

```powershell
$env:PYTHONPATH = 'src'
python scripts/midterm_a/repair_b_repository_coverage.py --source data/b_all_repos_20261026_v1 --directory data/b_repair_62_20261027_v1
```

当前截止时间沿用 2026-10-26T00:00:00Z，后续从远端读到的身份关联及元数据观察时间仍单独保留。
