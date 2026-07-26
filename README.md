# Project Maintenance Workflow

面向科研 Git 仓库的任务生命周期、探索分支、决策、交接和资料索引工具。软件通过
`pipx` 全局安装；科研项目只保存配置、规范和追加式事件记录，不需要复制软件源码。

## 安装 v1.0.2

先安装 [pipx](https://pipx.pypa.io/)，然后从 GitHub Release 安装：

```powershell
pipx install https://github.com/DawnDust/project-maintenance-template/releases/download/v1.0.2/project_maintenance_workflow-1.0.2-py3-none-any.whl
```

在一个已经初始化为 Git 仓库的科研项目中运行：

```powershell
Set-Location <research-project>
project-hooks init .
project-hooks check
```

`init` 创建项目配置、受管规范、Git Hook 和空的永久事件源；不会创建或覆盖科研资料目录。

## 一键升级

在科研项目干净且与 `origin/main` 同步的 `main` 分支中运行：

```powershell
project-hooks update
```

该命令从最新稳定 GitHub Release 下载并校验核心包，迁移当前项目、重建 SQLite
查询投影并运行完整性检查。它不会自动提交、推送或合并。

常用升级查询：

```powershell
project-hooks update --check
project-hooks update --to 1.1.0
project-hooks version
```

用户修改过的受管规范不会被覆盖；新版候选保存在
`.project_hooks/update-conflicts/<version>/` 并在结果中报告。以下内容永不被升级器覆盖：

- `maintenance/events.jsonl`
- 科研项目根 `README.md`
- `source/`、`data/`、`theory/`、`analysis/` 和 `outputs/`

## 日常入口

| 命令 | 用途 |
|:---|:---|
| `project-hooks context --format markdown` | 读取完整动态上下文 |
| `project-hooks start ...` | 开始任务生命周期 |
| `project-hooks end ...` | 完成任务并收尾 |
| `project-hooks dashboard` | 打开只读管理窗口 |
| `project-hooks catalog list` | 查询科研资料索引 |

`python -m project_hooks` 入口继续兼容。使用 `project-hooks --help-all` 查看全部命令。

Dashboard 工作台提供十九项独立提示词：七项用于文献、研究设计、复盘和规划，十二项用于
安装工作流软件、初始化或接管科研项目、查看版本、检查或执行升级、项目健康诊断、创建
版本提交、发布 GitHub Release 和验证正式版本。发布提示词在缺少具体版本号或明确发布
确认时不会执行推送或标签操作。

科研资料保存在项目目录中，SQLite 只保存可重建的查询投影；永久事实以
`maintenance/events.jsonl` 为准。完整生命周期和分支策略见
[maintenance/README.md](./maintenance/README.md)。

## 发布

版本标签 `v*` 会触发 GitHub Actions，在 Python 3.11–3.13 和 Windows/Linux 上运行测试，
构建 wheel、版本化核心 ZIP 和 `release-manifest.json`，然后创建 GitHub Release。
版本号必须同时与 `pyproject.toml` 和 `project_hooks.__version__` 一致。
