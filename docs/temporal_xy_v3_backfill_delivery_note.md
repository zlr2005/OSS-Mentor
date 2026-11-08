# v3 backfill 内容包与文件校验说明

适用于 GitHub 交付提交 `eb35864e` 的 `datasets/temporal_xy_2024_2026_v3`。公开文件 `file_contributions.jsonl` 与内部合并包的同名文件具有相同的 LF 字节。GitHub API 返回的 blob ID 是 `7f5d7e15dbf2baf49788f9050ce03bc8119e9800`。

| 校验对象 | SHA-256 |
|---|---|
| GitHub blob 与内部合并包的 LF 字节 | `4cf910842aaf2ab1c9c63911434bea76a433b80cc3efe309b27b7ad49b060946` |
| 同一文件经 Windows CRLF 转换后的工作区字节 | `2487963aa4e7f8fbc0d49a5da182f4b95ad6c3d9506b346d01341f913973b638` |

因此 `source_manifest.json` 中的 `4cf910…` 仍适用。对 Git blob 校验可以绕过工作区换行转换。在已取得交付提交的仓库根目录运行：

```powershell
python -c "import hashlib,subprocess; b=subprocess.check_output(['git','show','eb35864e:datasets/temporal_xy_2024_2026_v3/file_contributions.jsonl']); print(hashlib.sha256(b).hexdigest())"
```

也可将工作区 JSONL 的 CRLF 逐字节规范化为 LF 后计算 SHA-256。以上只适用于这份 JSONL 元数据。代码文件、证据文件和压缩包必须按原字节校验。

## B 的内部共享包

交付文件是 `temporal_xy_v3_backfill_for_B.zip` 和同名 `.zip.sha256` 校验文件。包内保留原 backfill 相对路径，包含 v3 X 所需代码及对应的 commit、blob 和 listing 证据。`manifest.json` 是完整来源清单的原字节副本，供比对 `source_manifest_sha256.backfill`。`v3_subset_manifest.json` 列出实际随包交付的文件与哈希。解包后按子集清单逐项核验，完整来源清单中其他文件无需为本次画像生成下载。

1. 用 `Get-FileHash -Algorithm SHA256 temporal_xy_v3_backfill_for_B.zip` 校验压缩包，与 `.zip.sha256` 比较。
2. 解包到单独的 backfill 目录。`code/`、`api_cache/` 和两份清单处于该目录根部。把 B 读取器的 `backfill` 来源根指向这里，`ab` 仍指向原 A/B v0.2 包。
3. 用 `Get-FileHash -Algorithm SHA256 manifest.json` 核对来源 manifest，与公开 `source_manifest.json` 中的 `source_manifest_sha256.backfill` 比较。按 `v3_subset_manifest.json` 的 `files_sha256` 检查包内文件，再按 X 三元组执行身份和时间校验。
4. 对每个 `sources` 条目按其 `source` 解析根目录。每条 X 的代码字节按 `content_sha256` 验证，核对覆盖后生成 `research_frozen` 画像。这个子集用于 B 的 X 重放。C 的历史 Issue 正文、候选对照及其关闭证据仍从原合并包和来源目录交接。
