# 最小网页界面 v0.1

## 1. 已实现功能

网页由本地 API 同源托管，不需要 Node.js、npm 或前端构建工具。用户可以：

1. 从本地匿名画像中选择当前身份；
2. 查看画像通道、语言、操作系统和可接受难度；
3. 获取已经通过领取状态与能力门槛的推荐任务；
4. 查看 `0–100` 匹配分、技能覆盖率和最大技能差距；
5. 阅读中文推荐理由；
6. 对照每项技能的当前等级、要求等级和差距；
7. 跳转到 GitHub 原始 Issue。

页面包含加载、空结果和错误状态；在初次加载后自动展示默认新手画像的推荐，切换
画像后可重新计算。

## 2. 启动方式

```powershell
$env:PYTHONPATH = "src"
python -m oss_mentor serve-api
```

浏览器访问：

```text
http://127.0.0.1:8765/
```

## 3. 文件

- `web/index.html`：语义化页面结构；
- `web/assets/styles.css`：桌面与移动端响应式样式；
- `web/assets/app.js`：画像、推荐和错误状态交互；
- `scripts/visual_smoke_test.py`：启动临时服务器并生成桌面、移动端截图；
- `src/oss_mentor/api.py`：白名单静态资源托管及安全响应头。

## 4. 安全与可访问性

- HTML、CSS、JavaScript 全部同源，不加载第三方脚本、字体或分析服务；
- 静态文件使用固定路由白名单，不能通过路径遍历读取工作区文件；
- 页面响应包含 CSP、`nosniff`、`no-referrer` 和禁止嵌入策略；
- 外部 Issue 链接使用 `noopener noreferrer`；
- 表单控件有标签，动态结果使用 live region，键盘焦点有可见样式；
- 尊重 `prefers-reduced-motion`；
- 画像和技能数据仍保存在本地 SQLite。

## 5. 验收结果

- 结构测试确认存在画像选择、推荐按钮、推荐列表、结果计数和消息区；
- JavaScript 测试确认调用画像与推荐 API，并渲染匹配分、技能缺口和推荐理由；
- 静态路由测试确认白名单之外的文件不可访问；
- Edge 无头模式实际加载页面并执行 API 请求；
- 桌面端 `1440×1100` 和移动断点 `500×1600` 均生成有效截图；
- 实际页面展示匿名 Python 新手画像和 `matplotlib/matplotlib#31936`，匹配分为
  `82`，技能覆盖率约 `90%`。

截图位于本地忽略目录 `data/ui_desktop.png` 和 `data/ui_mobile.png`，不提交 Git。
