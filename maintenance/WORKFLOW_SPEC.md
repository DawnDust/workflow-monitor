# 项目维护工作流规范

## 目的

本工作流维护项目状态、变更证据和跨任务交接。它不假定项目是研究项目，也不负责解析论文或自动生成领域问题。

## 每次任务的固定顺序

1. 依次读取 `CURRENT_TASK.md`、`PROJECT_CONTEXT.md`、`CHANGE_ARCHIVE.md`、`DECISION_LOG.md` 和本规范。
2. 新克隆或新 worktree 先运行一次 `python -m project_hooks install`，仅为当前仓库设置 `core.hooksPath=.githooks`。
3. 在首次写入前运行 `python -m project_hooks start <task_id> ...`，保存文件与 Git 基线。
4. 完成实现、验证和必要文档更新；失败、阻塞和不可判决同样记录。
5. 在 `CHANGE_ARCHIVE.md` 追加一条任务记录；若路线或架构改变，再更新 `DECISION_LOG.md`。
6. 最后更新 `CURRENT_TASK.md`，只保留当前判决、真实断点、最多三步和最近五次交接。
7. 运行 `python -m project_hooks end <task_id> ...`。未满足义务时不得绕过门禁。

## Git 规则

- 只有显式标记为 `large` 且使用 `--git-commit auto` 或 `always` 的任务才尝试自动提交。
- 自动提交只包含任务启动后实际变化且不与启动前脏文件重叠的路径。
- 保留任务启动前的暂存区；不执行 `push`。
- `auto` 遇到归属冲突时跳过并报告；`always` 遇到冲突时阻止结束。
- `.githooks/pre-commit` 随仓库版本控制；`python -m project_hooks install` 只修改当前仓库的 `.git/config`，不创建或使用全局 Hook。

## 文档职责

- `PROJECT_CONTEXT.md` 保存低频变化的稳定事实。
- `CHANGE_ARCHIVE.md` 保存不可抹除的任务历史，不复制长篇实现说明。
- `DECISION_LOG.md` 保存会影响后续工作的持久决策。
- `CURRENT_TASK.md` 是短期工作指针，必须最后更新。
- 任务级机器证据由 `.project_hooks/history/` 自动维护，不创建手工 `SESSION_LOG.md`。
