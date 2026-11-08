# C 端最终门控模型最小协作交接

基线：PR #40 `9bf9208d237f343ad3d1d993ccbe9a0f31c4935b`；模型 `coder-gated-marginal_positive-s20261102`。

## C 已自行完成

- 门控运行包、worker、缓存生命周期和显式准备中状态；
- B `enhanced-profile-export-v0.1` 的 `values/as_of/profile_version` 绑定；
- 画像版本、身份、平台画像和候选正文变化后的缓存失效；
- Mock B 确认画像、Mock D 身份与候选接口；
- MiniLM 与 repository mapping 的本地重建及一致性校验；
- 真实冻结资产的离线编码、发布、评分和版本切换验收。

## B 只需确认和提供

1. 最终 Schema 固定为 `enhanced-profile-export-v0.1`，继续使用 `values` 与 `as_of`；
2. 一个服务端正式画像出口，返回 confirmed snapshot；
3. `profile_version` 在任何模型输入变化时更新；
4. `developer_key` 由认证服务注入，浏览器不得提交。

画像出口必须返回：`profile_state`、`profile_key`、`profile_version`、
`developer_key`、`as_of`、`feature_schema_version`、`feature_names`、`values`、
`observed_mask`、`locked_mask`、`current_interests`、`evidence_refs`。其中
`profile_state=confirmed`，`developer_key=strip().casefold()` 后必须等于 D 注入的已认证
GitHub login，`feature_names` 顺序必须与冻结模型一致。B 不需要实现 pair features、worker
或缓存。

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

## 组长只需确认

1. 模型版本、seed、MiniLM revision 和 mapping 版本；
2. 交付包及关键资产 SHA-256；
3. 当前 repository mapping 的 OOS 策略是否获准上线。

待确认的本地重建结果：MiniLM revision
`c21050a7ef692090620a6d037dd736908f9c7cf6`；mapping 97 行且键值语义一致，
SHA-256 `d9bfdb59d2eb35f46ba899ca33edaaccb1b01def8e02b676a7b1f88c6125befd`；
门控 `weights.npz` SHA-256
`f933bf970c906abcd44fff88dc76c9b8c065ace6fedc7e95557db5b2e10f5505`。

## 联调完成标准

1. D 注入身份，C 拉取 B confirmed snapshot 与 D 当前候选；
2. 首次请求明确返回准备中/兜底，不伪装成门控结果；
3. worker 生成后 `scoring_engine=coder_gated`，`model_version` 为
   `coder-gated-marginal_positive-s20261102`；
4. B 的 `profile_version`、画像内容或 D 的候选正文改变后旧缓存失效；
5. `/api/v1/status` 展示真实模型版本，Secret、用户数据和运行缓存不进入 Git。

本地证据见 `docs/artifacts/c_gated_local_acceptance_20261108/`：正式平台测试
`1037 passed`；真实冻结资产 Mock B/D 验收通过。仓库实验脚本的全量自动收集另依赖
`transfer_cold_v3` 与 `lightgbm`，不属于生产 C 端测试环境。

生产验收只替换上述三个 Mock 边界，不再要求 B、D 或组长参与 C 端实现。
