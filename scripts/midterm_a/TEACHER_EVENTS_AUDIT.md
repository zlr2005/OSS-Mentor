# 老师 events 表：项目可用性初步核查

用户要求：远端只查询，本地处理与导出；严禁修改远端数据库。详见 `TEACHER_DATABASE_READONLY.md`。

## 当前证据

已通过只读查询取得 `opensource.events` 的 112 个字段、存储结构及 3 条无过滤样本。
表为按事件时间月份分区的 MergeTree。字段存在已经确认；有效行数量、仓库覆盖和时间范围尚不能由字段或 3 条样本推断。

## 可利用的内容

| 项目用途 | 来源事件/字段 | 用法与边界 |
|---|---|---|
| 历史 Issue 与当前任务发现线索 | IssuesEvent；repo_id/name、issue_id/number/title、body、issue_labels.name、issue_created_at/updated_at | 生成本地任务记录和 GitHub 复核队列；当前仍可参与必须另查 GitHub |
| 任务解决及 PR 关联 | issue_closed_at、issue_closed_by_pull_request_numbers；PullRequestEvent、pull_merged、pull_merge_commit_sha、pull_merged_at | 构造历史结果证据；必须确认事件类型、关联范围和时点，不能把合并者自动当作唯一解决者 |
| 开发者历史贡献 | actor_id/login、issue_author_id/login、repo_id/name、PushEvent、push_head、push_size、push_ref | 研究用户—项目行为、提交线索；事件操作者不总是代码作者 |
| 技术方向与贡献内容线索 | push_commits.message、Issue/PR 标题正文 | 在本地分析贡献主题；不能把文本关联直接当作能力标签 |
| 社区互动与支持 | IssueCommentEvent、issue_comment_id、issue_comment_author_id/login、body、评论时间 | 分析讨论/帮助；此类事件中的 body 可能是评论，不能统一当 Issue 正文 |
| 兴趣行为 | WatchEvent、ForkEvent、actor_id、repo_id、action | action 语义及实际覆盖需抽查；行为不是完成贡献证据 |

## 不能直接补齐的部分

- 当前字段没有完整文件源码、完整 PR 改动文件清单或每个历史版本的代码。
- `pull_changed_files` 是数量，不是文件路径列表。
- `pull_review_comment_path` 和 `commit_comment_path` 是评论涉及路径，不能充当完整修改路径集合。
- `push_head` 是 push 的头提交；不能代替该次 push 所有 commit 的 SHA 与逐文件变更。
- 因此该表可为 CODER 提供用户—项目/提交线索，但完整用户—文件边及源码仍需 GitHub/Git 补采。

## 数据质量与下一步

初始样本含 1970 年时间、空仓库名和 issue_number=0。部分值可能是缺失值、事件类型不适用字段或异常数据，不能不加区分导入。
当前脚本先在当月按事件类型各取最多 5 行；当月没有该类样本时，再在 2024-01-01 之后、当月之前受限抽样。
不做全表排序或全表计数，也不根据小样本推断全库数量。
抽样指定 UTC 时间输出，避免把服务端字符串时区误当成事件的统一时区。
只导出必要列，正文截取为明确标记的 excerpt，默认不采集提交邮箱。

补充核查入口：`connect_teacher_clickhouse.ps1 -InspectEvents`，用户名密码仅在本机输入且不保存。
仅执行固定 SELECT / DESCRIBE，使用 HTTP GET；每个事实表查询限制时间、读取行数和字节数。
补充样本成功前，本报告不宣称这些字段已形成可训练数据或当前可推荐任务库存。

## 2026-10-16 补充核查进展

连接认证、表结构再次读取成功，确认服务端时区为 Asia/Shanghai。
首次补充核查的 `system.parts` 查询及依赖它的五类样本查询均返回 HTTP 500，未取得有效新样本。
该次只保存了 HTTP 状态，不能据此确定是权限、查询或服务端资源问题。
已修改脚本：不再依赖系统分区表；保存脱敏后的服务端错误详情；部分查询失败时明确报告 `connected_with_query_errors`。
已重新打开本地输入窗口，等待凭据后执行修正查询。此前的 3 条无过滤样本和 112 列结构仍保留。

### 已取得的失败原因

后续脱敏错误记录明确为 Code 158 / TOO_MANY_ROWS：触发查询级 `max_rows_to_read=1000000`。
这能解释后续五类抽样的 HTTP 500；不能反推更早未保存详情的错误原因。
LIMIT 5 只限制返回结果数量，筛选过程仍可能读取大量行；并行批次也可能在检查限制时已经超过设置值。

已在本地调整脚本：显式匹配 `toYYYYMM(created_at)` 分区/排序前缀与事件类型；
单线程、8192 行块，不增加原扫描行数、字节或时间上限。
第一轮仅观察原始行，不增加 repo_id/名称非空筛选，避免因缺失字段导致长距离扫描。
移除自动跨年历史回退。新语句尚未执行验证，不能宣称已成功取得有效样本。

### 单线程验证结果

批次 `20261015T164827Z` 的第一条 IssuesEvent 查询仍触发 Code 158，服务端报告 current rows 13.48 million。
随后四类样本按约定未执行；认证和 112 列结构读取成功。单纯减少线程并未解决该读取保护问题。
不能仅凭错误里的 current rows 判断这些行已经物理读取完成或导出成功。

已准备更窄的本地 SQL：`verify_teacher_events_readonly.sql`。
只查 202610 月份、IssuesEvent/opened、facebook/docusaurus（repo_id=94911145），
并限定 org_id 为 0 或已从 GitHub 核实的 Facebook owner ID 69631，继续保留读取保护。
此语句尚未验证；返回 0 行也只说明该条件未取得样本，不能推出全库没有相应数据。
可在已经登录的 DBeaver 中执行，避免为了验证一条 SELECT 反复输入账号密码。

### DBeaver 查询和 D 盘导出已验证

2026-10-16 在已登录的 DBeaver 中执行上述限定仓库的 SELECT 成功，界面显示 5 行、0.19 秒。
通过 JSON 文件导出，抽取设置为 `Fetched rows only: Yes`，直接使用已取得结果。
本次只执行查询和本地文件导出，没有执行远程写入或修改连接安全设置。

导出文件：`D:\OSS-Mentor\data\teacher_clickhouse\docusaurus_issues_5_20261016_010857.json`。
本地解析确认 5 条记录，9896 字节；SHA256：
`5BFAE866B317DC77405A400AD9D4C6F5EEC3F504909337F7FB62B2665FA57247`。
仓库均为 facebook/docusaurus，Issue 编号为 12586、12578、12579、12584、12599。
字段包含标题、最多 1500 字符的正文摘录、标签和 UTC 事件时间；标签在此 DBeaver JSON 中导出为字符串。
文件保留 DBeaver 的 SQL 文本顶层键和记录数组结构，尚未规范化导入本地任务库。

这是历史 opened 事件的 5 条抽样，不能认定这些 Issue 当前仍开放或可推荐；后续应通过 GitHub 复核。
数据保持工作版本，未冻结。此前 HTTP 脚本的失败记录仍有效，本次成功仅指 DBeaver 查询和导出。

### 扩大批量的尝试（尚未取得结果）

用户指出 5 条不足以作为正式数据集。已准备 `export_teacher_issues_batch_readonly.sql`，
目标为 17 个已知仓库、202607—202610 的 opened 事件，最多返回 1000 条，保留原有读取保护。
该 SELECT 已在 DBeaver 发起，但未观察到返回或服务端错误；旧结果仍显示 5 行。
点击界面 Cancel 后窗口显示“未响应”，无法确认取消是否完成；没有强制结束进程或改动连接配置。
目前不能宣称新增 1000 条或导出成功。待客户端恢复后，应改为单仓库、单月分批，
在本地记录每批返回量、去重数量和 GitHub 状态复核结果，避免把历史 opened 事件直接当作当前候选。

### 首批 100 条上限的只读采集与 GitHub 核查

2026-10-16 批次 `docusaurus_202610_20261016T075612Z` 使用 HTTP GET、固定 SELECT、
单仓库单月份，未读取正文。结果已保存于 D 盘 teacher_clickhouse 下同名目录。
返回 8 条事件、8 个不同 Issue，未达到 LIMIT 100；不据此推断全库数量或完整覆盖。
服务端统计 elapsed=0.043981844 秒，rows_read=114243，bytes_read=3878170。

随后通过 GitHub GET 读取了 8 条详情、6 条开放 Issue 的时间线、仓库元数据及关联 PR #12685。
其中 2 条已关闭；其余分别存在认领/处理中评论、开放修复 PR、缺少复现或作者准备修复的情况。
保守处理：0 条批准进入推荐库；3 条排除（2 条关闭、1 条开放 PR），5 条待人工确认。
所有仍开放的样本均带 needs triage 标签，不标记为已经确认适合新手的任务。
结果和原始证据位于该 D 盘批次的 `github_verification/`；数据保持未冻结。

时间质量发现：老师库 `event_time_utc` 字符串不带时区，并与 GitHub 的创建时间存在差异。
不能只凭别名认定其为可靠 UTC；保留原值，暂不用于历史训练切分，后续以 epoch 和显式格式做核查。
后续应按相同轻量读取方式扩大仓库或月份；需再次提供本地凭据，不保存或复用已结束进程的密码。

### 14 仓库 × 4 月份扩展批次已落地

批次 `expanded_20261016T080404Z` 已完成本轮计划，原始结果位于 D 盘 teacher_clickhouse 同名目录。
本地重新解析核对：56 批中 55 批成功，1 批 HTTP 502（google/zx，202607）。
55 个有效原始 JSON 共 426 条事件，按 repo_id + issue_number 去重为 416 个 Issue，
与汇总文件数量一致，去掉 10 条重复事件。实际取得 Issue 的仓库为 10 个，不能声称全部 14 个都有记录。

分布：deepagents 225、vscode-mssql 107、Agents-for-net 23、docusaurus 21、zstd 18、
buck2 15、styleguide 3、zx 2、visx 1、Interactive-Book 1。
deepagents 的 202609 批次达到 100 条返回上限，已标记可能截断，后续应细分时间范围补采。
单批统计最大读取字节为 5995519，最大读取行数为 138703；本轮未提高原读取保护限制。

本轮不读取完整正文，未将这些发现记录导入推荐库存；416 是不同 Issue 的发现量，
不是 416 条已确认可推荐任务。仍需 GitHub 当前状态、正文、认领/PR 及任务可执行性复核。
本批次包含之前已核查的 8 个 docusaurus Issue，不应将旧批次再次叠加计数。
所有数据保持工作版本、未冻结；远端仅 SELECT/HTTP GET，未执行数据库修改。

### 扩展批次的 GitHub 核查及独立工作库

2026-10-16 已核查上述 416 个不同 Issue：412 条详情成功，4 条失败（重试仍失败，保留为未核实）。
初筛后取得 147 条时间线，并核查其中引用的 20 个 PR；10 个有发现记录的仓库均读取了当前元数据。
来源为 GitHub connector GET，未发送评论或改动任何远端数据。

最终：78 条通过当前可参与性检查，271 条排除，63 条待人工确认，4 条未核实。
排除包括 219 条关闭、45 条已分配、6 条引用开放 PR、1 条阻塞标签；采用互斥的首个原因计数。
待确认包括认领/进行中评论、内部任务范围、资料不足、支持问答或非本项目开发任务。
评论认领检测为保守文本启发式，可能漏检或过度标记；不保证没有未关联或未公开的工作。
78 条不是 78 道已确认难度的新手题，参与前需再次核查，个人技能适合度另行判断。

产物位于 D 盘批次目录 `github_review_v1/`：
- `candidates.md`：可读链接清单；`candidates.json`：完整正文、标签、状态、来源和有效期。
- `audit.json`：全部 416 条的决定与原因；`report.json`：统计与限制。
- `review_pack.json.gz`：经过字段投影的 GitHub 证据，保留正文、评论、相关身份、状态、时间与链接；不宣称为完整原始响应。
- `teacher_review.sqlite3`：独立审计工作库，416 条发现/审计、78 条候选，未接入网页、未与旧候选库合并。

核查开始时间 2026-10-16T08:11:30.220Z，保守有效期至 2026-10-17T08:11:30.220Z（北京时间 17 日 16:11）。
构建入口 `build_teacher_review.py` 仅做本地离线处理，拒绝覆盖已有 SQLite 库；数量、身份、候选状态不变量已检查。
本次实际构建通过 SQLite integrity_check 与 foreign_key_check，外键违规 0；数据未冻结。
发现范围中 1 批 502、1 批达到上限的缺口仍未补齐，不能把核查结束当作全量采集完成。
