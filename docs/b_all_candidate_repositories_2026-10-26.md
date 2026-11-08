# B：全部候选仓库的贡献与代码样本采集

目标：两份业务库 task_candidate 涉及仓库的并集，共 309 个仓库。记录各库候选数量和 GitHub numeric repository ID，采集时检查数字身份。每个仓库尝试取得截止 2026-10-26T00:00:00Z 前最近 10 个 commit，每 commit 最多选择 2 个识别的源文件；初步覆盖并不等于全历史、全部开发者或全部文件覆盖。

数据根目录为 `data/b_all_repos_20261026_v1/`。jobs.json 是逐仓库断点，progress.json 是计数，report.md 是可读进度，runner_state.json 记录当前仓库，network_state.json 记录请求时间和额度等待。目录下 snapshots 使用仓库名散列作为短路径，映射见 jobs.json.snapshot，以避免 Windows 路径过长。

复用首批 pytest、pydantic、Textual 快照，验证来源、截止时间和数字身份后纳入本轮。新增仓库使用 github-history-v2，支持 Python、Java、TypeScript、JavaScript、Rust、Go、C/C++、C#、Vim Script、Groovy 等扩展名。语言标签来自明确的文件扩展名映射，不是语法分析结果。

新版本保留 GitHub 当前仓库 SPDX 声明和固定 commit 的历史许可文本，核对文本 blob 和目录树；不会把当前声明冒充历史许可分类或自动发布许可。没有明确许可声明、找不到历史许可、树被截断、没有选中源文件、来源访问失败等情况均保留具体原因。无法访问不等于没有贡献。既有 github-history-v1 样本仍按原 MIT/Python 规则验证。

每个已完成仓库均通过 source_verified；包含作者关联状态、commit、文件版本、代码内容哈希、证据位置、缺失率和身份关联统计。verified 仅表示 GitHub 的 commit 作者账号关联，不代表实名或技术能力验证。机器人也可能有账号关联。

采集串行运行、成功 API 请求缓存复用；接近额度下限时自动等待 reset，每次睡眠不超过 30 秒；失败仓库再尝试一轮后标为 unresolved。各仓库独立完成，失败不丢弃其他仓库成果。大型仓库的全量目录树可能被 API 截断，这类仓库需要后续专用采集方案。

```powershell
$env:PYTHONPATH = 'src'
python scripts/midterm_a/collect_b_candidate_repositories.py --directory data/b_all_repos_20261026_v1 --until 2026-10-26T00:00:00Z
```

恢复前先确认同目录没有运行中的采集器，不能双写断点。仅使用本机现有 gh 凭据，令牌不写入文件。退出后的最终状态为 finished 或 finished_with_unresolved；程序退出后不会自行重新启动。

本轮不自动提交、推送 GitHub，不生成压缩包，不写入业务推荐库，不补造弱兴趣行为或推理结果。全部采完后以 jobs.json 报告真实成功比例，再针对未解决仓库补采，不能把“排入队列”算作“已覆盖”。
