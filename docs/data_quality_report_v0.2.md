# OSS-Mentor 数据质量报告 v0.2

- 生成时间：2026-07-29T08:21:25.003322+00:00
- 主要统计范围：`eligible_candidates`
- 报告结构版本：`data_quality_report_v0.2`

## 1. 执行摘要

- 任务类型覆盖率：90.30%（通过 90% 目标）
- 技能要求覆盖率：100.00%（通过 90% 目标）
- 难度合法率：100.00%
- 总体验收：通过

## 2. 数据范围

| 范围 | 任务数 |
|---|---:|
| 全部候选 | 1464 |
| 活跃仓库候选 | 1464 |
| 当前可推荐 | 608 |
| 新人友好可推荐 | 264 |

## 3. 核心缺失率

| 指标 | 缺失 | 总数 | 缺失率 |
|---|---:|---:|---:|
| 正文 | 4 | 608 | 0.66% |
| 主要语言 | 0 | 608 | 0.00% |
| GitHub 验证 | 0 | 608 | 0.00% |
| 有效任务类型 | 59 | 608 | 9.70% |
| 技能要求 | 0 | 608 | 0.00% |

## 4. 任务类型质量

公共任务类型：`bug_fix`, `build_tooling`, `documentation`, `feature`, `refactor`, `testing`。

- 有效识别：549 / 608（90.30%）
- 仅 `other`：54
- 仅非公共类型：59
- 公共与非公共类型混合：43
- 字段缺失或不可解析：0

### 类型分布

| 类型 | 任务数 |
|---|---:|
| `bug_fix` | 218 |
| `build_tooling` | 166 |
| `documentation` | 170 |
| `feature` | 213 |
| `other` | 54 |
| `performance` | 48 |
| `refactor` | 37 |
| `testing` | 126 |

## 5. 技能要求质量

- 覆盖任务：608 / 608（100.00%）
- 只有仓库主要语言要求：47
- 含平台要求的任务：135
- 非法平台要求任务：0
- 使用普通平台技能名的任务：0
- 特征版本不一致任务：0

### 技能来源分布

| 来源 | 记录数 |
|---|---:|
| `explicit_platform_signal` | 145 |
| `inferred_task_type` | 978 |
| `repository_primary_language` | 608 |

## 6. 难度质量

- 完整任务：608 / 608（100.00%）
- 合法任务：608 / 608（100.00%）
- 存在缺失：0
- 存在非法值：0

| 字段 | 缺失 | 非法 | 合法 |
|---|---:|---:|---:|
| `estimated_code_difficulty` | 0 | 0 | 608 |
| `estimated_setup_difficulty` | 0 | 0 | 608 |
| `estimated_project_context_difficulty` | 0 | 0 | 608 |
| `estimated_collaboration_difficulty` | 0 | 0 | 608 |
| `estimated_effort_bucket` | 0 | 0 | 608 |

## 7. 按仓库分析（问题优先，前 10 项）

| 仓库 | 语言 | 可推荐任务 | 类型覆盖率 | 技能覆盖率 | 正文缺失 | 难度异常 |
|---|---|---:|---:|---:|---:|---:|
| facebook/pyrefly | Rust | 7 | 71.43% | 100.00% | 0 | 0 |
| excalidraw/excalidraw | TypeScript | 29 | 72.41% | 100.00% | 2 | 0 |
| jenkinsci/jenkins | Java | 4 | 75.00% | 100.00% | 0 | 0 |
| pytorch/ao | Python | 35 | 77.14% | 100.00% | 1 | 0 |
| nodejs/node | JavaScript | 20 | 80.00% | 100.00% | 0 | 0 |
| trinodb/trino | Java | 50 | 86.00% | 100.00% | 0 | 0 |
| nushell/nushell | Rust | 23 | 86.96% | 100.00% | 0 | 0 |
| pytorch/pytorch | Python | 17 | 88.24% | 100.00% | 0 | 0 |
| rust-lang/rust-clippy | Rust | 19 | 89.47% | 100.00% | 0 | 0 |
| nodejs/undici | JavaScript | 30 | 90.00% | 100.00% | 1 | 0 |

## 8. 按语言分析

| 语言 | 可推荐任务 | 类型覆盖率 | 技能覆盖率 | 正文缺失 | 难度异常 |
|---|---:|---:|---:|---:|---:|
| Go | 36 | 94.44% | 100.00% | 0 | 0 |
| Java | 153 | 90.85% | 100.00% | 0 | 0 |
| JavaScript | 114 | 89.47% | 100.00% | 1 | 0 |
| Python | 193 | 91.71% | 100.00% | 1 | 0 |
| Rust | 49 | 85.71% | 100.00% | 0 | 0 |
| TypeScript | 63 | 87.30% | 100.00% | 2 | 0 |

## 9. 异常任务样例

### 正文缺失

| 仓库 | Issue | 标题 | 原因 |
|---|---:|---|---|
| excalidraw/excalidraw | [#1007](https://github.com/excalidraw/excalidraw/issues/1007) | We should have a different hint when drawing multisegments lines on mobile | body_text_missing |
| excalidraw/excalidraw | [#5301](https://github.com/excalidraw/excalidraw/issues/5301) | Feature Request: Import GIF pictures | body_text_missing |
| nodejs/undici | [#3276](https://github.com/nodejs/undici/issues/3276) | interceptors: move signal handling to interceptor | body_text_missing |
| pytorch/ao | [#2298](https://github.com/pytorch/ao/issues/2298) | Use Int8WeightOnlyConfig  to quant wan2.1 model, and export to onnx file, Why the onnx weights in my disk  are fp32 precision? | body_text_missing |

### 未识别到公共任务类型

| 仓库 | Issue | 标题 | 原因 |
|---|---:|---|---|
| apache/dubbo | [#12014](https://github.com/apache/dubbo/issues/12014) | 当接口调用传参为对象时引发一个序列化白名单的错误，提示让我把类加入白名单，要怎么操作？ | only_other |
| apache/pinot | [#10237](https://github.com/apache/pinot/issues/10237) | Disallow consuming segment deletion | only_other |
| apache/pinot | [#17378](https://github.com/apache/pinot/issues/17378) | Deep Stack Trace from Long Min/Max Column Values | only_other |
| apache/pinot | [#5559](https://github.com/apache/pinot/issues/5559) | Table truncate command | only_other |
| apache/pinot | [#7275](https://github.com/apache/pinot/issues/7275) | Tool for migrating from one deep store to another | only_other |
| apache/pinot | [#9016](https://github.com/apache/pinot/issues/9016) | Better UX for data ingestion quick-start | only_other |
| excalidraw/excalidraw | [#10573](https://github.com/excalidraw/excalidraw/issues/10573) | iuiu | only_other |
| excalidraw/excalidraw | [#11433](https://github.com/excalidraw/excalidraw/issues/11433) | Artifcating of shape when zoomed in at 3000% | only_other |
| excalidraw/excalidraw | [#11464](https://github.com/excalidraw/excalidraw/issues/11464) | Excalidraw created files location | only_other |
| excalidraw/excalidraw | [#7177](https://github.com/excalidraw/excalidraw/issues/7177) | not visible grouping line on a dark background in a light theme | only_other |

### 无有效技能要求

暂无。

### 难度缺失或非法

暂无。

## 10. 结论与下一步

- B2：优先分析未识别到公共任务类型的真实任务，并改进分类规则与证据链。
- B3：根据难度缺失、非法值和真实误判样例调整难度规则。
- B4：技能覆盖率只代表记录存在，还需继续检查技能词表、工具要求、置信度和证据。
