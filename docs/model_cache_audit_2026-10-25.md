# 模型运行预检与候选缓存覆盖审计

2026-10-25 整理（北京时间）。统计时钟以本地产物 `data/model_cache_audit_20261025_v2/summary.json` 的 `observed_at` 为准；有效数量会随过期与后台刷新变化。

## 运行结论

当前项目尚未具备可验证的真实 CODER 推理链路。默认 `D:/anaconda3/python.exe` 没有 PyTorch/Transformers；现有 `D:/anaconda3/envs/my_env/python.exe` 的 PyTorch 2.11.0+cpu、Transformers 5.9.0 导入成功，128×128 CPU 矩阵乘法通过。该环境 CUDA/XPU 均不可用，这不等于硬件没有 GPU。

用户确认目标为之前存放一万多条记录的本地候选库后，再次只读检查：网页库 `data/oss_mentor_web.sqlite3` 有 10,402 条候选，默认库 `data/oss_mentor.sqlite3` 有 11,766 条候选。两库的 `midterm_dataset_file/code/contribution/behavior/issue_file` 及其余 `midterm_dataset_*` 表计数全部为 0。已创建这些表不等于已经导入代码、文件贡献、行为和 Issue—文件关系；候选数量不能当作 CODER 文件图输入数量。上述结论只针对这两份业务库，不否定其他历史数据目录中的独立采集产物。

后续更新：同日按用户要求完成了首批真实 pytest 历史数据补入；上述空表描述为补入前状态。当前计数和验证见 [首批历史数据补入记录](model_history_backfill_2026-10-25.md)。

其余三个已存在的 Conda 环境也做了探测。`materials-ai` 有 LightGBM，但没有 PyTorch/Transformers；本次没有一个所测环境同时具备全部模型依赖。本检查未安装依赖或调整环境。

在项目 `data/midterm` 和所用 Hugging Face Hub 缓存中，只发现另一用途的 JobBERT 权重，没有取得经验证的 CodeBERT/CODER 模型包。9 个历史数据目录不可读，已在 JSON 中列出，不能据此判断目录为空。当前项目源码未找到模型加载网关；未执行 CodeBERT 编码、CODER 训练或模型预测。小张量测试不能算模型运行成功。

仍需要固定 CodeBERT checkpoint/修订、tokenizer 与分段池化策略，登记版本化向量、图、时间划分与训练产物，并验证独立进程加载及推理。已有规则推荐不能作为 CODER 模型结果。

## 候选及缓存覆盖

| 范围 | 结果 |
|---|---|
| 不可变原库存 | 10,361 条、309 个仓库；其原核验证据当前有效数为 0 |
| 默认业务库 | 11,766 条，当前满足推荐准入 0 条 |
| 网页业务库 | 10,402 条，当前可用 378 条，满足全部推荐准入 243 条 |
| 网页准入池新手标签信号 | 13 条；标签不代表人工确认难度或个人适配 |
| IssuePilot 历史缓存 | 21 个仓库、1,127 个唯一 Issue 身份 |
| 全部原库存与论文缓存 | 9/309 个仓库重叠；147/10,361 条来自这些仓库；10 条 Issue 身份重叠 |
| 网页准入池与论文缓存 | 1/72 个仓库、1/243 条候选命中；该条 Issue 身份也重叠 |

网页库已有独立的刷新记录，因此不能用原快照的到期情况推断网页库也没有可用任务。两份业务库均完整覆盖原库存的仓库/编号与 GitHub 全局 ID，未发现缺失或身份冲突；SQLite quick_check 通过，外键错误为 0。所有业务候选都有特征版本和至少一项技能要求，但存在这些字段不等于通过任务范围、状态和时间等准入条件。

另外读取了导入所用的 5 份本地采集证据包：10,361 条库存均能命中仓库元数据；2,011 条能命中全局 ID 一致的 Issue 详情，也有完整时间线缓存。该统计仅针对 JSON 中列出的 5 份证据包，未遍历 D 盘所有历史来源，因此其余 8,350 条只能称为“在所选证据包中未命中”，不能称为完全没有原始证据。这里也没有逐字节复核全部来源或重新确认其新鲜度。

IssuePilot 的 2025 年缓存可以提供历史仓库上下文，不能直接充当当前可领取状态、任务级文件定位、固定 commit 的代码快照或 CodeBERT 向量。71 个网页准入仓库未命中论文缓存的具体清单保存在 JSON 中。模型输入准备仍需要面向实际候选补上下文，而不能假定论文缓存覆盖当前库存。

## 复跑与验证

```powershell
python scripts/audit_model_readiness.py `
  --output data/model_cache_audit_<new_version> `
  --python D:/anaconda3/envs/my_env/python.exe

python -m unittest discover -s tests -p test_model_readiness_audit.py -v
```

输出目录必须尚不存在。脚本通过 SQLite `mode=ro` 读取，按审计时钟保留原核验期限，不刷新、不续期。子进程模型探测启用离线模式；不解压或执行论文包代码。

本次新增 5 项测试全部通过，覆盖仓库/Issue 口径、空池、只读缺失输入、压缩包安全读取及失效/未来时间。使用原生产 `SQLiteCandidateStore.matchable_candidates`、只读连接及相同审计时钟复核两份业务库，分别得到 0/243，与审计一致；结果在 `production_reader_parity.json`。原库存前后 SHA256 相同。

本轮仅新增审计脚本、测试、本文及本地报告，未修改业务实现和已有业务库。
