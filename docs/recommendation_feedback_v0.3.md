# 推荐反馈闭环 v0.3

## 1. 用户状态

每条推荐支持四种当前状态：

| 状态 | 含义 |
|---|---|
| `interested` | 用户认为任务值得进一步查看 |
| `not_suitable` | 用户认为当前推荐不适合自己 |
| `started` | 用户已经开始阅读、复现或实现任务 |
| `completed` | 用户认为本次贡献已经完成 |

用户可以修正状态。重复点击当前状态不会重复写入事件。

## 2. 本地数据结构

- `recommendation_feedback`：保存每个“反馈上下文 × 候选任务”的当前状态，供页面快速恢复；
- `recommendation_feedback_event`：只在状态真实变化时追加一条事件，保存前后状态与发生时间。

预设画像使用 `preset:<profile_key>` 作为本地反馈上下文。自定义画像由浏览器生成匿名 UUID，按贡献通道形成 `custom:<uuid>:<track>`；不采集 GitHub 登录名、邮箱或真实身份。

## 3. API

推荐响应新增：

- 顶层 `feedback_context`；
- 每个任务的 `feedback_state`，未反馈时为 `null`。

写入反馈：

```http
POST /api/v1/feedback
Content-Type: application/json

{
  "task_candidate_id": 1,
  "feedback_context": "preset:newcomer_python_macos",
  "feedback_state": "interested"
}
```

服务端校验任务 ID、上下文格式和状态白名单。自定义画像本身仍不写入数据库，只有匿名反馈上下文和任务状态保存在本地 SQLite。

## 4. 后续分析指标

事件表可计算：感兴趣率、开始率、完成率、不适合率，以及 `interested → started → completed` 的本地转化漏斗。v0.3 暂不自动用反馈重排推荐，避免在样本量过小时形成不稳定偏差。
