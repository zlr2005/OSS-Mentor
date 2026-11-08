# 优化CODER模型的平台接入与推理导出

本轮将“优化模型能进入真实平台评分链路”作为验收条件，完成只读运行包、既有ModelScorer适配、画像/任务缓存绑定、启动入口以及真实权重的服务/API/HTTP集成检查。**未修改公共API或数据库Schema，未覆盖当前默认模型。**

这轮重点是部署与推理可用性，没有新增训练或独立准确率提升。不能因为导出成功就称已对所有真实平台用户上线。

## 实际交付与验证

运行包：`data/gated-v1/bundle/`，包含已校验的 `weights.npz` 和 `manifest.json`；编码资产位于 `data/gated-v1/assets/`，冻结 CODER 源码位于 `data/gated-v1/coder_source/`。版本锁为 `config/gated_runtime_assets.lock.json`。选用已记录的多正例边际损失方向，**显式固定seed20261102**，没有按验证成绩选最优seed。

研究中的五seed均值为MRR0.639812、Recall@5 90.14%；当前单seed导出包在相同209条开发目标上的MRR为 **0.637815**、Recall@5 **89.95%**，不能将五seed均值冒充单个上线包的成绩。

- NumPy导出与PyTorch权重最大分数误差 **3.5763e-7**；209条已保存目标名次逐项一致，单seed主要指标完全复现。这不是所有未标记候选之间的全序逐项证明。
- 权重包 **13,577字节**。这里是门控排序头与TRAIN标准化参数，**不包含整个CODER图模型或特征缓存**；上游CODER仍在离线特征准备中使用。
- 请求时只依赖NumPy推理，不导入PyTorch、训练源码，不训练、下载或在线编码。固定历史查询30次微基准：NumPy含标准化中位0.239ms；PyTorch仅网络中位0.208ms，比较范围不同，不能称NumPy逐请求更快。
- 新子进程内单次导入/载入探测：NumPy运行端0.593秒，单独import torch为3.891秒。不是OS冷磁盘或整个HTTP服务的启动统计，主要说明运行依赖减少。
- 56项后端/API/页面测试通过，包含7项新增门控测试；随后对最后修改的画像规范化和24小时TTL检查重新运行7项门控测试，全部通过。
- 实际权重通过 `RecommendationService` 和 `RecommendationApi` 的公开及登录用户推荐路径；硬过滤在模型评分前执行，过期/语言不符任务不会传给模型，负反馈和多样性流程保留。
- 本机真实HTTP服务器5项检查通过：页面200、带真实model_version的推荐200、未登录401、非法参数400、任务正文变化后503/model_unavailable。该HTTP检查使用**隔离合成输入与真实研究权重**，没有宣称真实GitHub库存或真实OAuth流程已经验证。

证据目录：`platform_gated_parity_20261108_v1/`（209目标重放、分数误差、微基准与启动探测）和 `platform_gated_http_20261108_v2/`（HTTP检查）；前者、后者均位于 `data/midterm/working/`。HTTP v1为测试认证依赖未补齐的失败记录，正式通过记录是v2。

## 既有平台接口保持不变

`src/oss_mentor/gated_scoring.py` 的 `CachedGatedScorer` 实现已有：

```python
score(profile=DeveloperProfileV2, developer_key=..., candidates=[...]) -> ModelScoreBatch
```

通过 `RecommendationService(..., model_scorer=scorer)` 注入。返回已有ModelScore/ModelScoreBatch，模型版本、图/数据/编码器版本和覆盖状态进入原推荐快照。对网络原始分数采用单调sigmoid映射至[0,1]，不是成功概率校准。服务仍执行当前可用性/任务准备度和硬条件过滤，然后模型评分、负反馈、仓库/任务多样性及快照。

没有修改 `api.py`、`cli.py`、前端、既有评分端口或表结构。新增独立入口 `scripts/run_gated_api.py` 仅绑定127.0.0.1，保留现有认证/画像服务，不启用自动刷新，不添加历史缓存回退。

## 必须补齐的真实输入，不能省略

平台的DeveloperProfileV2表单不等于研究中的23维证据画像。优化模型需要冻结CODER适配后的用户/任务表示、历史路径/文本匹配以及原16维交互输入；不能仅将“Python技能2级”等字段任意改写成研究能力值。

平台接入采用**请求外的可信特征生产→只读上下文缓存→请求内选取评分**。本轮提供特征发布工具与校验器，**尚未将B侧真实当前证据自动生成为这些特征**。因此真实用户和当前候选缓存未准备前，不能声明生产库存已全部可用。新增任务、正文或画像变化需重新生成对应缓存；无缓存明确返回503，不静默复用历史结果或退回另一模型。

上游特征生产器必须使用与导出包 `backbone_sha256` 对应的同seed CODER/RC1/v2/v3，保留截止边界和缺失证据状态；输出以下无pickle NPZ：

| 数组 | 形状 | 含义 |
|---|---|---|
| issue_keys | N | 规范owner/repo#number，与候选列表顺序相同 |
| content_hashes | N | 当前标题/正文的绑定哈希，使用content_hash() |
| coder_features | N×128 | L2归一化CODER用户、任务、逐维乘积、绝对差 |
| pair_features | N×16 | 原始交互特征，尚未应用TRAIN标准化 |
| coder_logits | N | 同seed冻结v3的原始分数；请求内按实际过滤后候选池重新z标准化 |

16维顺序为：文本余弦、repo_seen、repo_commit_fraction、log_repo_commits、log_all_commits、log_paths、log_filename_matches、log_title_chars、log_body_chars、log_lines、log_code_fences、log_urls、bug/docs/test/feature文本标记。不得传入验证目标、解决者、未来修改文件或正例来源标记。

输入证据未齐时不能生成“真实特征”假象。`prepare_gated_context.py` 只发布已经准备好的特征、校验绑定与有效期，**不是一个自动收集和推断缺失历史的工具**。

## 缓存发布、失效与启动

每份上下文绑定规范平台画像全部字段的fingerprint与登录名；画像JSON使用平台已有 `as_developer_profile_v2` 同样的转换，支持整个含 `profile` 字段的响应或单独profile对象，避免user_input/user别名造成不匹配。

准备文件还必须附provenance JSON：

```json
{
  "context_key": "用context_key(profile, developer_key)计算",
  "feature_schema": "coder-gated-128+16-v1",
  "backbone_sha256": "与模型manifest一致",
  "features_sha256": "prepared.npz的SHA-256",
  "evidence_manifest_sha256": "可信上游证据清单的SHA-256",
  "scope": "live",
  "as_of": "真实特征快照的带时区时间",
  "expires_at": "不超过as_of后24小时"
}
```

`scope=historical_replay` 的缓存不能由平台启动入口用于实时推荐。时间戳必须来自真实特征快照，不能把历史材料日期改成当前日期以通过检查。缓存同时校验模型/seed、画像/登录身份、任务文本、文件哈希、维度/数值和24小时有效期；缺失候选、改动正文或画像、失效时间均拒绝评分。缓存发布到新目录，不覆盖已发布上下文。

已有模型包后，先用真实上游准备的输入发布缓存：

```powershell
python scripts/prepare_gated_context.py `
  --bundle data/gated-v1/bundle `
  --profile CURRENT_PROFILE.json --candidates CURRENT_TASKS.json `
  --prepared PREPARED_FEATURES.npz --provenance FEATURE_PROVENANCE.json `
  --cache NEW_LIVE_CACHE --developer-key ACTUAL_LOGIN
```

其中大写路径是尚需提供的真实输入，不是本轮已经生成的生产文件。匿名公开画像省略developer-key；登录用户必须与认证服务规范登录名一致。

再对实际库存检查覆盖，返回ready=false或退出码2时不应宣称已就绪：

```powershell
python scripts/check_gated_readiness.py `
  --database ACTUAL_PLATFORM.sqlite3 `
  --bundle data/gated-v1/bundle `
  --cache NEW_LIVE_CACHE --profile CURRENT_PROFILE.json --developer-key ACTUAL_LOGIN
```

检查会执行既有推荐服务的筛选与模型评分；没有合格任务也不会误报模型覆盖就绪。然后才使用新增入口启动，不覆盖旧默认启动脚本：

```powershell
python scripts/run_gated_api.py `
  --database ACTUAL_PLATFORM.sqlite3 `
  --bundle data/gated-v1/bundle `
  --cache NEW_LIVE_CACHE --port 8767
```

入口沿用现有OAuth/会话环境变量，不自动登录、采集或修改权限。需要已初始化数据库；启动会调用现有迁移初始化，不以测试库替代用户生产库。

## 后续优化的完成条件

今后训练收益与平台可用性一起验收：同协议性能记录、明确的输入Schema与模型版本、可导出权重、导出分数/名次核对、真实评分端口和认证API测试、缺输入/缓存失效处理。输入维度或上游表示变化时，应同步升级离线特征生产和运行包，不能只交训练脚本。

原底座图/历史来源审计缺口、新项目组样本少、开发验证多次复用仍存在。本轮不产生独立准确率结论，也不保证真实平台硬过滤后候选池具有同样的离线指标。平台通路已实现并验证，真实当前用户/库存特征生产与覆盖检查仍是上线前剩余项。
