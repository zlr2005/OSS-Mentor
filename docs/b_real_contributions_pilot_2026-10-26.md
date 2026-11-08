# B 的首批真实贡献记录与代码快照

本批使用真实 GitHub REST 响应生成，`fixture_only=false`。采集范围为当前候选库中的 `pytest-dev/pytest`、`pydantic/pydantic`、`Textualize/textual`。各仓库取截止 `2026-10-26T00:00:00Z` 前默认分支最近 10 个 commit，每个 commit 最多选择 2 个 Python 变更文件。它是有上限的小样本，不是完整贡献历史，也没有限定为 B 的最终目标用户。

## 本轮完成结果

| 仓库 | 抽样 commit | 逻辑文件 | 代码版本／文件贡献 | 已关联账号的贡献 |
|---|---:|---:|---:|---:|
| pytest-dev/pytest | 10 | 8 | 13 | 13/13 |
| pydantic/pydantic | 10 | 7 | 8 | 8/8 |
| Textualize/textual | 10 | 6 | 11 | 9/11 |
| 合计 | 30 | 21 | 32 | 30/32 |

32 个代码版本均 available，代码缺失率 0/32；2 条贡献作者账号关联未确认，保留来源，不强制合并。19 条 project_behaviors 全部是 contribution。各仓库开发者记录数之和为 10，不作为跨仓库去重人数。

实际覆盖时间：pytest 为 2026-10-24 至 2026-10-25，pydantic 为 2026-10-22 至 2026-10-23，Textual 为 2026-06-24 至 2026-10-09（均 UTC）；按最近 10 个 commit 抽样，各仓库时间跨度不一致。

3/3 快照均 source_verified；187 个快照文件 SHA-256 核验通过。独立验证库导入 completed，第二次不同 chunk-size 导入结果完全一致；integrity_check=ok，外键错误为 0。采集器现有 13 项离线测试通过。完成数据约 15.5 MB，未生成压缩包。

## 本地数据位置

根目录为 `data/b_real_contributions_20261026_v1/`，Git 不会自动提交该目录。

- `collection_plan.json`：固定选择条件。
- `summary.json`：采集完成后的各仓库版本、时间、记录数量、质量统计及失败状态。
- `snapshots/<owner>__<repo>/manifest.json`：每个仓库的不可变数据版本、时间范围、逐文件 SHA-256、已知缺口、历史 MIT 许可及来源索引。
- `snapshots/<owner>__<repo>/records.json`：ContributionRecord、CodeDocument 等对应实体。
- 快照内 `code/`（具体路径以 content_location 为准）、`licenses/`、`trees/`、`provenance/`：内容和证据。
- `SHA256SUMS`：所有快照文件的 SHA-256，包含 manifest 自身。
- `verification.json`：离线来源校验、重复导入及 SQLite 完整性验证结果。

本批没有提交或推送 GitHub。发布时应另行准备适合共享的证据包；现有来源 manifest 要求原始 API 证据保留本地，其中可能包含公开 Git 作者邮箱。不能只删掉 provenance 后仍把同一个快照称为来源校验通过。

## 字段和解释

`records.json.contributions` 包含 developer_key、repository_key、file_key、file_version_key、commit_sha、event_at、author_at、observed_at、behavior_type、author_association_status、evidence_location、source_location。commit_sha 对应贡献发生时的文件版本，不使用最新分支代码替代。

`records.json.code_documents` 包含文件路径、语言、commit_sha、content_hash、content_location、availability 和 exclusion_reason。代码可用时同时验证 Git blob SHA-1、字节长度及内容 SHA-256；删除、过大等情况保留 missing 和原因。

identity_status/author_association_status=verified 仅表示 GitHub API 将该 commit 作者关联到一个 numeric account ID，不代表实名验证、代码能力认证或人工审查。机器人账号没有自动当作真人排除；B 可结合来源自行判定。未关联作者保持独立、不凭姓名邮箱推测合并。

event_at 采用 commit 的 committer 时间；author_at 单独保留作者时间，observed_at 是 API 观察时间。B 做历史画像时应明确使用时间口径，并检查时间边界。当前仓库状态与当前作者账号关联不能直接当作历史时点已知特征。

manifest 的 quality_gaps 记录采样局限。每个 commit 最多读取首 100 条变更路径、选取前 2 个 Python 文件，可能漏掉其他文件；没有完整代码所有权结论。developers 还可能包括选中 commit 中没有入选 Python 文件的作者，构造文件贡献画像应从 contributions 出发。跨仓库汇总数量是各快照之和，开发者可能重复。

本批 project_behaviors 若包含记录，其类型是 contribution。没有将其冒充 star、fork 或拥有仓库等弱兴趣行为；后者仍待采集。没有新增 Issue—文件关系、模型训练、向量或 CODER 推理结果。

## B 的离线读取与验收

在项目根目录运行（数据目录已复制到本机后）：

```powershell
$env:PYTHONPATH = 'src'
python scripts/midterm_a/verify_b_real_pilot.py data/b_real_contributions_20261026_v1
```

校验器核对 SHA256SUMS，调用真实来源校验器，并导入独立的 `validation.sqlite3`。重复导入必须同批次、同计数；integrity_check=ok，外键错误为 0。不写入业务推荐库。

程序读取入口：

```python
from pathlib import Path
from oss_mentor.datasets.snapshot import load_snapshot

manifest, records, contents = load_snapshot(
    Path('data/b_real_contributions_20261026_v1/snapshots/pytest-dev__pytest')
)
for contribution in records['contributions']:
    print(contribution['developer_key'], contribution['file_version_key'])
```

`load_snapshot` 校验失败应保留失败原因，不跳过来源校验。B 可通过 file_version_key 连接 code_documents，再根据 content_location 读取 contents 中的原始字节。

## 采集复现

```powershell
$env:PYTHONPATH = 'src'
python scripts/midterm_a/collect_b_real_pilot.py --directory data/b_real_contributions_20261026_v1 --until 2026-10-26T00:00:00Z --allow-network
```

使用本机已有 gh 登录，令牌仅在进程内存中使用。成功请求缓存可复用，既有快照不覆盖，重跑会重新校验。首次网络响应固定了本批默认分支和作者关联观察状态；未来从空缓存重新采集可能产生新的版本，不能保证远端状态不变。
