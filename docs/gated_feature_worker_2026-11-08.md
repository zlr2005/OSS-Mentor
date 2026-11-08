# 实际特征编码与缓存自动发布

更新：新账号自动公开采集与无历史冷启动已完成，当前本地配置已启用。evidence_by_login不再是账号准入白名单；最新流程和测试见 `gated_all_accounts_2026-11-08.md`。下文“当前仅配置zlr2005”等内容为上一阶段记录。

已完成“准备请求→实际编码→发布→运行端读取”闭环。真实试用账号zlr2005已处理，4条当前画像下的合格库存任务得到实际模型评分，列表与详情分数逐条一致。任务可用性按用户授权的库存假设处理，没有联网复核。

## 实际产物

- 配置：`data/midterm/working/gated_live_worker_20261108_v1/config.json`
- 缓存：同目录 `cache/`；首版generation为3515314ed3344ecdbba8beb95d32f7af。
- 请求及状态：同目录 `requests/`、`requests/status/`。
- 真实账号服务验证：同目录 `real_account_verification.json`。这是实际画像、证据与库存的服务层运行，不是新增准确率实验，也没有声称完成真实OAuth浏览器登录。

## 启动

在仓库根目录运行以下命令，API端口8767；服务启动后台编码进程，服务退出时结束该进程。

```powershell
pwsh -File scripts/run_gated_platform.ps1 `
  -PythonPath 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe' `
  -WorkerConfig data/midterm/working/gated_live_worker_20261108_v1/config.json `
  -AssumeInventoryAvailable
```

单次处理已有请求（失败退出码2）：

```powershell
& 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe' scripts/run_gated_feature_worker.py `
  --config data/midterm/working/gated_live_worker_20261108_v1/config.json --once
```

配置的bundle、cache、requests、assets均为本地路径；evidence_by_login把规范GitHub登录名绑定到已核对数字身份的证据目录。当前仅配置zlr2005。其他账号或匿名画像没有证据时记录failed，不借用其他人的历史。编码器不联网下载，不训练，不刷新任务状态。

## 编码方式

复用冻结seed20261102的RC1适配器、v3余弦修正及原CODER任务融合权重。验证模型依赖校验和、MiniLM权重及真实代码Git blob/SHA-256。代码证据经B的现有贡献解释与特征融合规则形成23维值和缺失mask；平台表单仍用于身份绑定、偏好和硬过滤，不直接改写成研究能力值。

本账号没有提供已核实成功解决历史，使用HistoryFusion已有的“历史不可用时返回原画像表示”路径，不伪造成功历史。以实际代码文件与当前Issue文本生成MiniLM语义和16维交互特征；Issue的32维向量使用原任务编码函数（最多16段，未知协同/结构通道为零，保留已知仓库表示）。该冷启动策略与有成功历史的查询不同，不宣称完整历史已收齐。

后台编码依赖当前机器上的 `tmp/pr39-review/CODER/src` 冻结模型实现与D盘编码资产；迁移机器时需要显式部署这些依赖。当前请求内推理仍仅依赖NumPy。

## 更新与故障

新请求每2秒扫描，单请求处理间隔至少60秒。profile/context、任务文本、模型版本、seed、输入形状及数值均检查。完整NPZ/元数据在新generation中生成，实际加载评分检查通过后，原子切换current.json指针；服务每次读取当前generation，无需重启。失败只记录状态，保留上一版完整generation；若旧版不匹配当前请求，继续返回需要准备，不冒充新结果。

特征有效期不超过24小时，也不超过用户证据采集后的24小时。过期证据需先重新采集，后台不会擅自联网更新，也不会改写采集时间。仅配置这个证据目录并不等于为所有未来用户完成了自动采集。证据刷新后后台可重新处理失败请求。

验证：新增worker测试3项（原子发布/幂等/运行中热读取、编码失败保留指针、错误模型与无效输出不发布），门控/生命周期测试13项及CLI13项通过，共29项。另实际运行zlr2005编码、4条推荐和详情分数核对、再次运行worker复用有效缓存均通过。服务启动生命周期测试验证后台进程退出清理。原训练权重和平台数据库未由此次证据编码覆盖。
