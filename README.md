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
.\project-hooks.exe workbench external list
.\project-hooks.exe dashboard
```

双击无参数启动 Dashboard；在 PowerShell 中带参数运行时执行完整 CLI。`--help` 显示日常入口，`--help-all` 显示全部命令。

Dashboard 顶部近实时显示当前工作周期、文件变化、进度、写锁和阻塞原因，并独立显示工作区、提交、推送和正式发布状态；推送到 `origin/main` 不会被误称为 GitHub Release。“工作流”页通过任务、动作、阶段、探索和决策五个折叠类别统一展示完整流程。动作继续区分可执行、等待 AI 文本、执行中和暂不可用，并显示原因与安全下一步。它不提供业务写按钮，CLI 保留为 AI 自动化与故障救援入口。
用户只需在 AI 对话中用自然语言描述任务；项目内置规则引导 AI 自动调用结构化生命周期。放弃、迁移、数据库重建和软件更新仍需在对话中明确确认。
“检查更新”只在后台查询最新正式版本。遇到错误时，程序会在 `.project_hooks/diagnostics/` 保存轮转的脱敏本地记录；“诊断”页按故障指纹合并问题，显示是否进入过诊断包，并集中提供导出、报告 Bug 和确认解决入口，程序不会自动上传数据。成功升级后只自动清理旧版本的输入校验和普通冲突，内部异常与数据完整性问题继续保留待复查。
概览使用分割线显示项目资料、当前总体状态、判决、断点、阻塞、下一步、代码交付、最近完成和资料摘要；完整任务、阶段、探索及动作详情仍统一由“工作流”页展示。
“资料”页使用与工作流一致的两栏折叠树：先显示七个标准资源目录的文件/索引数量，展开目录后才加载资料索引，右侧显示只读详情。“工作台”分为内置提示词和外置工具两个折叠区；外置工具只是项目提醒，不会被自动检测、启动或联网验证，由 AI 通过 `workbench external` 结构化登记。
全局搜索框位于“搜索”页，不占用所有页面的顶部空间；“解释”页用中英文说明工作流、任务、动作、阶段、探索、决策、资料和 Git/Release 状态的含义及安全下一步。

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
