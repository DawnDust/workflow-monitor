"""Loss-aware compact failures; persistence is observed rather than assumed."""


def failure_result(c, exc, diagnostic, root, before_events):
    diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
    result = {"result": "failed", "code": diagnostic.get("code", "WORKFLOW_ERROR"),
              "reason": str(exc), "task_id": None, "saved": "unknown",
              "saved_events": [], "retry": "inspect", "next_action": diagnostic.get("suggestion") or "context --view verification"}
    if before_events is None:
        result["saved"] = "not-started"
    if root is None:
        return result
    try:
        events = c.load_events(c.journal_path())
        if before_events is not None:
            new = [e for e in events if e["event_id"] not in before_events]
            result["saved_events"] = [{"event_id": e["event_id"], "type": e["event_type"]} for e in new]
            result["saved"] = "yes" if new else "no-new-events"
        try:
            active = c.read_active()
        except c.WorkflowError as error:
            if "没有活动任务" not in str(error):
                raise
            active = None
        if active:
            result["task_id"] = active["task_id"]
            checkpoints = [e for e in events if e.get("task_id") == active["task_id"] and e["event_type"] == "task.checkpointed"]
            result["last_checkpoint"] = checkpoints[-1]["event_id"] if checkpoints else None
            pending = active.get("pending_report") or active.get("pending_start") or active.get("recovery_phase") == "finishing"
            result["retry"] = "recover-first" if pending else "fix-then-retry"
            result["next_action"] = "task recover" if pending else result["next_action"]
            result["logs"] = [str(p) for p in sorted((root / ".project_hooks/verification-logs" / active["task_id"]).glob("*")) if p.is_file()]
        if result["code"] in {"WRITER_LOCKED", "WORKSPACE_BUSY"}:
            result["retry"] = "retry-after-writer"
    except Exception:
        result["saved"] = "unknown"
        result["next_action"] = "context --view verification"
    return result
