"""Review submissions share report's lock, event batch and recovery journal."""

from datetime import datetime, timedelta

from ...core.events import resolve_timezone
from ...core.reviews import decode_token, parse_time


def show(c, args):
    items = c.context_data().get("reviews", [])
    if args.review_command == "show":
        item = next((i for i in items if i["object"] == args.object), None)
        if item is None:
            raise c.WorkflowError("找不到审阅对象；请运行 review list --all")
        # Include the actual delta, not just an opaque receipt token.
        result = {k: v for k, v in item.items() if k not in {"sources", "source_details", "task_ids"}}
        keys = item["changes"] or list(item["sources"])
        result["details"] = {key: item["source_details"].get(key, {"version": item["sources"][key]}) for key in keys}
        return result
    return {"items": [{k: item[k] for k in ("object", "title", "pending", "deferred", "reasons", "last_reviewed_at")}
                       for item in items if args.all or (item["related"] and item["pending"])],
            "next_action": "review show <object>"}


def prepare(c, record, submissions, pending):
    if submissions is None:
        return []
    if not isinstance(submissions, list):
        raise c.WorkflowError("reviews 必须是审阅对象数组")
    if not submissions:
        return []
    from ...core.reviews import digest
    history = c.load_events(c.journal_path())
    items = {i["object"]: i for i in c.context_data().get("reviews", [])}
    now = datetime.now(resolve_timezone(c.config()["timezone"])).replace(tzinfo=None)
    prepared = []
    for submission in submissions:
        if not isinstance(submission, dict) or set(submission) - {"token", "result", "reason", "until", "evidence"}:
            raise c.WorkflowError("reviews 包含未知字段；仅接受 token/result/reason/until/evidence")
        try:
            snap = decode_token(submission.get("token"))
        except ValueError as exc:
            raise c.WorkflowError(str(exc)) from exc
        item = items.get(snap["object"])
        if snap["branch"] != record["git"]["branch"] or item is None:
            raise c.WorkflowError("审阅范围不属于当前分支或对象已不存在；请重新 review show")
        if item.get("owner_branch") and item["owner_branch"] != snap["branch"]:
            raise c.WorkflowError("其他分支的探索只能查看；请在其所属分支审阅")
        for key, value in snap["sources"].items():
            if key.startswith("event:"):
                source = next((e for e in history if e["event_id"] == key[6:]), None)
                if source is None or digest(source["payload"]) != value or key not in item["sources"]:
                    raise c.WorkflowError("审阅事件范围无效；请重新 review show")
            elif key not in item["sources"]:
                # Removed files remain visible as new inventory changes; do not accept unrelated keys.
                raise c.WorkflowError("资料范围已变化；请重新 review show")
        result = submission.get("result")
        if result not in {"updated", "reviewed-no-change", "deferred"}:
            raise c.WorkflowError("审阅结果必须为 updated/reviewed-no-change/deferred")
        payload = {**snap, "result": result}
        previous = [e for e in history + prepared if e["event_type"] == "review.recorded"
                    and e["branch"] == snap["branch"] and e["payload"].get("object") == snap["object"]]
        if result == "updated":
            for event in pending:
                source = event["payload"]
                target = ("stage:" + source["stage_id"] if event["event_type"].startswith("stage.") and source.get("stage_id")
                          else "resource:" + source["item_id"] if event["event_type"].startswith("catalog.") and source.get("item_id") else None)
                if target == snap["object"]:
                    # The submitted edit is known to the reviewer; concurrent external edits are not.
                    snap["sources"]["event:" + event["event_id"]] = digest(source)
            valid = {e["event_id"] for e in history + pending if e.get("task_id") == record["task_id"]
                     and e["event_type"].startswith({"stage": "stage.", "attempt": "attempt.", "resource": "catalog."}.get(snap["object"].split(":", 1)[0], "never."))
                     and ("event:" + e["event_id"] in item["sources"] or "event:" + e["event_id"] in snap["sources"])}
            evidence = submission.get("evidence", sorted(valid))
            if not isinstance(evidence, list) or not evidence or not all(isinstance(i, str) for i in evidence) or not set(evidence) <= valid:
                raise c.WorkflowError("updated 的证据不是本任务对应对象的修改事件")
            payload["evidence"] = evidence
        if result == "deferred":
            if not isinstance(submission.get("reason"), str) or not submission["reason"].strip():
                raise c.WorkflowError("延后审阅必须说明原因")
            until = submission.get("until") or (now + timedelta(days=14)).isoformat(timespec="seconds")
            if not submission.get("until") and previous:
                last = previous[-1]["payload"]
                if (last.get("result") == "deferred" and last.get("sources") == snap["sources"]
                        and last.get("reason") == submission["reason"].strip()
                        and parse_time(last.get("until")) and parse_time(last["until"]) > now):
                    until = last["until"]
            if until == "resume":
                if item["status"] != "paused":
                    raise c.WorkflowError("仅暂停对象可延后至 resume")
            else:
                try:
                    date = datetime.fromisoformat(until)
                    if date.tzinfo is not None:
                        date = date.astimezone(resolve_timezone(c.config()["timezone"])).replace(tzinfo=None)
                    if date <= now:
                        raise ValueError()
                    until = date.isoformat(timespec="seconds")
                except (TypeError, ValueError) as exc:
                    raise c.WorkflowError("延后日期必须在将来，或暂停对象使用 resume") from exc
            payload.update(reason=submission["reason"].strip(), until=until)
        last_time = parse_time(previous[-1]["occurred_at"]) if previous else None
        if previous and previous[-1]["payload"] == payload and (result == "deferred" or (
                last_time and now < last_time + timedelta(days=c.config().get("review_interval_days", 14)))):
            continue
        prepared.append(c.emit("review.recorded", branch=snap["branch"], task_id=record["task_id"], payload=payload))
    return prepared
