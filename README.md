# Workflow Monitor

> 双击或无参数运行 `workflow-monitor.exe` 会直接打开本地 WebView2 工作台。界面不开放网络端口、不使用 CDN、不上传项目数据，业务数据保持只读。

面向 Windows 科研 Git 项目的单文件工作流工具，提供任务生命周期、探索分支、决策、交接、资料索引和只读 Dashboard。

## 普通用户下载和使用

使用前只需安装 [Git for Windows](https://git-scm.com/download/win)，不需要安装 Python、pip 或 pipx。

1. 打开项目的 [GitHub Releases](https://github.com/DawnDust/project-maintenance-template/releases/latest)。
2. 在最新版本的 **Assets** 中点击 `workflow-monitor.exe`。
3. 新建一个空文件夹，把下载的 `workflow-monitor.exe` 放进去。
4. 双击 `workflow-monitor.exe`。

首次双击会自动创建 `main` Git 仓库、初始化工作流并打开 Web 工作台；以后继续双击同一个 EXE 即可。EXE 必须保持文件名 `workflow-monitor.exe`，并保留在项目根目录。
科研阶段按阶段序号从新到旧展示目标、完成工作、关联探索和登记资料；当前阶段首次进入时自动展开，历史阶段默认折叠，后台刷新保留用户手动展开状态。资料优先按关联任务时间归属阶段并以登记时间回退，探索优先使用显式阶段并以创建时间回退，无法归属的记录单独保留。搜索页可按任务、探索、决策和资料快速筛选。资料页作为七类标准目录的导航与定位中心，只浏览选中目录，文字检索统一进入搜索页。

当前 EXE 未配置商业代码签名；Windows SmartScreen 首次提示时，需要确认文件来自本项目的正式 GitHub Release。

## 项目内命令

所有命令均通过项目根目录的 EXE执行：

```powershell
.\workflow-monitor.exe context --format markdown
.\workflow-monitor.exe project show
.\workflow-monitor.exe stage list
.\workflow-monitor.exe start --help
.\workflow-monitor.exe end --help
.\workflow-monitor.exe check
.\workflow-monitor.exe diagnostics status
.\workflow-monitor.exe diagnostics export
.\workflow-monitor.exe workbench external list
.\workflow-monitor.exe workbench item list
.\workflow-monitor.exe workbench package inspect <包文件>
```

双击无参数启动 Dashboard；在 PowerShell 中带参数运行时执行完整 CLI。`--help` 显示日常入口，`--help-all` 显示全部命令。

Dashboard 顶部近实时显示当前工作周期、文件变化、进度、写锁和阻塞原因，工作周期只保留工作区、提交和推送状态。左上角明确显示实际分支及稳定维护或探索类型。软件构建与发布信息收纳到“高级查看”：EXE 构建显示与当前软件源码仓库是否一致，最近发布显示正式 Release 的版本和时间，发布后软件修改只统计源码、测试、构建脚本与维护文档，不把科研 `resources/` 内容算成软件未发布。“工作流”页通过任务、动作、阶段、探索和决策五个折叠类别统一展示完整流程。动作继续区分可执行、等待 AI 文本、执行中和暂不可用，并显示原因与安全下一步。它不提供业务写按钮，CLI 保留为 AI 自动化与故障救援入口。
用户只需在 AI 对话中用自然语言描述任务；项目内置规则引导 AI 自动调用结构化生命周期。放弃、迁移、数据库重建和软件更新仍需在对话中明确确认。
用户打开“高级查看”后，Dashboard 才只读核对一次最近正式 Release；私有仓库可使用已登录的 `gh` 会话回退查询，程序不保存凭据。本地构建一致性与发布后软件修改会随工作区状态变化重新计算，不重复联网。“检查更新”按钮另行查询最新正式版本。只有内部异常、数据完整性或外部依赖故障才会在 `.project_hooks/diagnostics/` 保存轮转的脱敏本地记录；参数错误、工作流前置条件和普通冲突只显示提示，不作为 Bug 诊断持久化。“诊断”页按故障指纹合并问题，显示是否进入过诊断包，并集中提供导出、报告 Bug 和确认解决入口，程序不会自动上传数据。
概览使用分割线显示项目资料、当前阶段、当前总体状态、判决、断点、阻塞、下一步、代码交付和资料摘要；完整任务、阶段、探索及动作详情仍统一由“工作流”页展示。

大型模拟程序可以将 `resources/analysis/` 下的整个目录登记为一个模拟资料包，例如 `catalog add --kind simulation --title "模拟名称" --path resources/analysis/my-simulation --entrypoint main.py`。资料包在工作台和阶段统计中只算一项，内部文件通过文件数、总大小和整体摘要校验，不需要逐个登记。
“资料”页使用与工作流一致的两栏折叠树：先显示七个标准资源目录的文件/索引数量，展开目录后才加载资料索引，右侧显示只读详情。“工作台”不提供内置科研内容，使用互斥主类型和可多选科研用途索引用户创建或外部导入的科研辅助条目；条目不会自动执行、安装、联网验证或进入日常 context，用户明确选择后才复制给 Codex。
全局搜索框位于“搜索”页，不占用所有页面的顶部空间；“解释”页用中英文说明工作流、任务、动作、阶段、探索、决策、资料和 Git/Release 状态的含义及安全下一步。

## 科研工作台索引

工作台不内置提示词、科研流程、验证方法或理论内容。每个条目只有一个主类型：`tool`、`instruction`、`method`、`workflow`、`checklist`、`reference` 或 `template`；科研用途通过可重复的 `--purpose` 登记，学科与主题继续使用自由标签。跨项目常用理论使用 `reference` 和 `theory` 标签，当前项目理论仍放在 `resources/theory/`。Dashboard 只显示分类、概览、状态和来源；只有明确勾选后才复制对应全文给 Codex。

```powershell
.\workflow-monitor.exe workbench item add limit-check --kind checklist --purpose validation --title "极限检查" --summary "检查已知极限" --path workbench/local/limit-check.md
.\workflow-monitor.exe workbench item list
.\workflow-monitor.exe workbench package inspect .\physics-methods.workbench.zip
.\workflow-monitor.exe workbench package import .\physics-methods.workbench.zip
.\workflow-monitor.exe workbench package export physics-methods --name "物理方法" --version 1.0.0 --author "作者" --item limit-check
```

导入包只允许 `manifest.json` 与 Markdown，展开到 `workbench/imported/<包>/<版本>/`；导出包保存到被 Git 忽略的 `workbench/exports/`。工作台不会执行包内流程、检测或启动外置软件、安装 Skill、联网更新或把内容自动加入日常 `context`。当前研究实际采用的软件和方法仍通过探索证据、决策和项目资料记录。

## 崩溃恢复与遗忘任务

活动任务现场同时保存在原子写入的 `.project_hooks/active-task.json` 和 SQLite 投影中。命令异常退出后运行：

```powershell
.\workflow-monitor.exe task recover
.\workflow-monitor.exe task abandon --reason "不再继续的原因"
.\workflow-monitor.exe task recover --skip-auto-commit --reason "自动提交持续失败的原因"
```

`abandon` 只追加可审计记录并解除活动状态，不删除、不重置科研文件、暂存区或探索分支。Dashboard 在任务超过 24 小时后提醒，超过 7 天后加强提醒，但永不自动结束任务。

## 更新

在项目干净、没有活动任务且 `main` 与已知 `origin/main` 同步时运行：

```powershell
.\workflow-monitor.exe update --check
.\workflow-monitor.exe update
```

EXE从最新 GitHub Release 下载并校验新版程序，把版本化运行时保存在当前项目的 `.project_hooks/runtime/`，迁移项目模板并重建 SQLite。更新不会自动提交、推送或合并，也不会覆盖 `maintenance/events.jsonl`、项目根 README 或科研资料。

## 项目数据

- `maintenance/events.jsonl` 是 Git 跟踪的追加式永久事件源。
- `.project_hooks/maintenance.sqlite3` 是可重建的本地查询投影。
- 项目说明、大目标、阶段和探索进度均通过受约束 CLI 写入事件源，不直接编辑 SQLite 或概览文本。
- 初始化时固定创建 `resources/source/`、`data/`、`theory/`、`analysis/`、`outputs/`、`others/` 和 `reports/`；资料命令与 `check` 强制目录和索引一致。
- 旧项目先运行 `.\workflow-monitor.exe catalog migrate-layout --dry-run`，确认后再运行实际迁移；命令拒绝覆盖目标文件。
- `workflow-monitor.exe` 和 `.project_hooks/` 均被 Git 忽略。

完整生命周期和分支策略见 [maintenance/README.md](./maintenance/README.md)。
维护者进行功能开发或重构前，还应阅读 [maintenance/ARCHITECTURE.md](./maintenance/ARCHITECTURE.md)，遵守四层依赖方向和稳定入口约定。

## 发布维护

版本标签 `v*` 触发 Windows GitHub Actions，运行测试、构建单文件 EXE和 manifest，然后在 Release 中上传：

- `workflow-monitor.exe`：普通用户唯一需要手动下载的文件。
- `release-manifest.json`：由程序自动读取，用于版本发现和 SHA-256 校验，普通用户无需手动下载。

发布前应同步更新 `project_hooks.__version__` 与 `CHANGELOG.md`，然后提交、推送并创建相同版本的标签。

维护者日常写入后运行 `python scripts/run_tests.py fast`，任务结束前运行
`python scripts/run_tests.py full`，发布前运行 `python scripts/run_tests.py release`。
