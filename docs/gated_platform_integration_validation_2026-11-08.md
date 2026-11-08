# Coder Gated 平台接入验收记录

日期：2026-11-08  
基线：PR #40，`f550e70d7ae421164249dc1395b4a95a1eaa6ec7`  
审阅分支：`codex/gated-platform-integration-pr40`

## 验收范围

本次验收验证 C 提供的最终门控模型能够通过 D 的平台存储、session 身份、
B 确认画像和后台 worker 进入真实 HTTP 推荐路径。

模型：

```text
variant: coder_gated
version: coder-gated-marginal_positive-s20261102
feature_schema: coder-gated-128+16-v1
```

## 模型资产

- 可移植包：`portable_gated_pr40_9bf9208d.zip`
- ZIP SHA-256：`179d6fe270522f194e7242ddd837a1850637c3c2f581a0e35300069fbcfcff0f`
- `portable.py verify`：422 个受清单管理文件全部通过
- 门控权重 SHA-256：`f933bf970c906abcd44fff88dc76c9b8c065ace6fedc7e95557db5b2e10f5505`
- 官方离线 smoke：真实 MiniLM 编码、worker 发布和门控评分通过

## 联调环境

- 独立 SQLite 运行库，已应用 18 个迁移。
- 独立 runtime、cache、requests 和 evidence 目录，不使用原工作区数据库。
- 使用真实 GitHub 只读同步取得 `pytest-dev/pytest` 候选。
- 2 条候选通过实时 GitHub 复核进入 `available`。
- 隔离验收启用 1 条 expanded-content admission 候选，保留范围不确定 warning。
- 测试用户、session、画像和确认画像均写入隔离库；浏览器不能提供
  `user_id`、`github_login` 或 `developer_key`。

## HTTP 与 worker 结果

已验证：

1. 未登录访问 `/api/v1/me/recommendations` 返回 `401 authentication_required`。
2. 首次无缓存返回 `200`，明确标记
   `model_features_preparing`、`profile_bootstrap_recommendations` 和
   `scoring_engine=profile_bootstrap_rules`。
3. worker 生成真实缓存后，后续请求返回：
   - `scoring_engine=coder_gated`
   - `model_version=coder-gated-marginal_positive-s20261102`
   - `profile_mode=cold_start_population`
   - `coverage_state=gated_prepared_context`
4. 修改基础画像后，旧缓存失效，接口重新进入明确准备态；worker 重新编码后
   再次返回 `coder_gated`。
5. 修改候选正文后，旧缓存失效；恢复正文并重新准备后再次返回
   `coder_gated`。
6. 第二个 session 使用独立画像和多维上下文，没有读取第一个用户的缓存。
7. worker 每个画像上下文只处理最新请求；旧请求不会在 60 秒重试后覆盖新缓存。
8. `/api/v1/status` 返回：

```json
{
  "model": {
    "variant": "coder_gated",
    "version": "coder-gated-marginal_positive-s20261102",
    "status": "active"
  }
}
```

## 本次修正

- 修复可移植包内 CODER 研究源码发现逻辑，明确要求
  `coder_issue/training/advanced_issue_inputs.py` 存在。
- 将 `bind_enhanced_profile()` 统一放入 model gateway，避免 API 与评分器
  各自维护画像绑定逻辑。
- 修复同一画像上下文下多个历史 worker request 互相覆盖 current pointer 的
  问题，只处理最新请求。
- 将 `update_github_login()` 加入 `IdentitySessionStore` Protocol，并增加
  结构一致性测试。

## 验证结果

- Gated 专项：34/34
- Python unittest：1015 项通过
- Python pytest 模块：27/27
- Node：19/19
- `compileall`：通过
- `git diff --check`：通过

## 尚未覆盖

- 本次隔离 HTTP 验收使用服务端注入的测试 session，没有再次执行真实 OAuth
  浏览器授权。
- 原工作区 SQLite 仅做只读检查；未修改其数据。该库当前没有
  `candidate_availability=available` 的现存候选，不能直接用于真实推荐验收。
- 本机使用 Python 3.11.9 和交接包锁定依赖完成验证；生产部署建议使用交接包
  声明的 Python 3.13.3 环境重新执行 `portable.py verify/smoke`。
- 本次未验证 PostgreSQL 运行时、TLS 反向代理、进程托管或生产监控。

因此当前状态是：

```text
D_READY_FOR_INTEGRATION
```
