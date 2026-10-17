# 类 CODER 推荐算法分阶段开发计划

## 总体目标

使用：

- `CODER/dataset.bson`
- `CODER/resolved_issue.bson`

训练一个 Developer–Issue 推荐模型，并将模型接入 OSS-Mentor：

```text
历史 BSON 数据
→ 数据标准化
→ baseline
→ 类 CODER 模型
→ 离线评估
→ 模型打包
→ 平台开放 Issue 推理
→ 推荐展示
```

最终至少交付：

- 一个可复现的简单 baseline；
- 一个可训练、保存、重新加载的类 CODER 模型；
- 一套固定的训练/验证/测试协议；
- 一个可以接收平台画像和候选 Issue 的推理接口；
- baseline、完整模型和消融实验结果。

---

## 阶段 0：工程与数据安全准备

**预计：0.5～1 天**

### 工作内容

1. 整理 `/CODER` 目录结构。
2. 将两个大 BSON 文件加入精确的 `.gitignore`。
3. 建立独立模型环境，不污染普通网页运行环境。
4. 固定随机种子、配置格式和输出目录。
5. 记录原始数据文件 SHA-256 和大小。

### 建议目录

```text
CODER/
  dataset.bson
  resolved_issue.bson
  configs/
  src/coder_issue/
  scripts/
  tests/

data/midterm/
  normalized/
  embeddings/
  graphs/
  models/
  runs/
```

### 交付物

- 数据 manifest；
- 模型目录骨架；
- 环境与依赖说明；
- BSON 文件忽略规则需求。

### 验收条件

- 大文件不会被误提交；
- 模型代码可以独立导入；
- 配置、日志和产物目录明确。

---

## 阶段 1：M0 数据预处理

**预计：3～5 天**

这是整个项目最重要的阶段。数据层没有冻结之前，不开始正式模型对比。

### 1.1 流式解析 BSON

实现：

```text
bson_reader.py
normalize.py
prepare_data.py
```

不能一次性把 6.7 GB 数据读入内存。

### 1.2 建立统一主键

```text
repository_key = lower(owner/name)
issue_key      = lower(owner/name)#number
developer_key  = casefold(resolver)
```

为训练生成连续整数 ID，同时保存双向映射。

### 1.3 关联两个 BSON

正式主数据集使用两个文件成功关联的 **148,096 个 Issue**。

处理：

- 去除重复记录；
- 识别创建和关闭快照；
- 标记只有一个快照的异常 Issue；
- 记录无法关联的数据；
- 验证时间字段合法性。

### 1.4 防止数据泄漏

模型输入使用：

- Issue 创建时的标题和正文；
- 创建时可见标签；
- 预测时间之前的历史解决行为；
- 预测时间之前的仓库统计。

禁止作为输入：

- 当前目标 Issue 的 `resolver`；
- `resolved_at`、`closed_at`；
- `resolver_commit_num`；
- 关闭快照中新增加的结果性信息。

### 1.5 生成标准化数据

```text
developers.parquet
repositories.parquet
issues.parquet
issue_snapshots.parquet
developer_issue_edges.parquet
developer_repo_edges.parquet
issue_repo_edges.parquet
issue_label_edges.parquet
```

### 1.6 时间切分

确定两个全局时间点：

```text
train: interaction_at < T1
valid: T1 <= interaction_at < T2
test:  interaction_at >= T2
```

切分后统计：

- 用户数；
- Issue 数；
- 交互边数；
- 测试用户覆盖率；
- 冷启动用户数量；
- 跨仓库用户数量；
- 每个时间段的候选规模。

### 交付物

```text
data/midterm/normalized/<version>/
data/midterm/graphs/<version>/split_manifest.json
data/midterm/validation/c/data_quality.json
```

### 验收条件

- 能复现全部实体和交互统计；
- 相同配置重复运行产生相同 ID；
- 训练图不包含验证、测试时期的边；
- 随机抽查目标 Issue 时，看不到真实 resolver；
- 所有过滤数量都有明确原因。

---

## 阶段 2：简单 baseline

**预计：2～3 天**

先建立最低可用对照，验证数据和指标没有问题。

### 需要实现的 baseline

#### Baseline 1：Popularity

按照训练期 Issue 类型、仓库或解决次数排序。

用途：验证推荐是否至少优于简单热门策略。

#### Baseline 2：文本相似度

用户历史已解决 Issue 的文本向量取平均，与候选 Issue 文本计算余弦相似度。

用途：验证 Issue 文本是否有推荐价值。

#### Baseline 3：MF/BPR

只使用 Developer–Issue 交互矩阵：

```text
score(u, i) = user_embedding · issue_embedding
```

用途：作为最基础的协同过滤模型。

### 统一评估

报告：

- Recall@5/10/20；
- NDCG@5/10/20；
- MRR@5/10/20；
- Hit@5/10/20；
- 可预测用户数量；
- 无法预测用户数量；
- 冷启动用户单独结果。

### 交付物

```text
CODER/src/coder_issue/models/popularity.py
CODER/src/coder_issue/models/text_similarity.py
CODER/src/coder_issue/models/mf.py
data/midterm/runs/<run_id>/
```

### 验收条件

- 三个 baseline 使用完全相同的数据划分和候选集合；
- 模型能够保存和重新加载；
- 测试集只在模型与参数冻结后使用；
- 指标可以通过一条命令复现。

---

## 阶段 3：Developer–Issue LightGCN

**预计：3～4 天**

这一阶段建立类 CODER 的核心协同图。

### 工作内容

构建：

```text
Developer ↔ Issue
```

正边来自历史解决关系。

实现：

- LightGCN 归一化传播；
- 0～L 层表示平均；
- BPR 负采样；
- 用户和 Issue 向量导出；
- 已知用户批量评分。

初始参数可参考论文：

```yaml
embedding_dim: 32
layers: 4
batch_size: 1024
optimizer: Adam
```

但最终参数必须用验证集选择。

### 主要对比

```text
Popularity
MF
LightGCN
```

### 交付物

```text
CODER/src/coder_issue/models/lightgcn.py
CODER/src/coder_issue/training/losses.py
CODER/src/coder_issue/training/negative_sampling.py
```

### 验收条件

- LightGCN 可以训练、保存和加载；
- 负样本来自预测时刻可见的候选集合；
- 不把“未解决”解释成真实负反馈；
- 相同种子可以复现主要指标。

---

## 阶段 4：Issue 语义与结构增强

**预计：4～6 天**

这是第一个真正的类 CODER 增强模块。

### 4.1 Issue 文本编码

输入：

```text
title + body + code blocks
```

处理：

- 标题单独分段；
- 正文按段落和代码块切分；
- 最多保留固定数量片段；
- 使用冻结编码器；
- 编码结果离线缓存。

先比较：

```text
CodeBERT
通用句向量模型
```

输出：

```text
data/midterm/embeddings/<version>/
```

### 4.2 Repo–Issue–Label 结构图

构建：

```text
Repository ↔ Issue ↔ Label
```

使用 2～3 层 GAT，得到结构增强的 Issue 和 Repository 向量。

### 实验变体

```text
lightgcn                  # 纯协同
coder_issue_text          # 协同 + 文本
coder_issue_structure     # 协同 + 结构
coder_issue_base          # 协同 + 文本 + 结构
```

### 验收条件

- 编码缓存具有模型版本和行映射；
- 新运行不会重复编码相同文本；
- 文本和结构模块能够独立关闭；
- 所有变体共用同一划分、负样本和候选集合。

---

## 阶段 5：跨粒度项目建模

**预计：3～4 天**

### 工作内容

从解决关系派生：

```text
Developer → Repository
```

建立项目级 LightGCN 或归一化图传播。

融合：

```text
user = MLP([user_issue || user_repository])

issue = MLP([
    issue_collaborative
    || issue_semantic
    || issue_structural
    || repository_embedding
])
```

损失：

```text
L = L_issue + λ_repo · L_repository
```

### 实验变体

```text
coder_issue_base
coder_issue_repo
```

### 注意事项

项目贡献边由 Issue 解决边派生，不属于独立的 Star/Fork 行为。因此只能描述为：

> 项目级辅助传播或跨粒度约束。

不能宣传为真正的独立多行为学习。

### 验收条件

- Issue 推荐和项目推荐分别输出指标；
- 跨仓库用户单独报告；
- 报告项目级模块的实际覆盖率；
- 加入项目模块后，即使没有提升也保留真实结果。

---

## 阶段 6：冷启动画像与平台适配

**预计：3～5 天**

这是模型从离线实验进入产品的关键阶段。

### 6.1 已知历史用户

如果平台 GitHub 身份能够映射到 BSON 中的 `resolver`：

```text
登录用户
→ developer_key
→ 图模型用户向量
→ 候选 Issue 评分
```

### 6.2 新用户

如果用户没有历史节点：

```text
DeveloperProfileV2
→ 语言、技能、任务偏好、难度偏好编码
→ profile embedding
→ 与 Issue 向量计算匹配
```

采用门控融合：

```text
final_user =
    α(history_count) · graph_user
  + [1 - α(history_count)] · profile_user
```

零历史用户的 `α = 0`。

### 6.3 模型评分接口

实现独立接口：

```python
class CoderIssueScorer:
    def score(
        self,
        *,
        profile: DeveloperProfileV2,
        developer_key: str | None,
        candidates: list[dict],
    ) -> ModelScoreBatch:
        ...
```

### 6.4 候选增量编码

当平台刷新开放 Issue 后：

```text
候选同步
→ 资格检查
→ 任务特征提取
→ 文本编码
→ 结构编码
→ 写入候选向量缓存
```

HTTP 推荐请求不能现场重新训练或下载编码器。

### 验收条件

- 已知用户能使用历史图向量；
- 未知用户能通过画像获得结果；
- 缺向量、未知仓库、未知标签有明确回退；
- 模型不可用时返回明确状态；
- 不把冷启动推荐伪装成历史行为推荐。

---

## 阶段 7：接入现有推荐服务

**预计：2～3 天，需与 D 联调**

建议最终链路：

```text
CandidateStore.matchable_candidates()
              ↓
现有资格和安全硬过滤
              ↓
CoderIssueScorer.score()
              ↓
用户 not_suitable 反馈降权
              ↓
仓库与任务类型多样性重排
              ↓
RecommendationBatchV3
              ↓
保存推荐快照
```

保留现有能力：

- 候选可用性检查；
- 语言、操作系统和难度硬门槛；
- 用户反馈隔离；
- 每仓库最多推荐数量；
- 推荐快照和 `run_id`；
- `RecommendationBatchV3` 输出格式。

新增模型元数据：

```text
model_version
dataset_version
graph_version
encoder_version
profile_mode
coverage_state
```

需要 D 修改的公共文件或接口，应提交变更请求，不直接改公共契约或 API。

### 验收条件

- D 能在独立进程中加载模型；
- 模型启动时不访问网络；
- 相同输入和版本得到相同排名；
- 推荐结果全部是当前仍可领取的 Issue；
- 模型分数明确为匹配分，不是成功概率。

---

## 阶段 8：正式实验与答辩材料

**预计：3～5 天**

### 正式对比

至少包含：

```text
Popularity
Text Similarity
MF
LightGCN
LightGCN + Text
LightGCN + Structure
LightGCN + Text + Structure
完整跨粒度模型
```

### 必做消融

- 去掉文本编码；
- 去掉结构 GAT；
- 去掉项目级传播；
- 去掉画像冷启动；
- 去掉对比学习；
- 全局排序与先项目后 Issue 对比。

### 分组结果

- 普通历史用户；
- 历史交互不超过 2 次的用户；
- 完全未知用户；
- 单仓库用户；
- 跨仓库用户；
- 仓库内推荐；
- 跨仓库推荐。

### 最终模型包

```text
model_manifest.json
weights.pt
config.json
feature_schema.json
developer_mapping.parquet
repository_mapping.parquet
label_mapping.parquet
encoder_revision.json
score_calibration.json
environment.lock
```

### 验收条件

- baseline 和完整模型都可以重跑；
- 至少一个新增模块完成独立消融；
- 所有正式模型使用同一测试协议；
- 保存逐用户预测和错误案例；
- 报告运行时间、显存、磁盘和推理开销；
- 不因优化没有提升而更换测试集。
