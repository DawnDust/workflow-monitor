# 项目维护工作流规范

## 目的

本工作流使用 SQLite 维护项目状态、变更证据和跨任务交接，以单个追加式 JSONL 事件日志实现 Git 审阅、分支合并和数据库重建。

## 每次任务的固定顺序

1. 运行 `python -m project_hooks context --format markdown` 读取动态上下文，再依次读取 `project_context.md`、`branch_workflow.md` 和本规范。
2. 新克隆或新 worktree 先运行一次 `python -m project_hooks install`，仅为当前仓库设置 `core.hooksPath=.githooks`。
3. 在首次写入前运行 `python -m project_hooks start <task_id> ...`。稳定维护使用 `--track stable`；探索使用 `--track research|experiment|sandbox --topic <slug>`，由命令保存基线并安全创建或复用分支。
4. 使用 `state update` 更新当前状态；路线改变时使用 `decision add`；探索使用 `attempt update` 记录假设、证据和结论。
5. 运行 `python -m project_hooks end <task_id> ...`，由 Hook 自动写入任务档案与交接事件。探索任务额外声明 `--attempt-state active|validated|negative|inconclusive|paused`。

## Git 规则

- 只有显式标记为 `large` 且使用 `--git-commit auto` 或 `always` 的任务才尝试自动提交。
- 自动提交只包含任务启动后实际变化且不与启动前脏文件重叠的路径。
- 保留任务启动前的暂存区；不执行 `push`。
- `auto` 遇到归属冲突时跳过并报告；`always` 遇到冲突时阻止结束。
- `.githooks/pre-commit` 随仓库版本控制；`python -m project_hooks install` 只修改当前仓库的 `.git/config`，不创建或使用全局 Hook。
- 任务启动分支会写入活动状态；`status`、`end` 和 pre-commit 均拒绝任务中途切换分支。
- `prepare-pr` 和 `archive-attempt` 只检查或修改本地分支，不执行 push、创建 PR 或合并。
- `maintenance/events.jsonl` 只追加事件并使用 Git union 合并；SQLite 文件及 WAL 文件均留在被忽略的 `.project_hooks/`。

## 文档职责

- `project_context.md`、`branch_workflow.md` 和本规范保存低频变化的静态规则。
- `events.jsonl` 保存任务、状态、交接、决策和探索的不可抹除事件。
- SQLite 保存事件投影、查询索引、活动任务和临时文件基线，可随时从事件日志重建。

## 只读桌面窗口

- `python -m project_hooks dashboard` 打开 Tkinter 窗口，显示概览、Git 时间线、任务历史、决策、探索和原始事件。
- 时间线读取本地 Git DAG 和 `events.jsonl` 的提交历史：分支泳道显示全部可达提交，实线表示 Git 父子关系，虚线表示任务事件的跨分支关联；不会访问 GitHub 网络。
- 窗口支持分支筛选、关键词高亮、排序、复制、手动刷新和自动刷新；不直接写数据库、不修改事件日志，也不执行 Git 操作。
- Git 时间线刷新失败时保留上一次成功图形，SQLite 分页继续刷新并在状态栏显示警告。
- `--refresh-seconds <N>` 设置刷新间隔，默认 3 秒，`0` 表示关闭；`--branch <name>` 查看指定分支流。
- 图形环境不可用时继续使用 `context`、`history`、`decisions`、`explorations` 和 `db status`。
