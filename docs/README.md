# OSS-Mentor 文档索引

本目录只维护描述**当前实现**的文档。版本号式阶段报告、成员交接单、旧评估摘录和已执行完的计划已删除；需要追溯时请使用 Git 历史，不再让历史状态与现行契约并列展示。

## 当前文档

| 文档 | 回答的问题 | 主要事实源 |
|---|---|---|
| [系统架构](architecture.md) | 模块怎样连接、运行时走哪条链路？ | `src/oss_mentor/`、`CODER/src/coder_issue/` |
| [数据与模型](data-and-model.md) | 使用什么数据、算法如何计算、指标和限制是什么？ | 数据 manifest、模型 manifest、`metrics.json` |
| [接口契约](interfaces.md) | B 输入什么、C 返回什么、D 怎样接平台？ | `contracts.py`、`model_scoring.py`、`api.py` |
| [OpenAPI v0.6](openapi_v0.6.yaml) | 当前 HTTP 路径、请求/响应、身份与错误语义是什么？ | `api.py`、`contracts_midterm.py`、契约测试 |
| [运行手册](operations.md) | 如何安装、生成缓存、启动、恢复训练和测试？ | CLI 与脚本参数、当前配置 |

根目录 [README](../README.md) 是新成员的唯一总入口；[PRODUCT](../PRODUCT.md) 只描述产品承诺和边界。

## 文档优先级

出现冲突时按以下顺序判断：

1. 运行时代码、数据库迁移和冻结 manifest；
2. 本目录四份当前文档；
3. Git 历史中的旧阶段文档。

`openapi_v0.6.yaml` 是与当前路由一起维护的机器可读规范。修改 API 字段、
认证方式或错误语义时，必须同步更新该文件和[接口契约](interfaces.md)，并
由契约测试检查新增路由是否被登记。

## 维护规则

- 行为、字段或默认路径改变时，在同一提交更新对应当前文档。
- 实验运行的原始 JSON/CSV 放入 `data/runs/`、`data/models/` 或 `data/reports/`，不要再复制成手写 Markdown 报告。
- 临时计划、交接记录和一次性验收结论放到 Issue/PR，不进入 `docs/`。
- 指标必须注明 dataset、graph、split、candidate mode 和 group，不能只写一个裸分数。
- 不把计划中的功能写成已实现功能；不把离线排序分数解释为线上成功率。
