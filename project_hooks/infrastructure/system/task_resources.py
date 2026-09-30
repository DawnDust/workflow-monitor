"""Collect filesystem evidence for the pure task resource gate."""

from pathlib import Path

from ...core.resource_gate import normalize_references, task_resource_gate
from .resource_layout import catalog_consistency_issues
from .resource_layout import is_simulation_bundle


def inspect_task_resources(root, record, events, items, relations, folders, changed_paths):
    references = []
    for source in [record.get("declaration", {})] + [e["payload"] for e in events
                                                     if e["event_type"] in {"task.started", "task.checkpointed"}]:
        references.extend(source.get("depends_on") or [])
        references.extend(source.get("deliverable") or [])
    references = normalize_references(references)
    issues = catalog_consistency_issues(root, items, folders)
    baseline = record.get("resource_baseline")
    if baseline is None:
        baseline = next((e["payload"].get("resource_baseline", []) for e in events if e["event_type"] == "task.started"), [])
    gate = task_resource_gate(baseline, issues, items, relations,
                              events, changed_paths, references)
    by_id = {i["item_id"]: i for i in items}
    required = list(references) + ["catalog:" + value for value in gate["related_item_ids"]]
    seen = set()
    for reference in required:
        if reference in seen:
            continue
        seen.add(reference)
        prefix, value = reference.split(":", 1)
        item = by_id.get(value) if prefix == "catalog" else None
        path = item.get("path") if item else value if prefix == "path" else None
        # A pathless catalog record may be a valid logical resource; an unknown ID cannot.
        if item and not path:
            continue
        valid = False
        if path:
            target = root / path
            try:
                target.resolve().relative_to(root.resolve())
                valid = (target.is_dir() if is_simulation_bundle(item) else target.is_file()) if item else target.exists()
            except ValueError:
                pass
        if not valid:
            gate["blockers"].append({"code": "TASK_RESOURCE_MISSING", "path": path or "",
                                     "item_id": value if prefix == "catalog" else None,
                                     "message": f"本任务交付物或直接依赖缺失: {reference}",
                                     "evidence": {"reference": reference}, "hard": False})
    return gate
