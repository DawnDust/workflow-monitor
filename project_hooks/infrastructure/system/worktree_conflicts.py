"""Inspect per-task file and research object versions without choosing a winner."""

from __future__ import annotations

import hashlib
import json
import tempfile
import subprocess
from pathlib import Path

from ...core.events import canonical_json
from ..git import client as git
from ..persistence.project_registry import OCCUPIED, RegistryError
from ..persistence.store import load_events


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def task_changes(task, row, pending_events=()):
    root = Path(task["worktree"])
    record = json.loads(row["state_json"]).get("record", {})
    baseline = record.get("baseline", {})
    base_commit = record.get("git", {}).get("base_head")
    names = set(baseline)
    if base_commit:
        result = git.run(root, ["diff", "--name-only", "-z", base_commit], check=False)
        if result.returncode:
            raise RegistryError("无法读取任务基线差异")
        names.update(filter(None, result.stdout.split("\0")))
    names.update(filter(None, git.run(root, ["ls-files", "--others", "--exclude-standard", "-z"]).stdout.split("\0")))
    changes = {}
    for name in names:
        if name in {"maintenance/events.jsonl", ".git", "workflow-monitor.exe"} or name.startswith((".worktrees/", ".project_hooks/", "__pycache__/")):
            continue
        target = root / name
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        current = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        previous = baseline.get(name, {}).get("sha256")
        if current != previous:
            changes["path:" + name] = {"hash": current, "previous_hash": previous, "path": name, "action": "deleted" if current is None else "modified" if previous else "added"}
    config = json.loads((root / ".codex/project-maintenance-workflow.json").read_text(encoding="utf-8"))
    events = load_events(root / config.get("maintenance_store", {}).get("journal", "maintenance/events.jsonl"))
    seen = {event["event_id"] for event in events}
    events.extend(event for event in pending_events if event["event_id"] not in seen)
    catalog = {}
    for event in events:
        payload, kind = event["payload"], event["event_type"]
        if kind == "catalog.item_upserted":
            catalog.setdefault(payload["item_id"], {}).update(payload)
        if event.get("task_id") not in task.get("workflow_tasks", [task["task_id"]]):
            continue
        identifier = payload.get("stage_id") if kind.startswith("stage.") else payload.get("item_id") if kind.startswith("catalog.") else None
        if identifier:
            key = ("stage:" if kind.startswith("stage.") else "catalog:") + identifier
            entry = changes.setdefault(key, {"events": [], "fields": {}, "event_ids": []})
            entry["events"].append(event)
            entry["event_ids"].append(event["event_id"])
            entry["fields"].update(payload)
            entry["hash"] = digest(entry["events"])
    for identifier, item in catalog.items():
        resource_path = (item.get("path") or "").rstrip("/")
        touched = {key: value["hash"] for key, value in changes.items() if key.startswith("path:") and resource_path and (value["path"] == resource_path or value["path"].startswith(resource_path + "/"))}
        if touched:
            entry = changes.setdefault("catalog:" + identifier, {"event_ids": [], "fields": dict(item)})
            entry["file_versions"] = touched
            entry["hash"] = digest({"fields": entry["fields"], "files": touched})
    for reference in record.get("declaration", {}).get("deliverable", []):
        if reference not in changes:
            changes[reference] = {"hash": None, "declared_write": True}
    for reference in record.get("declaration", {}).get("depends_on", []):
        if reference not in changes:
            changes[reference] = {"hash": None, "read_dependency": True}
    for key, entry in changes.items():
        if key.startswith(("stage:", "catalog:")) and "fields" in entry:
            entry["content_hash"] = digest({"fields": entry["fields"], "files": entry.get("file_versions", {})})
    return changes


def file_merge_status(sources):
    # A clean textual merge is advisory: research meaning is reviewed separately.
    if len(sources) != 2:
        return "compare_versions"
    left, right = sources
    if left["version"]["previous_hash"] != right["version"]["previous_hash"]:
        return "different_baselines"
    if any(source["version"]["hash"] is None for source in sources):
        return "delete_modify_conflict"
    root = Path(left["worktree"])
    name = left["version"]["path"]
    base = git.run(root, ["show", f"{left['baseline_commit']}:{name}"], check=False) if left["baseline_commit"] else None
    base_text = base.stdout if base and base.returncode == 0 else ""
    # Git's text gateway decodes with replacement. A byte hash must still match
    # the captured baseline before we use that text as a merge ancestor.
    candidates = {hashlib.sha256(value.encode("utf-8")).hexdigest() for value in (base_text, base_text.replace("\r\n", "\n").replace("\n", "\r\n"))}
    if left["version"]["previous_hash"] and left["version"]["previous_hash"] not in candidates:
        return "compare_versions"
    with tempfile.TemporaryDirectory(prefix="workflow-compare-") as directory:
        paths = [Path(directory) / name for name in ("ours", "base", "theirs")]
        try:
            ours_text = (root / name).read_bytes().decode("utf-8")
            theirs_text = (Path(right["worktree"]) / right["version"]["path"]).read_bytes().decode("utf-8")
            if "\0" in ours_text + theirs_text + base_text:
                return "binary_or_unavailable"
            paths[0].write_text(ours_text.replace("\r\n", "\n"), encoding="utf-8", newline="")
            paths[1].write_text(base_text, encoding="utf-8", newline="")
            paths[2].write_text(theirs_text.replace("\r\n", "\n"), encoding="utf-8", newline="")
            result = git.run(root, ["merge-file", "-p", *(str(path) for path in paths)], check=False)
        except (OSError, UnicodeError):
            return "compare_versions"
    return "clean_text_merge" if result.returncode == 0 else "text_conflict" if result.returncode == 1 else "binary_or_unavailable"


def detect_conflicts(registry, tasks):
    objects, result = {}, []
    pending = {}
    for item in registry.pending():
        pending.setdefault(item["worktree_id"], []).append(json.loads(item["event_json"]))
    groups = {}
    for task in tasks:
        groups.setdefault(task.get("workflow_id") or task["branch"], []).append(task)
    workflows = {item["workflow_id"]: item for item in registry.workflows()}
    for workflow_id, group in groups.items():
        workflow = workflows.get(workflow_id)
        if workflow and workflow["status"] != "open":
            continue
        task = dict(group[-1], workflow_tasks=[item["task_id"] for item in group])
        # Completed branches remain relevant until their worktree is removed.
        if not Path(task["worktree"]).is_dir():
            continue
        try:
            first = registry.task(group[0]["task_id"])
            if workflow and workflow["base_commit"]:
                task["baseline_commit"] = workflow["base_commit"]
            changes = task_changes(task, first, pending.get(task["worktree_id"], []))
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            task["issues"].append("冲突范围待核实: " + str(exc))
            task["verification"] = "状态待核实"
            continue
        for key, version in changes.items():
            source_ids = list(dict.fromkeys(event["task_id"] for event in version.get("events", [])))
            objects.setdefault(key, []).append({"task_id": (source_ids or [task["task_id"]])[-1],
                                                "workflow_id": workflow_id, "task_ids": source_ids or task["workflow_tasks"],
                                                "worktree": task["worktree"], "branch": task["branch"],
                                                "baseline_commit": task["baseline_commit"], "version": version})
    for key, sources in objects.items():
        if len(sources) < 2:
            continue
        if all(source["version"].get("read_dependency") for source in sources):
            continue
        potential = any(source["version"].get("declared_write") or source["version"].get("read_dependency") for source in sources)
        hashes = {source["version"].get("content_hash", source["version"].get("hash")) for source in sources}
        identical = len(hashes) == 1 and not potential
        conflict_id = digest([key, sorted(source["task_id"] for source in sources)])[:24]
        fingerprint = digest(sources)
        resolution = registry.resolution(conflict_id)
        research = key.startswith(("stage:", "catalog:"))
        comparison = None if research or identical or potential else file_merge_status(sources)
        result.append({"conflict_id": conflict_id, "object": key, "kind": "potential_overlap" if potential else "identical" if identical else "research_review" if research else comparison if comparison in {"text_conflict", "delete_modify_conflict"} else "overlapping_file", "text_comparison": comparison, "status": "mechanical" if identical else "resolved" if resolution and resolution["fingerprint"] == fingerprint else "pending", "fingerprint": fingerprint, "sources": sources, "decision": json.loads(resolution["decision_json"]) if resolution and resolution["fingerprint"] == fingerprint else None, "previous_decision_stale": bool(resolution and resolution["fingerprint"] != fingerprint), "requires_user_decision": research and not identical and not potential})
    return result
