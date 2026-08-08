"""Pure health rules shared by CLI checks and recovery tooling."""

from __future__ import annotations

def active_task_errors(task_ids: list[str]) -> list[str]:
    if len(task_ids) <= 1:
        return []
    rendered = ", ".join(task_ids)
    return [
        f"检测到 {len(task_ids)} 个活动任务（{rendered}）；项目只允许一个活动任务，请运行 task recover"
    ]
