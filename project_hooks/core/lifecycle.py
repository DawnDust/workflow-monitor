"""Pure lifecycle-state projection rules."""

from __future__ import annotations

from datetime import datetime


def _preflight_item(code: str, message: str, next_action: str | None = None) -> dict:
    value = {"code": code, "message": message}
    if next_action:
        value["next_action"] = next_action
    return value


def finish_preflight(state: dict) -> dict:
    """Evaluate objective finish gates from already collected project facts."""
    blockers: list[dict] = []
    warnings: list[dict] = []
    checkpoint_status = state.get("checkpoint_status") or "missing"
    if checkpoint_status == "missing":
        blockers.append(_preflight_item(
            "CHECKPOINT_REQUIRED", "结束前必须记录 task checkpoint", "state update",
        ))
    elif checkpoint_status == "stale":
        blockers.append(_preflight_item(
            "CHECKPOINT_STALE", "checkpoint 后工作内容又发生变化", "state update",
        ))
    elif checkpoint_status == "legacy-unknown":
        warnings.append(_preflight_item(
            "CHECKPOINT_FRESHNESS_UNKNOWN", "旧 checkpoint 没有工作内容指纹；允许兼容结束",
        ))
    for error in state.get("health_errors") or []:
        blockers.append(_preflight_item("HEALTH_CHECK_FAILED", str(error), "health.check"))
    verification = state.get("verification") or {}
    for problem in verification.get("problems") or []:
        blockers.append(_preflight_item(
            f"VERIFICATION_{str(problem.get('reason') or 'missing').upper().replace('-', '_')}",
            f"{problem.get('suite')} 测试回执：{problem.get('reason')}",
            problem.get("command"),
        ))
    linked_stage = state.get("linked_stage_id")
    stage_changed = bool(state.get("stage_changed"))
    stage_review = state.get("stage_review")
    if linked_stage and not stage_changed and stage_review != "reviewed-no-change":
        blockers.append(_preflight_item(
            "STAGE_REVIEW_REQUIRED", "关联阶段未更新，需要明确审阅为无需变更",
            "end --stage-review reviewed-no-change",
        ))
    inferred = {
        "main_goal": "changed" if state.get("project_updated") else "unchanged",
        "stage_review": "updated" if stage_changed else stage_review,
    }
    required_actions = [
        item["next_action"] for item in blockers if item.get("next_action")
    ]
    return {
        "status": "blocked" if blockers else "ready",
        "checkpoint_status": checkpoint_status,
        "blockers": blockers,
        "warnings": warnings,
        "required_actions": list(dict.fromkeys(required_actions)),
        "inferred": inferred,
    }


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
            return {"key": "completed", "label": "已完成", "index": 7, "needs_recovery": False}
        return {"key": "idle", "label": "未开始", "index": 0, "needs_recovery": False}
    phase = sidecar.get("phase") or "active"
    if phase == "starting":
        return {"key": "starting", "label": "周期已建立", "index": 1, "needs_recovery": True}
    if phase == "finishing":
        return {"key": "finishing", "label": "正在收尾", "index": 6, "needs_recovery": True}
    if not active:
        return {"key": "active", "label": "周期已建立", "index": 1, "needs_recovery": False}
    preflight = state.get("finish_preflight") or finish_preflight(state)
    checkpoint = preflight.get("checkpoint_status")
    if checkpoint == "missing":
        if state.get("changed_paths"):
            return {"key": "working", "label": "工作中", "index": 2, "needs_recovery": False}
        return {"key": "active", "label": "周期已建立", "index": 1, "needs_recovery": False}
    if checkpoint == "stale":
        return {
            "key": "working",
            "label": "工作中",
            "index": 2,
            "needs_recovery": False,
            "warning": {
                "code": "CHECKPOINT_STALE",
                "message": "Checkpoint 已过期，请重新记录进展",
                "next_action": "state update",
            },
        }
    if not state.get("worktree_quiet", True):
        return {"key": "progress-recorded", "label": "进展已记录", "index": 3, "needs_recovery": False}
    if preflight.get("status") != "ready":
        return {"key": "verification-required", "label": "待验证或审阅", "index": 4, "needs_recovery": False}
    return {"key": "ready", "label": "门禁已通过", "index": 5, "needs_recovery": False}
