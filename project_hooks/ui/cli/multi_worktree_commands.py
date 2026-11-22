"""CLI adapters for shared worktree services; ordinary single-tree commands stay intact."""

from __future__ import annotations

import json
from pathlib import Path

from ...core.events import new_event
from ...infrastructure.git.worktrees import invoke_workflow, repository_context
from ...infrastructure.persistence.project_registry import registry_for, RegistryError
from ...infrastructure.persistence.store import sync_registry_events
from ...infrastructure.system import multi_worktree as service


def start_action(root, args, requested_task_id):
    args.task_id = requested_task_id
    raw = ["start", *([requested_task_id] if requested_task_id else [])]
    for name in ("kind", "scope", "out_of_scope", "acceptance", "depends_on", "deliverable", "task_size", "git_commit", "track", "topic", "verification_profile", "without_stage_reason", "stage", "new_stage", "previous_stage"):
        value = getattr(args, name, None)
        if value is None:
            continue
        option = "--" + name.replace("_", "-")
        for item in value if isinstance(value, list) else [value]:
            raw.extend([option, json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item)])
    return service.start(root, args, raw)


def project_markdown(data):
    lines = ["# 项目并行任务", "", "|任务|状态|工作树|分支|当前步骤|最近更新|工作区|核实状态|", "|---|---|---|---|---|---|---|---|"]
    for item in data["tasks"]:
        values = [item["task_id"], item["status"], item["worktree"], item["branch"], item["current_step"], item["updated_at"], item["workspace"], "；".join(item["issues"]) or "正常"]
        lines.append("|" + "|".join(str(value).replace("|", "\\|").replace("\n", " ") for value in values) + "|")
    lines.extend(["", f"待同步事件：{data['pending_events']}；重叠对象：{len(data['conflicts'])}；迁移冲突：{len(data['migration_conflicts'])}", ""])
    for conflict in data["conflicts"]:
        lines.append(f"- {conflict['object']}：{conflict['kind']} / {conflict['status']}；来源=" + ", ".join(source["task_id"] + " @ " + source["branch"] for source in conflict["sources"]))
    return "\n".join(lines) + "\n"


def dispatch(runtime, args):
    root = runtime.ROOT
    if args.command == "worktree" and args.worktree_command == "enable":
        return True, service.enable(root, apply=args.apply)
    if args.command == "worktree" and args.worktree_command == "migrate":
        from ...infrastructure.system.worktree_migration import plan_migration, migrate_project
        return True, migrate_project(root) if args.apply else plan_migration(root)
    if args.command == "workflow":
        from ...infrastructure.system.workflow_views import project_snapshot, workflow_snapshot, close_workflow
        if args.workflow_command == "list":
            return True, project_snapshot(root)["workflows"]
        if args.workflow_command == "show":
            return True, workflow_snapshot(root, args.workflow_id)
        return True, close_workflow(root, args.workflow_id, cleanup=args.cleanup, confirmed=args.confirmed)
    registry = registry_for(root)
    relevant = args.command in {"worktree", "events", "conflict"} or (args.command in {"status", "context"} and args.all) or (args.command == "task" and (registry or args.task_command in {"pause", "resume", "heartbeat"} or getattr(args, "task_id", None)))
    if not relevant:
        return False, None
    if not registry:
        raise RegistryError("请先 worktree enable --check，再显式 --apply 启用")
    if args.command == "events":
        return True, {"sync_failures": sync_registry_events(registry), "pending_events": len(registry.pending())}
    if args.command == "worktree" and args.worktree_command == "remove":
        if registry.setting("unified"):
            from ...infrastructure.system.workflow_views import close_workflow
            task = registry.task(args.task_id)
            if not task:
                raise RegistryError("任务不存在")
            return True, close_workflow(root, task["workflow_id"], cleanup=True, confirmed=args.confirmed)
        return True, service.remove(root, args.task_id, confirmed=args.confirmed)
    if args.command == "task":
        task = registry.task(args.task_id) if args.task_id else registry.active(root)
        if not task:
            raise RegistryError("没有指定的未结束任务")
        task_id, path = task["task_id"], Path(task["path"])
        if args.task_command == "recover" and task["worktree_id"] == repository_context(root).worktree_id and path.resolve() != root.resolve():
            registry.relocate(root, task_id)
            task, path = registry.task(task_id), root
        if args.task_command == "heartbeat":
            registry.heartbeat(task_id, service.process_identity(args.owner_pid) if args.owner_pid else None)
            return True, {"task_id": task_id, "updated_at": registry.task(task_id)["updated_at"]}
        if args.task_command in {"pause", "resume"}:
            return True, service.change_lifecycle(root, task_id, "paused" if args.task_command == "pause" else "active", reason=args.reason or "显式恢复任务", next_steps=args.next)
        if args.task_command == "recover" and not json.loads(task["state_json"]).get("record"):
            return True, service.recover_start(root, task)
        if args.task_command == "abandon" and not path.is_dir():
            event = new_event("task.finished", branch=task["branch"], task_id=task_id, payload={"result": "abandoned", "note": args.reason, "evidence": "工作树路径失效；保留历史记录"}, timezone=runtime.config()["timezone"])
            registry.record_events(path, [event], worktree_id=task["worktree_id"])
            return True, {"task_id": task_id, "state": "abandoned", "pending_events": len(registry.pending())}
        if path.resolve() != root.resolve():
            forwarded = ["task", args.task_command]
            if getattr(args, "reason", None):
                forwarded.extend(["--reason", args.reason])
            if getattr(args, "skip_auto_commit", False):
                forwarded.append("--skip-auto-commit")
            child = invoke_workflow(path, forwarded)
            if child.returncode:
                raise RegistryError(child.stderr)
            return True, json.loads(child.stdout)
        if args.task_command == "recover":
            return True, runtime.task_recover(args)
        return True, runtime.task_abandon(args)
    data = service.snapshot(root)
    if args.command == "conflict":
        if args.conflict_command == "list":
            return True, data["conflicts"]
        conflict = next((item for item in data["conflicts"] if item["conflict_id"] == args.conflict_id), None)
        if not conflict:
            raise RegistryError("冲突已经变化或不存在，请重新查询")
        if args.conflict_command == "show":
            return True, conflict
        if not args.confirmed:
            raise RegistryError("内容取舍必须先由用户确认，再提交 --confirmed")
        if conflict["fingerprint"] != args.fingerprint:
            raise RegistryError("来源版本已变化，请重新比较并确认")
        decision = {"decision": args.decision, "reason": args.reason, "evidence": args.evidence, "confirmed_by": "user", "confirmed_at": service.now(), "fingerprint": args.fingerprint}
        # Durable audit is written before marking the conflict resolved.
        event = new_event("conflict.resolved", branch=runtime.current_branch(), task_id=(registry.active(root) or {}).get("task_id"), payload={"conflict_id": args.conflict_id, **decision}, timezone=runtime.config()["timezone"])
        registry.record_events(root, [event])
        registry.resolve(args.conflict_id, args.fingerprint, decision)
        sync_registry_events(registry)
        return True, {"conflict_id": args.conflict_id, "status": "resolved", "decision": decision, "next": "按确认方案通过 report/stage/catalog 追加内容修订；Git 合并另行确认"}
    return True, project_markdown(data) if args.command == "context" and args.format == "markdown" else data
