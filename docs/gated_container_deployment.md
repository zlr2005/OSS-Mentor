# Gated 平台容器部署

本部署方案使用 SQLite 作为运行时数据库，使用独立的 `gated-api` 和
`gated-worker` 容器。模型资产、缓存和请求目录通过 `./data` 共享；生产环境
应将该目录放在受控持久卷上并配置备份。

## 1. 前置条件

- Docker Compose v2
- Python 3.11+，用于重建模型资产和初始化数据库
- 已批准的正式交付包

不要将 OAuth Client Secret、GitHub Token、Session Secret 或用户数据库写入 Git。

## 2. 重建模型资产

```powershell
python scripts/rebuild_gated_runtime_assets.py `
  --archive model-assets/portable_gated_pr40_9bf9208d.zip `
  --output data/gated-v1
```

重建结果至少应包含：

```text
data/gated-v1/
├── assets/
├── bundle/
└── coder_source/
```

## 3. 初始化 SQLite

先用平台 CLI 初始化并准备候选：

```powershell
python -m oss_mentor init-demo --database data/oss_mentor.sqlite3
python -m oss_mentor doctor --database data/oss_mentor.sqlite3
python -m oss_mentor sync-candidates --all-enabled --allow-network --database data/oss_mentor.sqlite3
python -m oss_mentor extract-features --database data/oss_mentor.sqlite3
```

真实环境应使用受控的只读 GitHub Token，并保留候选刷新、失败和限流报告。

## 4. 配置 Secret

从 `.env.example` 复制配置到未提交的 `.env`，至少设置：

```text
GITHUB_TOKEN=
GITHUB_OAUTH_CLIENT_ID=
GITHUB_OAUTH_CLIENT_SECRET=
OSS_MENTOR_SESSION_SECRET=
OSS_MENTOR_BASE_URL=https://your-domain.example
```

`OSS_MENTOR_BASE_URL` 必须与 OAuth App 的 callback URL 主机和协议一致。
生产环境必须通过 HTTPS 反向代理访问，不直接暴露容器端口。

## 5. 启动

```powershell
docker compose -f compose.gated.yaml up --build
```

检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/api/v1/status
```

状态接口应显示：

```json
{
  "model": {
    "variant": "coder_gated",
    "version": "coder-gated-marginal_positive-s20261102",
    "status": "active"
  }
}
```

首次推荐可能返回 `profile_bootstrap_rules` 和
`model_features_preparing`；worker 完成缓存发布后应切换为
`scoring_engine=coder_gated`。准备中、模型不可用和认证失败必须保持可区分，
不能静默回退到规则排序。

## 6. 停止和回滚

```powershell
docker compose -f compose.gated.yaml down
```

回滚时应保留数据库、模型 bundle、已发布 cache generation 和请求目录，
回滚代码版本后重新启动，不要在运行中替换权重或复用不同 backbone 的 cache。

## 7. 当前边界

- 本编排验证的是 SQLite 运行时。
- PostgreSQL 迁移 CI 不等于 PostgreSQL 运行时存储适配。
- 当前 worker 只处理最新 request；缓存 TTL 和模型版本变化会触发重新准备。
- Docker 容器内部署不能代替 GitHub OAuth、HTTPS、反向代理、进程托管和备份演练。
