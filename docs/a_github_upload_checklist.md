# A 补充上传 GitHub 清单

此清单相对本机已同步 main 18fd830a 的文件树整理。远端后续合并了C/D工作，正式提交应在同步后的干净分支复核；当前混合工作区不要git add .。本次只整理清单，不暂存、提交或推送。

## 第一批：建议补传43个文件

### src/oss_mentor/datasets/：13个

```text
b_delivery.py
c_encoder_input.py
code_languages.py
connector_cache.py
gfibot.py
git_history.py
git_objects.py
git_source_validation.py
github_history.py
identity_map.py
issue_links.py
path_tree.py
source_validation.py
```

### scripts/midterm_a/：18个

```text
audit_coder_resources.py
b_delivery_reader.py
collect_b_candidate_repositories.py
collect_b_real_pilot.py
collect_git_history.py
collect_github_history.py
download_gfibot.ps1
enrich_issue_links.py
export_b_delivery.py
export_platform_cache_input.py
import_b_delivery_011.py
import_connector_reads.py
import_gfibot.py
inspect_working_dataset.py
repair_b_repository_coverage.py
upgrade_b_delivery.py
verify_b_delivery.py
verify_b_real_pilot.py
```

### tests/midterm_a/：8个

```text
__init__.py
test_b_delivery.py
test_c_encoder_input.py
test_dataset_pipeline.py
test_gfibot.py
test_git_history.py
test_github_history.py
test_issue_links.py
```

### config/midterm_datasets/：2个

```text
coder_resource_audit_v0.1.json
gfibot_source_v0.1.json
```

### docs/：2个

```text
a_pipeline_github_delivery.md
a_github_upload_checklist.md
```

## 第二批：老师库与库存扩展，需单独整理

老师库connect/export脚本、SQL、分批计划、筛选/库存扩展实现和相应测试目前也有未跟踪文件。但不应把整个目录直接加入第一批：它们涉及本机路径、历史运行计划、额外依赖和另一套库存/页面改动，需要单独检查脱敏、依赖闭合与回归。

主要入口包括connect_teacher_clickhouse.ps1、export_teacher_issue_discovery.ps1、export_teacher_issue_discovery_expanded.ps1、export_teacher_issues_batch_readonly.sql、verify_teacher_events_readonly.sql，以及TEACHER_DATABASE_READONLY.md和TEACHER_EVENTS_AUDIT.md。expanded入口依赖多轮config计划，不能仅单传一个脚本宣称全部功能可复现。

本批未包括这些文件，不代表老师库或库存功能已全部上传。

## 已在main，不需重复加

- datasets/b_real_contributions_v0.2/、datasets/c_issue_candidates_v1/。
- 011_midterm_datasets迁移、DatasetStore、基础snapshot/normalization与A合成fixture。
- collect_b_weak_interest*、import_b_weak_interest_db、相关弱兴趣测试及已合并交付文档。

## 不上传/暂不混入

- data/原始API缓存、业务SQLite、老师原始数据、模型/向量大文件、tmp/、output/本地产物、账号/密码/token。
- datasets/b_real_contributions_v0.1/旧版，不与最终v0.2重复上传。
- prepare_source_delivery.py、verify_source_delivery.py：仅用于已取消的本地交接包流程，不纳入本次正式源码交付。
- 先前a_source_delivery*、a_final_handoff*等本地工作记录：引用已删除路径、含历史快照，不直接作为当前仓库交付说明；本批由a_pipeline_github_delivery.md统一说明。
- 本地012_candidate_lifecycle/013_candidate_identity_lookup迁移，与main的画像迁移分开处理。
- api.py、cli.py、sqlite_store.py、web/、画像和推荐模块的混合改动，不放进此次A采集源码提交。
- GitHubClient的本地HTTPException重试改动可以另做小修复PR；核心采集代码已在main原客户端上通过验证，不是本批必需依赖。

正式提交前：用上述白名单核对差异和敏感字段，在隔离的最新main基线运行相关测试。不要把全部未跟踪文件当成已准备好上传。
