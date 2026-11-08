# 进阶项目推荐首批工作库（2026-10-23）

用户要求从现有仓库整理进阶项目与持续贡献路径，推荐对象不限于 Issue。

- 只读来源：`D:/OSS-Mentor/data/working/merged_candidates_v0.12.sqlite3`，320个仓库。
- GitHub重新读取320个仓库元数据，319个通过元数据初筛；`eslint-community/eslint-plugin-n` 的 fork 标记需进一步判断，不推断其不值得参与。
- 按方向覆盖定向深入18个项目，17个进入项目候选池，HTTPX因抽样未观察到近90天人类作者合并PR而待复核。不是对全部320个项目排序，其他302个尚未深入筛选。
- 项目层次：进阶起步5个、专项进阶7个、深度挑战5个；这些属于助手建议，不是难度真值或个人匹配结果。
- 项目含模块路径、技能基础、建议成果、验收方式、环境准备与贡献指南，并保留维护信号和证据哈希。

## 产物

- `data/growth_projects_20261023_v1/results/report.md`：中文清单与17张项目卡片。
- 同目录 `growth_projects.sqlite3`：独立工作库，320条仓库审计、17条项目、17条贡献路径、66个贡献/模块资源和1条待复核。
- `project_candidates.json`、`review_pending.json`、`repository_audit.json`、`summary.json`、`manifest.json`：结构化记录与校验。
- 上级 `final_evidence.json`：官方仓库元数据、贡献文档、目录、README及PR样本；早期checkpoint只用于恢复，不作为最终结果。

复现：`python scripts/midterm_a/build_growth_projects.py data/growth_projects_20261023_v1`。输出目录必须不存在，保留旧结果再在新的运行目录复现。
路径配置：`config/midterm_datasets/growth_project_pathways_v0.1.json`。

准入检查包括身份、非归档/禁用、当前非fork信号、180天内推送、已读贡献文档、已观察模块路径、90天内人类作者合并PR样本，证据有效期7天。抽样不足保留待核实，不宣称项目不活跃。没有用star数或历史Issue数量代替项目适配。

6项准入边界测试通过；项目身份唯一、320条来源覆盖、无Issue编号伪装、SQLite完整性/外键及输出哈希通过。
未运行这些上游项目的构建或测试，不保证环境成本和贡献被接纳。未改业务库、原Issue库、网页及老师数据库；老师库请求0次，远端写入0次。

## 补充的官方文档入口

- Polars：[贡献与开发](https://docs.pola.rs/development/contributing/)，提供Rust/Python开发与测试入口。
- OpenRefine：[扩展开发](https://openrefine.org/docs/technical-reference/writing-extensions)，说明扩展布局及样例入口。
- typescript-eslint：[本地开发](https://typescript-eslint.io/contributing/local-development/)，说明构建、测试及类型检查流程。
- ESLint：[自定义规则](https://eslint.org/docs/latest/extend/custom-rules)，支持从独立规则插件切入。

以上官方页面于2026-10-23核查；卡片中的成果与验收步骤为助手提出的参与建议，不是维护者发布的待领取任务。
