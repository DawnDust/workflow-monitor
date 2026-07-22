# Agent 操作规范

<!-- project-maintenance-hooks:begin -->
## 项目维护生命周期

本项目使用 `.codex/project-maintenance-workflow.json`、`project_hooks`、SQLite 和版本控制内的 `.githooks/` 管理任务、探索、决策、交接与 Git 门禁，不依赖全局 Hook 或插件。新 clone 或 worktree 首次写入前运行 `python -m project_hooks install`。每次任务先运行 `python -m project_hooks context --format markdown` 获取动态上下文，再按 `core_read_order` 阅读静态规范；首次写入前运行 `python -m project_hooks start`。稳定维护使用 `--track stable` 并只在 `main` 进行；新理论、算法、实验和不确定改动使用 `--track research|experiment|sandbox --topic <slug>`。任务中通过 `state update` 更新状态，路线变化通过 `decision add` 记录，探索证据通过 `attempt update` 记录，最后运行 `end`。只有 `validated` 尝试可准备 Squash PR，且必须等待用户明确确认才能合并。禁止手工修改既有 `maintenance/events.jsonl` 行、直接编辑 SQLite、删除活动状态、绕过 `end`、直接在 `main` 试改或自动合并。
<!-- project-maintenance-hooks:end -->
