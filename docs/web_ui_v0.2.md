# 自定义用户画像 v0.2

## 用户流程

页面保留“零贡献 / 首次贡献者”和“进阶开发者”两个本地快捷模板，同时新增自定义画像：

1. 选择贡献阶段；
2. 多选偏好语言、可用操作系统和任务类型；
3. 按 `0–4` 填写 Python、JavaScript、测试、Git、构建工具和文档技能；
4. 可选调整代码难度、环境难度与期望技能跨度；
5. 获取匹配分、推荐理由和逐项技能差距。

贡献阶段会给出合理默认值，用户仍可手动调整。没有预设画像时，页面会自动进入自定义模式。

## 数据与隐私

自定义画像通过 `POST /api/v1/recommendations/custom` 发送给同源本地服务，仅用于当前请求的内存计算，不写入 SQLite。响应明确返回：

```json
{
  "profile_persisted": false,
  "custom_profile_version": "custom-profile-v0.2"
}
```

后端采用字段白名单，限制数组数量、字符串长度、技能数量、等级范围和请求体大小；操作系统只能通过专用字段声明，不能用伪造的 `platform:*` 技能绕过平台门槛。

## 启动与验证

```powershell
$env:PYTHONPATH = "src"
python -m oss_mentor serve-api
```

访问 `http://127.0.0.1:8765/`。调试自定义表单布局时可访问 `http://127.0.0.1:8765/?mode=custom`。

自动化验证：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python scripts\visual_smoke_test.py
```

视觉脚本会验证健康检查、真实自定义画像与反馈 POST 请求，并生成预设桌面、移动端及自定义画像三张截图。预设截图高度覆盖完整推荐卡片和反馈按钮。
