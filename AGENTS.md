# Agent 操作规范

<!-- project-maintenance-hooks:begin -->
## 项目维护生命周期

- 所有命令使用仓库根目录的 `.\workflow-monitor.exe`。
- 新 clone 或 worktree 首次写入前，将 `workflow-monitor.exe` 放到仓库根目录并运行 `.\workflow-monitor.exe install`。
- 每次任务先运行 `.\workflow-monitor.exe context --format markdown`，再按 `core_read_order` 阅读静态规范；首次写入前必须运行 `start`。
- 用户只需用自然语言描述任务；AI 从对话提取目标、验收和证据，任务 ID、时间、分支、状态令牌和安全默认值由工作流代码处理，不要求用户复制提示词或命令。
- 稳定维护只在 `main` 使用 `--track stable`；新理论、算法、实验和不确定改动使用 `--track research|experiment|sandbox --topic <slug>`。
- 无法可靠判断稳定维护还是探索时必须在对话中询问用户，不得静默选择轨道。
- 通过 `state update` 更新进度、`attempt update` 保存探索证据，最后必须运行 `end`。只有 `validated` 尝试可准备 Squash PR，合并必须等待用户明确确认。
- 项目资料和研究篇章只通过 `project update` 与内部 `stage` 指令记录；新证据改变判断时用 `stage update --revision ... --summary ... --evidence ...` 追加修订；探索当前步骤、进展和下一步使用 `attempt update` 的结构化字段。
- 科研资料文件只放在 `resources/` 的标准子目录，并通过 `catalog` 指令登记；`resources/sparks/` 中未登记的 Markdown 灵感除外。`check` 必须保持通过。
- 仅当用户明确要求纯 Git 发布，且 `main` 没有活动任务、不再编辑、改动已经结束并验证通过时，才可直接检查、暂存、提交和推送。
- 禁止改写既有 `maintenance/events.jsonl` 行、直接编辑 SQLite、删除活动状态、绕过 `end`、在 `main` 试改、自动合并。
- 崩溃后运行 `task recover`；明确放弃时运行 `task abandon --reason <原因>`，不得手工删除 sidecar 或活动任务行。
- Dashboard 只读展示当前任务、研究篇章、动作可用性和阻塞原因；生命周期写入由 AI 调用结构化服务。放弃、迁移、数据库重建和软件更新必须在对话中取得用户确认。
<!-- project-maintenance-hooks:end -->

## 软件维护测试

- 每次代码写入后运行 `python scripts/run_tests.py fast`。
- 任务结束前运行 `python scripts/run_tests.py full`；发布前运行 `python scripts/run_tests.py release`。
- 不得通过删除现有测试场景缩短耗时；使用 `python scripts/run_tests.py list` 查看场景清单。
