# 推荐库存扩充与选项保障 v0.3

## 目标

本轮同时扩充“零贡献 / 首次贡献者”和“进阶开发者”候选池，并避免界面展示实际没有任务的选项。库存口径只计算健康仓库中经 GitHub 验证、当前状态为 `eligible` 且已完成特征提取的任务。

## Wave 3 仓库

新增 12 个启用仓库，使配置从 10 个扩展到 22 个：

| 语言 | 仓库 |
|---|---|
| JavaScript | `nodejs/node`、`vercel/next.js` |
| TypeScript | `storybookjs/storybook`、`excalidraw/excalidraw` |
| Java | `quarkusio/quarkus`、`elastic/elasticsearch`、`apache/dubbo` |
| Go | `kubernetes/kubernetes`、`prometheus/prometheus` |
| Rust | `nushell/nushell`、`rust-lang/rust-clippy`、`facebook/pyrefly` |

这些仓库在 2026-07-16 通过 GitHub 当前仓库元数据和贡献页面筛选。配置中的新人标签只用于发现；同步时仍会逐条复核 Issue 是否开放、未分配、未锁定、没有阻塞标签和关联开放 PR。

## Wave 4 零组合定向补齐

在 Wave 3 回填后，仍有新人 `Python × 重构`、`JavaScript × 功能开发`、`Java × 重构` 以及进阶 `Java × 重构` 为零。Wave 4 新增 5 个仓库，使启用配置达到 27 个：

| 缺口 | 仓库 | 当前 GitHub 证据 |
|---|---|---|
| Python × 重构 | `pytorch/ao`、`pytorch/pytorch` | contribute 页面存在标题明确包含 refactor 的 good first issue |
| JavaScript × 功能开发 | `nodejs/undici` | contribute 页面存在多条同时带 good first issue 和 enhancement 的任务 |
| Java × 重构 | `trinodb/trino`、`jenkinsci/jenkins` | contribute 页面存在 Refactor 或 cleanup 标题的 good first issue |

目标任务中包含较早但仍开放的 Issue，因此 Wave 4 使用每仓库 100 条的发现上限；最终是否进入推荐池仍以同步时的 GitHub 状态、分配、锁定和关联 PR 复核为准。

Wave 4 回填后，Trino 的明确重构任务因已经分配而被正确排除，新人 `Java × 重构` 仍为零。Wave 5 因此新增 `apache/pinot`：其当前 contribute 页面同时列出多条未分配、标题或标签明确包含 refactor/cleanup 的 good first issue，为最后一个组合提供冗余来源。配置随之达到 28 个启用仓库。

第一次 Wave 5 回填显示 Ecosyste.ms 的标签索引没有及时包含 GitHub contribute 页面上的这些任务。因此候选发现逻辑同步调整为：候选标签优先查询 GitHub 当前开放 Issue，再由 Ecosyste.ms 标签结果和近期 Issue 补足；所有发现结果仍逐条执行 GitHub 状态和关联 PR 复核。

## 库存保障规则

- 语言和任务类型是硬约束，不再用偏好加分代替过滤。
- 自定义画像界面通过 `/api/v1/recommendation-options` 计算当前真实库存。
- 零库存选项会被禁用；当前组合为零时，推荐按钮不可用。
- 提交推荐前再次检查库存，避免快速切换选项造成竞态。
- 少于 5 条的选项会显示低库存提示。
- 聚合报告的运营目标为：单项至少 10 条、语言×任务类型组合至少 5 条、新人任务至少 100 条并以 130 条作为缓冲目标、任一仓库占比不高于 30%。
- 若某个组合达不到目标，界面以真实库存为准，不承诺不存在的数据；后续采集根据报告缺口定向扩仓。

## 执行顺序

真实同步需要用户在自己的 PowerShell 会话中设置只读 `GITHUB_TOKEN`：

```powershell
python -m oss_mentor sync-candidates `
  --wave 3 `
  --limit-per-repo 40 `
  --output data/reports/wave3_sync_40.json `
  --allow-network

python -m oss_mentor extract-features

python -m oss_mentor candidate-report `
  --output data/reports/candidate_pool_v0.3.json
```

最后一个 Java 新人重构缺口运行：

```powershell
python -m oss_mentor sync-candidates `
  --wave 5 `
  --limit-per-repo 100 `
  --output data/reports/wave5_java_refactor_100.json `
  --allow-network

python -m oss_mentor extract-features

python -m oss_mentor candidate-report `
  --output data/reports/candidate_pool_v0.3.json
```

零组合定向补齐运行：

```powershell
python -m oss_mentor sync-candidates `
  --wave 4 `
  --limit-per-repo 100 `
  --output data/reports/wave4_zero_fill_100.json `
  --allow-network

python -m oss_mentor extract-features

python -m oss_mentor candidate-report `
  --output data/reports/candidate_pool_v0.3.json
```

同步前可删除 `--allow-network` 并增加 `--dry-run` 查看仓库、标签和请求量，不会访问网络。报告 JSON 与本地数据库继续由 `.gitignore` 排除；提交到 Git 的 Markdown 只包含聚合统计。
