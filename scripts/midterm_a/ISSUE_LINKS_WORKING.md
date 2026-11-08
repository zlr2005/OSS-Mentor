# Issue → PR/commit → 文件：第一轮工作结果

状态：working，frozen=false，未正式交付、未建立实验划分。
本轮在原 100 commit / 54 条文件贡献上增加历史任务关联，保留原始数据。

## 实测结果

从 37 个有选中 Python 文件的 commit 中，优先抽取 3 个带关闭关键词的 commit，
再增加 1 个无该关键词的对照。**这是有偏的小样本，不代表仓库整体关联率。**

| 项目 | 实测 |
|---|---:|
| 抽查 commit | 4 |
| 找到关联 PR 的 commit | 4 |
| 不同 PR | 4 |
| 有明确 Issue 解决证据的 PR | 3 |
| 确认解决的不同 Issue | 3 |
| 与已有代码样本重叠的文件关联证据 | 5 |
| 当前正文的路径提示 | 0 |
| 排除的非 Issue 引用 | 1（#9125 实为 PR） |

样本内 commit→PR 为 4/4，PR→明确 Issue 解决为 3/4，
已确认 Issue→样本文件为 3/3。分母与抽样方式必须同时报告，不能写成模型准确率。

| Issue | 解决 PR | 关联到的已有样本路径 |
|---|---|---|
| [#9471](https://github.com/pytest-dev/pytest/issues/9471) | [#9475](https://github.com/pytest-dev/pytest/pull/9475) | `src/_pytest/pytester.py`、`src/_pytest/pytester_assertions.py` |
| [#9488](https://github.com/pytest-dev/pytest/issues/9488) | [#9498](https://github.com/pytest-dev/pytest/pull/9498) | `doc/en/example/nonpython/conftest.py`、`src/_pytest/nodes.py` |
| [#9478](https://github.com/pytest-dev/pytest/issues/9478) | [#9532](https://github.com/pytest-dev/pytest/pull/9532) | `src/_pytest/config/__init__.py` |

对照 PR #9585 在本轮采集规则下未找到明确 Issue 结果，不补造标签。
实际路径、时间与来源见工作数据的 records.json 和 manifest。

## 怎样判断关联可靠

1. commit→PR 使用 GitHub 的 commit 关联 PR 接口；不靠标题中的数字猜测。
2. 关闭关键词只用于寻找候选。普通引用、相同关闭时间、closed 但未合并的 PR 都不足以确认解决关系。
3. 使用关闭事件的 commit 证据，或 GitHub Issue 页面结构化数据中明确的
   closedByPullRequestsReferences，并核对 Issue、PR、merged 状态和关闭时间。
4. PR 文件列表与本地已有代码样本取交集。未选中的文件不凭空增加代码版本。

官方语义参考：[commit 关联 PR](https://docs.github.com/en/rest/commits/commits#list-pull-requests-associated-with-a-commit)、
[链接 PR 与 Issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)。
Issue 页面只解析结构化 JSON，不执行页面脚本；结构变化或缺证据时保留未确认状态。

文件关联表示“该解决 PR 修改了这个样本文件”，不证明这个文件单独造成修复。
记录带 mapping_scope 和 causal_file_attribution_verified=false；confidence=null，未编造置信概率。
解决者 developer_key 保留 null，不把提交者、PR 作者或关闭操作者直接当作唯一解决者。

## 时间与特征使用

- 解决 PR 的文件关联全部为 outcome_only，available_at 不早于关闭、合并与样本代码时点。
- 当前抓取的正文和标签不假装成历史版本。正文路径提示若出现，使用 observed_text_hint，
  available_at 为正文观察时间；不能回填成 Issue 创建时间。
- 本轮实际没有可靠的历史事前路径提示，不伪造这部分覆盖率。
- 历史 Issue 一律从在线库存排除；当前真实开放候选仍由既有采集/刷新流程维护。

## 位置与操作

当前工作数据：`data/midterm/working/pytest_issue_links_v0.3/`
工作数据库：`data/midterm/working/pytest_issue_links.sqlite3`
原始来源缓存：`data/midterm/sources/github/pytest_issue_links_v0.1/`
质量与验证记录：`data/midterm/validation/a/pytest_issue_links_working.json`
数据版本：`git-issue-history-a7c2417dde00cd9698c7`。

v0.1、v0.2 是匿名 API 限流时的部分结果，错误已记录，不视为“没有关系”；
当前使用 v0.3。通过已连接 GitHub 只读接口补齐元数据，没有向 GitHub 发表评论或写入数据。
连接器来源单独标记 github_connector；未知的 HTTP 状态、响应头与 API 版本保持未知，
不伪装成直接 HTTP 抓取。实际工具读取结果可经 import_connector_reads.py 导入缓存。

查看当前质量与数据库状态：

```powershell
$env:PYTHONPATH = "src"
python scripts/midterm_a/inspect_working_dataset.py data/midterm/working/pytest_issue_links_v0.3 --database data/midterm/working/pytest_issue_links.sqlite3 --output data/midterm/validation/a/pytest_issue_links_working.json
```

离线重建（选择不存在的工作目录；不会冻结）：

```powershell
python scripts/midterm_a/enrich_issue_links.py data/midterm/working/pytest_history_100_v0.1 --repo pytest-dev/pytest --source-dir data/midterm/sources/github/pytest_issue_links_v0.1 --working-dir data/midterm/working/pytest_issue_links_rebuilt --commits 4
```

缺少来源缓存时可加 --allow-network；限流、超时与缺失记录为来源失败，不能记成负样本。
新扩充批次应使用新工作目录和数据版本，不覆盖旧结果。数据库中的多个版本不相加计数。

本轮验证覆盖普通引用不算解决、未合并 PR、关闭原因、重新打开、截止日期、跨仓库同号、
当前正文不回填历史时间、非 PR 文件排除、HTML 缓存、连接器元数据、来源篡改和重复入库。
A 专属 64 个测试通过；关联与质量统计模块 14 个测试额外回归通过。
