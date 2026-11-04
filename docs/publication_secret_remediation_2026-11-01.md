# 公开数据中的凭据告警修复

## 来源定位

GitHub告警#1指向B贡献代码包中从huggingface/datasets采集的tests/fixtures/hub.py；其中CI测试样例含访问令牌格式字符串。告警#2指向C候选分片task_candidate.0010.jsonl，来源golang/go Issue #77096，正文及嵌套inventory_record_json转载了JFrog reference token格式字符串。

两条来源均为第三方公开内容，不能据此认定是本项目的登录凭据；也不能因位于测试样例或公开Issue便认定令牌无效。没有使用令牌调用服务或探测所有者，未在此文档记录令牌明文。

## 修复策略

- B包：移除该代码内容文件，保留file_version_key、commit、原内容哈希和来源；将availability改为missing，原因secret_redacted_publication。不是修改源码后冒充原始Git blob。同步开发者贡献索引与质量计数。
- C包：仅用明确脱敏标记替换疑似凭据子串，保留候选ID、Issue身份、原状态、核验/到期时间及其他正文。正文和嵌套来源副本一并处理。
- 两包增加publication_redaction.json，记录父版本/父manifest哈希、处理范围；版本增加-security-redacted-v1，重新生成文件清单与manifest.sha256。已有固定SHA引用将失效，消费端需切换到安全修订版本。
- 增加publication_secrets模块、离线扫描入口和导出拦截，匹配此次HF/JFrog两类模式。此检查不宣称覆盖所有密钥类型；错误只报告类型/文件，不输出匹配值。

本次没有修改工作数据库、缓存、原始来源归档、stash或Git历史，也没有暂存、提交、推送或关闭远程告警。原始本地缓存和数据库可能仍含旧内容，不应再发布其旧快照；再次导出需运行发布检查。历史文件、分叉和先前下载副本不会自动被本次修复清除。

## 验证入口

```powershell
$env:PYTHONPATH='src'
python -m unittest tests.midterm_a.test_publication_secrets tests.midterm_a.test_b_delivery -q
python datasets/b_real_contributions_v0.2/verify.py
python datasets/c_issue_candidates_v1/dataset.py
python scripts/midterm_a/check_publication_secrets.py
```

发布检查与B导出回归15项通过。后两项验证数据清单、引用和内容哈希；扫描只针对当前工作区已跟踪的datasets文件，不扫描历史提交。Windows沙箱临时目录需指向工作区可写目录。

两包安全修订版本均实测delivery_verified；B现为3,714可用、151缺失（新增1条secret_redacted_publication），C候选/技能要求数量不变。当前datasets下10,559个已跟踪且仍存在的工作文件扫描通过，未发现此次两类模式。该结果不适用于旧提交、数据库或原始本地缓存。

## GitHub告警与后续操作

先将修订提交到分支并合并到使用这些数据的分支。旧版本的导入库、向量及文本缓存应失效，使用新的manifest/data version重新生成；不要继续将原3,715可用代码统计当作修订后统计。

若凭据确实有效，需由凭据持有人在Hugging Face/JFrog撤销或轮换。项目仓库中删除内容并不等于撤销第三方凭据，不能将告警直接标记revoked。只有确认是非敏感测试值才适合按证据关闭为误报/测试用途。

若决定清除历史，需要另行制定团队协调的历史清理方案；不直接对共享main强推。历史改写会改变提交SHA，旧分支/PR缓存/克隆可能仍保留敏感内容。官方说明：https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository 。
