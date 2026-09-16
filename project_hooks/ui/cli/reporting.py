"""One semantic report produces task, attempt and optional stage records."""

from __future__ import annotations

import argparse
import hashlib
import json


def report(c, args):
    record = c.read_active()
    if record.get("pending_report") or record.get("pending_start") or record.get("recovery_phase") == "finishing":
        raise c.WorkflowError("前次操作尚未完成，请先运行 task recover")
    branch = c.assert_active_branch(record)
    attempt = c.get_attempt(branch) if record["git"]["track"] != "stable" else None
    events = []
    if getattr(args, "main_goal_version", None) is not None:
        raise c.WorkflowError("主目标版本请使用 project update")
    if getattr(args, "goal", None) not in (None, record["declaration"]["scope"]):
        raise c.WorkflowError("goal 与任务目标不同，请显式新建任务")
    fields = {name: getattr(args, name, None) for name in (
        "current_step", "judgment", "breakpoint", "blocker", "hypothesis", "progress", "conclusion")}
    if getattr(args, "status", None):
        if fields["current_step"] and fields["current_step"] != args.status:
            raise c.WorkflowError("status 与 current_step 冲突")
        fields["current_step"] = args.status
    clear = getattr(args, "clear", None) or []
    if any(name not in fields for name in clear):
        raise c.WorkflowError("clear 仅支持进展、判断、阻塞、假设及结论字段")
    for name in clear:
        if fields[name] is not None:
            raise c.WorkflowError(f"不能同时设置和清空 {name}")
        fields[name] = ""
    fields = {name: value for name, value in fields.items() if value is not None}
    evidence = getattr(args, "evidence", None) or []
    next_steps = getattr(args, "next", None)
    stage = getattr(args, "stage_update", None)
    reviews = getattr(args, "reviews", None)
    if not attempt and any(name in fields for name in ("hypothesis", "progress", "conclusion")):
        raise c.WorkflowError("假设、探索进展和结论仅用于探索任务")
    if attempt and next_steps is not None and len(next_steps) > 1:
        raise c.WorkflowError("探索汇报仅支持一个下一步")
    semantic = {"fields": fields, "evidence": evidence, "next": next_steps, "stage": stage, "reviews": reviews}
    signature = hashlib.sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    fingerprint = c.work_content_fingerprint(c.ROOT)
    task_events = [e for e in c.load_events(c.journal_path()) if e.get("task_id") == record["task_id"]]
    prior = next((e for e in reversed(task_events) if e["event_type"] == "task.checkpointed"), None)
    if not reviews and prior and prior["payload"].get("report_signature") == signature and prior["payload"].get("workspace_fingerprint") == fingerprint:
        return {"task_id": record["task_id"], "result": "unchanged", "changed_count": 0}
    if not fields and not evidence and next_steps is None and not stage and not reviews:
        if prior and prior["payload"].get("workspace_fingerprint") == fingerprint:
            return {"task_id": record["task_id"], "result": "unchanged", "changed_count": 0}
        raise c.WorkflowError("report 需要实际进展；请填写 current_step 或科研记录")
    if stage:
        if not isinstance(stage, dict):
            raise c.WorkflowError("stage_update 必须是对象")
        linked = (record.get("stage") or {}).get("stage_id")
        if not linked:
            raise c.WorkflowError("任务未关联篇章，不能隐式修订")
        allowed = {"summary", "current_step", "next_step", "blocker", "evidence", "revision", "status", "pause_kind", "pause_note"}
        if set(stage) - allowed:
            raise c.WorkflowError("stage_update 包含未知字段")
        values = {name: None for name in allowed}
        values.update(stage)
        c.stage_command(argparse.Namespace(stage_command="update", stage_id=linked, **values), pending_events=events)
    checkpoint = {name: value for name, value in fields.items() if name in {"judgment", "breakpoint", "blocker"}}
    previous_values = {}
    previous_evidence = set()
    for event in task_events:
        if event["event_type"] == "task.checkpointed":
            previous_values.update(event["payload"])
            previous_evidence.update(event["payload"].get("evidence", []))
    checkpoint = {name: value for name, value in checkpoint.items() if previous_values.get(name) != value}
    attempt_changed = False
    if attempt:
        payload = {"attempt_id": attempt["attempt_id"]}
        for name in ("hypothesis", "progress", "conclusion", "current_step"):
            if name in fields and fields[name] != attempt.get(name):
                payload[name] = c.clean_text(fields[name], name, 2000) if fields[name] else ""
        fresh = [c.clean_text(item, "证据", 1000) for item in evidence if item not in attempt.get("evidence", [])]
        if fresh:
            payload["evidence"] = fresh
        if next_steps is not None:
            value = next_steps[0] if next_steps else ""
            if value != attempt.get("next_step"):
                payload["next_step"] = value
        if len(payload) > 1:
            attempt_changed = True
            event = c.emit("attempt.updated", branch=branch, task_id=record["task_id"], payload=payload)
            events.append(event)
            checkpoint["source_event_id"] = event["event_id"]
        else:
            previous = next((e for e in reversed(c.load_events(c.journal_path())) if e["event_type"] == "attempt.updated" and e["payload"].get("attempt_id") == attempt["attempt_id"]), None)
            if previous:
                checkpoint["source_event_id"] = previous["event_id"]
    else:
        checkpoint.update({name: value for name, value in fields.items() if previous_values.get(name) != value})
        if next_steps is not None:
            if len(next_steps) > 3:
                raise c.WorkflowError("最多三个下一步")
            if previous_values.get("next_actions") != next_steps:
                checkpoint["next_actions"] = next_steps
        fresh = [item for item in evidence if item not in previous_evidence]
        if fresh:
            checkpoint["evidence"] = fresh
    from .review_commands import prepare, coverage_completion
    events.extend(prepare(c, record, reviews, events))
    actual_fields = {name for name in checkpoint if name != "source_event_id"}
    if not events and not attempt_changed and not actual_fields and prior and prior["payload"].get("workspace_fingerprint") == fingerprint:
        return {"task_id": record["task_id"], "result": "unchanged", "changed_count": 0}
    checkpoint.update(workspace_fingerprint=fingerprint, report_signature=signature)
    events.append(c.emit("task.checkpointed", branch=branch, task_id=record["task_id"], payload=checkpoint))
    completed = coverage_completion(c, record, events)
    if completed:
        events.append(completed)
    # Persist pending IDs in the existing recovery sidecar before the event batch.
    durable = c._base_active_record(record)
    durable["pending_report"] = events
    c.save_active(durable, state_updated=int(record["state_updated"]))
    c.persist(events)
    durable.pop("pending_report")
    c.save_active(durable, state_updated=1)
    return {"task_id": record["task_id"], "result": "success", "changed_count": len(events),
            "next_actions": next_steps or []}
