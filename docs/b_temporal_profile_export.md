# B 历史画像导出（Advanced Adapter v1）

本导出只构造 Adapter 的历史画像 X，不读取或解释未来结果 Y。代码在仓库内运行，A 提供的
backfill 大文件保存在仓库外，并通过命令行参数传入，避免误提交内部数据。

## 输入边界

- Git 索引：`datasets/temporal_xy_2024_2026_v3/`
- A/B v0.2 来源：`datasets/b_real_contributions_v0.2/`
- backfill 来源：机器本地目录，通过 `--backfill-root` 指定
- 23 维顺序：`config/midterm_profile/questionnaire_v0.1.json`

每条 X 必须满足：身份数字 ID 一致、`event_at < cutoff_at`、代码内容可用、代码字节
SHA-256 与索引一致。GitHub login 只从与数字 ID 一致的 API 证据中恢复；账号改名时使用最新
可验证 login，并保留旧 login 作为审计 alias。

画像只使用直接文件贡献。未知维度固定为 `value=0.0`、`observed_mask=false`；历史数据不会
虚构问卷、操作系统、每周时间或当前兴趣。路径规则提供保守的代码、测试、文档和开源经验
代理，文件扩展名只映射六种已冻结语言偏好。所有这些值都是可解释代理，不是能力概率。

## 生成联调预览

```powershell
python scripts/midterm_b/export_temporal_training_profiles.py `
  --backfill-root C:\oss\OSS-Mentor-local-data\temporal_xy_v3_backfill_for_B `
  --output-dir data\midterm\working\b_temporal_profile_preview_v0.1 `
  --preview-per-split 4
```

产物：

```text
profiles.jsonl
profile_manifest.json
profile_failures.jsonl
profile_audit.jsonl
```

预览行符合 `enhanced-profile-training-export-v0.1` 和 `profile_state=research_frozen`，同时包含
`delivery_status=preview_not_for_training`。manifest 的 `training_allowed=false` 是硬边界：C 只能
用它联调字段、shape、mask 和读取器，不能训练或调参。双方验收后再用
`--preview-per-split 0` 生成完整候选，并经过独立冻结流程转为正式训练输入。

## 时间与隐私边界

- 不消费组内 `y` 字段；改变 Y 不会改变画像字节。
- 不消费时间未知的 fork、owned repository 或用 2026 语言解释历史 star。
- commit 时间是当前 X 时间口径；首次公开可用时间未逐条独立核实，manifest 会保留限制。
- backfill 的机器路径不会写入产物，只记录来源 manifest 哈希。
- 线上评分仍只接受用户真实确认的 `confirmed` 画像；本产物仅用于离线研究。
