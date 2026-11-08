# 论文驱动的快速改进：已有实测结果与激进路线

结论：继续小幅调整原Adapter不是当前首选。本轮已经实际验证了一个更有效的开发方向：**显式的开发者–候选交互特征＋LambdaMART**。它在同一新数据、同一209条开发验证样本和相同21候选上，五seed平均开发者等权MRR从0.536424升至0.666232。这个结果支持切换研究优先级，不等于独立泛化已确认或可以发布。

## 1. 先回答“已经比之前好多少”

### 可直接比较的新公开证据数据版本

训练1,937条、332人；验证209条、135人。未改变候选列表、样本或指标来制造增益。

| 方案 | 开发者等权MRR（主要） | Outcome MRR | Recall@5 | NDCG@5 |
|---|---:|---:|---:|---:|
| 未训练MiniLM文本余弦 | 0.536424 | 0.511766 | 76.08% | 0.551030 |
| 前轮新文本双塔，100%训练量 | 0.504138 | 0.497495 | 76.65% | 0.543567 |
| 本轮交互特征＋LambdaMART | **0.666232** | **0.633516** | 94.93% | **0.708336** |
| 本轮保留余弦、受限树修正 | 0.641912 | 0.611370 | **95.22%** | 0.691540 |

LambdaMART相对当前最强余弦基线：

- 主要MRR绝对增加 **0.129807**，相对约 **24.2%**。
- Outcome MRR增加0.121750，相对约23.8%。
- Recall@5增加 **18.85个百分点**；不是成功解决概率增加18.85个百分点。
- 对135名开发者聚类、保留同一人的全部outcome及五seed的条件性重采样区间：主要MRR差 **[+0.077103,+0.182222]**。

受限修正主要MRR增加0.105487，区间[+0.072004,+0.140230]。两个方向均为开发验证结果，不能当作独立显著泛化；开发验证已经多轮使用，checkpoint选择偏差不被该区间覆盖。

### 旧版本单独看

原v3至原v5正文方案：Outcome MRR 0.294592→0.331506，增加0.036914（约12.5%）；Recall@5 41.35%→48.25%，增加6.90个百分点。这是另一套特征覆盖和评估口径，**不能与新0.666232直接相减宣称翻倍**。上表新旧“文本模型”也不是CODER底座的同协议替代评测。

## 2. 为什么这轮改动更有效，以及哪里还不够

原小网络主要学习压缩画像与平均文本向量的映射。新排序器直接观察“这个人的历史是否与这个候选匹配”，而不要求网络从较少成功标签中重新学出全部关系。

本轮只用16个特征：余弦相似度；截止前获证X中的候选仓库熟悉度、不同commit比例/数量、总commit/路径数、历史文件名在Issue文本中的匹配；历史Issue标题/正文长度、行数、代码块、URL及简单bug/docs/test/feature文本标记。任务标记是文本代理，不是人工能力真值。没有使用当前待预测Issue的解决PR、实际修改文件、解决者、关闭时间、未来评论、数值用户ID或“positive/control”来源标记。

模型是LightGBM4.6.0的LambdaRank：120棵树上限、深度4、15叶、lr=.03、L2=5、最小叶40、特征采样.9；相同五seed。只在预先列出的10、20……120棵树上按原开发验证主要MRR选checkpoint，没有在原test或外部117条记录上选参数。每条查询21个候选，训练按开发者频次倒数加权。

受限方案以10倍余弦作为初始分数，推理时显式加回余弦，树修正截断在[-1,1]；保存/加载后的逐样本排名一致。它比完全替换表示更保守，但不保证每一条排序都不下降。

### 防止被大指标误导的诊断

- 同仓库诊断187条outcome，每条平均仅2.55个同仓库对照。余弦MRR 0.599376→普通树0.626239，增加约0.026863；受限树0.610820。**同仓库增益明显小于跨仓库混合池总增益**，不能把全池24.2%推广成精细同项目排序提升。
- 在同一cutoff内错配不同开发者的历史特征、保留原余弦时，普通树主要MRR降至0.404175；表明模型依赖历史匹配特征，但这只是分布扰动诊断，不是因果证明。
- 去掉余弦和全部历史特征，只用Issue统计特征的五seed诊断模型，主要MRR约0.196731；纯任务先验解释不了本轮全部增益。
- 当前评估仍是1正例＋20抽样对照，Recall接近95%不能代表真实全候选池可用率。下一步应先扩为每个cutoff全部已获证候选，报告召回覆盖和排序指标；不得缩小候选池或强行插入正例后宣称提升。

## 3. 老师提供的十篇论文，分别应该借什么

核对PDF研究目标、方法和关键实验页；不是所有模型都适合直接拿来刷当前MRR。中间文本和文件SHA-256索引见 `tmp/papers_strategy_20261107/`，PFIRec第9页图8/9另做渲染核对。

| 论文 | 对我们的可用启发 | 不应误读 |
|---|---|---|
| GFI-Bot，FSE2022，5页 | XGBoost任务新手友好度、内容/背景/动态特征；适合作任务质量先验 | AUC最高0.8302是一般GFI识别，不是个性化MRR或解决概率 |
| RecGFI，ICSE2022，13页 | 恢复创建时/开始处理前快照，经典模型与明确任务特征 | 创建时AUC0.801、第二时点0.853，不是我们的Recall@5 |
| PFIRec，ASE2023，13页 | **首要参考**：用户–Issue交互特征、内容/领域偏好、经验/活跃度＋LambdaMART | 11,615个场景、候选中位数32，与我们不同。论文R@1中位数0.07→0.15（+114.3%）不能套成本项目预期 |
| Mentoring on GFIs，ICSE2023，12页 | 将支持可得性、澄清、环境和测试指导纳入导学；用历史支持特征作辅助 | 专家参与相关性不等于导师因果收益，不能把未来专家回复作历史特征 |
| CTCK，KAIS2025，28页 | 多关系知识图谱、欧氏/双曲空间及跨空间对比学习 | 其AUC0.7716、F1 0.7260不是本项目MRR；稀疏、零历史群体和公开复现材料仍有限，不是最快路线 |
| IssuePilot，MSR2026，5页 | 当前兴趣问答、技能证据、仓库分析、个性化上手文档 | 15人主观用户调查不是离线排序提升或真实成功率；80%接受不能称80%准确 |
| CodeBERT，12页 | 为代码片段–自然语言建立代码语义通道，可考虑任务适配训练 | MLM/RTD预训练不自动等于良好句向量检索；不能直接替换MiniLM后承诺增长 |
| FLOSScoach，12页 | 解决找入口、贡献流程、环境说明等非排序瓶颈 | 65名学生的门户研究不是个性化排序benchmark，也没有消除全部技术障碍 |
| 首次贡献实证，4页 | 文档、测试、配置等非代码首贡也需覆盖；任务类型与修改规模分层 | 3501个PR的分布研究不直接提供用户–Issue适配真值，不能一律认定新人只配小任务 |
| CODER，WWW2023，11页含封面 | 代码分段、用户条件注意力、层级结构、文件/项目多行为，可作后续研究骨架 | 原任务为开发者–代码文件，规模数十万文件，并非我们当前Issue任务和数据的严格复现 |

PFIRec第8页明确比较SimCSE、CodeBERT、RoBERTa、BERTOverflow，整体接近，SimCSE的FirstHit最好；第9页LambdaMART优于其他分类排序变体。第10页表II中移除Issue背景后性能下降较大，值得补上下文；其消融描述文字与表格中最大下降项有不一致，不能照抄文字当精确归因。

PFIRec正文对时间排序使用“descending”但又描述历史训练模拟。复用其数据必须核对真实折日期与代码，不能机械复制折编号就宣称无未来泄漏。作者资源已再次访问：[PFIRec代码](https://github.com/mcxwx123/PFIRec)。

CodeBERT官方说明其基于CodeSearchNet的代码/文档及MLM+RTD预训练，支持代码搜索任务；因此更合适的下一步是独立代码通道并做检索适配，而不是将原始pooling向量当现成万能排序器。[官方模型说明](https://huggingface.co/microsoft/codebert-base)

## 4. 激进路线，按收益证据与落地顺序排

### A：先采用已实测有效的排序路线作为研究主线

保留余弦强基线；以PFIRec启发的树排序做主对照，固定当前权重及特征Schema，先扩大同cutoff候选评估，再检查无历史、低覆盖、新仓库组。不要继续大量扫原MLP隐藏维数和残差尺度。LightGBM的ranker原生支持LambdaRank查询分组，适合目前规模。[接口文档](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRanker.html)

### B：更激进但尚未实测——两阶段召回与交叉编码精排

先用文本/BM25等检索形成Top50～100，再用CrossEncoder联合阅读用户证据与Issue，输出匹配分数。将“用户证据—任务要求—不足项”显式放进输入，而不是各自平均后做点积；训练采用TRAIN难对照和分级适配标注。先在真实的完整已获证池上量召回上限，不能在现有21候选上把“Top50召回”称为系统改进。[官方检索与精排流程](https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html)

交叉编码器计算更重，先离线评分少量开发样本，不在实时请求下载/训练。若有效，再用教师分数蒸馏回树排序器或双塔，兼顾质量和延迟。其增益尚未知，不能把本轮树排序24.2%继续当CrossEncoder预期。

### C：语义输入升级，比盲目加深图更优先

针对当前完整文件前缀大量截断的问题，提取函数/类、API调用、依赖、测试类型、文档结构等可追溯片段；以Issue内容检索已有历史片段，取Top-k或做用户条件注意力。自然语言Issue与代码分别使用合适编码器，再将相似度/覆盖/缺失作为排序特征。

可以用本地或受控LLM生成结构化候选技能和任务要求，但必须带来源片段和不确定性；它们是弱标注，不是开发者真实能力或成功结果。当前兴趣、每周时间应问用户，不能从commit臆测，更不能回填过去。目标Issue最终解决PR中的文件/实现细节只能用于结果核验，不能拿来为预测时任务补技能。

### D：Agent/导师/图作为后续专项，而非第一步追分

IssuePilot/导师/FLOSScoach更适合提升选中任务后的启动成功：具体文件、环境、最小测试、PR规范和求助路径。用启动耗时、步骤可执行性、真实贡献评价，不强行折算为MRR。

CTCK及完整CODER适合关系证据、时间切分和高质量交互足够之后再做。当前图来源审计未齐、监督稀疏，重建大图和复杂对比损失风险/成本高，不能据论文AUC预测本项目增幅。

## 5. 哪些新数据最值得补

优先顺序不是“随机再抓一万条Issue”：

1. **人工适配等级和真实困难对照**：已生成200组、600条TRAIN盲标模板（每组一个已知TRAIN正例和两个语义近但未标记对照），没有使用验证/test挖难例。按0～3及unknown标注匹配度，部分双人复核，理由绑定截止前证据。模板目前0条完成，不自动充当训练标签。
2. **历史PR标题/描述、已解决Issue文本和review行为**：围绕同一开发者不同任务构造更丰富偏好，严格截断到cutoff；评论不等同于成功。近期30/90天的活跃与任务类型可能有用，但不优先猜情感或能力分。
3. **仓库领域和上手上下文**：先审计已有76份仓库上下文、203份文档，补README/贡献指南/依赖/测试命令的历史公开证据，再补缺口。已有缓存大多仅有commit时间，未证明首次公开；不能直接加入历史模型。
4. **更难、更完整的同仓库候选与独立后续结果**：补足相似任务、真正新仓库/低历史用户，保留未标记状态；扩候选可使指标下降，但能检验新树排序是否真正有效。独立结果与训练材料持续隔离。

PFIRec资源可以作为独立基准或方法复现，但其基于GFI-Bot，合并前必须按用户/仓库/Issue/时间去重；不建议未经重叠审计直接拼入现有训练集。

标注产物：`data/midterm/working/ranker_annotation_20261107_v1/blind_annotation.jsonl`（600条空标签）和单独的 `sampling_provenance.jsonl`。尚未联系任何人或向第三方上传这些材料。

## 6. 本轮实际交付与边界

- 核对十篇PDF并生成来源SHA-256索引，关键PFIRec图页渲染核验。
- LightGBM4.6.0仅安装在工作区 `tmp/research_lightgbm_4_6_0`，未修改原训练环境或全局依赖。
- 完成两个方向各五seed，共10个排序模型；另五个Issue-only诊断模型，不用于选择发布候选。
- 三项新泄漏/特征/同仓库诊断测试通过。10个排序模型保存加载后排名复现，输入哈希复核通过，编码未改、原test及117条外部结果未读/重评。
- 预注册之后仍需独立评价；原前瞻600条预备Issue和408人名册没有因这次结果自动入组，90天计时未启动，发布模型不变。

实验目录：

`data/midterm/working/pfirec_ranker_pilot_20261107_v1/`：训练前协议、10个模型、验证排名、结果、重要性。

`data/midterm/working/pfirec_ranker_diagnostics_20261107_v1/`：配对区间、同仓库诊断、历史错配、Issue-only对照。

复现（工作区根目录，使用全新输出目录）：

```powershell
& 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe' scripts/midterm_a/pfirec_inspired_ranker.py --dataset data/midterm/working/public_text_dataset_20261107_v2 --features data/midterm/working/public_text_features_20261107_v1 --profiles data/midterm/working/public_x_profiles_20261107_v1 --output data/midterm/working/pfirec_ranker_NEW
```

## 论文原文

1. [GFI-Bot](<C:/Users/zhuli/Desktop/大学/OSS-Mentor/补充论文/2022-FSE-Automated Good First Issue Recommendation on GitHub.pdf>)，关键第2～4页。
2. [RecGFI](<C:/Users/zhuli/Desktop/大学/OSS-Mentor/补充论文/2022-ICSE-Recommending Good First Issues in GitHub OSS Projects.pdf>)，关键第3～6页。
3. [PFIRec](<C:/Users/zhuli/Desktop/大学/OSS-Mentor/补充论文/2023-ASE-Personalized_First_Issue_Recommender_for_Newcomers_in_Open_Source_Projects.pdf>)，关键第4～10页，图7～9、表II。
4. [导师研究](<C:/Users/zhuli/Desktop/大学/OSS-Mentor/补充论文/2023-ICSE-Is_It_Enough_to_Recommend_Tasks_to_Newcomers_Understanding_Mentoring_on_Good_First_Issues.pdf>)，关键第3、7、9～11页。
5. [CTCK](<C:/Users/zhuli/Desktop/大学/OSS-Mentor/补充论文/2025-KAIS-Cross-space topological contrastive learning for knowledge graph-aware issue recommendation.pdf>)，关键第13～16、23页；本地PDF共28页。
6. [IssuePilot](<C:/Users/zhuli/Desktop/大学/OSS-Mentor/补充论文/2026-MSR-An Agentic Framework for Personalized Issue.pdf>)，关键第2～4页。
7. [CodeBERT](<C:/Users/zhuli/Desktop/大学/OSS-Mentor/参考论文/CodeBERT模型的核心能力.pdf>)，模型/微调与代码搜索实验。
8. [FLOSScoach](<C:/Users/zhuli/Desktop/大学/OSS-Mentor/参考论文/FLOSScoach 一站式新人支持门户.pdf>)，研究设计、门户评估及讨论。
9. [首次贡献研究](<C:/Users/zhuli/Desktop/大学/OSS-Mentor/参考论文/分析初学者贡献类型.pdf>)，第2～3页。
10. [CODER](<C:/Users/zhuli/Desktop/大学/OSS-Mentor/参考论文/给有一定经验的开发者做推荐.pdf>)，第4～9页的方法、数据与消融。
