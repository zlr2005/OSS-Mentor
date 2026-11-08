# v5 数据充分性与本地补充材料

现有数据可以支持继续进行受控小模型实验，不能据此认定训练量已经充分或上线泛化可靠。v5 原冻结训练有 2,445 个 outcome、48,900 个展开 pair；pair 不等于独立正例。开发验证集反复用于选方向，新仓库仅 28 个 outcome、无历史组 52 个。尚未完成原文件首次公开可见时间审计。

本次没有新增训练、评分 test、读取原有 117 条外部评价或更换发布模型。未发起网络采集。

## 已完成的补充

从 v5 选中的 3,867 份路径/内容去重快照逐一验证内容 SHA-256，精确匹配缓存 commit 响应中的 filename，整理出 **3,848 份 patch**；另 **19 份缺 patch**。这些是已有材料的另一种表达，不增加独立开发者或成功标签数。

训练 1,193 份画像全部至少有一个 patch，其中 1,178 份所有已选文件均有 patch；验证 268 份画像全部至少有一个 patch，265 份完整。这里是输入画像数，不是实际准入训练的 1,181 份不同画像，也不是 outcome 数。

产物位于 `data/midterm/working/v5_patch_supply_20261107_v2/`：

- `patches.json`：修改片段、来源证据及内容哈希。
- `collection_gaps.json`：19 份缺失文件及原缓存位置。
- `summary.json`：覆盖、来源清单哈希及使用边界。

API patch 可能被截断，含上下文行，不保证每一行均为用户编写。当前包 `training_ready=false`，历史公开性仍标记 unverified。

## 后续数据优先级

1. 补证所用 commit 在画像 cutoff 前已公开可见，证据需绑定 repository、commit SHA、公开事件时间和来源；作者/提交时间以及本次抓取时间都不能单独证明。不能证明的材料隔离，报告覆盖损失。
2. 补齐 19 份缺 patch；若 API 无法返回，保留缺失，不用完整文件伪装修改片段。
3. 在公开性合格的统一子集上比较路径、路径加完整文件前缀、路径加 patch；保持容量、编码预算、候选及五 seed 相同，同时报告截断比例。当前不能承诺 patch 一定提高分数。
4. 复用已有 v6 增量时先完成与冻结训练的身份、标签去重及准入对账。优先补低历史用户在不同 Issue 上的真实成功关系及交互前历史；每人 3～5 个不同 Issue 仅为排程目标，不是样本充分性保证。
5. 另建一次性独立后续评估集，覆盖低历史、低画像覆盖、新仓库和补采用户，保留完整 90 天结果观察期。同一时点候选应有历史正文、状态和哈希；未解决候选属于未标记对照。独立评估集不能再混入训练或用于挑模型。

暂不设没有统计依据的固定总量门槛。下一次准入后绘制按开发者分组的训练量学习曲线，并用开发者聚类区间判断新增覆盖和边际收益；不能仅靠当前均值推算还需多少数据。

## 复现和检查

从工作区根目录运行：

```powershell
python scripts/midterm_a/audit_v5_patch_supply.py --root . --semantics D:/OSS-Mentor-assets/pr39/runs/contribution-semantics-20261107-v1 --output data/midterm/working/v5_patch_supply_NEW
python -m pytest tests/midterm_a/test_v5_patch_supply.py -q -p no:cacheprovider --basetemp tmp/v5_patch_pytest_NEW
```

5 项测试通过，覆盖精确文件选择、哈希不符、缺片段、缺文件和重复文件拒绝。首次测试因默认临时目录不可写未能执行；改用工作区独立临时目录后通过。
