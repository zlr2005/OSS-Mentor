# CODER门控融合：细粒度语义与同仓库辅助目标

完成三个方向各五seed，共15个模型、450epoch。只有同仓库辅助目标的主要MRR均值略高，但幅度很小且Recall@5略降，尚不能认定稳定收益；新增语义特征的两个方向未超过基线。**继续保留原CODER门控融合为默认研究候选**，不覆盖原模型或发布新权重。

## 固定协议

训练仍为1,937条查询、332名开发者；同一209条开发验证outcome、135名开发者、159份画像；每查询199～348个候选，全部目标符合资格，不补入目标。沿用相同五seed、30epoch、batch64、AdamW lr=.001/weight_decay=.001、梯度上限1、开发者倒数频次权重。没有按验证选checkpoint、额外损失权重或语义特征子集。

三个方向保持CODER表示和评分的真实门控通道，采用原21候选困难对照：10条合格原对照与10条CODER困难对照，只在TRAIN挖掘，排除该用户其它已知TRAIN正例。未标记对照不是真负例。

### 语义特征方向

严格复用原 `selected_files.json` 选材：最多3份最新、内容去重、截止前获证的历史文件，不额外挑选更有利文件。使用原冻结MiniLM版本、256token、文件8192字符上限，本地重新编码2,402份文件，并编码4,681个原历史Issue标题；无下载、无上传。

将文件向量重新平均，与原1,165份画像的384维用户向量逐项比较，最大绝对误差为 **0.0**，确认没有编码器或选材漂移。

新增6维为用户平均向量—标题余弦、最佳文件—Issue余弦、最佳文件—标题余弦、各文件—Issue相似度标准差、最近文件—Issue余弦、最近文件—标题余弦。原Issue完整文本向量直接复用，未增加token上限。标准化只在TRAIN拟合。两种语义方向将pair输入从16增至22维，仍使用32维隐藏层和相同CODER门控结构；输入维数变化会改变初始化，不能把差值全部归因于语义信息本身。

### 同仓库辅助目标方向

在原查询内CE和0.01总分平方惩罚之外，增加固定 **0.25倍同仓库候选CE**。仅在TRAIN按已知正例仓库构造辅助损失mask，正例在mask内，同仓库没有对照时此项为0；mask不作为推理特征，评价目标仓库不输入模型。

单独使用辅助目标的方向保持原16维pair输入。逐seed实际复核：初始模型权重、原16维标准化参数、全部TRAIN困难对照与原基线完全相同。因此该方向主要变化确实是训练目标。虽然实验统一生成22维缓存，其推理仅需原16维，不要求在线标题/文件重新编码。

## 实际结果

均为同一大候选池上的五seed单模型指标均值，不是集成或某个最优seed成绩。

| 方向 | 开发者等权MRR | Outcome MRR | Recall@5 | NDCG@5 | 同仓库MRR |
|---|---:|---:|---:|---:|---:|
| 原CODER门控融合 | 0.632714 | 0.602979 | **90.24%** | **0.672985** | **0.611115** |
| 只加6维语义特征 | 0.626879 | 0.592525 | 89.28% | 0.661857 | 0.600135 |
| **只加同仓库辅助目标** | **0.633308** | **0.603062** | 90.14% | 0.672678 | 0.610394 |
| 语义＋同仓库辅助目标 | 0.629598 | 0.595105 | 89.38% | 0.664157 | 0.603717 |

只加同仓库目标相对原基线：主要MRR增加 **0.000594**，Recall@5下降 **0.0957个百分点**；按135名开发者聚类、保留同人outcome和五seed的条件性MRR差95%区间[-0.007313,+0.007787]。这不足以宣称稳定改善，且同仓库MRR也未提高。仅按MRR最高均值选择会掩盖其它指标的退步。

语义方向MRR差-0.005835，条件区间[-0.028939,+0.016851]；组合差-0.003116，区间[-0.026219,+0.019561]。低画像覆盖组两方向MRR差约+0.0262/+0.0259，但区间均跨0，不能因局部均值选方案。

12条新项目代理样本：原基线和仅同仓库目标Recall@5均为五seed均值3.33%；两个语义方向均为0。候选量、语义匹配维度增加，没有自动解决可迁移技能或历史意愿缺失问题。本轮不能据此推断所有语义模型都无效。

## 检查与产物

- 新增3项测试通过：逐文件匹配保留平均后抵消的信号、文件顺序对最大值/标准差及最近文件项的预期影响；同仓库损失隔离跨仓库候选/单正例组为0/目标mask缺失拒绝；扩展模型维度与真实CODER评分通道有效。
- 15份权重保存加载后预测逐项相同，输出有限且满足总分界限；五seed原CODER大池基线逐条复现。
- 实际TRAIN对照的截止资格、唯一性与已知正例排除检查通过；所有输入哈希运行前后匹配。
- 五seed初始权重、原16维标准化、训练对照一致性结果保存于 `within_comparability.json`。
- 脚本编译与diff格式检查通过。

目录：

- `data/midterm/working/coder_semantic_matches_20261108_v1/`：运行前选材/编码协议、文件与标题向量及原画像重构核验。
- `data/midterm/working/coder_semantic_ranker_20261108_v1/`：三方向协议、15份权重、标准化、损失/逐条排名、完整分组@5/10/20、通道检查与verification。
- `within_model_manifest.json`：仅同仓库目标的研究备选清单，推理可使用原 `GatedFusion` 类与normalizer前16项，标记未替换原基线。该模型没有新增在线编码需求。

```powershell
$env:PYTHONPATH='src;tmp/pr39-review/CODER/src'
& 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe' scripts/midterm_a/test_coder_semantic_ranker.py
& 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe' scripts/midterm_a/prepare_coder_semantic_matches.py data/midterm/working/coder_semantic_matches_REPLAY
& 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe' scripts/midterm_a/train_coder_semantic_ranker.py --output data/midterm/working/coder_semantic_ranker_REPLAY
& 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe' scripts/midterm_a/analyze_coder_semantic.py data/midterm/working/coder_semantic_ranker_REPLAY
```

训练脚本当前读取正式语义缓存目录，重新编码命令仅用于另目录复核编码，不会自动切换训练依赖。所有研究仍继承旧CODER图/底座来源审计缺口和旧适配器较大训练集合；验证已被多轮使用，以上区间不覆盖方向选择偏差。未评分原test、未读取外部117条评价指标、未改公共接口/数据库/前瞻协议或平台权重。

现有结果说明，继续简单增加语义统计或辅助损失的收益已经很小。下一阶段应优先定位标签/历史证据质量和用户实际需求覆盖，并在可信的独立评价上验证，不能把反复开发集微涨包装成持续泛化提升。
