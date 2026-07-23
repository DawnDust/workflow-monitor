# Agent 操作规范

<!-- project-maintenance-hooks:begin -->
## 项目维护生命周期

- 新 clone 或 worktree 首次写入前运行 `python -m project_hooks install`。
- 每次任务先运行 `python -m project_hooks context --format markdown`，再按 `core_read_order` 阅读静态规范；首次写入前必须运行 `start`。
- 稳定维护只在 `main` 使用 `--track stable`；新理论、算法、实验和不确定改动使用 `--track research|experiment|sandbox --topic <slug>`。
- 通过 `state update` 更新进度、`decision add` 记录路线变化、`attempt update` 保存探索证据，最后必须运行 `end`。只有 `validated` 尝试可准备 Squash PR，合并必须等待用户明确确认。
- 仅当用户明确要求纯 Git 发布，且 `main` 没有活动任务、不再编辑、改动已经结束并验证通过时，才可直接检查、暂存、提交和推送。
- 禁止改写既有 `maintenance/events.jsonl` 行、直接编辑 SQLite、删除活动状态、绕过 `end`、在 `main` 试改、自动合并。
<!-- project-maintenance-hooks:end -->
