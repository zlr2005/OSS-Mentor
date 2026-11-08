# 新账号自动初始化与可用性

已取消“仅evidence_by_login登记账号能使用模型”的限制。当前8767本地服务已启用auto_collect_public，使用主库认证身份。新账号正常登录、完成B的画像后，首次请求有合格候选的推荐会触发自动准备，无需管理员登记账号或手动采集代码。

## 用户流程

1. 保留原GitHub登录和B的画像填写/确认流程。没有画像时引导填写，不伪造技能或用户偏好。
2. 特征尚未准备时，立即返回画像规则推荐，并明确标记profile_bootstrap_rules及准备中提示，不冒充新模型成绩。候选为空则正常显示空状态，不能保证每种筛选条件都有任务。
3. 后台根据服务端登录名查询主库GitHub数字ID，自动采集有限公开提交与本人修改代码。只读公开API，不依赖操作员gh身份，也不读取用户私有仓库或OAuth token。
4. 编码与缓存发布完成后，同一页面每5秒自动重取结果，最多24次；之后仍可点击重新获取。推荐结果切换为coder_gated。
5. 没有可用公开代码时，使用23维缺失mask与总体用户表示的冷启动模型，文本余弦缺失项为零并记录cold_start_population/缺历史说明。不会借用zlr2005或其他用户的历史。

GitHub暂时不可访问时，记录collection_status=unavailable，继续生成明确标记的冷启动特征；证据和相应缓存有效期最多5分钟，随后可重试采集。未知、已停用的账号或身份不一致仍拒绝采集。匿名/自定义画像只使用绑定该画像的无历史冷启动，不向GitHub查询他人数据。

## 实现与运行

- `gated_account_bootstrap.py` 按数字用户ID隔离证据目录，并复用有效证据；公开采集至多10条提交、3份去重代码。
- `collect_gated_user_evidence.py` 支持注入无token公开API客户端，校验登录名与数字ID。
- `encode_gated_live.py` 支持缺少代码的合法冷启动，Issue文本按32条分批编码。
- `RecommendationService` 仅在显式启用初始化推荐时，对model_features_required提供标注的规则初始推荐；模型损坏等异常不会伪装成已完成初始化。列表和详情遵守相同处理。
- 新推荐页提供准备提示、初始推荐说明和自动重试；原首页也有对应中文说明。
- Windows下服务停止会终止worker进程树，避免虚拟环境Python启动器留下旧worker占用队列锁。

当前配置 `data/midterm/working/gated_live_worker_20261108_v1/config.json` 已设置 `auto_collect_public=true` 与实际主库路径。沿用gated_feature_worker_2026-11-08.md的启动命令即可。库存状态仍按用户授权假设为可用。

## 验证范围

66项相关自动化检查通过：账号初始化4、门控14、worker3、证据3、推荐服务10、推荐API6、CLI13、推荐页Node行为4、网页静态9。

另使用非zlr2005的真实公开账号octocat、真实导出模型、隔离测试数据库及合成任务，跑通自动公开采集→编码→原子发布→同一评分器由规则初始化切换到门控模型。该次采集有14条路径引用、无可用代码文件，验证了实际冷启动路径仍可生成模型推荐。记录：`data/midterm/working/gated_new_account_20261108_v1/verification.json`。

这不是新增准确率实验；没有代替第三方账号执行真实OAuth登录，也不声称已经浏览器验收所有账号。当前本地服务health与推荐页面200检查通过。
