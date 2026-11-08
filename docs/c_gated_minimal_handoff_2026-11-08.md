# C 端最终门控模型最小协作交接

资产来源基线：PR #40 `9bf9208d237f343ad3d1d993ccbe9a0f31c4935b`；模型 `coder-gated-marginal_positive-s20261102`。

## C 已自行完成

- 门控运行包、worker、缓存生命周期和显式准备中状态；
- B `enhanced-profile-export-v0.1` 的 `values/as_of/profile_version` 绑定；
- 画像版本、身份、平台画像和候选正文变化后的缓存失效；
- Mock B 确认画像、Mock D 身份与候选接口；
- MiniLM 与 repository mapping 的本地重建及一致性校验；
- 真实冻结资产的离线编码、发布、评分和版本切换验收。
- 已接收并校验 `portable_gated_pr40_9bf9208d.zip`，资产版本和校验和不再依赖组长确认；
- 正式资产已安装到本地忽略目录 `data/gated-v1`；原始 ZIP 通过 Git LFS 存放在
  `model-assets/portable_gated_pr40_9bf9208d.zip`，Git 中同时保存资产锁、安装器和验收报告。

## B 已确认

1. 最终 Schema 固定为 `enhanced-profile-export-v0.1`，继续使用 `values` 与 `as_of`；
2. 正式出口为登录态 `GET /api/v1/me/profile/enhanced-features`，只返回 confirmed snapshot；
3. 画像值、mask、锁定状态、证据引用或用户确认结果变化时更新 `profile_version`；
4. `developer_key` 由认证服务从 session 中的 GitHub login 注入并执行
   `strip().casefold()`，浏览器不得提交身份字段。

画像出口必须返回：`profile_state`、`profile_key`、`profile_version`、
`developer_key`、`as_of`、`feature_schema_version`、`feature_names`、`values`、
`observed_mask`、`locked_mask`、`current_interests`、`evidence_refs`。其中
`profile_state=confirmed`，`developer_key=strip().casefold()` 后必须等于 D 注入的已认证
GitHub login，`feature_names` 顺序必须与冻结模型一致。B 不需要实现 pair features、worker
或缓存。

已接受身份边界：GitHub login 属于认证身份，不要求仅因改名而重写不可变特征快照的
`profile_version`。C 的 context key 独立绑定规范化认证 login、完整平台画像指纹和 confirmed
snapshot 指纹；因此 login 变化即使沿用原 `profile_version`，旧缓存也会失效。worker 编码前
还会拒绝 confirmed snapshot 的 `developer_key` 与当前认证 login 不一致。B/D 无需补充身份
版本接线。

正式 gated 路由强制要求该 confirmed snapshot：画像缺失、未确认或画像服务未配置时分别
返回 `profile_required`、`profile_not_confirmed` 或 `service_not_ready`，不会用公开证据生成
替代 23 维画像后进入 `coder_gated`。

## D 只需提供和配置

1. 已认证身份接口：规范 GitHub login、数字 ID、user ID；
2. 当前候选接口：稳定 ID、仓库、Issue 编号、标题、正文和可用性时间；
3. 生产 SQLite/Postgres、OAuth 与只读 GitHub Token；
4. worker、缓存、请求队列目录和 Secret 的进程级配置；
5. 对准备中、401、422、503 状态沿用现有前端处理。

候选出口的每条记录至少包含稳定 `task_candidate_id`、`repository`、
`issue_number`、`title`、`body_text`、`html_url`、`candidate_availability`、
`github_verified_at`、`task_feature_version` 及现有难度、技能和任务类型字段。D 只在服务端
注入身份；浏览器请求中的 login 或数字 ID 不作为可信输入。D 不需要实现模型编码或排序。

## 资产已闭环，无需再向组长索取

- 压缩包 SHA-256：`179d6fe270522f194e7242ddd837a1850637c3c2f581a0e35300069fbcfcff0f`；
- 内部清单：422 个文件全部通过 SHA-256 校验；
- 固定 seed：`20261102`；
- MiniLM revision：`c21050a7ef692090620a6d037dd736908f9c7cf6`，一致性探针为 `[1, 384]`；
- 正式 repository mapping：97 行，原始 SHA-256
  `c0c665d7153caa4f65c04fcbecc8f1a341380cc078e2b5a75762456bdbccc2ab`；
- 门控 `weights.npz` SHA-256：
  `f933bf970c906abcd44fff88dc76c9b8c065ace6fedc7e95557db5b2e10f5505`。

版本锁位于 `config/gated_runtime_assets.lock.json`。可用以下命令从原始压缩包重建本地运行目录：

```powershell
python scripts/rebuild_gated_runtime_assets.py `
  --archive model-assets/portable_gated_pr40_9bf9208d.zip `
  --output data/gated-v1
```

worker 配置从 `config/gated_worker.example.json` 复制到忽略目录后，由 D 注入生产路径和
Secret；模型文件、用户数据、缓存与 Secret 均不进入 Git。

worker 使用锁定的独立依赖组安装：

```powershell
python -m pip install --extra-index-url https://download.pytorch.org/whl/cpu `
  -e ".[gated-worker]"
```

容器部署可使用 `Dockerfile.gated-worker`，挂载重建后的 `data/gated-v1`、请求/缓存目录与
生产配置。CI 会在干净 Python 3.11 环境拉取 LFS、安装该依赖组、重建正式资产并执行真实
冻结资产的 Mock B/C/D smoke test。

## 联调完成标准

1. D 注入身份，C 拉取 B confirmed snapshot 与 D 当前候选；
2. 首次请求明确返回准备中/兜底，不伪装成门控结果；
3. worker 生成后 `scoring_engine=coder_gated`，`model_version` 为
   `coder-gated-marginal_positive-s20261102`；
4. B 的 `profile_version`、画像内容或 D 的候选正文改变后旧缓存失效；
5. `/api/v1/status` 展示真实模型版本，Secret、用户数据和运行缓存不进入 Git。

本地证据见 `docs/artifacts/c_gated_local_acceptance_20261108/`：正式平台测试
`1040 passed`；真实冻结资产 Mock B/D 验收通过。仓库实验脚本的全量自动收集另依赖
`transfer_cold_v3` 与 `lightgbm`，不属于生产 C 端测试环境。

生产验收只需替换 B 画像与 D 身份/候选两个 Mock 边界，不再要求组长提供材料或参与实现。
