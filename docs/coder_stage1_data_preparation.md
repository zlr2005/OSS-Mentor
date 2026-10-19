# 类 CODER 推荐算法：阶段 1 数据准备与时序图

本文记录 M0 数据预处理协议、真实全量统计和复现方式。当前版本为
`gfibot-m0-v1.1`，仅提供数据层，不包含 Baseline、模型或训练逻辑。

## 1. 输入与可复现环境

输入文件由 `CODER/data_manifest.json` 固定：

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `CODER/dataset.bson` | 7,043,563,852 | `4cde2499108a3f85b92c82f92ff850644137f54c3c8bb4c86f4cd32997192039` |
| `CODER/resolved_issue.bson` | 658,228,041 | `c6af28f9b7c13535dc15ae30c7b00df3ace76d15c39bae246f421e912f5d6638` |

阶段 1 固定依赖为 Python ≥3.11、`pymongo==4.15.3`、
`pyarrow==21.0.0` 和 `tzdata==2025.2`。未安装任何深度学习或 GPU 框架。

```powershell
CODER/.venv/Scripts/python -m pip install -e "CODER[test]"
CODER/.venv/Scripts/coder-prepare-data.exe --config CODER/configs/data_preparation.toml
CODER/.venv/Scripts/coder-validate-data.exe --config CODER/configs/data_preparation.toml
```

流水线逐文档读取 BSON/BSON.GZ，使用本地 SQLite 维护确定性实体索引，
使用分批 Parquet writer 输出。它不会将完整 BSON 转成列表或 DataFrame。
发布前在唯一的 `.staging` 目录完成校验；已存在的版本目录不会被覆盖。

## 2. 真实 Schema 与字段使用政策

完整字段类型、出现次数、非空和非空集合比例位于：

```text
data/midterm/validation/gfibot-m0-v1.1/data_quality.json
  → field_profile
```

### 2.1 `dataset.bson`

共 296,164 条快照记录，所有顶层字段的出现率和非空率均为 100%；但数组
或文本可能为空。

| 字段或字段组 | 实际类型 | 非空/非空集合比例 | 解释与使用政策 | 泄露风险 |
|---|---|---:|---|---|
| `_id` | ObjectId | 100% | 快照来源追踪 ID，不作模型特征 | 低 |
| `owner`, `name`, `number` | string, string, integer | 100% | 构造 Repository/Issue 主键 | 低 |
| `created_at` | datetime | 100% | Issue 创建时间；用于资格和时间校验 | 低，但只作时间控制 |
| `closed_at` | datetime | 100% | 关闭时间；创建快照中强制置空 | 高 |
| `before` | datetime | 100% | 快照时间，不是布尔值 | 低，但只作时间控制 |
| `title` | string | 100% | 快照文本；创建快照可用于后续模型 | 关闭快照内容可能泄露 |
| `body` | string | 98.4745% 非空 | 快照文本；空正文原样保留 | 关闭快照内容可能泄露 |
| `labels` | array[string] | 58.1823% 非空 | 按快照和观察时间输出标签边 | 关闭后标签不能回填 |
| `label_category.*` | integer | 100% | 数据源派生的标签类别，原样 JSON 保存 | 需按快照时间使用 |
| `n_stars`, `n_contributors`, `n_commits`, `n_pulls`, `n_open_issues`, `n_closed_issues`, `r_open_issues` | integer/number | 100% | 仅保存在带时间快照的 `features_json` | 不能当静态最新值使用 |
| `len_*`, 可读性、代码/URL/图片计数 | integer/number | 100% | 快照局部文本统计，保存在 `features_json` | 必须使用预测时刻可见快照 |
| `reporter_feat`, `owner_feat` | object | 100% | 采集时点语义未证明，暂不作模型输入 | 高/不确定 |
| `resolver_commit_num`, `prev_resolver_commits` | integer/array | 100% | 描述真实解决者，明确排除 | 目标泄露 |
| `comments` | array[string] | 37.2277% 非空 | 没有逐条时间，暂不标准化 | 高/不确定 |
| `events` | array[string] | 49.4614% 非空 | 仅事件类型字符串、无时间；共 2,158,111 项，暂不作时序事件 | 高/不确定 |
| `comment_users`, `event_users` | array[object] | 37.1926% / 70.3080% 非空 | 没有逐行为时间，暂不作历史边 | 高/不确定 |
| `issue_close_time` | number | 100% | 结果性时长字段，排除 | 目标泄露 |

`reporter_feat`、`owner_feat` 具有 `name`、提交/Issue/PR/Review 数量和最大
Star 等嵌套字段；这些字段虽然完整，但缺少可靠的历史观察时点，因此完整
统计保留在质量报告中，第一版训练特征不使用。

### 2.2 `resolved_issue.bson`

共 159,919 条记录。

| 字段 | 实际类型 | 非空比例 | 解释与使用政策 | 泄露风险 |
|---|---|---:|---|---|
| `_id` | ObjectId | 100% | 解决记录追踪 ID | 低 |
| `owner`, `name`, `number` | string, string, integer | 100% | 与快照使用完整 Issue Key 关联 | 低 |
| `created_at` | datetime | 100% | 与快照创建时间交叉核验 | 低 |
| `resolved_at` | datetime | 100% | 监督时间；暂作贡献开始时间代理，不作输入特征 | 高 |
| `resolver` | string | 100% | 监督标签，规范化为 Developer Key | 目标泄露 |
| `resolver_commit_num` | integer | 100% | 描述目标解决者，排除 | 目标泄露 |
| `resolved_in` | integer 或 string | 100% | 混合表示 Issue/Commit，语义未统一，暂不使用 | 不确定 |
| `events` | array[object] | 99.9994% 非空 | 可验证的带时间事件来源 | 必须按预测时间过滤 |

`events` 共 2,977,075 条：`type` 和 `time` 均 100% 非空；`actor`、
`assignee`、`commenter` 非空率分别为 99.7576%、4.3195%、22.1685%。
标准事件表只保留规范化身份、类型、时间、标签、Commit 标识以及是否有评论，
不复制评论正文。`source` 为整数/空值混合，语义未确认，暂不导出。

数据中未发现可供主键使用的稳定 GitHub 数值用户或仓库 ID，因此本版继续使用
规范化 Login 与 `owner/name`，不混用 `_id` 或其他推测 ID。

## 3. 主键、快照和关联协议

```text
repository_key = casefold(owner/name)
issue_key      = repository_key#number
developer_key  = casefold(resolver)
label_key      = casefold(trim(label))
```

实体整数 ID 按规范化键字典序从 0 连续分配，与 BSON 顺序无关。Developer
映射同时保存原始 `developer_login`；Repository 保存原始 `owner/name`；Label
保存原始显示值。大小写归并碰撞和拼写冲突单独统计。

快照分类严格采用：

- `before == created_at`：`creation_snapshot`；
- `before == closed_at`：`closure_snapshot`；
- 其他情况：`unknown_snapshot`。

全量得到 148,096 个创建快照、148,066 个关闭快照和 2 个未知快照；
148,068 个 Issue 有多个快照，28 个只有一个快照。未知快照不被强制解释。
创建快照输出中的 `closed_at` 强制为空，防止关闭信息回填。

两个 Collection 仅以完整 Issue Key 关联：159,919 条解决记录均具有有效键、
解决者和时间；成功关联 148,096 个，另有 11,823 个在 `dataset` 中无对应
Issue。未发现重复解决记录、冲突解决记录、时间无效记录、Issue 时间冲突、
Repository 拼写碰撞或 Developer 大小写碰撞。参考数字 148,096 与本次实际
结果一致，但实现中没有写死该数字。

## 4. 标准化产物

最终产物位于 `data/midterm/normalized/gfibot-m0-v1.1/`：

| 表 | 行数 | 说明 |
|---|---:|---|
| `developers.parquet` | 18,360 | Developer ID、规范化键和原始 Login |
| `repositories.parquet` | 97 | Repository ID、键、原始 owner/name |
| `issues.parquet` | 148,096 | Issue 实体与时间 |
| `issue_snapshots.parquet` | 296,164 | 时间化文本、标签和快照特征 |
| `developer_issue_edges.parquet` | 148,096 | `resolved` 监督边 |
| `developer_repo_edges.parquet` | 148,096 | `contributed_via_issue` 派生边 |
| `issue_repo_edges.parquet` | 148,096 | Issue 所属项目 |
| `issue_label_edges.parquet` | 401,686 | 带观察时间和快照类型的标签边 |
| `issue_events.parquet` | 2,745,332 | 成功关联 Issue 的可验证带时间事件 |
| `mappings/*.parquet` | 与实体数一致 | 四类确定性双向映射 |

全部 Parquet 带有 Schema、数据版本、原始数据版本、候选范围和时间来源元数据。
`schema_manifest.json` 记录列和行数。正式产物总计约 110 MB（Zstandard 压缩）。

## 5. 时间切分和固定训练图

原始 `resolved_at` 覆盖 2010-02-09 至 2022-06-18。仅根据全量时间分布和
保证验证/测试规模的原则，在运行模型之前固定自然年边界：

```text
train: interaction_at < 2021-01-01T00:00:00Z
valid: 2021-01-01T00:00:00Z <= interaction_at < 2022-01-01T00:00:00Z
test:  interaction_at >= 2022-01-01T00:00:00Z
```

协议为 `fixed_train_graph`：验证和测试目标边都不会加入训练图，测试时也不会
悄悄把验证目标边加入历史图。`resolved_at` 只作为无法获得真实领取/开始贡献
时刻时的代理，标记为 `time_source=resolution_proxy`。

| 指标 | Train | Valid | Test |
|---|---:|---:|---:|
| 交互/Issue | 123,481 | 17,581 | 7,034 |
| Developer | 15,144 | 3,548 | 1,913 |
| Repository | 96 | 87 | 82 |
| 单交互用户 | 9,102 | 2,053 | 1,087 |
| 多次贡献用户 | 6,042 | 1,495 | 826 |
| 冷启动用户 | 0 | 2,271 | 1,218 |
| 训练图可表示用户 | 15,144 | 1,277 | 695 |
| 训练图用户覆盖率 | 100% | 35.9921% | 36.3304% |
| 窗口内跨仓库用户 | 799 | 95 | 34 |
| 相对训练历史的新仓库行为 | 0 | 5,112 | 2,992 |
| 可评估新项目用户 | 0 | 116 | 53 |

全体 18,360 名 Developer 平均解决 8.0662 个 Issue；7,327 人有多次解决记录
（39.9074%），993 人跨多个 Repository（5.4085%）。项目级新项目推荐在
Valid/Test 仅有 116/53 名可评估用户，后续结果必须报告该有限覆盖，不能将
派生贡献边宣传为独立 Star/Fork 行为。

## 6. 历史候选协议

候选范围固定为：

```text
candidate_scope = historical_resolved_issue_subset
```

统一资格函数支持 `same_repo` 和 `cross_repo`。在预测时刻 `t`，候选必须已
创建、记录的解决时间不早于 `t`、未被当前用户历史解决，并满足仓库范围。
目标正样本在“解决事件发生前一瞬”视为仍可候选。采样先按 ID 排序，再使用
固定种子；不把用户与所有候选的笛卡尔积写盘。

这只是数据中最终被解决的历史 Issue 子集，不是任一历史时刻 GitHub 全部开放
Issue；未观察到的用户–Issue 关系不是可信负反馈。所有 148,096 个关联正样本
按现有创建/关闭时间都能构造代理推荐场景，但这不消除候选覆盖偏差。

## 7. 异常、事件和泄露审计

- 两个快照无法按严格相等规则归类，保留为 `unknown_snapshot`。
- 2 个关联 Issue 的 `closed_at` 与 `resolved_at` 不一致；时间原值保留。
- `resolved_issue` 中有 10,646 个 `reopened` 事件，说明关闭状态可能反复变化。
- 成功关联事件中有 217 条早于 Issue 创建时间、439,964 条晚于解决时间；均保留
  供审计，但任何预测构图必须按 `event_at < prediction_time` 过滤。
- 创建快照中暴露 `closed_at` 的数量为 0；快照表不含 `resolver`、
  `resolved_at`、`resolver_commit_num`。
- Repository 统计只保存在快照中，不生成“最新值”静态特征。
- Developer–Repository 边明确标记 `contributed_via_issue` 并保留源 Issue ID。

## 8. 验证、版本和重新运行

自动验证覆盖：BSON/BSON.GZ 流式读取、截断错误、缺失字段、大小写规范化、
标签去重、输入顺序无关映射、冲突关联隔离、多快照去重、快照分类、候选时间
条件、确定性采样、固定训练图、未来字段隔离、Parquet 引用完整性、连续 ID、
Schema 元数据和拒绝覆盖已有版本。

每个成功目录包含 `_SUCCESS`。更改原始数据、Schema、切分边界或字段政策时，
必须修改 `output_version`，不能覆盖 `gfibot-m0-v1.1`。完整质量报告和切分协议：

```text
data/midterm/validation/gfibot-m0-v1.1/data_quality.json
data/midterm/validation/gfibot-m0-v1.1/schema_manifest.json
data/midterm/graphs/gfibot-m0-v1.1/split_manifest.json
```

仓库中缺少任务描述所引用的 `docs/OSS-Mentor_多粒度推荐模型设计_v1.md`；
本实现依据实际存在的 `CODER/AGENTS.md`、阶段计划、阶段 0 文档和本次任务规范。
