"""Read-only review inventory built from the journal and resource layout."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...core.events import resolve_timezone
from ...core.reviews import digest, project_review
from .resource_layout import files_under, resource_for_path, is_simulation_bundle, bundle_contains


def inventory(root, branch, events, context, catalog_items, catalog_relations, config, *, now=None):
    now = now or datetime.now(resolve_timezone(config.get("timezone", "Asia/Shanghai"))).replace(tzinfo=None)
    days = config.get("review_interval_days", 14)
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise ValueError("review_interval_days 必须为正整数")
    objects = {}
    starts = {e["task_id"]: e["payload"] for e in events if e["event_type"] == "task.started"}
    attempts = {e["payload"].get("attempt_id"): e["payload"] for e in events if e["event_type"] == "attempt.started"}

    def obj(key, title=None):
        review_kind = "stage" if key.startswith("stage:") else ("attempt" if key.startswith("attempt:") else "resource")
        return objects.setdefault(key, {"object": key, "branch": branch, "title": title or key,
            "status": "active", "sources": {}, "source_details": {}, "issues": [], "task_ids": [],
            "review_kind": review_kind, "usage": "formal", "last_changed_at": None,
            "last_activity_at": None, "legacy_reviewed_at": None})

    def add(target, event, *, content=True):
        key = "event:" + event["event_id"]
        target["sources"][key] = digest(event["payload"])
        target["source_details"][key] = {"type": event["event_type"], "at": event["occurred_at"],
                                         "payload": event["payload"]}
        target["last_activity_at"] = event["occurred_at"]
        if content:
            target["last_changed_at"] = event["occurred_at"]
        if event.get("task_id"):
            target["task_ids"].append(event["task_id"])

    for event in events:
        kind, payload = event["event_type"], event["payload"]
        # Merged history is visible; acknowledgements are still branch-local.
        if kind.startswith("stage.") and payload.get("stage_id"):
            target = obj("stage:" + payload["stage_id"], payload.get("title"))
            if payload.get("title"):
                target["title"] = payload["title"]
            target["status"] = payload.get("status", target["status"])
            add(target, event)
        if kind.startswith("attempt.") and payload.get("attempt_id"):
            aid = payload["attempt_id"]
            target = obj("attempt:" + aid)
            initial = attempts.get(aid, {})
            target["owner_branch"] = initial.get("branch") or event["branch"]
            target["stage_id"] = initial.get("stage_id")
            target["status"] = payload.get("state", target["status"])
            add(target, event)
            sid = initial.get("stage_id")
            if sid:
                add(obj("stage:" + sid), event, content=False)
        if kind == "task.finished":
            sid = (payload.get("stage_review") or {}).get("stage_id")
            if sid:
                target = obj("stage:" + sid)
                target["last_activity_at"] = event["occurred_at"]
                if (payload.get("stage_review") or {}).get("result") in {"updated", "reviewed-no-change"}:
                    target["legacy_reviewed_at"] = event["occurred_at"]

    registered = {}
    for item in catalog_items:
        target = obj("resource:" + item["item_id"], item.get("title"))
        target["status"] = item.get("status", "active")
        target["path"] = item.get("path")
        target["sources"]["catalog:" + item["item_id"]] = digest(item)
        target["last_changed_at"] = item.get("updated_at")
        target["source_details"]["catalog:" + item["item_id"]] = item
        if item.get("path"):
            registered[item["path"]] = item
        metadata = item.get("metadata") or {}
        tags = {str(tag).lower() for tag in item.get("tags", [])}
        declared_usage = metadata.get("review_usage")
        target["usage"] = declared_usage if declared_usage in {"formal", "reference", "example"} else (
            "example" if tags & {"example", "demo"} else "reference" if tags & {"reference", "stable-reference"} else "formal")
        target["stage_id"] = metadata.get("stage_id")
        target["attempt_id"] = metadata.get("attempt_id")
    for relation in catalog_relations:
        for iid in (relation.get("source_id"), relation.get("target_id")):
            target = objects.get("resource:" + str(iid))
            if target:
                key = "relation:" + relation["relation_id"]
                target["sources"][key] = digest(relation)
                target["source_details"][key] = relation
    for event in events:
        if event["event_type"].startswith("catalog."):
            payload = event["payload"]
            iid = payload.get("item_id") or (payload.get("item") or {}).get("item_id")
            target = objects.get("resource:" + str(iid))
            if target:
                add(target, event)

    all_files = files_under(root, "resources")
    assigned = set()
    for path, item in registered.items():
        target = objects["resource:" + item["item_id"]]
        absolute = root / path
        if not absolute.resolve().is_relative_to(root.resolve()):
            target["issues"].append("资料路径超出项目")
            continue
        paths = [p for p in all_files if bundle_contains(path, p.relative_to(root).as_posix())] if is_simulation_bundle(item) else ([absolute] if absolute.is_file() else [])
        if not paths:
            target["sources"]["file:" + path] = "missing"
            target["issues"].append("已登记资料文件缺失")
        for file in paths:
            relative = file.relative_to(root).as_posix()
            assigned.add(relative)
            try:
                if not file.resolve().is_relative_to(root.resolve()):
                    raise OSError("资料链接超出项目")
                # Streaming avoids loading large datasets into memory.
                import hashlib
                with file.open("rb") as stream:
                    value = hashlib.file_digest(stream, "sha256").hexdigest()
                target["sources"]["file:" + relative] = value
                modified = datetime.fromtimestamp(file.stat().st_mtime, resolve_timezone(config.get("timezone", "Asia/Shanghai"))).replace(tzinfo=None).isoformat(timespec="seconds")
                target["source_details"]["file:" + relative] = {"path": relative, "version": value, "modified_at": modified}
                if parse_date(target["last_changed_at"]) < parse_date(modified):
                    target["last_changed_at"] = modified
            except OSError as exc:
                target["sources"]["file:" + relative] = "unreadable"
                target["issues"].append(f"资料不可读取：{relative}: {exc}")
    for file in all_files:
        relative = file.relative_to(root).as_posix()
        directory = resource_for_path(relative)
        if relative in assigned or (directory and directory.kind == "spark"):
            continue
        target = obj("path:" + relative)
        target["path"] = relative
        target["issues"].append("科研资料尚未登记")
        target["sources"]["file:" + relative] = digest([file.stat().st_size, file.stat().st_mtime_ns])

    # Preserve previously observed bundle members as explicit deletion changes.
    for event in events:
        if event["event_type"] != "review.recorded" or event["branch"] != branch:
            continue
        target = objects.get(event["payload"].get("object"))
        if target and target["object"].startswith("resource:"):
            for key in event["payload"].get("sources", {}):
                if key.startswith("file:") and key not in target["sources"]:
                    target["sources"][key] = "removed"
    for target in list(objects.values()):
        if target["object"].startswith("attempt:") and target.get("stage_id") and "stage:" + target["stage_id"] not in objects:
            target["issues"].append("关联篇章不存在；请核对探索关联")
        if target["object"].startswith("resource:") and target.get("stage_id"):
            parent = objects.get("stage:" + target["stage_id"])
            if parent:
                parent["sources"].update(target["sources"])
                parent["source_details"].update(target["source_details"])
                if parse_date(parent["last_activity_at"]) < parse_date(target["last_changed_at"]):
                    parent["last_activity_at"] = target["last_changed_at"]
    for sid in context.get("stage_version_conflicts", []):
        if "stage:" + sid in objects:
            objects["stage:" + sid]["issues"].append("本地分支存在判断分歧；请查看 stage list --all-branches")
    for version in context.get("stage_versions", []):
        target = objects.get("stage:" + version["stage_id"])
        if target and version["stage_id"] in context.get("stage_version_conflicts", []):
            key = "branch-version:" + version["version"]
            target["sources"][key] = digest(version["event_ids"])
            target["source_details"][key] = {"sources": version["sources"], "summary": version.get("summary"), "version": version["version"]}
    active = context.get("active_task") or {}
    current_stage = context.get("current_stage") or {}
    current_attempts = [a for a in context.get("attempts", []) if a.get("branch") == branch]
    linked = active.get("stage_id") or next((a.get("stage_id") for a in current_attempts if a.get("state") == "active"), None)
    active_id = active.get("task_id")
    changed = active.get("changed_paths") or []
    reviews = [e for e in events if e["event_type"] == "review.recorded"]
    results = []
    for target in objects.values():
        verification_times = [r.get("finished_at") for e in events if e.get("task_id") in target["task_ids"]
                              and e["event_type"] == "task.finished" for r in e["payload"].get("verification", [])]
        if active_id in target["task_ids"]:
            verification_times.extend(r.get("finished_at") for r in (context.get("verification") or {}).get("receipts", []))
        target["last_verified_at"] = max((v for v in verification_times if v), default=None)
        key = target["object"]
        target["related"] = bool(
            (active_id and active_id in target["task_ids"])
            or (linked and (key == "stage:" + linked or target.get("stage_id") == linked))
            or (not active_id and key == "stage:" + str(current_stage.get("stage_id")))
            or (target.get("path") and any(p == target["path"] or p.startswith(target["path"].rstrip("/") + "/") for p in changed)))
        if target.get("owner_branch") and target["owner_branch"] != branch:
            target["related"] = False
        results.append(project_review(target, reviews, now, days))
    return results


def summary(items, events=(), branch=None):
    if branch is not None:
        items = [item for item in items if not item.get("owner_branch") or item["owner_branch"] == branch]
    pending = [item for item in items if item["pending"]]
    related = [item for item in pending if item["related"]]
    research_items = [item for item in items if item.get("usage") != "example"]
    changed_at = max((item["last_changed_at"] for item in research_items
                      if item.get("last_changed_at")), key=parse_date, default=None)
    terminal = {"paused", "completed", "cancelled", "archived", "validated", "negative", "inconclusive"}
    reviewable = [item for item in research_items if item.get("status") not in terminal or item.get("issues")]
    completed = [event for event in events if event["event_type"] == "review.coverage_completed"
                 and (branch is None or event["branch"] == branch)]
    overall_reviewed_at = completed[-1]["occurred_at"] if completed else None
    hard = [item for item in pending if "integrity_issue" in item.get("reason_codes", [])]
    deferred = [item for item in items if item.get("deferred")]
    status = "action_required" if hard else "review_suggested" if pending else "normal"
    counts = {kind: sum(item.get("review_kind") == kind for item in pending)
              for kind in ("stage", "attempt", "resource")}
    return {"status": status, "hard_issue_count": len(hard),
            "deferred_count": len(deferred),
            "deferred_until": min((item["deferred_until"] for item in deferred
                                   if item.get("deferred_until") and item["deferred_until"] != "resume"),
                                  key=parse_date, default=None),
            "reviewable_count": len(reviewable),
            "pending_count": len(pending), "related_count": len(related),
            "pending_by_kind": counts, "last_research_changed_at": changed_at,
            "last_overall_reviewed_at": overall_reviewed_at,
            "items": [{key: item.get(key) for key in ("object", "title", "review_kind", "usage", "reasons", "reason_codes", "last_changed_at", "last_activity_at", "last_reviewed_at", "last_verified_at")}
                      for item in related], "next_action": "review list --all" if pending else None}


def parse_date(value):
    from ...core.reviews import parse_time
    return parse_time(value) or datetime.min
