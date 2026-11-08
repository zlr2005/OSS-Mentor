# 公开证据补齐与贡献修改片段 v6 对照

完成 15 次新 CPU 训练（3 组×5 seed）、235 epoch、20 份验证记录。当前共同获证材料上，正文视图的开发验证均值最高；patch 未超过正文，低覆盖组也有均值下降，不支持替换。所有主要配对区间均跨 0，不能认定稳定泛化或统计显著提升。未评分原 test、未读取原有 117 条外部评价或更换发布模型。

## 公开证据的实际增加

对前轮未决的 2,902 个精确 repository/commit 执行 363 批 GitHub 只读关联查询。两批初次失败，重试后 363 批全部成功；所有目标均返回精确 commit，260 个没有关联 PR，没有关联列表触及分页上限。

从中选择精确合并 SHA 相同、仓库相同且合并时间早于所需 cutoff 的 2,382 个 PR，执行 120 条独立公开事件 SELECT；全部成功，没有触及每条 200 行上限。

新增复合证据要求：目标 commit SHA 等于当前 API 的 PR mergeCommit，API mergedAt 不晚于独立归档公开 closed/merged 事件，事件仓库和 PR 号精确一致，事件时间严格早于画像 cutoff。没有仅凭关联 PR、当前公开状态、作者时间或 closed 状态放行。这是复合历史推断，不是当时完整 blob 的存档证明。

| 范围 | 画像 | 至少一个选中文件获证 | 全部选中文件获证 |
|---|---:|---:|---:|
| train | 1,193 | 1,007 | 635 |
| validation | 268 | 121 | 24 |

通过引用从 181/4,235 增至 **2,577/4,235**，新增 2,396 条复合证据。仍有 1,216 个不同 commit 未决：519 个缺同仓库精确合并元数据，697 个有精确合并元数据但没有合格的截止前公开事件。按引用计，有 1 条训练引用的已知合并时间不早于 cutoff，未放行；这不单独证明当时没有其他公开路径。

这些统计只覆盖 v5 最多三份已选材料，不能称原 X 全部通过。冻结 RC1/v2/v3 的原训练材料审计尚不完整；本轮新增特征仅用通过材料，并不使整个模型成为已审计的时序安全模型。

## 同材料对照与预算

取 v5 原选文件、公开证明和完整缓存 patch 的交集，不替补文件，不用标签选文件。共同集合 **2,324 个路径/内容去重文档**，覆盖 1,006 份训练画像、121 份验证画像。实际准入的 2,445 条训练 outcome 中 2,056 条有特征（997 份不同画像）；371 条验证 outcome 中 165 条有特征（116 份不同画像）。其余保留原集合，精确回退 v3。

三种视图为路径、路径加完整文件前缀、路径加 API patch；同一画像对同一组文档等权平均。完整文件快照不代表用户编写了全部内容；patch 也包含上下文行。

使用与 v5 相同的本地固定 MiniLM revision，权重 SHA-256 复核通过；离线编码、384 维，8192 字符上限和 256-token 预算相同。仅在实际进入训练且有材料的 997 份不同画像上分别拟合 PCA32/标准差，缺材料向量为零。相比 v5，本轮特征覆盖和 PCA 拟合输入均改变，不能把跨轮分数差直接归因为某一种材料或泄漏。

冻结相同 RC1/v2/v3，新增残差容量和上限保持相同（78→16→32，上限为基础用户范数的 25%，零初始化）。BPR、hard weight=1、batch=128、lr=0.001、weight_decay=0.0001、最多100 epoch、patience=10；固定 seed 20261102～20261106，只用原 validation MRR 选 checkpoint。CPU 训练/评价合计约207秒，不含查询、编码及审计。

## 五 seed validation 结果

| 方案 | MRR | Recall@5 | NDCG@5 | Pair accuracy |
|---|---:|---:|---:|---:|
| 冻结 v3 | 0.294592 | 41.35% | 0.280099 | 66.22% |
| 路径 | 0.299471 | 41.62% | 0.284709 | 66.19% |
| 路径＋完整文件前缀 | **0.305147** | **43.13%** | **0.293214** | **67.49%** |
| 路径＋patch | 0.301425 | 41.73% | 0.286254 | 66.67% |

正文对 v3 的 MRR 平均增加0.010555，Recall@5增加1.78个百分点；正文对路径五个 seed 全部提高。patch 对正文仅1/5 seed提高。开发者等权 MRR 则为 v3 0.296680、路径0.298786、正文0.306721、patch 0.308081，说明不同汇总口径会改变局部排序，不能只挑有利指标。

按开发者聚类重采样4,000次，保留每位开发者的相关 outcome 和五个 seed：

| 配对 | MRR差 | 条件性95%区间 | 改善 seed |
|---|---:|---:|---:|
| 正文－v3 | +0.010555 | [-0.000274,+0.021692] | 5/5 |
| 正文－路径 | +0.005676 | [-0.005633,+0.017826] | 5/5 |
| patch－v3 | +0.006833 | [-0.002658,+0.016365] | 5/5 |
| patch－正文 | -0.003722 | [-0.013886,+0.006165] | 1/5 |

全部跨0；不能声称已证明正文显著优于路径，或 patch 显著退化。原开发验证集反复参与方向选择，区间也不包含这种选择偏差。

| 分组MRR | v3 | 路径 | 正文 | patch |
|---|---:|---:|---:|---:|
| 无原训练解决历史（52 outcome） | 0.261848 | 0.261244 | 0.268190 | 0.252597 |
| 新仓库（28 outcome） | 0.288957 | 0.263617 | 0.286771 | 0.285465 |
| 低覆盖（42 outcome） | 0.241359 | 0.241911 | 0.249863 | 0.221095 |

patch 的低覆盖、无历史组均值下降，正文的新仓库组也未超过 v3；各差值区间均跨0。不能将总体收益推广到每组。

仅看有合格特征的165条验证 outcome：v3/路径/正文/patch MRR分别为0.327111/0.338082/0.350844/0.342475。该子集本来就有较高基线，不能与全验证集直接比较来宣称收益更大。

## 截断与验证

共同文档的路径均未触及 token 上限。完整前缀 2,175/2,324（93.6%）触及256-token截断，1,377份触及8192字符截断；patch 分别为1,316/2,324（56.6%）、82份。减少截断并未带来更好的平均排序，说明“改用 patch”本身不足以解决特征问题。

16项来源/时间/文件检查测试及3项模型/PCA检查通过。五个 v3 参考逐条复现旧结果；15个新增 checkpoint 保存加载后输出一致、所有权重有限；无材料 outcome 逐条等于 v3。冻结输入、底座权重、语义缓存及导入项目源码哈希复核通过。

首次训练启动在依赖哈希阶段遇到 PyTorch 无实体文件的模块名，未产生训练结果。增加实体文件过滤后在全新 v2 目录重新运行；失败目录保留，不混入结果。

## 产物与复现

工作区根目录下：

- `data/midterm/working/v5_pr_links_20261107_v1/`：关联元数据及来源。
- `data/midterm/working/v5_pr_events_20261107_v1/`：120条公开事件查询及响应。
- `data/midterm/working/v5_publication_20261107_v3/`：逐引用证据及剩余分类。
- `data/midterm/working/patch_semantics_v6_20261107_v1/`：共同材料、三视图编码、协议及截断检查。
- `data/midterm/working/patch_semantics_v6_runs_20261107_v2/`：训练前协议、PCA、15个权重、20份逐条验证记录、覆盖及 `analysis.json`。

```powershell
& 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe' scripts/midterm_a/prepare_v6_semantics.py --original D:/OSS-Mentor-assets/pr39/runs/contribution-semantics-20261107-v1 --patches data/midterm/working/v5_patch_supply_20261107_v3 --proofs data/midterm/working/v5_publication_20261107_v3 --assets D:/OSS-Mentor-assets/pr39 --output data/midterm/working/v6_semantics_NEW
& 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe' scripts/midterm_a/patch_semantics_v6.py --data-root . --assets D:/OSS-Mentor-assets/pr39 --semantics data/midterm/working/v6_semantics_NEW --output data/midterm/working/v6_runs_NEW
& 'D:/OSS-Mentor-assets/pr39/runtime-cpu/Scripts/python.exe' scripts/midterm_a/analyze_patch_semantics_v6.py data/midterm/working/v6_runs_NEW --semantics data/midterm/working/v6_semantics_NEW --assets D:/OSS-Mentor-assets/pr39 --data-root .
```

后续优先补独立评估覆盖及底座训练材料公开证据，继续保留正文方向作研究对照；不因 patch 截断较少而替换，不把未获证材料默认当合格数据。下一轮表示优化可预注册比较删除模板/生成文件及按代码结构取片段，但不得根据当前验证错误反复修改选材后把同一验证集称独立测试。
