# 开发者画像与个性化匹配 v0.1

## 1. 完成范围

本版本把任务侧分数与开发者的显式技能、偏好和本地环境组合，形成可解释的
个性化匹配。所有画像默认保存在本地 SQLite，不要求 GitHub 登录，也不采集姓名、
邮箱、地理位置或私有仓库信息。

## 2. 最小画像字段

| 字段 | 用途 |
|---|---|
| `service_track` | `newcomer`、`growth` 或 `hybrid` |
| `preferred_languages` | 用户愿意参与的语言 |
| `operating_systems` | 用于过滤明确的平台任务 |
| `preferred_task_types` | bug、测试、文档、构建工具等偏好 |
| `max_code_difficulty` | 用户可接受的代码难度上限 |
| `max_setup_difficulty` | 用户可接受的环境难度上限 |
| `desired_skill_stretch` | 进阶通道希望跨越的技能等级，`0–2` |
| `skills` | 技能名称到 `0–4` 等级的映射 |

真实用户画像必须标记 `profile_source=user_input`，并记录同意版本。仓库中的两个
画像均为匿名演示数据，不能用于分析真实个人。

## 3. 任务技能要求

任务技能要求由以下信息推断：

- 仓库主语言；
- 自动识别的任务类型；
- 标题和标签中的明确操作系统信号；
- 正文仅在标题和标签没有平台信号时作为兜底。

每项要求保存最低等级、重要性、来源和特征版本。语言和明确平台属于关键要求，
任务类型属于软要求。

## 4. 匹配规则

共同硬过滤：

- 候选任务必须已经通过资格门禁；
- 代码和环境难度不得超过画像上限；
- 明确的平台要求必须与用户环境匹配；
- 新手不能缺失关键语言能力。

新手通道组合：任务新手分、技能覆盖率、语言偏好和任务类型偏好。新手任务必须
带有已配置的新人信号。

进阶通道组合：任务成长价值、技能覆盖率、偏好，以及实际技能差距与
`desired_skill_stretch` 的接近程度。它允许受控的一级技能跨度。

输出包括总分、技能覆盖率、最大技能差距、逐项技能缺口和命中原因，避免只给出
无法解释的黑盒分数。

## 5. 真实候选验证

对 `matplotlib/matplotlib#31936` 使用两个匿名画像：

| 画像 | 匹配分 | 技能覆盖率 | 最大技能差距 | 结果解释 |
|---|---:|---:|---:|---|
| Python 新手（macOS） | 82.34 | 0.895 | 1 | Python、macOS 和测试能力满足；bug/build_tooling 是可学习缺口 |
| Python 进阶开发者 | 75.20 | 0.947 | 1 | 核心要求满足，bug_fix 是一级成长跨度 |

验证过程中曾发现正文中用于对比的 Linux 被误识别为必须环境。v0.1 已改为标题和
标签优先，因此 macOS 用户不会再被错误过滤。对应回归测试已加入测试集。

## 6. 使用方法

```powershell
$env:PYTHONPATH = "src"

python -m oss_mentor extract-features
python -m oss_mentor import-profiles --file config/demo_profiles_v0.1.json

python -m oss_mentor match-candidates `
  --profile newcomer_python_macos `
  --limit 10

python -m oss_mentor match-candidates `
  --profile growth_python_crossplatform `
  --limit 10
```

## 7. 下一步

当前技能等级来自显式输入。下一版可以在用户授权后，从公开贡献历史生成
`developer_skill_evidence`，但必须保留证据来源、置信度和用户修改入口，且不得把
GitHub 活跃度简单等同于能力。
