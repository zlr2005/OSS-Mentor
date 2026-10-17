# 类 CODER 推荐算法：阶段 0 工程与数据安全说明

本文仅描述阶段 0 的工程基础；不包含 BSON 解析、数据标准化、Baseline、模型或训练实现。

## 隔离范围

- 算法项目位于 `CODER/`，包名为 `coder_issue`，不修改现有 `oss_mentor` 公共 API。
- Python 要求为 3.11 或更新版本；阶段 0 运行时只依赖标准库。
- 测试依赖固定为 `pytest==8.4.2`，通过 `CODER/pyproject.toml` 的 `test` extra 声明。
- 原始 `*.bson`、`*.bson.gz`、MongoDB dump、模型权重和训练产物由根 `.gitignore` 保护。

## 环境创建

在仓库根目录执行：

```powershell
python -m venv CODER/.venv
$env:PYTHONPATH = "CODER/src"
CODER/.venv/Scripts/python -c "import coder_issue"
```

阶段 0 的运行时代码只使用标准库，因此无需联网安装包。若需要 editable
安装和 pytest，可在允许安装开发工具依赖时执行：

```powershell
CODER/.venv/Scripts/python -m pip install "setuptools>=68"
CODER/.venv/Scripts/python -m pip install -e "CODER[test]"
```

某些 Python 3.13 发行版创建的 venv 不再预装 `setuptools`；此时第一条安装
命令是必要的构建工具引导步骤。未获准联网时继续使用上面的 `PYTHONPATH`
方式即可，不影响阶段 0 功能。

阶段 0 不需要也不会安装 PyTorch、CUDA 或其他大型机器学习框架。

## 配置、版本与产物约定

统一入口为 `CODER/configs/base.toml`。路径相对仓库根目录解析，配置包含：

- `dataset_version`：原始数据逻辑版本；
- `seed`：所有工作流的统一随机种子；
- `artifact_root`：固定为 `data/midterm`；
- `normalized`、`embeddings`、`graphs`、`models`、`runs`：各阶段产物目录；
- Manifest 文件列表、输出位置与 SHA-256 分块大小；
- 日志等级和 JSON Lines 格式。

运行标识格式为：

```text
<UTC时间>-<Git短提交或unknown[-dirty]>-<配置名>
```

每个后续正式运行应在自己的 `data/midterm/runs/<run_id>/` 中保存配置快照、运行元数据、指标和错误记录。产物不进入 Git；原始数据只读，派生结果不得写回 `CODER/*.bson[.gz]`。

`set_global_seed()` 当前固定 Python 标准库随机源并为子进程设置 `PYTHONHASHSEED`。加入 NumPy/PyTorch 的阶段必须在依赖锁定后显式补充各自种子和确定性选项。

## 数据 Manifest

安装本地包后，在仓库根目录执行：

```powershell
CODER/.venv/Scripts/python CODER/scripts/generate_manifest.py
```

也可执行已安装入口 `coder-data-manifest`。工具逐块读取文件（默认 8 MiB），记录相对路径、文件名、字节数和 SHA-256，不解析 BSON，也不会把文件整体载入内存。只有全部文件检查成功后才原子替换 `CODER/data_manifest.json`。

Manifest 证明字节级输入版本，但不证明 BSON 语义质量；记录数、关联率、时间合法性等属于阶段 1。

## 验证

无需第三方依赖的最小验证：

```powershell
$env:PYTHONPATH = "CODER/src"
python -m unittest discover -s CODER/tests -v
python -c "import coder_issue"
git check-ignore -v CODER/dataset.bson CODER/resolved_issue.bson
```

若已安装 test extra，也可运行：

```powershell
CODER/.venv/Scripts/python -m pytest CODER/tests
```

## 安全边界与已知限制

- Manifest 会顺序读取约 7.7 GB 原始数据，耗时取决于磁盘吞吐，但内存占用受分块大小约束。
- 不连接生产数据库、不下载数据、不访问网络、不训练模型。
- `data.zip` 的内容与归属尚未确认，本阶段不修改、不删除，也不自动加入忽略规则。
- 原始 BSON 当前不在 Git 索引中；如以后发现已被跟踪，应先报告并经确认后再修改索引。
