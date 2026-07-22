# Agent 操作规范

<!-- project-maintenance-hooks:begin -->
## 项目维护生命周期

本项目使用 `.codex/project-maintenance-workflow.json`、`project_hooks` 和版本控制内的 `.githooks/` 维护任务基线、探索分支、永久变更档案、交接与 Git 门禁，不依赖任何全局 Hook 或插件。新克隆或新 worktree 首次写入前，先运行 `python -m project_hooks install` 启用本仓库 Hook。每次任务按标记中的 `core_read_order` 依次读取文件；首次写入前运行 `python -m project_hooks start`。稳定维护使用 `--track stable` 并只在 `main` 进行；新理论、算法、实验和不确定改动必须使用 `--track research|experiment|sandbox --topic <slug>` 建立或复用探索分支。结束前更新 `maintenance/change_archive.md`，最后更新 `maintenance/current_task.md`，探索任务同时维护对应 `maintenance/attempts/<task-id>.md`，再运行 `python -m project_hooks end`。只有 `validated` 尝试可准备 Squash PR，且必须等待用户明确确认才能合并；不得通过删除活动状态、绕过 `end`、直接在 `main` 试改或自动合并规避门禁。
<!-- project-maintenance-hooks:end -->
