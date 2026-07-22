# Agent 操作规范

<!-- project-maintenance-hooks:begin -->
## 项目维护生命周期

本项目使用 `.codex/project-maintenance-workflow.json`、`project_hooks` 和版本控制内的 `.githooks/` 维护任务基线、永久变更档案、交接与 Git 门禁，不依赖任何全局 Hook 或插件。新克隆或新 worktree 首次写入前，先运行 `python -m project_hooks install` 启用本仓库 Hook。每次任务按标记中的 `core_read_order` 依次读取文件；首次写入前运行 `python -m project_hooks start`；结束前更新 `maintenance/CHANGE_ARCHIVE.md`，最后更新 `maintenance/CURRENT_TASK.md`，再运行 `python -m project_hooks end`。不得通过删除活动状态或绕过 `end` 规避归档。
<!-- project-maintenance-hooks:end -->
