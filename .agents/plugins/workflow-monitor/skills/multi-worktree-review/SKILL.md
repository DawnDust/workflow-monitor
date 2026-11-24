---
name: multi-worktree-review
description: >-
  Use when resuming linked worktrees, checking parallel task status or research
  conflicts, or submitting change-based scientific reviews.
---

# 多工作树并行与科研审阅

1. **续办与汇总**：
   - 查看所有工作区与任务：`status --all` 或 `context --all`。
   - 同一工作流续办任务：`start --workflow <workflow_id> ...`。
   - 暂停/恢复任务：`task pause` / `task resume`。
2. **分歧与冲突**：运行 `conflict list` 与 `conflict show <id>` 核对版本证据；研究判断取舍与正式合并必须由用户确认。
3. **按变化审阅**：
   - 查看待办：`review list`（或 `review list --all` / `--kind stage|attempt|resource`）。
   - 查看精简变化与短令牌：`review show <object>`（需要完整来源时加 `--full`）。
   - 审阅结果随 `report` 或 `end` 的 `--reviews` 一次性提交，无变化不改写科学结论。
