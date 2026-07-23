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
| `source/` | 外部原始资料与来源证据，首次使用时创建 |
| `data/` | 原始、过程和处理后数据，首次使用时创建 |
| `theory/` | 理论框架、假设、定义和推导，首次使用时创建 |
| `analysis/` | 分析代码、Notebook、实验与过程记录，首次使用时创建 |
| `outputs/` | 报告、图表、模型和可交付成果，首次使用时创建 |

### 项目约定

- 原始资料不覆盖，过程与成果分开，同一文件只保留一个权威位置。
- 通用文件名优先使用 `YYYYMMDD_topic_v01.ext`，目录规模增大后再增加子层级。
- 动态数据由 `.project_hooks/maintenance.sqlite3` 管理，以 `maintenance/events.jsonl` 作为可重建事件源。
- 事件日志只追加，不手工改写既有行；SQLite 不纳入 Git，也不是唯一备份。
- `main` 只保存稳定、可复用、已验证内容；不确定改动使用独立探索分支。
- Hook 只管理本地状态和分支，不自动 push、创建 PR 或合并。
- Dashboard 只读，不写数据库、不修改日志，也不执行 Git 或网络操作。

### 日常入口

```powershell
python -m project_hooks
python -m project_hooks context --format markdown
python -m project_hooks start --help
python -m project_hooks end --help
python -m project_hooks dashboard
```

无参数入口显示行动优先概览；`context` 保留完整的目标、判决、断点和交接信息，供生命周期与自动化读取。普通 `--help` 只列日常入口，`--help-all` 列出高级命令。新 clone 或 worktree 必须先运行一次 `install`，仅为当前仓库设置 `core.hooksPath=.githooks`。

### 高级命令

| 分类 | 命令 |
|:---|:---|
| 安装与检查 | `install`、`check`、`status`、`branch-status` |
| 状态与记录 | `state`、`history`、`decisions`、`decision`、`explorations` |
| 探索流程 | `attempt`、`exploration`、`prepare-pr`、`archive-attempt` |
| 数据维护 | `db status`、`db verify`、`db rebuild`、`db migrate` |

使用 `python -m project_hooks <命令> --help` 查看具体参数。`pre-commit` 是仓库 Hook 的内部入口，不属于用户命令。

## 二、任务生命周期

每次任务固定按以下顺序执行：

1. 运行 `python -m project_hooks context --format markdown`，再读取本规范。
2. 首次写入前运行 `python -m project_hooks start <task_id> ...`。
3. 使用 `state update` 更新当前状态；路线改变时使用 `decision add`；探索任务使用 `attempt update` 记录假设、证据和结论。
4. 运行 `python -m project_hooks end <task_id> ...`，由 Hook 写入单一完成事件；也可直接在 `end` 中提供状态、判决、断点和下一步等参数，一步完成最终状态更新与任务结束。

稳定维护使用 `--track stable`。探索使用 `--track research|experiment|sandbox --topic <slug>`，由命令保存基线并安全创建或复用分支。

只有标记为 `large` 且使用 `--git-commit auto|always` 的任务才尝试自动提交。自动提交只包含任务启动后实际变化且不与启动前脏文件重叠的路径，保留既有暂存区，且不执行 push。`auto` 遇到归属冲突时跳过并报告，`always` 遇到冲突时阻止结束。

任务启动分支会写入活动状态；`status`、`end` 和 pre-commit 均拒绝任务中途切换分支。禁止绕过 `end`、删除活动状态、直接编辑 SQLite 或手工修改既有事件。

用户明确要求提交或推送已完成改动时，如果当前位于 `main`、没有活动任务、不再编辑文件、改动全部属于已结束且验证通过的任务，并且项目检查与数据库校验通过，可直接执行 Git 检查、暂存、提交和推送，无需创建 `publish_*` 任务。修复检查、修改内容、PR、合并和探索不属于纯 Git 发布，仍须使用正常生命周期。发布后不追加确认事件，状态由 Git 投影实时判断。

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

`events.jsonl` 保存任务、状态、决策和探索的不可抹除事件。任务完成事件同时提供交接投影，旧版独立交接事件继续兼容并按任务去重。SQLite 保存事件投影、查询索引、活动任务和临时文件基线，可随时从事件日志重建。

`context` 和 Dashboard 分开显示两个断点：持久化的“工作断点”说明最后完成到哪里；只读“真实断点”实时比较本地 HEAD 与本地 `origin/main` 跟踪引用，显示同步、领先、落后、分叉或不可用。该检查不执行 `fetch` 或其他网络操作。

任务的发布状态不单独写事件，而是根据任务关联提交与 `origin/main` 的祖先关系实时推导为已发布、部分发布、待发布、仅记录或未知。Git 同步后，概览会隐藏已完成的提交、推送和远端核对步骤；如果没有其他后续工作，则只读派生“已完成并发布”状态，不修改原始事件。发布后不再创建“记录已发布”专用任务。

运行 `python -m project_hooks dashboard` 打开 Tkinter 窗口，包含：

- 五个日常分页：概览、搜索、任务、时间线和记录。
- 概览按状态、活动任务、阻塞、下一步和 Git 同步顺序显示行动信息；当前目标和最近完成记录各压缩为一行，完整判决、断点和历史仍可从其他页面查看。
- 跨任务、决策、探索和提交的搜索，以及最近任务、失败探索和未合并分支快捷筛选。
- 任务页默认只列出时间、任务、结果和状态；历史 `publish_*` 与 `record_*_publication_*` 辅助任务折叠到主体任务详情，搜索和高级查看仍保留原始记录。
- 记录页统一查看和筛选决策与探索；原始事件、Schema、日志哈希和刷新诊断集中在独立的只读“高级查看”窗口。
- 任务、记录、提交与原始事件之间支持双向定位。
- 本地 Git DAG：实线表示父子关系，虚线表示事件关联；不访问 GitHub。
- 默认保留最近 50 次提交，旧提交折叠并可临时展开；搜索旧提交时自动展开。
- 分支筛选、本页高亮、排序、复制、手动刷新和自动刷新。

Git 时间线刷新失败时保留上一次成功图形，SQLite 分页继续刷新并显示警告。`--refresh-seconds <N>` 设置刷新间隔，默认 3 秒，`0` 表示关闭；`--branch <name>` 查看指定分支流。

图形环境不可用时，继续使用 `context`、`history`、`decisions`、`explorations` 和 `db status`。
