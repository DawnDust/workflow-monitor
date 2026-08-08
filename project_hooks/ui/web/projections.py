"""Pure projections used by the local Web Dashboard.

The functions in this module never read files or mutate workflow state.  They
only transform an existing Dashboard snapshot so the same rules can be tested
without a browser or WebView2 runtime.
"""

from __future__ import annotations

from collections import defaultdict

from .glossary import glossary_snapshot


EVIDENCE_RELATIONS = frozenset({"supports", "validates", "contradicts"})
RESULT_KINDS = frozenset({"output", "report"})


def evidence_matrix(snapshot: dict) -> dict:
    """Project only explicit evidence; missing cells deliberately mean unregistered."""
    items = {item["item_id"]: item for item in snapshot.get("catalog_items") or []}
    theories = [item for item in items.values() if item.get("kind") == "theory"]
    columns = {"paper": [], "experiment": [], "result": []}
    for item in items.values():
        kind = item.get("kind")
        if kind == "literature":
            columns["paper"].append(item)
        elif kind == "simulation":
            columns["experiment"].append(item)
        elif kind in RESULT_KINDS:
            columns["result"].append(item)

    evidence: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for relation in snapshot.get("catalog_relations") or []:
        relation_type = relation.get("relation_type")
        if relation_type not in EVIDENCE_RELATIONS:
            continue
        source, target = relation.get("source_id"), relation.get("target_id")
        if source in items and target in items:
            evidence[(source, target)].append(relation)
            evidence[(target, source)].append(relation)

    rows = []
    for theory in theories:
        cells = {}
        for column, candidates in columns.items():
            relations = [rel for candidate in candidates
                         for rel in evidence.get((theory["item_id"], candidate["item_id"]), [])]
            cells[column] = {
                "status": "registered" if relations else "unregistered",
                "label": ", ".join(sorted({rel["relation_type"] for rel in relations}))
                         if relations else "未登记",
                "relations": relations,
            }
        rows.append({"theory": theory, "cells": cells})
    return {"columns": columns, "rows": rows, "empty_label": "未登记"}


def _stage_for_time(stages: list[dict], occurred_at: object) -> dict | None:
    timestamp = str(occurred_at or "")
    if not timestamp:
        return None
    for stage in stages:
        started_at = str(stage.get("started_at") or "")
        finished_at = str(stage.get("finished_at") or "")
        if started_at and started_at <= timestamp and (not finished_at or timestamp <= finished_at):
            return stage
    return None


def research_stages(snapshot: dict) -> dict:
    """Combine stages, registered materials and attempts without mutating input."""
    source_stages = (snapshot.get("context") or {}).get("stages") or []
    ordered = sorted(
        source_stages,
        key=lambda item: (int(item.get("sequence") or 0), str(item.get("started_at") or "")),
        reverse=True,
    )
    stages = [{
        **stage,
        "work_summary": stage.get("summary") or stage.get("current_step") or "",
        "materials": [],
        "explorations": [],
    } for stage in ordered]
    by_id = {str(stage.get("stage_id")): stage for stage in stages if stage.get("stage_id")}

    task_stages: dict[str, dict] = {}
    for task_id, task in (snapshot.get("task_details") or {}).items():
        stage = _stage_for_time(stages, task.get("started_at") or task.get("finished_at"))
        if stage is not None:
            task_stages[str(task_id)] = stage

    unassigned_materials = []
    for item in snapshot.get("catalog_items") or []:
        material = {
            "item_id": item.get("item_id"),
            "kind": item.get("kind"),
            "kind_label": item.get("kind_label") or item.get("kind") or "资料",
            "title": item.get("title") or item.get("item_id") or "未命名资料",
            "status": item.get("status"),
            "path": item.get("path"),
            "task_id": item.get("task_id"),
            "created_at": item.get("created_at"),
        }
        stage = task_stages.get(str(item.get("task_id") or ""))
        if stage is None:
            stage = _stage_for_time(stages, item.get("created_at"))
        (stage["materials"] if stage is not None else unassigned_materials).append(material)

    unassigned_explorations = []
    for attempt in snapshot.get("attempts") or []:
        branch = str(attempt.get("branch") or "")
        exploration = {
            "attempt_id": attempt.get("attempt_id"),
            "goal": attempt.get("goal") or attempt.get("attempt_id") or "未命名探索",
            "track": attempt.get("track") or (branch.split("/", 1)[0] if "/" in branch else "stable"),
            "branch": branch,
            "state": attempt.get("state"),
            "conclusion": attempt.get("conclusion") or attempt.get("progress") or "",
            "created_at": attempt.get("created_at"),
            "stage_id": attempt.get("stage_id"),
        }
        stage = by_id.get(str(attempt.get("stage_id") or ""))
        if stage is None:
            stage = _stage_for_time(stages, attempt.get("created_at"))
        (stage["explorations"] if stage is not None else unassigned_explorations).append(exploration)

    current = None
    for stage in stages:
        stage["material_count"] = len(stage["materials"])
        stage["exploration_count"] = len(stage["explorations"])
        stage["is_current"] = stage.get("status") == "active"
        if current is None and stage["is_current"]:
            current = stage
    return {
        "items": stages,
        "current": current,
        "unassigned_materials": unassigned_materials,
        "unassigned_explorations": unassigned_explorations,
    }


def web_snapshot(snapshot: dict) -> dict:
    """Attach the active research views without altering the source snapshot."""
    projected = dict(snapshot)
    projected["research"] = {
        "stages": research_stages(snapshot),
        "evidence_matrix": evidence_matrix(snapshot),
    }
    projected["status_glossary"] = glossary_snapshot()
    return projected
