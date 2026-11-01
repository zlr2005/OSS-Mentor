# 中期画像问卷 v0.1

本目录由成员 B 维护。`questionnaire_v0.1.json` 是中期阶段的首版问卷和特征映射配置，包含 8 道基础题，以及代码实现、质量测试、技术文档三个方向各 2 道情境题。

## 解释边界

- 问卷结果是自评和当前意图证据，不是经过心理测量或技能考试验证的能力分数。
- 配置中的 `value` 是供模型试验使用的有序代理特征，不能在产品或答辩中解释成成功概率或精确熟练度。
- `confidence` 表示对该条证据含义的保守置信，不表示用户完成任务的概率。
- 未回答、非适用方向和缺少外部证据必须通过 `observed_mask=false` 保留为未知，不能当作低能力零分。
- 本期收集的回答只描述提交时点的状态，不能倒填到历史训练时点。
- 用户确认和锁定优先；直接贡献、自评和弱兴趣信号发生冲突时必须展示冲突，不静默覆盖。

## 当前状态

这一版提供 B 模块内部的确定性评分、GitHub 贡献证据解释、证据融合、确认规则、
确认后特征交付和固定 fixture。
确认规则使用私有字典结构，不保存数据库，也不接收浏览器提交的用户身份。
尚未完成的内容包括：

- D 的 `contracts_midterm.py` 适配；
- 问卷回答、预览和确认记录的 SQLite `012` 迁移；
- D 负责的 HTTP API 和页面；
- A 的真实贡献数据公共契约适配（当前已有与计划字段对齐的私有适配器）；
- C 的 CodeBERT 编码产物适配；
- 基于真实样本的权重校准和误差分析。

在 S0 公共契约冻结前，不应把本模块的私有字典结构复制成第二套公共契约。

## 画像确认规则

`profiling.confirmation` 将尚未保存的 assessment preview 与当前特征状态进行比较，
生成带稳定建议 ID 的确认计划。调用方只提交建议 ID 对应的 `accept` 或 `reject`；
没有提交决定的建议继续保持待处理。确认函数返回新的内存画像版本和审计结果，
`persisted=false` 明确表示还没有写入数据库。

主要入口：

```python
from oss_mentor.profiling.confirmation import (
    build_feature_state,
    build_confirmation_plan,
    confirm_assessment_plan,
    set_feature_lock,
)
```

- `build_feature_state` 为测试或适配器建立完整特征状态；未提供的字段默认为未知。
- `build_confirmation_plan` 只为有变化或新增证据的已观察字段生成建议。
- 当前画像或预览中标记为锁定的字段会生成 blocked 建议，不能接受或拒绝。
- 冲突建议不会自动生效；接受或拒绝都会记录确认时已展示冲突。
- `confirm_assessment_plan` 检查 `base_profile_version`，旧计划确认新版画像时失败。
- 接受只更新对应字段；拒绝保持原值；未处理建议 ID 保留在结果中。
- `set_feature_lock` 表示用户显式锁定或解锁，并生成新的画像版本。
- 所有函数都不修改输入；相同输入与时间生成相同计划、建议 ID 和结果。

固定确认场景位于 `fixtures/midterm/b/confirmation_scenarios_v0.1.json`，覆盖：
全部接受、全部拒绝、部分处理、冲突确认、当前字段锁定、预览锁定、过期版本、
重复确认、未知建议 ID、未知字段保持未知，以及解锁后重新确认。

这一阶段的 `profile-feature-state-v0.1` 是 B 模块内部特征状态，不能直接当作
`DeveloperProfileV2` 或 D 的持久化契约。真实保存时由后续适配器把 session 用户、
数据库记录和公共契约接入这些纯业务函数。

## C / D 最小交接样例

本节对应 B 的第一阶段交付。全部样例是合成 fixture，即使场景描述写有
“真实贡献经历”，其中的 `fixture:commit:*`、`fixture:pr:*` 也只是模拟引用。
当前导出来自尚未确认的预览，供接入测试；不是已保存画像或正式训练数据。
无需 OAuth、数据库、网络、GPU 或第三方 Python 依赖，使用 Python 3.11+。

在仓库根目录执行（PowerShell）：

```powershell
python scripts/midterm_b/export_assessment_handoff.py --output data/midterm/validation/b/assessment_handoff_v0.1.json
$env:PYTHONPATH = "$PWD/src"
python -m unittest discover -s tests/midterm_b -p "test_*.py" -v
```

输出文件已存在时脚本报错并保留原文件；复跑可选择新文件名。
不传 `--output` 时，完整 JSON 写到标准输出。脚本也可从其他目录通过绝对路径执行。
输出位于已忽略的 `data/` 内，团队通过脚本和小 fixture 重建，无需提交生成的大 JSON。

输出包包含问卷、六类输入、预览、特征导出、检查摘要及两份输入文件的 SHA-256。
同一份代码和输入应生成相同字节；脚本成功时打印产物 SHA-256。
`bundle_format` 只是本脚本的样例包装格式，不是 D 的公共业务契约。

| 场景 ID | 接收方重点检查 |
|---|---|
| `zero_history` | 无外部贡献也能生成预览；`capability.debugging` 为未知 |
| `experienced_contributor` | 模拟直接证据选出 `capability.code_change=0.75` |
| `direction_change` | 当前兴趣为 quality；历史 documentation 证据仍保留 |
| `evidence_conflict` | `capability.code_change=0.25` 且 `conflict=true`，需展示冲突 |
| `partial_evidence` | 非适用方向的 `capability.change_scope` 保持未知 |
| `manual_lock` | `capability.code_change=0.25` 且 `blocked_reason=field_locked` |

### 直接调用 B 模块

从仓库根目录启动 Python，先按上面的命令设置 `PYTHONPATH`：

```python
import json
from pathlib import Path
from oss_mentor.profiling.assessment import (
    load_questionnaire, build_assessment_preview, export_enhanced_features,
)

questionnaire = load_questionnaire("config/midterm_profile/questionnaire_v0.1.json")
scenario = json.loads(
    Path("fixtures/midterm/b/assessment_scenarios_v0.1.json").read_text(encoding="utf-8")
)["scenarios"][0]
preview = build_assessment_preview(
    questionnaire,
    scenario["submission"],
    external_evidence=scenario["external_evidence"],
)
features = export_enhanced_features(
    preview,
    profile_key="fixture:zero_history",
    profile_version="fixture-preview-v0.1",
    as_of=scenario["submission"]["submitted_at"],
)
assert preview["saved"] is False
assert len(features["feature_names"]) == len(features["values"]) == 23
assert len(features["observed_mask"]) == len(features["evidence_refs"]) == 23
```

当前函数不接收完整当前画像，也不更新 `DeveloperProfileV2`。确认值和锁定值只能通过
受信任的 `external_evidence` 传入；最终的当前值/建议值差异、确认和持久化由下一阶段实现。

### 给 C：特征含义

- 按 `feature_names` 同一索引读取 `values`、`observed_mask` 和 `evidence_refs`。
  当前共有 23 维，顺序固定为特征名排序；接入时检查 `feature_schema_version`。
- `observed_mask=false` 的数值 0 仅为占位，不能解释成低能力。
  已回答问题中未选中的分类项也可能为 0，但其 mask 为 true。
- `current_interests` 单独表达当前方向。不要把兴趣或环境偏好解释为能力等级。
- `profile_version=fixture-preview-v0.1`、`profile_key=fixture:*` 仅用于样例；
  `developer_key=null`，没有与历史开发者身份做关联。
- 这些接口只验证输入消费方式。正式模型需要确认后的画像、真实版本及时间审计；
  当前函数不会自动过滤未来证据，也不能把今日问答倒填到历史实验。

### 给 D：预览和错误含义

- `features` 每项包含 `proposed_value`、`confidence`、`source_kind`、
  `evidence_refs`、`observed`、`conflict`、`blocked_reason`。
  `saved=false` 表示没有保存；调用导出函数也不会确认画像。
- 不适用方向的情境题不应提交；缺必答题、未知选项、问卷版本不符、
  无效时间和未知证据特征会抛出 `ValueError`。HTTP 错误转换由 D 的契约决定。
- 本模块不提供 session 鉴权；真实用户身份和锁定/确认记录需由服务端注入，
  不能信任浏览器自行提交的 `user_locked` 或 `user_confirmed` 证据。
- 示例的 `as_of` 取固定提交时间，保证复现；不表示已完成历史时点验证。

### 本阶段接收标准与后续依赖

C 验证 23 维顺序、mask 和当前兴趣可以消费；D 验证问卷选项、条件题及预览状态可展示。
两方都需记录验收的提交 SHA、命令和结果。当前测试命令显式发现 `tests/midterm_b`；
共享 CI 是否登记这一目录由 D 确认，不能仅凭本地通过认定 CI 已覆盖。

下一阶段仍需：D 的 S0 契约及迁移编号确认；B 的回答、预览和确认记录持久化；
A 的真实本人贡献记录；C 的统一编码产物。确认业务规则已经实现，但此交付没有实现
公共 API、页面或真实贡献数据接入。

## GitHub 贡献证据解释

`profiling.evidence.interpret_github_contributions` 消费计划约定的 `ContributionRecord`
和 `CodeDocument` 字段，输出可直接传入 `build_assessment_preview` 的保守证据行，并为
每条原始记录保留审计状态。规则只对已确认身份、预测时点之前且能定位到可用文件的
记录生成能力证据；身份未确认、未来事件、缺少文件快照和不可用文件均保留排除原因。

- 源代码修改支持 `capability.code_change`，但不会因 commit 数增加而提高能力值；
- 测试文件修改支持 `capability.testing`，只对独立测试设计给出保守的低等级证据；
- 文档文件修改支持 `capability.documentation`；
- review 只能证明参与经历，不能单独证明实现、调试或测试设计能力；
- 完全没有 GitHub 历史和采集失败都是合法输入，结果保持未知；
- 重复导入按稳定记录标识去重并报告 `duplicate_count`；
- 输出保留仓库、文件版本、commit、时间、行为和来源位置，便于追溯。

固定输入位于 `fixtures/midterm/b/github_evidence_scenarios_v0.1.json`。当前只实现无需
A/C 最终产物即可验证的路径规则；CodeBERT 语义信号必须使用 C 冻结的编码 manifest，
不会在 B 模块下载或维护第二套模型。

## 确认后增强画像交付

`profiling.delivery.build_confirmed_feature_snapshot` 只接受通过确认规则得到的完整
`profile-feature-state-v0.1`，导出与 C 约定一致的有序值、缺失 mask、锁定 mask、兴趣和
证据引用。以下命令用合成 fixture 生成可重复的正式目录结构：

```powershell
python scripts/midterm_b/export_confirmed_profiles.py `
  --source-commit <当前提交 SHA> `
  --snapshot-version b-w1-fixture-v0.1
```

生成内容：

- `data/midterm/profiles/<version>/enhanced_profile_features.json`
- `data/midterm/profiles/<version>/manifest.json`
- `data/midterm/validation/b/<version>.json`

脚本拒绝覆盖已有版本。manifest 记录输入与产物 SHA-256、代码提交、特征版本、数量和
限制；验证记录包含数组对齐、未知值占位、冲突数量及确认前后差异。当前数据明确标记为
`synthetic_fixture`，不能当作真实研究参与者、正式训练集或能力概率。
