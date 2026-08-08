"""Pure lifecycle-state projection rules."""

from __future__ import annotations

from datetime import datetime


def active_task_warning(active: dict | None, now: datetime | None = None) -> dict | None:
    if not active or not active.get("started_at"):
        return None
    clean = str(active["started_at"]).split("（", 1)[0].split("(", 1)[0].strip()
    try:
        started = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    age_hours = max(0.0, ((now or datetime.now()) - started).total_seconds() / 3600)
    if age_hours >= 24 * 7:
        return {"level": "red", "age_hours": round(age_hours, 1), "message": "活动任务已超过 7 天，请恢复或明确放弃"}
    if age_hours >= 24:
        return {"level": "yellow", "age_hours": round(age_hours, 1), "message": "活动任务已超过 24 小时，请确认是否继续"}
    return None


def lifecycle_step(state: dict) -> dict:
    active = state.get("active_task")
    sidecar = state.get("sidecar") or {}
    if not active and not sidecar:
        if state.get("last_completed"):
            return {"key": "completed", "label": "已完成", "index": 6, "needs_recovery": False}
        return {"key": "idle", "label": "未开始", "index": 0, "needs_recovery": False}
    phase = sidecar.get("phase") or "active"
    if phase == "starting":
        return {"key": "starting", "label": "周期已建立", "index": 1, "needs_recovery": True}
    if phase == "finishing":
        return {"key": "finishing", "label": "正在收尾", "index": 5, "needs_recovery": True}
    changed = state.get("changed_paths") or []
    if active and active.get("state_updated"):
        if not state.get("health_errors") and state.get("worktree_quiet"):
            return {"key": "ready", "label": "可完成", "index": 4, "needs_recovery": False}
        return {"key": "progress", "label": "进展已记录", "index": 3, "needs_recovery": False}
    if changed:
        return {"key": "working", "label": "工作中", "index": 2, "needs_recovery": False}
    return {"key": "active", "label": "周期已建立", "index": 1, "needs_recovery": False}
