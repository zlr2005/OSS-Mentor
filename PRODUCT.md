# OSS-Mentor 产品边界

## 用户与问题

OSS-Mentor 服务于希望开始或继续开源贡献的开发者。用户需要从仍可领取的真实 Issue 中找到符合当前语言、设备环境、任务偏好和能力上限的任务，并理解“为什么推荐”“还缺什么技能”。团队需要能复现每次推荐、审计模型版本并区分在线产品数据与离线研究数据。

## 产品承诺

- 先保证任务可领取，再比较模型分数。
- 硬约束不能被软评分、多样性或反馈逻辑突破。
- 推荐理由基于可核验特征；模型分数不包装为成功概率。
- 每次结果保留画像哈希、候选哈希、版本信息和推荐运行 ID。
- 用户负反馈只影响绑定的画像上下文。
- 未覆盖能力和研究风险必须在界面或文档中明确说明。

## 当前产品链路

```text
公开候选发现
  -> GitHub 当前状态复核
  -> SQLite 候选与特征
  -> DeveloperProfileV2 硬过滤
  -> CODER 只读排序
  -> 反馈降权与多样性
  -> RecommendationBatchV3
  -> 网页/API
```

产品保留 `newcomer` 和 `growth` 两条服务轨道。默认排序引擎为 `coder_issue_repo`；`--legacy-ranking` 仅用于无模型产物的兼容演示。

## 不承诺的内容

- 不声称是 CODER 官方复现。
- 不声称离线 NDCG 等同于真实贡献成功率。
- 不声称已解决未知用户冷启动；当前未知用户使用总体用户向量回退。
- 不声称 Docker 配置已能部署默认 CODER 模型。
- 不采集或使用私有仓库内容作为当前推荐依据。

## 体验原则

- 页面先帮助用户判断任务是否适合，再展示算法细节。
- 空结果、缓存不完整、模型不可用和登录失效必须显式报错。
- 使用语义化 HTML、键盘可达控件、清晰焦点状态和响应式布局。
- 文案保持克制可信，不把开源贡献包装成竞赛，也不虚构用户、商业或效果数据。

## 当前事实来源

产品现状以 `src/oss_mentor/`、`CODER/src/coder_issue/`、模型 `model_manifest.json` 和成功运行 `_SUCCESS` 为准。结构说明见 [docs/architecture.md](docs/architecture.md)，数据与模型说明见 [docs/data-and-model.md](docs/data-and-model.md)，跨成员与 HTTP 契约见 [docs/interfaces.md](docs/interfaces.md)。
