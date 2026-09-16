# Agent 操作规范

<!-- project-maintenance-hooks:begin -->
## 项目维护生命周期

- 项目生命周期操作和维护状态写入使用仓库根目录的 `.\workflow-monitor.exe`；文件读取、搜索和测试使用相应工具。
- 新 clone 或 worktree 首次写入前，将 `workflow-monitor.exe` 放到仓库根目录并运行 `.\workflow-monitor.exe install`。
- 每次任务先运行 `.\workflow-monitor.exe context --view brief --format markdown`，按 `core_read_order` 读取尚未掌握或已变更的规范；首次写入前必须运行 `start`。
- 纯只读咨询和审查不启动写入生命周期，也不调用 `end`；准备首次写入时再判断轨道。已启动的任务必须通过 `end` 或规定的放弃流程收尾。
- 用户只需用自然语言描述任务；AI 从对话提取目标、验收和证据，任务 ID、时间、分支、状态令牌和安全默认值由工作流代码处理，不要求用户复制提示词或命令。
- 稳定维护只在 `main` 使用 `--track stable`；新理论、算法、实验和不确定改动使用 `--track research|experiment|sandbox --topic <slug>`。
- 无法可靠判断稳定维护还是探索时必须在对话中询问用户，不得静默选择轨道。
- 已启动的任务通过 `report` 一次更新进度和科研证据，完成时运行 `end`。只有 `validated` 尝试可准备 Squash PR，合并必须等待用户明确确认。
- 项目总目标使用 `project update`；科研进展、证据和关联篇章修订统一通过 `report` 填报，程序生成记录。新证据改变判断时必须提供修订说明、概况和证据；旧 `stage`、`attempt update` 继续兼容。
- 科研资料文件只放在 `resources/` 的标准子目录，并通过 `catalog` 指令登记；`resources/sparks/` 中未登记的 Markdown 灵感除外。`check` 必须保持通过。
- 仅当用户明确要求纯 Git 发布，且 `main` 没有活动任务、不再编辑、改动已经结束并验证通过时，才可直接检查、暂存、提交和推送。
- 禁止改写既有 `maintenance/events.jsonl` 行、直接编辑 SQLite、删除活动状态、绕过 `end`、在 `main` 试改、自动合并。
- 崩溃后运行 `task recover`；明确放弃时运行 `task abandon --reason <原因>`，不得手工删除 sidecar 或活动任务行。
- Dashboard 只读展示当前任务、研究篇章、动作可用性和阻塞原因；生命周期写入由 AI 调用结构化服务。放弃、迁移、数据库重建和软件更新必须在对话中取得用户确认。

## Codex 简短调用

- 默认读取 `context --view brief --format markdown`；只查结束门禁使用 `context --view verification`，追溯全部事实使用 `context --view full`。篇章和交接详情按需使用 `stage show`、`history`。
- `start` 可省略任务 ID，由工作流自动分配；`end` 可省略 ID，使用当前活动任务。两者及 `report` 可加 `--compact` 返回简短回执；复杂填报支持 `--input-json <文件或 ->`。
- 中途仅在阶段完成、判断变化、阻塞或交接时保存 checkpoint。短任务可通过 `end --current-step ... --next ...` 同时保存最终进展，不在结束前重复调用内容相同的 `state update`。
- 精简显示不改变测试、篇章审阅和授权门禁；失败与警告必须处理。不要用无参 EXE 获取上下文，无参冻结 EXE 会启动桌面界面。
- 科研维护以相关变化为主，14 天未审阅仅作提醒；通过 `review show` 获取范围，随 `report` 或 `end` 一次提交 `reviews`。AI 自主处理当前任务有关事项，无关事项延后；必要时可主动审阅。无变化不改写内容，延期不替代门禁。
<!-- project-maintenance-hooks:end -->

## 软件维护测试

- 完成一批相关代码修改后运行 `report --current-step ... --verify fast`；已通过的检查仅在新改动、失败或未解决问题影响其结果时重跑。
- 任务结束时程序按实际门禁自动补齐同一软件输入指纹的测试回执：软件改动要求 fast/full，release profile 要求 fast/release；纯文档和治理改动由 `end` 内置检查验证。
- 不得通过删除现有测试场景缩短耗时；使用 `python scripts/run_tests.py list` 查看场景清单。
