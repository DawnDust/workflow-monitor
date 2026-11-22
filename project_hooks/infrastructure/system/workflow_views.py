"""Clone-wide workflow views and explicit closure of saved explorations."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ...core.events import new_event
from ...core.lifecycle import lifecycle_step
from ..git import client as git
from ..git.worktrees import repository_context, list_worktrees
from ..persistence.project_registry import OCCUPIED, RegistryError, registry_for
from ..persistence.read_model import MaintenanceReadModel
from ..persistence.store import load_events, sync_registry_events
from ..persistence.transaction import mutation_lock, read_writer_lock
from . import multi_worktree as service


def primary_root(root):
    marker = root / ".git"
    if not marker.exists() or (marker.is_dir() and not (marker / "commondir").exists()):
        return root
    return Path(list_worktrees(root)[0]["path"])


def project_snapshot(root, *, local_context=None, sync=False, known_primary=False):
    if not known_primary:
        root = primary_root(root)
    registry = registry_for(root) if (root / ".git").exists() else None
    monitor = service.snapshot(root, sync=sync, registry=registry) if registry else None
    tasks = (monitor or {}).get("tasks", [])
    workflows = registry.workflows() if registry else []
    if not workflows:
        workflows = [{"workflow_id": "main", "branch": "main", "track": "stable",
                      "path": str(root), "status": "open"}]
    if not registry and local_context and local_context.get("active_task"):
        active = local_context["active_task"]
        tasks = [{**active, "workflow_id": "main", "status": "active", "worktree": str(root),
                  "current_step": (local_context.get("state") or {}).get("current_step")}]
    grouped = {}
    for task in tasks:
        grouped.setdefault(task.get("workflow_id") or ("main" if task["branch"] == "main" else task["branch"]), []).append(task)
    for workflow in workflows:
        group = grouped.get(workflow["workflow_id"], [])
        active = next((task for task in group if task["status"] in OCCUPIED), None)
        workflow["task_count"] = len(group)
        workflow["current_task"] = active
        workflow["last_task"] = group[-1] if group else None
        if "closure_json" in workflow:
            closure = workflow.pop("closure_json")
            workflow["closure"] = json.loads(closure) if closure else None
    return {**(monitor or {}), "enabled": bool(registry), "unified": bool(registry and registry.setting("unified")),
            "tasks": tasks, "workflows": workflows, "worktrees": (monitor or {}).get("worktrees", []),
            "conflicts": (monitor or {}).get("conflicts", []), "pending_events": (monitor or {}).get("pending_events", 0)}


def checkout_model(root, branch):
    config = service.settings(root)
    store = config["maintenance_store"]
    return MaintenanceReadModel(root / store["database"], root / store["journal"], lambda: branch, repo=root)


def branch_events(root, workflow):
    branch = workflow.get("archive_branch") or workflow["branch"]
    path = Path(workflow["path"]) if workflow.get("path") else None
    if path and service.owns_checkout(root, {**workflow, "branch": branch}):
        return load_events(path / service.settings(path)["maintenance_store"]["journal"])
    relative = service.settings(root)["maintenance_store"]["journal"]
    result = git.run(root, ["show", f"refs/heads/{branch}:{relative}"], check=False)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()] if result.returncode == 0 else []


def workflow_snapshot(root, workflow_id, task_id=None):
    root = primary_root(root)
    registry = registry_for(root)
    workspace = None
    row = registry.workflow(workflow_id) if registry else None
    if not row and workflow_id == "main":
        branch = git.current_branch(root) or "main"
        row = {"workflow_id": "main", "branch": branch, "path": str(root), "status": "open",
               "track": "stable", "worktree_id": "main"}
    if not row:
        raise RegistryError("找不到工作流: " + workflow_id)
    branch = row.get("archive_branch") or row["branch"]
    if service.owns_checkout(root, {**row, "branch": branch}):
        workspace = Path(row["path"])
    events = {event["event_id"]: event for event in branch_events(root, row)}
    if registry:
        for event in registry.events():
            if event["branch"] in {row["branch"], branch}:
                events.setdefault(event["event_id"], event)
    by_task = {}
    for event in sorted(events.values(), key=lambda item: item["occurred_at"]):
        if event["branch"] not in {row["branch"], branch} or not event.get("task_id"):
            continue
        by_task.setdefault(event["task_id"], []).append(event)
    rows = {task["task_id"]: task for task in (registry.tasks() if registry else [])
            if task.get("workflow_id") == workflow_id}
    history = []
    for key in list(dict.fromkeys([*by_task, *rows])):
        history.append(history_entry(key, rows.get(key), by_task.get(key, [])))
    history.sort(key=lambda task: (task["started_at"], task["task_id"]), reverse=True)
    current = next((task for task in history if task["status"] in OCCUPIED), None)
    selected = next((task for task in history if task["task_id"] == task_id), None) if task_id else current or next(iter(history), None)
    if task_id and not selected:
        raise RegistryError("所选任务不属于当前工作流")
    result = {"workflow": row, "task": selected, "history": history, "workspace_available": bool(workspace),
              "context": {}, "action_matrix": {"state": {}}, "changes": [], "conflicts": []}
    if registry:
        monitor = service.snapshot(root, sync=False)
        result["conflicts"] = [item for item in monitor["conflicts"] if any(
            source.get("workflow_id") == workflow_id for source in item["sources"])]
    if workspace:
        model = checkout_model(workspace, branch)
        if (current is not None and selected is current) or selected is None:
            context = model.context(branch)
            active = context.get("active_task")
            state = {"active_task": active, "git": context["git_state"],
                     "dirty_paths": git.dirty_paths(workspace), "changed_paths": (active or {}).get("changed_paths", []),
                     "writer_lock": read_writer_lock(workspace / service.settings(workspace).get("state_dir", ".project_hooks"))}
            state["lifecycle_step"] = lifecycle_step({**context, **state})
            result.update(context=context, action_matrix={"state": state})
        elif selected:
            result["context"] = {"verification": {"accepted": selected["verification"]}}
        if row.get("base_commit"):
            changed = git.run(workspace, ["diff", "--name-status", "-z", "--no-renames", row["base_commit"], "--"], check=False)
            fields = changed.stdout.rstrip("\0").split("\0") if changed.stdout else []
            result["changes"] = [{"kind": kind, "path": path} for kind, path in zip(fields[::2], fields[1::2])
                                 if path != "maintenance/events.jsonl"]
            result["changes"].extend({"kind": "?", "path": path} for path in git.run(
                workspace, ["ls-files", "--others", "--exclude-standard", "-z"]).stdout.split("\0") if path)
    if selected and not result["context"]:
        result["context"] = {"verification": {"accepted": selected["verification"]}}
    return result


def workflow_content(root, workflow_id):
    root = primary_root(root)
    registry = registry_for(root)
    row = registry.workflow(workflow_id) if registry else None
    if not row:
        raise RegistryError("找不到已登记工作流")
    branch = row.get("archive_branch") or row["branch"]
    if not service.owns_checkout(root, {**row, "branch": branch}):
        raise RegistryError("工作区暂不可用。可在任务历史中查看已保存的证据")
    data = checkout_model(Path(row["path"]), branch).dashboard_snapshot(branch)
    return {"branch": branch, "stages": (data.get("context") or {}).get("stages", []),
            "catalog_items": data.get("catalog_items", []), "search_index": data.get("search_index", [])}


def history_entry(task_id, row, events):
    start = next((event for event in events if event["event_type"] == "task.started"), None)
    finish = next((event for event in reversed(events) if event["event_type"] == "task.finished"), None)
    finish_values = finish["payload"] if finish else {}
    state = json.loads(row["state_json"]) if row else {}
    record = state.get("record", {})
    evidence = []
    for event in events:
        values = event["payload"].get("evidence") or []
        evidence.extend([values] if isinstance(values, str) else values)
    return {"task_id": task_id, "started_at": (row or {}).get("started_at") or (start or events[0])["occurred_at"],
            "finished_at": finish["occurred_at"] if finish else None,
            "status": (row or {}).get("status") or ("completed" if finish else "historical"),
            "result": finish_values.get("result"), "note": finish_values.get("note"),
            "scope": (start or {}).get("payload", {}).get("scope") or record.get("declaration", {}).get("scope"),
            "verification": finish_values.get("verification", []), "stage_review": finish_values.get("stage_review"),
            "checkpoint": record.get("checkpoint", {}), "evidence": list(dict.fromkeys(evidence))}


def close_workflow(root, workflow_id, *, cleanup=False, confirmed=False):
    root = primary_root(root)
    registry = registry_for(root)
    lock_dir = repository_context(root).shared_dir
    if not confirmed:
        return closure_preview(root, registry, workflow_id, cleanup)
    with mutation_lock(lock_dir, command="workflow.close"):
        plan = closure_preview(root, registry, workflow_id, cleanup)
        if plan["blockers"]:
            raise RegistryError("工作流收尾受阻: " + " | ".join(plan["blockers"]))
        path = Path(plan["worktree"]) if plan["worktree"] else None
        if path and path.is_dir():
            state_dir = path / service.settings(path).get("state_dir", ".project_hooks")
            with mutation_lock(state_dir, command="workflow.close", timeout=0.25):
                plan = closure_preview(root, registry, workflow_id, cleanup)
                if plan["blockers"]:
                    raise RegistryError("工作流收尾受阻: " + " | ".join(plan["blockers"]))
                return apply_closure(root, registry, plan)
        return apply_closure(root, registry, plan)


def closure_preview(root, registry, workflow_id, cleanup):
    row = registry.workflow(workflow_id) if registry else None
    if not row or workflow_id == "main":
        raise RegistryError("请选择有效探索工作流")
    branch, archived = row["branch"], row.get("archive_branch")
    target = archived or "archive/" + branch
    path = Path(row["path"]) if row["path"] else None
    blockers = []
    if any(task.get("workflow_id") == workflow_id and task["status"] in OCCUPIED for task in registry.tasks()):
        blockers.append("存在未结束任务")
    if any(item["worktree_id"] == row["worktree_id"] for item in registry.pending()):
        blockers.append("审计事件尚未同步")
    ref = archived or branch
    tip = git.run(root, ["rev-parse", "--verify", f"refs/heads/{ref}"], check=False)
    if tip.returncode and row["status"] == "closing":
        ref = target
        tip = git.run(root, ["rev-parse", "--verify", f"refs/heads/{ref}"], check=False)
    if tip.returncode:
        blockers.append("缺少可保存的分支提交")
    integration = None
    events = branch_events(root, {**row, "archive_branch": ref if ref != branch else None})
    verdict = next((event["payload"].get("state") for event in reversed(events)
                    if event["event_type"] == "attempt.state_changed" and event["payload"].get("attempt_id") == workflow_id), None)
    if row["status"] in {"closing", "archived"} and row.get("closure_json"):
        previous = json.loads(row["closure_json"])
        integration, verdict = previous.get("integration"), previous.get("verdict")
        if previous["commit"] != tip.stdout.strip():
            blockers.append("分支提交在收尾过程中发生变化")
    elif verdict == "validated" and tip.returncode == 0:
        from .multi_worktree import assert_integratable
        try:
            assert_integratable(root, branch)
        except RegistryError as exc:
            blockers.append(str(exc))
        event = next((event for event in reversed(events) if event["event_type"] == "exploration.recorded"
                      and event["payload"].get("attempt_id") == workflow_id and event["payload"].get("result") == "validated"), None)
        if event:
            integration = git.integration_evidence(root, "main", ref, required_path=service.settings(root)["maintenance_store"]["journal"],
                                                   required_text=event["event_id"])
        if not integration or integration["state"] != "merged":
            blockers.append("主线尚无完整集成证据")
    elif verdict not in {"negative", "inconclusive", "paused"} and row["status"] != "archived":
        blockers.append("请先结束任务并保存探索结论")
    if row["status"] == "open" and target != ref and git.run(root, ["show-ref", "--verify", "--quiet", "refs/heads/" + target], check=False).returncode == 0:
        blockers.append("归档分支名已存在")
    ignored, changes = [], []
    if path and path.is_dir():
        if not service.owns_checkout(root, {**row, "branch": ref}):
            blockers.append("工作区身份与分支不一致")
        else:
            changes = git.dirty_paths(path)
            ignored = [name for name in git.run(path, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"]).stdout.split("\0")
                       if name and not name.startswith(".project_hooks/")]
            if changes or (cleanup and ignored):
                blockers.append("先保存已修改、未跟踪或被忽略的工作成果")
    elif path and row["status"] == "open":
        blockers.append("工作区位置失效")
    return {"workflow_id": workflow_id, "branch": ref, "archive_branch": target, "worktree": str(path) if path else None,
            "commit": tip.stdout.strip(), "cleanup": cleanup, "verdict": verdict, "integration": integration,
            "uncommitted": changes, "ignored": ignored, "blockers": blockers, "confirmation_required": True}


def apply_closure(root, registry, plan):
    workflow_id = plan["workflow_id"]
    row = registry.workflow(workflow_id)
    path = Path(plan["worktree"]) if plan["worktree"] else None
    cleanup = plan["cleanup"]
    if path and path.is_dir() and cleanup and (path == root or not path.resolve().is_relative_to(root / ".worktrees")):
        raise RegistryError("只能清理项目管理目录中的工作区")
    if row["status"] != "archived":
        registry.set_closure(workflow_id, "closing", plan)
    if cleanup and path and path.is_dir():
        git.run(root, ["worktree", "remove", str(path)])
        registry.set_workflow_location(workflow_id, None, None)
    if plan["branch"] != plan["archive_branch"]:
        if path and path.is_dir():
            git.run(path, ["branch", "-m", plan["archive_branch"]])
        else:
            git.run(root, ["branch", "-m", plan["branch"], plan["archive_branch"]])
    if row["status"] != "archived":
        event = new_event("attempt.archived", branch=row["branch"], task_id=None,
                          payload={"attempt_id": workflow_id, "archive_branch": plan["archive_branch"],
                                   "commit": plan["commit"], "integration": plan["integration"]},
                          timezone=service.settings(root)["timezone"],
                          event_id="workflow-close-" + workflow_id + "-" + plan["commit"])
        if not any(previous["event_id"] == event["event_id"] for previous in registry.events()):
            registry.record_events(root, [event])
        registry.set_closure(workflow_id, "archived", plan, plan["archive_branch"])
        sync_registry_events(registry)
    return {**plan, "status": "archived", "removed": bool(cleanup and path and not path.exists())}
