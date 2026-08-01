# 项目维护规范

本项目将生命周期、状态、决策、探索和交接管理全部保留在仓库内。SQLite 提供查询投影，`events.jsonl` 是可审阅、可重建的追加式事件源；不依赖全局 Codex Hook、全局 Git Hook 或插件。

## 一、项目边界

### 目录职责

| 路径 | 职责 |
|:---|:---|
| `.codex/project-maintenance-workflow.json` | 工作流启用标记与策略 |
| `project_hooks/` | SQLite 数据层、生命周期、分支门禁、检查和安全自动提交 |
| `.githooks/pre-commit` | 随仓库克隆的提交门禁入口 |
| `maintenance/events.jsonl` | Git 跟踪的追加式动态事件源 |
| `resources/source/` | 外部原始资料与来源证据 |
| `resources/data/` | 原始、过程和处理后数据 |
| `resources/theory/` | 理论框架、假设、定义和推导 |
| `resources/analysis/` | 分析代码、Notebook、实验与过程记录 |
| `resources/outputs/` | 图表、模型和其他可交付成果 |
| `resources/others/` | 暂时无法可靠分类的资料 |
| `resources/reports/` | 面向外部受众的项目总结与报告 |

### 项目约定

- 原始资料不覆盖，过程与成果分开，同一文件只保留一个权威位置。
- 通用文件名优先使用 `YYYYMMDD_topic_v01.ext`，目录规模增大后再增加子层级。
- 动态数据由 `.project_hooks/maintenance.sqlite3` 管理，以 `maintenance/events.jsonl` 作为可重建事件源。
- 事件日志只追加，不手工改写既有行；SQLite 不纳入 Git，也不是唯一备份。
- `main` 只保存稳定、可复用、已验证内容；不确定改动使用独立探索分支。
- Hook 只管理本地状态和分支，不自动 push、创建 PR 或合并。
- Dashboard 对业务数据完全只读，不直接写数据库、修改日志、执行 Git 或启动 Codex；仅在用户明确选择保存位置后写出脱敏诊断 ZIP。资料目录、索引与类型一致性由命令和 `check` 强制维护。

### 日常入口

所有命令使用仓库根目录的 `.\project-hooks.exe`，不依赖全局命令或 Python 环境。

```powershell
.\project-hooks.exe
.\project-hooks.exe context --format markdown
.\project-hooks.exe start --help
.\project-hooks.exe end --help
.\project-hooks.exe dashboard
```

无参数入口打开 Dashboard；`context` 保留完整的目标、判决、断点和交接信息。普通 `--help` 只列日常入口，`--help-all` 列出高级命令。空文件夹首次双击自动初始化；新 clone 或 worktree 将 EXE放到根目录后运行 `.\project-hooks.exe install`，为当前仓库设置 `core.hooksPath=.githooks`。

### 软件升级

工作流软件只使用项目根目录的 Windows EXE。在干净且与
`origin/main` 同步的 `main` 分支运行：

```powershell
.\project-hooks.exe update
```

命令从 GitHub Release 获取并校验新版 EXE，在 `.project_hooks/runtime/` 保存项目内版本化运行时，迁移配置、保留事件日志既有内容、重建
SQLite 并验证完整性。升级器不自动提交或推送。用户修改过的规范文件不会被覆盖，新版
候选写入 `.project_hooks/update-conflicts/<version>/`；科研资料、项目根 README 和
`maintenance/events.jsonl` 永不被模板替换。

### 高级命令

| 分类 | 命令 |
|:---|:---|
| 安装与检查 | `install`、`check`、`status`、`branch-status` |
| 项目与阶段 | `project show/update`、`stage list/show/start/update` |
| 状态与记录 | `state`、`history`、`decisions`、`decision`、`explorations`、`catalog` |
| 探索流程 | `attempt`、`exploration`、`prepare-pr`、`archive-attempt` |
| 数据维护 | `db status`、`db verify`、`db rebuild`、`db migrate` |
| 故障反馈 | `diagnostics status`、`diagnostics export` |

使用 `.\project-hooks.exe <命令> --help` 查看具体参数。`pre-commit` 是仓库 Hook 的内部入口，不属于用户命令。

## 二、任务生命周期

每次任务固定按以下顺序执行：

1. 运行 `.\project-hooks.exe context --format markdown`，再读取本规范。
2. 首次写入前运行 `.\project-hooks.exe start <task_id> ...`。
3. 使用 `state update` 更新当前状态；路线改变时使用 `decision add`；探索任务使用 `attempt update` 记录假设、证据和结论。
4. 运行 `.\project-hooks.exe end <task_id> ...`，由 Hook 写入单一完成事件；也可直接在 `end` 中提供状态、判决、断点和下一步等参数，一步完成最终状态更新与任务结束。

项目说明和长期大目标只通过 `project update` 记录；全项目同一时间最多一个 active 大阶段，
通过 `stage start/update` 创建和推进。探索分支自动关联创建时的当前阶段，使用
`attempt update --current-step ... --progress ... --next-step ...` 记录执行位置。

稳定维护使用 `--track stable`。探索使用 `--track research|experiment|sandbox --topic <slug>`，由命令保存基线并安全创建或复用分支。

只有标记为 `large` 且使用 `--git-commit auto|always` 的任务才尝试自动提交。自动提交只包含任务启动后实际变化且不与启动前脏文件重叠的路径，保留既有暂存区，且不执行 push。`auto` 遇到归属冲突时跳过并报告，`always` 遇到冲突时阻止结束。

任务启动分支会写入活动状态；`status`、`end` 和 pre-commit 均拒绝任务中途切换分支。禁止绕过 `end`、删除活动状态、直接编辑 SQLite 或手工修改既有事件。

用户明确要求提交或推送已完成改动时，如果当前位于 `main`、没有活动任务、不再编辑文件、改动全部属于已结束且验证通过的任务，并且项目检查与数据库校验通过，可直接执行 Git 检查、暂存、提交和推送，无需创建 `publish_*` 任务。修复检查、修改内容、PR、合并和探索不属于纯 Git 发布，仍须使用正常生命周期。发布后不追加确认事件，状态由 Git 投影实时判断。

### 崩溃恢复与明确放弃

活动任务异常中断后运行 `task recover`，由 sidecar、事件日志和 SQLite 投影恢复现场。确认不再继续时运行
`task abandon --reason <原因>`；它保留全部科研文件、暂存区和分支。只有完成事件已经写入且自动提交持续失败时，才可运行
`task recover --skip-auto-commit --reason <原因>`。任务超过 24 小时或 7 天只产生提醒，不会自动结束。

## 三、分支与探索

`main` 只接收规范整理、Hook 维护、已确认小修和验证后的成果。新理论、算法、实验及结果不确定的改动不得直接在 `main` 尝试。

| 分支格式 | 用途 |
|:---|:---|
| `research/<topic>` | 理论、模型和知识路线探索 |
| `experiment/<topic>` | 可验证的实验、数据或算法尝试 |
| `sandbox/<topic>` | 方向尚未稳定的短期试验 |
| `archive/<type>/<topic>` | 失败、暂停或不可判决尝试的长期保留 |

`topic` 只使用小写字母、数字和连字符。一个探索分支对应一条尝试记录；同一分支上的后续任务复用该记录。

探索结束必须显式选择：

- `active`：仍需继续。
- `validated`：验收成立，证据与结论完整，可准备 PR。
- `negative`：结果否定原假设。
- `inconclusive`：证据不足，暂不可判决。
- `paused`：主动暂停并保留现场。

只有 `validated` 可运行 `prepare-pr`。成功成果基于最新 `main` 通过 Squash PR 合并，必须等待用户明确确认；冲突只在探索分支解决。`negative`、`inconclusive` 和 `paused` 使用 `archive-attempt` 归档，之后在 `main` 通过 `exploration import` 导入结论和证据，不合并探索内容。

`prepare-pr` 和 `archive-attempt` 只检查或修改本地分支。所有 push、PR、远程分支删除和合并均由外部流程显式执行。

## 四、记录与只读 Dashboard

`events.jsonl` 保存项目资料、阶段、任务、状态、决策、探索和科研资料索引的不可抹除事件。任务完成事件同时提供交接投影，旧版独立交接事件继续兼容并按任务去重。SQLite 保存事件投影、查询索引、活动任务和临时文件基线，可随时从事件日志重建。Schema v3 继续接受历史 v1/v2 事件；旧项目不会从自由文本推断项目资料或阶段，未初始化时明确显示“未设置”。

### 科研资料索引

科研资料索引分为 `literature`、`data`、`theory`、`simulation`、`output`、`other` 和 `report` 七类。实体文件保留在项目目录，数据库只记录项目相对路径、摘要、状态、标签、来源、扩展信息和类型化关系。所有写命令要求已有活动任务：

```powershell
.\project-hooks.exe catalog scan --dry-run
.\project-hooks.exe catalog scan
.\project-hooks.exe catalog ingest <文件> --kind literature
.\project-hooks.exe catalog add --kind theory --title "理论名称" --summary "简短说明"
.\project-hooks.exe catalog bulk-update --tag to-read --add-tag reviewed
.\project-hooks.exe catalog link <source-id> supports <target-id>
.\project-hooks.exe catalog list --kind literature
.\project-hooks.exe catalog context --tag core --format markdown
.\project-hooks.exe catalog migrate-layout --dry-run
```

默认扫描将 `resources/source/`、`data/`、`theory/`、`analysis/`、`outputs/`、`others/`、`reports/` 依次映射到七类资料。路径和资料类型必须匹配；扫描不会删除记录，文件消失时只标记为 `missing`。`check` 会拒绝缺失目录、错位条目和标准目录中的未索引文件。条目使用 `archive` 和 `restore` 软归档，关系使用 `link` 和 `unlink` 维护。

`catalog ingest` 对标准目录内文件自动推断类型；项目外文件需要 `--kind`，并复制到对应标准目录。没有活动任务时命令自动包装一个小型生命周期，已有活动任务时只登记资料。旧项目使用 `catalog migrate-layout --dry-run` 预览根目录五类资料的迁移；实际迁移只允许在 main 的 stable 活动任务中执行，拒绝覆盖并保留资料 ID 与关系。`catalog bulk-update` 可按 ID、类型、状态、标签、关键词或关系筛选后统一补充标签、摘要和来源；批量更新全部资料必须显式使用 `--all`。

`context` 和 Dashboard 分开显示两个断点：持久化的“工作断点”说明最后完成到哪里；只读“真实断点”实时比较本地 HEAD 与本地 `origin/main` 跟踪引用，显示同步、领先、落后、分叉或不可用。该检查不执行 `fetch` 或其他网络操作。

任务的发布状态不单独写事件，而是根据任务关联提交与 `origin/main` 的祖先关系实时推导为已发布、部分发布、待发布、仅记录或未知。Git 同步且当前任务已发布后，概览会隐藏已完成的提交、发布、推送和远端核对步骤，并只读派生“已完成并发布”状态；普通后续事项继续保留，不修改原始事件。发布后不再创建“记录已发布”专用任务。

双击 `.\project-hooks.exe` 打开 Tkinter Dashboard，包含：

- 七个日常分页：概览、搜索、资料、工作台、任务、阶段和记录。
- 顶部显示当前 EXE 与项目版本；“检查更新”在后台只读查询最新正式版本，不应用更新。
- “导出诊断包”仅在用户选择保存位置后写出脱敏 ZIP；“报告 Bug”打开预填 GitHub Issue，不自动上传任何数据。
- 概览按项目资料、当前大阶段、当前执行、探索概览和资料概览分区显示，区块之间使用横线分隔并可整体复制。
- 阶段页显示当前阶段、当前探索和阶段历史；探索表直接显示所属分支、当前步骤、下一步、状态和更新时间。
- 跨科研资料、任务、决策和探索的搜索，以及最近任务和失败探索快捷筛选；搜索结果使用左侧列表、右侧完整详情的分栏布局。
- 资料页顶部显示七个资源目录的实际/已索引文件数和健康状态，可直接打开目录；下方按类型、状态和文本筛选，显示精简详情、关系和缺失警告，并可打开文件所在位置或复制 AI 上下文。
- 概览显示七类资料数量、缺失与归档数量和最近新增资料。
- 工作台共列出十五项提示词：八项科研与沟通提示词包含“对外项目总结”，七项软件提示词按软件升级、版本发布和软件诊断分类。对外报告提示词写入前必须确认受众、周期、语言、语气和保密边界，产物保存在 `resources/reports/` 并登记为 `report`，不自动提交、推送或发布。
- 任务页默认只列出时间、任务、结果和状态；历史 `publish_*` 与 `record_*_publication_*` 辅助任务不进入日常列表，原始记录仍可在搜索和高级查看中审计。
- 记录页使用左侧列表、右侧完整详情的分栏布局，统一查看和筛选决策与探索；原始事件、Schema、日志哈希和刷新诊断集中在独立的只读“高级查看”窗口。
- 任务、记录与原始事件之间支持双向定位。
- 阶段历史、探索分支进度、排序、复制、手动刷新和自动刷新。

Dashboard 只读扫描本地探索分支的事件日志以汇总进度，不绘制提交图，也不访问 GitHub。`--refresh-seconds <N>` 设置刷新间隔，默认 3 秒，`0` 表示关闭；`--branch <name>` 查看指定分支流。

图形环境不可用时，继续使用 `context`、`history`、`decisions`、`explorations`、`catalog list` 和 `db status`。
