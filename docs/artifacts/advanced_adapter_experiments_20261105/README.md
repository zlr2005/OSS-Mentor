# Advanced Adapter 全实验对比索引

生成日期：2026-11-05

## 覆盖范围

- 独立训练目录：41；
- RC1 优化内部去重试验/派生产物：25；
- 冻结 test 对照记录：4；
- 全指标长表记录数：38301；
- RC1 weights SHA-256：`6112db508fd1f14876c272624a5e8d4d711d0d32a694fb02d3267f5f04091b78`。

`runs.csv` 是一行一个实验的宽表；`metrics_long.csv` 保存训练历史、validation 分组、
test 和嵌套指标的全部标量值。以下只展示用于快速比较的核心列，不能替代全指标长表。

本地存在完整实验目录时，可从仓库根目录重新生成：

```powershell
& '.\CODER\.venv\Scripts\python.exe' '.\CODER\scripts\export_adapter_experiment_comparison.py'
```

## 独立训练目录

| Run | Seed | Best epoch | Val MRR | Best nonzero MRR | Recall@5 | Recall@10 | Pair acc | Unseen MRR | Selection | Decision |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| data/models/advanced_adapter_protected_p1_s20261103 | 20261103 | 0 | 0.250195 | 0.246902 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_p2_s20261103 | 20261103 | 0 | 0.250195 | 0.249448 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_p3_s20261103 | 20261103 | 0 | 0.250195 | 0.249448 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round2_f02_lr1e4_s20261103 | 20261103 | 4 | 0.250579 | 0.250579 | 0.307278 | 0.541779 | 0.571159 | 0.261716 | 0.255439 | experimental |
| data/models/advanced_adapter_protected_round2_f02_lr2e5_s20261103 | 20261103 | 13 | 0.251805 | 0.251805 | 0.307278 | 0.547170 | 0.571698 | 0.259569 | 0.255880 | experimental |
| data/models/advanced_adapter_protected_round2_f02_lr5e5_s20261102 | 20261102 | 7 | 0.251824 | 0.251824 | 0.307278 | 0.547170 | 0.572102 | 0.260450 | 0.255955 | experimental |
| data/models/advanced_adapter_protected_round2_f02_lr5e5_s20261103 | 20261103 | 17 | 0.251890 | 0.251890 | 0.304582 | 0.544474 | 0.570755 | 0.260329 | 0.256183 | experimental |
| data/models/advanced_adapter_protected_round2_f02_lr5e5_s20261104 | 20261104 | 7 | 0.251379 | 0.251379 | 0.304582 | 0.536388 | 0.570350 | 0.259715 | 0.255688 | experimental |
| data/models/advanced_adapter_protected_round2_f02_lr5e5_s20261105 | 20261105 | 1 | 0.251118 | 0.251118 | 0.304582 | 0.539084 | 0.570755 | 0.256974 | 0.254838 | experimental |
| data/models/advanced_adapter_protected_round2_f02_lr5e5_s20261106 | 20261106 | 4 | 0.250440 | 0.250440 | 0.304582 | 0.541779 | 0.572642 | 0.260095 | 0.255022 | experimental |
| data/models/advanced_adapter_protected_round2_f05_lr1e4_s20261103 | 20261103 | 0 | 0.250195 | 0.247798 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round2_f05_lr2e5_s20261103 | 20261103 | 0 | 0.250195 | 0.250001 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round2_f05_lr5e5_s20261103 | 20261103 | 0 | 0.250195 | 0.249763 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round2_f10_lr1e4_s20261103 | 20261103 | 0 | 0.250195 | 0.248639 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round2_f10_lr2e5_s20261103 | 20261103 | 0 | 0.250195 | 0.247758 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round2_f10_lr5e5_s20261103 | 20261103 | 0 | 0.250195 | 0.248747 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round3_f005_lr2e5_m01_s20261103 | 20261103 | 0 | 0.250195 | 0.251101 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round3_f005_lr5e5_m01_s20261103 | 20261103 | 0 | 0.250195 | 0.251080 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round3_f01_lr2e5_m01_s20261103 | 20261103 | 0 | 0.250195 | 0.250906 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round3_f01_lr5e5_m01_s20261103 | 20261103 | 0 | 0.250195 | 0.250440 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round3_f02_lr5e5_m05_s20261103 | 20261103 | 0 | 0.250195 | 0.249697 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round3_f02_lr5e5_m10_s20261103 | 20261103 | 0 | 0.250195 | 0.250169 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round3_f02_lr5e5_m20_s20261103 | 20261103 | 0 | 0.250195 | 0.249925 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round4_f02_lr5e5_m01_h025_s20261103 | 20261103 | 0 | 0.250195 | 0.250621 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round4_f02_lr5e5_m01_h05_s20261103 | 20261103 | 0 | 0.250195 | 0.251684 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round4_f02_lr5e5_m01_h0_s20261103 | 20261103 | 0 | 0.250195 | 0.249829 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round4_f02_lr5e5_m01_h10_s20261103 | 20261103 | 0 | 0.250195 | 0.246207 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round4_f02_lr5e5_m01_h3_s20261103 | 20261103 | 0 | 0.250195 | 0.248879 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_protected_round4_f02_lr5e5_m01_h5_s20261103 | 20261103 | 0 | 0.250195 | 0.247741 | 0.326146 | 0.533693 | 0.570889 | 0.246897 | 0.252839 | fallback_to_rc1 |
| data/models/advanced_adapter_supplement_n2_c1 | 20261105 | — | 0.249297 | 0.249297 | 0.309973 | 0.533693 | 0.566712 | 0.243951 | 0.254056 | experimental |
| data/models/advanced_adapter_supplement_n2_c1_s20261102 | 20261102 | — | 0.241058 | 0.241058 | 0.291105 | 0.539084 | 0.560512 | 0.255357 | 0.248095 | experimental |
| data/models/advanced_adapter_supplement_n2_c1_s20261103 | 20261103 | — | 0.253233 | 0.253233 | 0.323450 | 0.552561 | 0.573854 | 0.244399 | 0.257287 | experimental |
| data/models/advanced_adapter_supplement_n2_c1_s20261104 | 20261104 | — | 0.247949 | 0.247949 | 0.296496 | 0.547170 | 0.568059 | 0.250363 | 0.255224 | experimental |
| data/models/advanced_adapter_supplement_n2_c1_s20261106 | 20261106 | — | 0.240795 | 0.240795 | 0.334232 | 0.547170 | 0.564690 | 0.258327 | 0.245104 | experimental |
| data/models/advanced_adapter_supplement_n2_c5_hybrid | 20261105 | — | 0.229558 | 0.229558 | 0.299191 | 0.512129 | 0.552561 | 0.224590 | 0.225235 | experimental |
| data/models/advanced_adapter_supplement_v1 | 20261105 | — | 0.243137 | 0.243137 | 0.309973 | 0.544474 | 0.570081 | — | — | experimental |
| data/models/advanced_adapter_supplement_v1_calendar_quarter | 20261105 | — | 0.245651 | 0.245651 | 0.299191 | 0.533693 | 0.561321 | — | — | experimental |
| data/models/advanced_adapter_supplement_v1_pre_interaction | 20261105 | — | 0.249642 | 0.249642 | 0.328841 | 0.533693 | 0.566577 | — | — | experimental |
| data/models/advanced_adapter_supplement_weighting_e2 | 20261105 | — | 0.240295 | 0.240295 | 0.307278 | 0.509434 | 0.552426 | 0.236139 | 0.229944 | experimental |
| data/models/advanced_adapter_supplement_weighting_e3 | 20261105 | — | 0.241359 | 0.241359 | 0.304582 | 0.520216 | 0.554313 | 0.226860 | 0.229679 | experimental |
| data/models/advanced_adapter_v1 | 20261102 | — | 0.238534 | 0.238534 | 0.293801 | 0.536388 | 0.563612 | — | — | experimental |

## RC1 优化内部试验

| Run | Seed | Val MRR | Recall@5 | Recall@10 | Pair acc | Selection |
|---|---:|---:|---:|---:|---:|---:|
| optimization/round1/checkpoint_average/round1_checkpoint_average-s20261102 | 20261102 | 0.155080 | 0.191375 | 0.407008 | 0.462264 | 0.180028 |
| optimization/round1/seed/round1_seed_20261103-s20261103 | 20261103 | 0.238818 | 0.315364 | 0.520216 | 0.558895 | 0.239345 |
| optimization/round1/seed/round1_seed_20261104-s20261104 | 20261104 | 0.237021 | 0.315364 | 0.552561 | 0.559030 | 0.240957 |
| optimization/round1/seed/round1_seed_20261105-s20261105 | 20261105 | 0.242842 | 0.320755 | 0.528302 | 0.570485 | 0.250336 |
| optimization/round1/seed/round1_seed_20261106-s20261106 | 20261106 | 0.237646 | 0.301887 | 0.517520 | 0.550000 | 0.241120 |
| optimization/round1/structural/r1_scale025_h32-s20261102 | 20261102 | 0.225584 | 0.296496 | 0.512129 | 0.550000 | 0.220101 |
| optimization/round1/structural/r1_scale050_drop10_l2e3-s20261102 | 20261102 | 0.228906 | 0.301887 | 0.506739 | 0.547978 | 0.226300 |
| optimization/round1/structural/r1_scale050_drop10_l2e4-s20261102 | 20261102 | 0.234998 | 0.309973 | 0.530997 | 0.561186 | 0.232682 |
| optimization/round1/structural/r1_scale050_h16_lr5e4-s20261102 | 20261102 | 0.228961 | 0.318059 | 0.495957 | 0.550674 | 0.224939 |
| optimization/round1/structural/r1_scale050_h32-s20261102 | 20261102 | 0.239917 | 0.301887 | 0.525606 | 0.560512 | 0.243994 |
| optimization/round1/structural/r1_scale050_h64_lr5e4-s20261102 | 20261102 | 0.231613 | 0.293801 | 0.536388 | 0.557817 | 0.225614 |
| optimization/round1/structural/r1_scale075_h32-s20261102 | 20261102 | 0.242874 | 0.307278 | 0.549865 | 0.563747 | 0.246156 |
| optimization/round1/structural/r1_scale075_lr5e4_drop10-s20261102 | 20261102 | 0.228618 | 0.315364 | 0.544474 | 0.555930 | 0.226107 |
| optimization/round2/checkpoint_average/round2_checkpoint_average-s20261102 | 20261102 | 0.156646 | 0.183288 | 0.420485 | 0.462129 | 0.179498 |
| optimization/round2/seed/round2_seed_20261103-s20261103 | 20261103 | 0.233692 | 0.312668 | 0.528302 | 0.559838 | 0.228598 |
| optimization/round2/seed/round2_seed_20261104-s20261104 | 20261104 | 0.235163 | 0.301887 | 0.536388 | 0.558086 | 0.234914 |
| optimization/round2/seed/round2_seed_20261105-s20261105 | 20261105 | 0.250195 | 0.326146 | 0.533693 | 0.570889 | 0.252246 |
| optimization/round2/seed/round2_seed_20261106-s20261106 | 20261106 | 0.237473 | 0.307278 | 0.533693 | 0.559030 | 0.234177 |
| optimization/round2/structural/r2_gate-s20261102 | 20261102 | 0.224537 | 0.291105 | 0.517520 | 0.549461 | 0.219199 |
| optimization/round2/structural/r2_gate_hard05-s20261102 | 20261102 | 0.223676 | 0.283019 | 0.509434 | 0.545148 | 0.216701 |
| optimization/round2/structural/r2_gate_hard10-s20261102 | 20261102 | 0.222618 | 0.277628 | 0.506739 | 0.542992 | 0.216232 |
| optimization/round2/structural/r2_gate_hard10_drop20-s20261102 | 20261102 | 0.228447 | 0.304582 | 0.536388 | 0.565633 | 0.220453 |
| optimization/round2/structural/r2_gate_hard10_temp05-s20261102 | 20261102 | 0.223477 | 0.285714 | 0.512129 | 0.542722 | 0.216894 |
| optimization/round2/structural/r2_gate_hard20-s20261102 | 20261102 | 0.222657 | 0.277628 | 0.509434 | 0.540836 | 0.216128 |
| optimization/round2/structural/r2_no_gate_hard10-s20261102 | 20261102 | 0.235104 | 0.309973 | 0.541779 | 0.564151 | 0.240945 |

## 冻结 Test 对照

| Model | MRR | MRR@5 | NDCG@10 | Recall@5 | Recall@10 | Mean rank | Pair acc |
|---|---:|---:|---:|---:|---:|---:|---:|
| adapter_v1 | 0.248794 | 0.189465 | 0.311062 | 0.344340 | 0.608491 | 9.254717 | 0.587264 |
| coder_original | 0.169986 | 0.101730 | 0.215845 | 0.216981 | 0.485849 | 11.089623 | 0.495519 |
| round1_best | 0.248937 | 0.192060 | 0.309779 | 0.358491 | 0.603774 | 9.165094 | 0.591745 |
| round2_best | 0.263006 | 0.205896 | 0.322088 | 0.367925 | 0.608491 | 8.990566 | 0.600472 |

## 解释

- `fallback_to_rc1` 表示实验完成了训练，但发布门最终保存的是 epoch 0 RC1 精确权重；
- `experimental` 表示本地研究产物，不批准 D 接入；
- `test_evaluation` 只来自已经按协议解锁的冻结测试；补采保护实验没有打开 test；
- 同一优化 trial 若同时出现在 structural 和 seed 列表中，导出时按 `(label, seed)` 去重；
- 所有未在 Markdown 展开的指标均保留在 `metrics_long.csv`。
