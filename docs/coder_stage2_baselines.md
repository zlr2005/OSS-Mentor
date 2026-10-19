# 类 CODER 推荐算法：阶段 2 Baseline

## 范围与状态

阶段 2 只实现三个可复现 Baseline：Popularity、TF-IDF 文本相似度和 BPR 矩阵分解。没有实现 LightGCN、GAT、神经文本编码器，也没有接入或修改 OSS-Mentor 的公共推荐 API、数据库和线上流程。

正式运行 `stage2-baselines-v1.2` 已完成，使用的数据版本为 `gfibot-m0-v1.1`。

## 冻结输入

配置文件 `CODER/configs/baselines.toml` 同时固定：

- 数据版本：`gfibot-m0-v1.1`；
- 切分协议：`fixed_train_graph`；
- 候选范围：`historical_resolved_issue_subset`；
- 候选模式：`same_repo`、`cross_repo`；
- 候选随机种子：`20261017`；
- `split_manifest.json`、`train/valid/test.parquet`、三份 ID 映射，以及实际读取的 `issues`、`developer_issue_edges`、`issue_snapshots` 表的 SHA-256。

流水线会在读取数据后、训练模型前验证上述版本、协议和哈希。任何切分或映射漂移都会立即失败。三个模型读取同一份持久化候选文件，不会分别生成候选池。

每个预测场景的候选必须满足：Issue 已创建、在预测时刻尚未解决、属于阶段 1 的历史已解决 Issue 子集，并排除该开发者训练期已经解决的 Issue。正例总是加入候选。`same_repo` 进一步限定目标仓库；`cross_repo` 不限定仓库。为控制实验规模，每个验证/测试场景用同一固定种子最多保留 500 个候选。训练负例使用同一时序可观察规则，最多保留 5 个负例。

验证和测试始终只使用固定训练图构造用户历史。验证行为不会回流到测试用户画像或模型参数。

## Baseline 定义

### Popularity

候选 Issue 的分数为其仓库在训练集中的解决次数 `log1p(count)`。该模型不依赖用户历史，因此冷启动用户也可预测。同仓库候选同分时，统一按 `issue_id` 升序打破平局。

### Text Similarity

只使用创建快照的标题和正文。TF-IDF 的词表和 IDF 只在训练 Issue 上拟合；候选 Issue 只用冻结后的向量器转换。开发者向量是其训练 Issue 向量之和再做 L2 归一化，与候选向量计算余弦相似度，这与归一化后的均值方向等价。

训练图中没有历史的用户记为不可预测；评测仍保留这些场景并使用统一的确定性平局规则，同时单独报告 `predictable`、`unpredictable`、`warm_start` 和 `cold_start`。

### BPR Matrix Factorization

模型只使用 Developer-Issue 训练矩阵，不使用仓库、文本或验证/测试交互。负例来自训练交互发生时可观察且尚未被该开发者解决的候选。网格参数只按验证集 `cross_repo` 的可预测用户 `NDCG@10` 选择：

- 维度：16、32；
- 学习率：0.01、0.05；
- 正则：0.0001；
- 训练轮数：15。

最终选中 `dimensions=32, learning_rate=0.05`。全部 123,481 条训练交互均找到合法时序负例，没有跳过。参数冻结后才执行测试评测。

## 指标协议

每个解决事件是一个单正例查询，统一报告 `Recall/NDCG/MRR/Hit@5/10/20`。在单正例设置下 Recall 与 Hit 数值相同。所有模型统一按分数降序、`issue_id` 升序打破平局。

正式测试集共有 7,034 个查询；Text Similarity 和 BPR 有 4,129 个可预测查询。以下是测试集主要结果：

| 模型 | 候选模式 | Recall@10 | NDCG@10 | MRR@10 | 可预测用户场景 NDCG@10 |
|---|---:|---:|---:|---:|---:|
| Popularity | same_repo | 0.360535 | 0.182394 | 0.128942 | 0.182394 |
| Popularity | cross_repo | 0.017344 | 0.007848 | 0.005043 | 0.007848 |
| Text Similarity | same_repo | 0.453938 | 0.245553 | 0.182166 | 0.289292 |
| Text Similarity | cross_repo | 0.302104 | 0.187791 | 0.152316 | 0.311893 |
| BPR MF | same_repo | 0.419534 | 0.220647 | 0.160502 | 0.246864 |
| BPR MF | cross_repo | 0.027154 | 0.012971 | 0.008745 | 0.014077 |

完整的分组及 @5/@10/@20 指标保存在忽略目录 `data/runs/gfibot-m0-v1.1/baselines/stage2-baselines-v1.2/metrics.json`。

## 环境与运行

阶段 2 使用 Python 3.13.9 和 CPU 依赖 NumPy 2.3.4、SciPy 1.16.2、scikit-learn 1.7.2、joblib 1.5.2。没有安装 GPU 框架。

在仓库根目录运行：

```powershell
CODER/.venv/Scripts/python.exe -m pip install -e "CODER[test]"
CODER/.venv/Scripts/python.exe CODER/scripts/run_baselines.py `
  --config CODER/configs/baselines.toml `
  --run-id <新的运行标识>
```

为保护已有实验，运行标识对应的模型或结果目录已存在时命令会拒绝覆盖。模型输出位于 `data/models/gfibot-m0-v1.1/baselines/<run-id>/`，候选、指标、选参记录和运行 Manifest 位于 `data/runs/gfibot-m0-v1.1/baselines/<run-id>/`。这些大型产物已由仓库 `.gitignore` 的 `data/*` 规则排除。

模型保存格式：Popularity 和 BPR 使用压缩 NumPy 文件，文本稀疏矩阵使用 SciPy NPZ，向量器使用 joblib。只应加载本项目自行生成且 Manifest 可核验的 joblib 文件。

## 验收

- 三个 Baseline 均使用相同冻结切分、映射和候选文件；
- Popularity、文本相似度、BPR 均可保存并重新加载；
- BPR 只在验证集选参，测试集只在参数冻结后评测一次；
- 完整输出含配置哈希、输入哈希、候选文件哈希、环境版本和成功标记；
- `CODER/tests` 共 21 项测试通过；
- Git 忽略规则已验证模型和运行产物不会进入版本控制；
- 改动仅位于 `CODER/` 与本说明文档，没有改动现有平台 API 或推荐契约。

阶段 2 验收通过，可以在明确指令后进入阶段 3；本阶段没有提前实现阶段 3 内容。
