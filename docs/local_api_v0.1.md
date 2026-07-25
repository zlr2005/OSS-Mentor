# 本地推荐 API v0.1

## 1. 目标

该 API 将 SQLite 候选池、匿名画像和个性化匹配封装为前端可调用的只读 HTTP
接口。实现只使用 Python 标准库，默认监听 `127.0.0.1:8765`。

## 2. 接口

| 方法与路径 | 用途 |
|---|---|
| `GET /health` | 返回 API 版本和数据库是否就绪 |
| `GET /api/v1/profiles` | 返回前端可展示的画像摘要，不返回详细技能证据 |
| `GET /api/v1/recommendations?profile_key=...&limit=10` | 返回匹配任务、分数、技能缺口和推荐理由 |

OpenAPI 契约见 `docs/openapi_v0.1.yaml`。

## 3. 安全边界

- 只支持 `GET`，写入画像和同步数据仍通过本地 CLI 完成；
- 默认只允许环回地址，绑定其他地址必须显式传入 `--allow-remote`；
- CORS 默认关闭，只有传入 `--cors-origin` 才返回允许来源头；
- 响应不包含 Issue 正文、用户详细技能证据、数据库绝对路径或 API token；
- 返回 `Cache-Control: no-store` 和 `X-Content-Type-Options: nosniff`；
- 参数错误使用固定错误码，不返回内部异常或 SQL 信息。

该接口尚无身份认证，因此不应直接暴露到公网。未来需要远程部署时，应在反向代理
或应用层增加认证、TLS、请求限速和审计。

## 4. 启动

```powershell
$env:PYTHONPATH = "src"
python -m oss_mentor serve-api
```

指定端口：

```powershell
python -m oss_mentor serve-api --port 9000
```

同源前端通常不需要 CORS。若本地前端运行在另一个端口，可明确允许单个来源：

```powershell
python -m oss_mentor serve-api `
  --cors-origin http://127.0.0.1:5173
```

## 5. 实际验证

已短暂启动本地服务器并请求三个接口：

- 健康检查返回 `200`；
- 画像接口返回两个匿名演示画像；
- 新手推荐接口返回 `matplotlib/matplotlib#31936`，匹配分 `82.34`，并包含
  Python、macOS、测试、bug_fix 和 build_tooling 的逐项技能差距。

验证后服务器进程已关闭。
