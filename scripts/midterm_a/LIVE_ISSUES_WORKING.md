# 第一批当前 GitHub Issue 候选工作库

本批完成于 2026-10-15，保持 working / frozen=false，未接入网页或冻结实验。
最新产物为 **v0.3**。v0.1/v0.2 是核查过程中的工作版本，不应叠加计数。

## 本批结果

| 项目 | 数量 |
|---|---:|
| IssuePilot 论文附件提供的仓库种子 | 21 |
| 通过活动状态检查的仓库 | 15 |
| 初始发现的不同开放 Issue | 246 |
| 优先选取并重新读取详情/完整时间线的 Issue | 131 |
| 排除后继项目迁移仓库后的不同 Issue | 223 |
| 最终可推荐状态候选 | 110 |
| 最终候选覆盖仓库 | 14 |
| 有新手或求助相关标签的候选 | 20 |
| 未完成复核、保留但不推荐 | 39 |

最终候选语言分布：Python 29、TypeScript 32、JavaScript 24、C 8、Rust 3、HTML 8、C# 6。
项目主要语言不等于每一条 Issue 具体需要的语言。

## 产物

- 最新工作库：`data/midterm/working/live_issuepilot_v0.3.sqlite3`
- 可读链接清单：`data/midterm/working/live_issuepilot_v0.3/candidates.md`
- 完整正文/状态 JSON：`data/midterm/working/live_issuepilot_v0.3/candidates.json`
- 所有入库 Issue 的筛选证据：同目录 `audit.json`
- 统计、排除原因、来源 SHA-256：同目录 `report.json`
- GitHub 原始响应：`data/midterm/sources/live_issuepilot_v0.1/`
- 批次人工规则：`config/midterm_datasets/live_issuepilot_review_v0.1.json`

工作库复用现有 `repository`、`task_candidate` 等表及 `SQLiteCandidateStorage`，
因此后续可供 D 接入既有候选读取接口；本次没有修改网页配置或现有在线数据库。
每条候选包含标题、正文、标签、原始 GitHub ID、仓库、当前状态、更新时间、
负责人状态、锁定状态、开放 PR 检查结果、核查时间和 24 小时有效期。

## 论文怎么用，GitHub 怎么用

只从 [IssuePilot 附件](https://zenodo.org/records/18363387) 复用 21 个仓库名字。
附件 MD5 为 `6e1bbbe42b2ae66582f00c111902c6a1`，已重新核对；不执行附件程序。
论文中 2025 年的缓存 Issue 没有直接入库。候选内容来自本次 GitHub REST 读取，
不能将论文附件的许可直接套用到新采集的 GitHub 内容上。

本轮用 GitHub connector 读取 REST 资源，原始信封记录 retrieval_method、实际观察时间和 URL。
连接器不提供 HTTP 响应头/状态时保留 null/unknown，不伪造响应信息。
候选表中的 `source_system=github_rest` 表示数据来自 REST 端点；具体读取方式保存在 audit/原始信封。

## 筛选范围与限制

每个活跃仓库读最多 30 条最近更新的开放 API 对象，再补最多 30 条带
`good first issue` 标签的对象。排除 PR，按仓库与 Issue 编号去重。
每仓库优先复核最多 10 条，优先新手/求助标签。这不是完整仓库遍历，也未达到计划中争取 500 条的规模。

最终候选需满足：

1. 仓库未归档/禁用，180 天内有 push，开放 Issue 功能。
2. 重新读取的详情确认仍开放、未分配、未锁定，来源身份与仓库匹配。
3. 完整读取时间线，未发现交叉引用的开放 PR；有下一页必须继续，缺页则不推荐。
4. 正文非空，没有已知阻塞、进行中、问答或讨论标签。
5. 批次检查额外排除公告、法律审查、发布时间问答和大型汇总任务。

Material Design Lite 的当前官方 README 指向后继项目，本轮将整个旧项目排除，
即使其尚未归档且有近期 push，也不纳入任务推荐。
两个归档仓库、三个超过 180 天未 push 的仓库不进入采集；facebook/jsx 的 API 返回迁移信息，
未确认新地址前按来源未核实处理。

这些是**当前状态候选**，不是已人工确认难度的 110 道新手题。
20 条带新手/求助标签，另外 90 条一般候选；仍需根据用户技能、任务清晰度与工作量排序。
没有关联 PR 的时间线证据不保证不存在未关联的工作，也不保证维护者一定接受贡献。

## 复核与重建

现有原始响应 24 小时内可离线重建，必须选择新的输出路径：

```powershell
$env:PYTHONPATH = 'src'
python scripts/midterm_a/live_issue_pool.py plan
python scripts/midterm_a/live_issue_pool.py build --database data/midterm/working/live_issuepilot_rebuilt.sqlite3 --output data/midterm/working/live_issuepilot_rebuilt
```

`plan` 输出需要读取详情和时间线的 Issue 编号。新一批应重新采集仓库状态、发现列表、
Issue 详情与所有时间线页；仅重建旧缓存不会刷新状态。过期缓存不再视作可推荐证据。
当前脚本负责离线规划/校验/入库，网络采集由本次连接器完成，不宣称已配置后台定时刷新。
用户参与前必须再查一次 GitHub。

验证：A 专属 81 项测试及相关候选规则/同步/刷新报告 40 项测试通过；SQLite integrity_check=ok，外键违规 0。
未冻结、未提交、未向维护者发送任何消息。
