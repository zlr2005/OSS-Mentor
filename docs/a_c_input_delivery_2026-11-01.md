# A → C 第二项本地补齐结果

已新增标准库实现 `src/oss_mentor/datasets/c_encoder_input.py`，生成 `output/local_delivery/a_c_code_input_20261101_v1/`。

结果：3,865 个固定代码版本，其中 3,714 非空可编码、1 空内容、150 缺失保留原因；从已有来源核验 1,276 个 User 类型账号候选 login，22 个非 User/未知类型和 27 个未确认身份不生成候选映射。没有网络请求，原数据包未修改。

读取器严格验证输入清单、原来源 manifest 和实际代码内容哈希，使用字节解码保留 CRLF；不推测未知身份、不将 committer 替换为 author、不为缺失代码填充假向量。身份来源、观察时间和未验证训练映射状态均保留。

## 验证

- 8/8 专项测试通过：CRLF/空/缺失、作者身份、login 冲突、跨账号冲突、内容/索引篡改、路径边界、重复版本与覆盖拒绝、未知编码和身份。
- Windows Python 3.13.9；默认沙箱 TEMP 不可写导致首次测试未运行成功，将 TEMP/TMP 指向工作区新目录后 8/8 通过。
- 用 C PR #24 固定 Head `8b8f8656ae78ea1e808f9b507c3aa2ced21470f4` 的真实 CodeDocument 类型读取全部 3,715 条可用记录，包含 1 条空内容，19 条 CRLF 完整保留。
- 实际读取结果在交付目录 verification.json。未进行 CodeBERT 编码、训练或模型效果验收。

本地重新生成（output 必须新建）：

```powershell
$env:PYTHONPATH='src'
python -m oss_mentor.datasets.c_encoder_input --source datasets/b_real_contributions_v0.2 --output output/local_delivery/a_c_code_input_<新版本>
```

本次只补第二项，没有更新第一项已经冻结的压缩包。新增模块与测试需随后续正式源码交付一起审阅。

接入 README 和未发送的验收请求草稿随包提供。C 仍需确定编码配置、核对训练身份映射、确认新代码用途与实验时点，并实际签收；本地准备完成不代表全组中期任务完成。

本次没有 git add、commit、push、开 PR、发送消息或修改业务数据库。
