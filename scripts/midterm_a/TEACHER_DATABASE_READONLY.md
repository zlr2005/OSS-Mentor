# 老师数据库：只读查询与本地导出

用户于 2026-10-16 明确要求：只查询并导出数据，绝不修改老师数据库中的任何数据或对象。
该约束适用于本项目后续所有老师库接入、采集和排错操作。

- 远端仅允许查询数据及元数据的 SELECT、DESCRIBE、SHOW。
- 不执行远端 INSERT、UPDATE、DELETE、CREATE、ALTER、DROP、TRUNCATE、OPTIMIZE、SYSTEM、授权或配置修改。
- 不创建远端表、临时表、视图、索引或导出辅助对象；不通过写入远端来加速采集。
- 数据转换、清洗、建表和导出文件仅在本地独立工作目录/工作库中进行。
- 查询按仓库、时间范围及结果量分批限制；正式数据量不能用 3 条探测样本推断。
- 密码仅由用户在本地安全输入，不写聊天、脚本、Git 或导出文件。

## 已验证状态

本地连接报告：`data/midterm/validation/a/teacher_clickhouse_connection.json`。
已通过只读 HTTP GET 验证连接、查询 `opensource.events` 结构、读取存储元数据及 3 条样本。
`events` 有 112 个字段，MergeTree 引擎，按 `toYYYYMM(created_at)` 分区。
未执行远端数据写入或结构修改，尚未批量导出。

第一批无过滤探测样本存在 1970 年时间及空仓库名，不把这些记录直接作为有效任务。
正式查询前需核查时间字段、事件类型和无效值，当前任务仍需 GitHub 状态复核。

连接检查程序：`scripts/midterm_a/connect_teacher_clickhouse.ps1`。
加 `-InspectEvents` 可显式按当月分区及五类事件各返回最多 5 行，不自动扩大历史范围。
使用单线程及 8192 行块；当前版本不依赖 `system.parts`。
事实表样本查询上限为 15 秒、100 万读取行及 128 MiB 读取字节；不做全表计数或全表排序。
这些为数据库查询保护设置，检查可能在读取批次后执行，不应视作绝对物理读取量保证。
HTTP 只使用 GET，固定 SQL 额外检查 SELECT/DESCRIBE/SHOW 前缀及单语句约束。
凭据只在本次进程内使用，检查完成后释放；后续真实查询需要重新由用户本地提供凭据。
