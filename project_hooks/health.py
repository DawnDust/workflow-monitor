"""Pure health rules shared by CLI checks and recovery tooling."""

from __future__ import annotations

import sqlite3


def active_task_errors(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT task_id FROM active_tasks ORDER BY started_at, task_id"
    ).fetchall()
    if len(rows) <= 1:
        return []
    task_ids = ", ".join(str(row[0]) for row in rows)
    return [
        f"检测到 {len(rows)} 个活动任务（{task_ids}）；项目只允许一个活动任务，请运行 task recover"
    ]
