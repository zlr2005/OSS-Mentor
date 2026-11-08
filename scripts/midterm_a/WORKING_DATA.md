# pytest 历史扩充工作数据（未冻结）

按用户要求，本批只完成扩充、入库与验证，先放在本地。
状态为 working、frozen=false、team_acceptance=not_requested。
没有建立实验划分，没有生成正式交付包，没有发送团队消息。
数据版本和文件校验和仅用于追溯本次内容，不表示完成实验冻结。

验证状态：A 专属 50 个测试通过；工作库 integrity_check=ok，外键检查无异常。

## 实际结果

固定起点：9d2ffe207bfea9a8b57452bafb9961aba67edf7a。
选择该提交祖先中按 Git 拓扑顺序排列的 100 个 commit，时间截止 2022-02-01T00:00:00Z。
不宣称这是默认分支某个日期区间的全部历史。

| 指标 | 实测值 |
|---|---:|
| commit（包括上下文） | 100 |
| 只作上下文、不归属文件贡献的 merge commit | 32 |
| 有本次选中 Python 文件的 commit | 37 |
| 开发者来源身份 | 11 |
| 直接关联 GitHub 的开发者身份 | 10 |
| 未确认 GitHub 身份 | 1 |
| 逻辑文件 | 30 |
| 文件版本 / 文件贡献 | 54 / 54 |
| 可用代码 | 54 / 54（100%） |
| 有直接 GitHub 作者证据的贡献 | 52 / 54（约 96.30%） |
| 实际改名样本 | 0（改名逻辑有合成测试） |
| Issue / Issue—文件关联 | 0 / 0 |

每个非 merge commit 最多选择 2 个 Python 修改文件，其余文件未纳入。
未确认作者使用来源内的精确 Git 作者标识散列，不按昵称、邮箱相似性跨来源合并。
同一精确 Git 标识不等于已经确认了对应 GitHub 账号；身份状态仍保持 unconfirmed。

## 存放位置

- 工作数据：`data/midterm/working/pytest_history_100_v0.1/`
- 独立工作库：`data/midterm/working/pytest_history_100.sqlite3`
- 持久映射：`data/midterm/sources/git/pytest_history_100/identity_map.json`
- Git 对象缓存：`data/midterm/sources/git/pytest_history_100/repository.git/`
- API 请求缓存：`data/midterm/sources/github/pytest_history_v0.1/`
- 质量记录：`data/midterm/validation/a/pytest_history_100_working.json`

本批数据版本：`git-history-59f706014f3d2c0c6b24`。
旧的 3 commit 小样本与原独立运行库保留；本次扩充放在工作库中。

## 查看与验证

在仓库根目录使用 PowerShell：

```powershell
$env:PYTHONPATH = "src"
python scripts/midterm_a/inspect_working_dataset.py data/midterm/working/pytest_history_100_v0.1 --database data/midterm/working/pytest_history_100.sqlite3 --output data/midterm/validation/a/pytest_history_100_working.json
```

status=completed 仅表示数据库导入完成；dataset_lifecycle=working 和 frozen=false 表示仍未冻结。
重复导入相同工作数据不会增加同版本记录；不同数据版本隔离存放，不把多版本计数相加宣传规模。

```powershell
python scripts/midterm_a/dataset_pipeline.py --database data/midterm/working/pytest_history_100.sqlite3 import data/midterm/working/pytest_history_100_v0.1
```

离线重建（选择尚不存在的 working-dir）：

```powershell
python scripts/midterm_a/collect_git_history.py --repo pytest-dev/pytest --anchor 9d2ffe207bfea9a8b57452bafb9961aba67edf7a --until 2022-02-01T00:00:00Z --git-dir data/midterm/sources/git/pytest_history_100/repository.git --source-dir data/midterm/sources/github/pytest_history_v0.1 --identity-map data/midterm/sources/git/pytest_history_100/identity_map.json --working-dir data/midterm/working/pytest_history_100_rebuilt --commits 100 --files-per-commit 2
```

Git/API 缓存缺失时，先获取 Git 对象；采集命令加 --allow-network 可补 API 元数据。
所有网络请求为公开来源只读请求。未配置 token 时，本次通过 3 个 API 分页请求确认所需元数据。
每次成功 API 响应立即缓存，失败后重跑可继续；Git 本地缓存保留已获取对象。

本机最初 Git 未自动使用系统代理。下列命令只为本次下载设置参数，不修改全局 Git 配置：

```powershell
$target = Join-Path (Get-Location) 'data/midterm/sources/git/pytest_history_100/repository.git'
if (-not (Test-Path -LiteralPath $target)) { git init --bare $target }
$proxy = [System.Net.WebRequest]::DefaultWebProxy.GetProxy([uri]'https://github.com/').AbsoluteUri
git --git-dir=$target -c credential.helper= -c core.hooksPath=NUL -c http.proxy=$proxy -c http.sslBackend=openssl -c http.version=HTTP/1.1 fetch --no-tags --depth=150 https://github.com/pytest-dev/pytest.git 9d2ffe207bfea9a8b57452bafb9961aba67edf7a
```

## 映射与证据

Git commit/tree/blob 原始对象均验证 Git 对象散列；导出的工作数据带有用到的原始对象，
本地检查不需要 Git、网络或原始裸仓库。采集本身使用 Git，但不 checkout 或执行上游代码。
目录树保留完整结构；文件内容只取该 commit 中的 blob。

作者和提交者分开。GitHub 身份仅来自包含同一 commit SHA 的 API 元数据；
API 的 tree SHA/时间与本地 Git 对象核对。不将所有改动算作 committer 的贡献。
merge commit 不生成文件作者边，避免把合入代码归属给合并者。

改名关联只使用父子树中唯一的相同 blob 移动，明确标记为推断证据，不称为 Git 原生改名事件。
改名同时编辑的文件暂不自动合并。持久映射保留已存在 key；若新证据连接两个已存在 key，
记录 rename_conflicts/previous_file_key，不改写历史 ID。

本批保留 1 个未确认来源身份、无 Issue 标签、未补历史 star。
它仍是单仓库工作样本，不能直接宣称已完成 CODER 训练、复现或推荐效果验证。
