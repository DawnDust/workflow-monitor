"""On-demand search across the registered workspaces of one Git clone."""

import json
from hashlib import sha256
from pathlib import Path

from ...core.events import canonical_json
from ..git import client as git
from ..git.worktrees import list_worktrees
from ..persistence.project_registry import registry_for
from ..persistence.search_projection import search_records
from ..persistence.store import load_events
from .workflow_views import history_entry
from .multi_worktree import settings


def file_version(path):
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size
    except FileNotFoundError:
        return None


def task_search_record(detail, branch):
    task_id = detail["task_id"]
    stamp = detail.get("finished_at") or detail.get("updated_at") or detail.get("started_at") or ""
    status = detail["status"]
    scope = detail.get("scope") or task_id
    latest = detail.get("checkpoint", {}).get("current_step") or detail.get("note") or ""
    return {
        "kind": "task", "kind_label": "任务", "target": "history", "record_id": task_id,
        "occurred_at": stamp, "title": scope, "summary": latest or detail.get("result") or status,
        "branch": branch, "status": status, "status_code": status, "task_ids": [task_id],
        "archive_status_code": "unarchived", "merge_status_code": "not_applicable",
        "search_text": " ".join(str(value or "") for value in (task_id, scope, latest, branch, status, *detail["evidence"])),
        "detail": detail,
    }


def merge_search_records(sources):
    records = {}
    for workspace, item in sources:
        material = {key: value for key, value in item.items() if key not in {"branch"}}
        # Each projection has its own checkout provenance. Only equal content
        # with the same record identity can share a row.
        key = sha256(canonical_json(material).encode("utf-8")).hexdigest()
        record = records.get(key)
        if record is None:
            record = records[key] = {**item, "search_key": key, "sources": []}
        record["sources"].append(workspace)
    for record in records.values():
        source_branches = {source["branch"] for source in record["sources"]}
        record["branches"] = sorted(source_branches | {record["branch"]})
        record["workspaces"] = sorted({source["workflow_id"] for source in record["sources"]})
        if all(source["archived"] for source in record["sources"]):
            record["archive_status_code"] = "archived"
    return sorted(records.values(), key=lambda item: (item["occurred_at"], item["record_id"]), reverse=True)


class ProjectSearch:
    def __init__(self, root, model):
        self.root = root
        self.model = model
        self.version = None
        self.result = None

    def sources(self, registry):
        main = {"workflow_id": "main", "branch": git.current_branch(self.root) or "main",
                "path": str(self.root), "status": "open"}
        workflows = [main]
        if registry:
            workflows.extend(row for row in registry.workflows() if row["workflow_id"] != "main")
        return workflows

    def load(self, *, force=False):
        registry = registry_for(self.root)
        workflows = self.sources(registry)
        worktrees = {tree["path"]: tree for tree in list_worktrees(self.root)} if registry else {}
        refs = git.run(self.root, ["for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads", "refs/remotes"],
                       check=False).stdout
        versions = []
        journals = []
        for item in workflows:
            branch = item.get("archive_branch") or item["branch"]
            path = Path(item["path"]) if item.get("path") else None
            listed = worktrees.get(str(path))
            local = bool(path and path.is_dir() and (item["workflow_id"] == "main" or listed and listed["branch"] == branch))
            journal = None
            if local:
                try:
                    journal = path / settings(path)["maintenance_store"]["journal"]
                except (OSError, ValueError, KeyError):
                    pass
            versions.append((item["workflow_id"], branch, item["status"], str(journal), file_version(journal) if journal else None))
            journals.append(journal)
        if registry:
            versions.extend(file_version(path) for path in (registry.path, registry.path.with_name(registry.path.name + "-wal")))
        signature = (refs, tuple(versions))
        if not force and self.result is not None and self.version == signature:
            return self.result

        task_rows = {item["task_id"]: item for item in registry.tasks()} if registry else {}
        events_by_branch = {}
        if registry:
            for event in registry.events():
                events_by_branch.setdefault(event["branch"], []).append(event)
        sources, scopes, warnings = [], [], []
        all_events = {}
        for workflow, journal in zip(workflows, journals):
            branch = workflow.get("archive_branch") or workflow["branch"]
            scope = {
                "workflow_id": workflow["workflow_id"], "branch": branch,
                "label": workflow.get("topic") or branch, "archived": workflow["status"] == "archived",
            }
            scopes.append(scope)
            try:
                events = self.events(workflow, journal)
            except (OSError, RuntimeError, ValueError) as exc:
                warnings.append({"branch": branch, "message": str(exc)})
                events = []
            combined = {event["event_id"]: event for event in events}
            for origin in {workflow["branch"], branch}:
                for event in events_by_branch.get(origin, []):
                    combined.setdefault(event["event_id"], event)
            for key, event in combined.items():
                all_events.setdefault(key, event)
            sources.append((scope, workflow, list(combined.values())))

        histories = {}
        for event in sorted(all_events.values(), key=lambda item: item["occurred_at"]):
            if event.get("task_id"):
                histories.setdefault(event["task_id"], []).append(event)
        archived = {row["branch"] for row in workflows if row["status"] == "archived"}
        task_records = {}
        for task_id in task_rows.keys() | histories.keys():
            row, history = task_rows.get(task_id), histories.get(task_id, [])
            if not row and not any(event["event_type"] in {"task.started", "task.finished"} for event in history):
                continue
            branch = row["branch"] if row else history[0]["branch"]
            detail = history_entry(task_id, row, history)
            if row:
                detail["updated_at"] = row["updated_at"]
            elif detail["status"] == "historical" and any(event["event_type"] == "task.started" for event in history):
                detail["status"] = "active"
            record = task_search_record(detail, branch)
            if branch in archived:
                record["archive_status_code"] = "archived"
            task_records[task_id] = record

        all_records = []
        for scope, workflow, events in sources:
            try:
                records = search_records(self.model, events)
            except RuntimeError as exc:
                warnings.append({"branch": scope["branch"], "message": str(exc)})
                records = []
            visible = {event.get("task_id") for event in events}
            visible.update(task_id for task_id, row in task_rows.items()
                           if row.get("workflow_id") == workflow["workflow_id"] or
                           (not row.get("workflow_id") and row["branch"] == workflow["branch"]))
            visible.intersection_update(task_records)
            records = [record for record in records if record["kind"] != "task" or not visible.intersection(record["task_ids"])]
            records.extend(task_records[task_id] for task_id in sorted(visible))
            all_records.extend((scope, record) for record in records)
        self.result = {"branch": workflows[0]["branch"], "search_index": merge_search_records(all_records),
                       "search_scopes": scopes, "search_warnings": warnings}
        self.version = signature if not warnings else None
        return self.result

    def events(self, workflow, journal):
        if journal:
            return load_events(journal)
        branch = workflow.get("archive_branch") or workflow["branch"]
        relative = settings(self.root)["maintenance_store"]["journal"]
        result = git.run(self.root, ["show", f"refs/heads/{branch}:{relative}"], check=False)
        if result.returncode:
            raise RuntimeError("无法读取保存的分支审计记录")
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

