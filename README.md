# Project Maintenance Workflow

面向 Windows 科研 Git 项目的单文件工作流工具，提供任务生命周期、探索分支、决策、交接、资料索引和只读 Dashboard。

## 普通用户下载和使用

使用前只需安装 [Git for Windows](https://git-scm.com/download/win)，不需要安装 Python、pip 或 pipx。

1. 打开项目的 [GitHub Releases](https://github.com/DawnDust/project-maintenance-template/releases/latest)。
2. 在最新版本的 **Assets** 中点击 `project-hooks.exe`。
3. 新建一个空文件夹，把下载的 `project-hooks.exe` 放进去。
4. 双击 `project-hooks.exe`。

首次双击会自动创建 `main` Git 仓库、初始化工作流并打开 Dashboard；以后继续双击同一个 EXE 即可。EXE 必须保持文件名 `project-hooks.exe`，并保留在项目根目录。

当前 EXE 未配置商业代码签名；Windows SmartScreen 首次提示时，需要确认文件来自本项目的正式 GitHub Release。

## 项目内命令

所有命令均通过项目根目录的 EXE执行：

```powershell
.\project-hooks.exe context --format markdown
.\project-hooks.exe project show
.\project-hooks.exe stage list
.\project-hooks.exe start --help
.\project-hooks.exe end --help
.\project-hooks.exe check
.\project-hooks.exe diagnostics status
.\project-hooks.exe diagnostics export
.\project-hooks.exe dashboard
```

双击无参数启动 Dashboard；在 PowerShell 中带参数运行时执行完整 CLI。`--help` 显示日常入口，`--help-all` 显示全部命令。

Dashboard 顶部直接显示当前 EXE 与项目版本；点击“检查更新”可在后台只读查询最新正式版本，不会修改项目或卡住界面。
遇到错误时，程序会在 `.project_hooks/diagnostics/` 保存轮转的脱敏本地记录。Dashboard 可在用户明确选择位置后导出诊断 ZIP，并打开预填的 GitHub Bug 报告；程序不会自动上传数据。
概览按项目资料、当前大阶段、当前执行、探索和资料分区显示；“阶段”页集中展示阶段历史与各探索分支当前步骤。
“资料”页显示七个标准资源目录的文件/索引数量并可直接打开目录；“工作台”的“对外项目总结”提示词将确认后的 Markdown 报告保存到 `resources/reports/` 并登记索引。

## 崩溃恢复与遗忘任务

活动任务现场同时保存在原子写入的 `.project_hooks/active-task.json` 和 SQLite 投影中。命令异常退出后运行：

```powershell
.\project-hooks.exe task recover
.\project-hooks.exe task abandon --reason "不再继续的原因"
.\project-hooks.exe task recover --skip-auto-commit --reason "自动提交持续失败的原因"
```

`abandon` 只追加可审计记录并解除活动状态，不删除、不重置科研文件、暂存区或探索分支。Dashboard 在任务超过 24 小时后提醒，超过 7 天后加强提醒，但永不自动结束任务。

## 更新

在项目干净、没有活动任务且 `main` 与已知 `origin/main` 同步时运行：

```powershell
.\project-hooks.exe update --check
.\project-hooks.exe update
```

EXE从最新 GitHub Release 下载并校验新版程序，把版本化运行时保存在当前项目的 `.project_hooks/runtime/`，迁移项目模板并重建 SQLite。更新不会自动提交、推送或合并，也不会覆盖 `maintenance/events.jsonl`、项目根 README 或科研资料。

## 项目数据

- `maintenance/events.jsonl` 是 Git 跟踪的追加式永久事件源。
- `.project_hooks/maintenance.sqlite3` 是可重建的本地查询投影。
- 项目说明、大目标、阶段和探索进度均通过受约束 CLI 写入事件源，不直接编辑 SQLite 或概览文本。
- 初始化时固定创建 `resources/source/`、`data/`、`theory/`、`analysis/`、`outputs/`、`others/` 和 `reports/`；资料命令与 `check` 强制目录和索引一致。
- 旧项目先运行 `.\project-hooks.exe catalog migrate-layout --dry-run`，确认后再运行实际迁移；命令拒绝覆盖目标文件。
- `project-hooks.exe` 和 `.project_hooks/` 均被 Git 忽略。

完整生命周期和分支策略见 [maintenance/README.md](./maintenance/README.md)。

## 发布维护

版本标签 `v*` 触发 Windows GitHub Actions，运行测试、构建单文件 EXE和 manifest，然后在 Release 中上传：

- `project-hooks.exe`：普通用户唯一需要手动下载的文件。
- `release-manifest.json`：由程序自动读取，用于版本发现和 SHA-256 校验，普通用户无需手动下载。

发布前应同步更新 `project_hooks.__version__` 与 `CHANGELOG.md`，然后提交、推送并创建相同版本的标签。

维护者日常写入后运行 `python scripts/run_tests.py fast`，任务结束前运行
`python scripts/run_tests.py full`，发布前运行 `python scripts/run_tests.py release`。
