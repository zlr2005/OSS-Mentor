# A 数据流水线补充交付

本次补充采集、来源校验、身份与代码版本处理，以及供 C 使用的编码/候选缓存输入导出实现。原始数据包不重新提交，本地交接压缩包已按用户要求删除。

## 范围与运行

Python 3.11+ 标准库；本次验证环境 Python 3.13.9。本地 Git 历史采集额外需要 Git CLI。普通离线测试不需要模型、GPU、GitHub 凭据或老师数据库。

```powershell
$env:PYTHONPATH='src'
python -m unittest tests.midterm_a.test_gfibot tests.midterm_a.test_git_history tests.midterm_a.test_github_history tests.midterm_a.test_issue_links tests.midterm_a.test_b_delivery tests.midterm_a.test_dataset_pipeline tests.midterm_a.test_c_encoder_input -v
```

先前独立 main 基线加核心采集源码验证69项，相关既有回归31项通过；随后编码输入适配器8项通过。不要将两次不同范围验证写成已执行完整系统回归。迁移及JSON契约检查已通过；未进行模型训练或端到端验收。

Windows沙箱默认TEMP不可写时，将TEMP/TMP指向工作区专用临时目录。测试输出、数据库和采集缓存不提交Git。

主要入口均支持 --help。collect_github_history 默认只重放已有来源缓存，显式 --allow-network 才补请求；批量采集和下载脚本属于手动网络入口，不能用来运行离线测试。源数据、Git对象与论文归档属于外部输入。

## C 的编码输入

```powershell
python -m oss_mentor.datasets.c_encoder_input --source datasets/b_real_contributions_v0.2 --output data/midterm/c_encoder_input/<新版本>
```

输出固定代码清单、来源manifest哈希、独立reader和身份对照；读取时严格UTF-8、保留CRLF。3,865代码版本中3,714非空、1空内容、150缺失；空文件由C记录编码失败，缺失代码不填假向量。1,276个候选login有来源依据，22个非User/未知类型和27个未确认身份不生成映射。所有训练mapping验证仍为false，需C实际核对。代码来自2024—2026年，不能进入旧2021截止的历史实验输入。

## 平台候选缓存输入

```powershell
python scripts/midterm_a/export_platform_cache_input.py --source datasets/c_issue_candidates_v1 --output data/midterm/platform_input/<新版本>
```

输出11,766条候选身份、标题/正文、文本哈希和原资格/有效期，未修改运行库，不代表当前可领取清单。平台MiniLM候选缓存与贡献文件CodeBERT向量不是同一产物。C的模型资产在其电脑；本机未编码、训练或真实推理。C需确认离线编码入口，交付模型/映射/缓存，D再完成集成验收。

## 来源与验收边界

GFI-Bot resolved工作库有159,919条历史解决记录，不等于完整训练输入或当前库存；官方CODER附件未取得，PFIRec/RecGFI没有本次可确认的正式数据交付。GitHub真实贡献与弱兴趣是有界样本，不能宣称完整历史、能力认证或效果提升。老师库已有真实只读发现工作，未冻结；远端只允许只读查询，相关脚本及过程计划单独整理。

PR24/26/28现已合并且有B/C/D对应契约验收记录，但这不代替A最终数据的接收方签收、C模型训练或D端到端验收。用户要求跳过候选在线复核，未续期，不改日期绕过准入。

本次不提交原始API归档、老师数据/凭据、业务SQLite、模型权重、临时输出或旧交接包脚本。精确源码范围见a_github_upload_checklist.md。
