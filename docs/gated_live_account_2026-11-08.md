# 多正例门控模型：首个真实账号接入进度

最新进度：实际编码和自动缓存发布已实现，zlr2005的4条库存候选已在真实模型服务层得到推荐，列表/详情分数一致。启用后台worker后可自动消费新画像准备请求。详见 `gated_feature_worker_2026-11-08.md`；下文未实现编码的描述保留为阶段记录。

## B/D兼容修复

已修复同候选池列表/详情评分一致性，真实门控引擎记录为coder_gated；模型推荐中的规则理由改为匹配背景说明，保留实际负反馈降权。画像/任务变化与首次使用的缺特征状态返回model_features_required，推荐页和原首页有对应提示。

正式启动入口自动登记去重特征准备请求（缓存旁gated_feature_requests），不修改B的画像融合规则或公共字段结构。这里自动化的是需求登记：实际证据编码与新缓存发布尚未实现为后台工作进程，真实账号的推荐仍需完成此步骤。不能把本轮接口修复当成真实推荐已上线。详细边界和测试记录见interfaces.md的2026-11-08补充。

## 用户授权的库存假设更新

用户已明确要求暂不复核任务状态，将现有库存视为可用。新增 `--assume-inventory-available` 试用选项，跳过available库存的核验时效检查，不修改数据库原始核验时间，也不启用网络刷新。启动日志、输入导出报告及推荐条目warning均标明该假设。已知不可用任务、内容门槛及画像条件仍按现有数据筛选。

启动便捷脚本时加 `-AssumeInventoryAvailable`；直接使用 `serve-api` 时同时设置 `--refresh-interval-seconds 0 --assume-inventory-available`。输入导出工具也支持同名选项。

按该假设重新导出 `zlr2005` 的真实画像输入后，合格候选为4条。报告在 `data/midterm/working/platform_gated_zlr2005_assumed_20261108_v1/readiness.json`。当前剩余项是模型特征缓存缺失，库存过期不再作为本次试用的阻塞条件。新增假设模式测试2项、门控10项、CLI13项均通过。

固定模型为 `coder-gated-marginal_positive-s20261102`。正式 `serve-api` 现支持成对的 `--gated-bundle`、`--gated-cache`，与旧CODER路径及legacy参数互斥。启动日志记录实际引擎与模型版本；没有更换现有默认启动方式。

便捷入口 `scripts/run_gated_platform.ps1` 默认使用有真实账号的 `data/oss_mentor.sqlite3`，端口8767，关闭后台刷新。准备好当前缓存后可执行：

```powershell
pwsh -File scripts/run_gated_platform.ps1 -PythonPath 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe'
```

## 已核对的真实输入

- 用户指定首个账号 `zlr2005`，公开GitHub数字ID为186778075。主库已有绑定该ID的用户及画像，已只读导出；没有创建或冒充登录会话。
- 公开提交采集最多10条，保存69条本人提交涉及的文件路径引用、3份内容去重的代码文件，以及逐响应校验和。每条采纳提交均核对数字作者ID、作者/提交时间；代码验证Git blob SHA-1。完整文件快照不等于用户独立编写了全部内容。
- 证据目录：`data/midterm/working/gated_zlr2005_evidence_20261108_v1/`。这是有限当前证据样本，不是全部历史，也不是过去截止点的公开性证明。
- 就绪报告：`data/midterm/working/platform_gated_zlr2005_readiness_20261108_v1/readiness.json`，当前 `ready=false`，合格任务0。
- 网页库有6,411条静态eligible记录，但available记录的最新核验时间为2026-10-26，已超过24小时有效期。存储层还会检查新鲜度与内容准备度，因此不能把6,411直接当成当前可推荐数量。

## 验证

真实导出权重下门控测试10项、推荐服务10项、CLI13项、推荐API6项通过。HTTP v4的5项检查通过（200推荐含正确版本、401、400、正文变化503）；使用隔离合成输入和真实权重，未声称真实库存推荐通过。

初次测试受外部SSLKEYLOGFILE写入限制，后续仅在测试进程移除此变量后通过。HTTP v3受沙箱本机socket限制失败；v4为实际通过记录。

## 尚未完成的真实推荐条件

需通过GitHub重新核验候选可领取状态，并将真实用户证据、当前任务文本编码为与固定seed一致的128维CODER表示、16维交互特征及CODER分数。23维研究证据画像不能用平台表单技能等级直接替换。当前采集器只产出证据，明确标记 `features_ready=false`。

`scripts/export_gated_inputs.py` 与线上服务共享 `eligible_candidates` 取得评分前候选池，源数据库以SQLite只读模式打开，不迁移。输出用于后续特征生产；它不生成模型特征，也不修改历史核验时间。补齐后由 `prepare_gated_context.py` 发布，再运行 `check_gated_readiness.py`。账号真实推荐尚未上线。
