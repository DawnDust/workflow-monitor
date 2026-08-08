"""Pure projections used by the local Web Dashboard.

The functions in this module never read files or mutate workflow state.  They
only transform an existing Dashboard snapshot so the same rules can be tested
without a browser or WebView2 runtime.
"""

from __future__ import annotations

from collections import defaultdict, deque
import re
from typing import Iterable

from .glossary import glossary_snapshot


EVIDENCE_RELATIONS = frozenset({"supports", "validates", "contradicts"})
RESULT_KINDS = frozenset({"output", "report"})
KEY_FILE_TAGS = frozenset({"core", "map"})


def _node(identifier: str, kind: str, label: str, **metadata: object) -> dict:
    return {"id": identifier, "kind": kind, "label": label, **metadata}


def research_graph(snapshot: dict) -> dict:
    """Build explicit semantic and derived process edges from current schema."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    for item in snapshot.get("catalog_items") or []:
        node_id = f"catalog:{item['item_id']}"
        nodes[node_id] = _node(
            node_id, item.get("kind") or "other", item.get("title") or item["item_id"],
            record_id=item["item_id"], target="catalog", status=item.get("status"),
            branch=item.get("branch"), task_id=item.get("task_id"),
        )

    task_details = snapshot.get("task_details") or {}
    for task_id, task in task_details.items():
        node_id = f"task:{task_id}"
        nodes[node_id] = _node(
            node_id, "task", task.get("goal") or task_id, record_id=task_id,
            target="task", status=task.get("status") or task.get("result"),
            branch=task.get("branch"),
        )

    current_attempt = snapshot.get("attempt") or None
    attempts = [current_attempt] if current_attempt else []
    for attempt in attempts:
        attempt_id = attempt.get("attempt_id") or attempt.get("branch")
        if not attempt_id:
            continue
        node_id = f"attempt:{attempt_id}"
        nodes[node_id] = _node(
            node_id, "attempt", attempt.get("goal") or attempt_id,
            record_id=attempt_id, target="attempt", status=attempt.get("state"),
            branch=attempt.get("branch"), stage_id=attempt.get("stage_id"),
        )

    for exploration in snapshot.get("explorations") or []:
        record_id = exploration.get("event_id") or exploration.get("branch")
        if not record_id:
            continue
        node_id = f"exploration:{record_id}"
        nodes[node_id] = _node(
            node_id, "exploration", exploration.get("goal") or exploration.get("branch") or record_id,
            record_id=record_id, target="exploration", status=exploration.get("result"),
            branch=exploration.get("branch"), task_id=exploration.get("task_id"),
        )

    for relation in snapshot.get("catalog_relations") or []:
        source = f"catalog:{relation.get('source_id')}"
        target = f"catalog:{relation.get('target_id')}"
        if source not in nodes or target not in nodes:
            continue
        edges.append({
            "id": f"explicit:{relation.get('relation_id') or len(edges)}",
            "source": source, "target": target,
            "relation": relation.get("relation_type") or "related",
            "provenance": "explicit", "note": relation.get("note") or "",
        })

    # Process links are projections, not scientific evidence.  They are kept
    # visually and semantically separate from catalog relations.
    for node in list(nodes.values()):
        if node["kind"] in {"task", "stage"}:
            continue
        task_id = node.get("task_id")
        if task_id and f"task:{task_id}" in nodes:
            edges.append({
                "id": f"derived:task:{node['id']}:{task_id}", "source": node["id"],
                "target": f"task:{task_id}", "relation": "task_id",
                "provenance": "derived", "note": "由 task_id 投影",
            })

    branch_groups: dict[str, list[str]] = defaultdict(list)
    for node in nodes.values():
        if node.get("branch") and node["kind"] in {"attempt", "exploration"}:
            branch_groups[str(node["branch"])].append(node["id"])
    for members in branch_groups.values():
        for left, right in zip(members, members[1:]):
            edges.append({
                "id": f"derived:branch:{left}:{right}", "source": left, "target": right,
                "relation": "branch", "provenance": "derived", "note": "由 branch 投影",
            })

    return {"nodes": list(nodes.values()), "edges": edges}


def bfs_neighborhood(graph: dict, start_id: str, depth: int = 1) -> dict:
    """Return a bounded undirected neighborhood for graph focus mode."""
    if depth < 0:
        raise ValueError("depth must be non-negative")
    node_ids = {node["id"] for node in graph.get("nodes") or []}
    if start_id not in node_ids:
        return {"nodes": [], "edges": []}
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.get("edges") or []:
        adjacency[edge["source"]].add(edge["target"])
        adjacency[edge["target"]].add(edge["source"])
    seen = {start_id}
    queue = deque([(start_id, 0)])
    while queue:
        current, distance = queue.popleft()
        if distance >= depth:
            continue
        for neighbor in adjacency[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, distance + 1))
    return {
        "nodes": [node for node in graph.get("nodes") or [] if node["id"] in seen],
        "edges": [edge for edge in graph.get("edges") or []
                  if edge["source"] in seen and edge["target"] in seen],
    }


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


def version_timeline(snapshot: dict) -> dict:
    """Project a sparse, connected evolution map from existing event data."""
    events = sorted(snapshot.get("events") or [], key=lambda item: str(item.get("occurred_at") or ""))
    versions: dict[str, dict] = {}
    task_versions: dict[str, str] = {}
    current_version: str | None = None
    for event in events:
        payload = event.get("payload") or {}
        explicit = payload.get("main_goal_version")
        if explicit:
            current_version = str(explicit)
            record = versions.setdefault(current_version, {
                "id": f"version:{current_version}", "version": current_version,
                "first_at": event.get("occurred_at"), "last_at": event.get("occurred_at"),
                "initial_goal": None, "final_judgment": None, "latest_status": None,
                "task_ids": set(), "decision_count": 0,
            })
            if payload.get("goal") and not record["initial_goal"]:
                record["initial_goal"] = payload["goal"]
            if payload.get("judgment"):
                record["final_judgment"] = payload["judgment"]
            if payload.get("status"):
                record["latest_status"] = payload["status"]
        if current_version:
            versions[current_version]["last_at"] = event.get("occurred_at")
        task_id = event.get("task_id")
        if task_id and current_version:
            task_versions[str(task_id)] = current_version
            versions[current_version]["task_ids"].add(str(task_id))
            if event.get("event_type") == "decision.recorded":
                versions[current_version]["decision_count"] += 1

    def version_key(value: dict) -> tuple[int, str]:
        match = re.search(r"(\d+)", value["version"])
        return (int(match.group(1)) if match else 10**9, value["version"])

    version_rows = sorted(versions.values(), key=version_key)
    for row in version_rows:
        row["task_count"] = len(row.pop("task_ids"))
        row["exploration_count"] = 0
        row["file_count"] = 0

    by_version = {row["version"]: row for row in version_rows}
    exploration_rows: list[dict] = []
    unassigned_explorations: list[dict] = []
    for item in snapshot.get("explorations") or []:
        version = task_versions.get(str(item.get("task_id") or ""))
        if not version:
            occurred = str(item.get("occurred_at") or "")
            candidates = [row for row in version_rows if str(row.get("first_at") or "") <= occurred]
            version = candidates[-1]["version"] if candidates else None
        record = {
            "id": f"exploration:{item.get('event_id') or item.get('branch')}",
            "record_id": item.get("event_id"), "version": version,
            "label": item.get("goal") or item.get("branch") or "探索",
            "branch": item.get("branch"), "task_id": item.get("task_id"),
            "occurred_at": item.get("occurred_at"), "result": item.get("result"),
        }
        if version in by_version:
            exploration_rows.append(record)
            by_version[version]["exploration_count"] += 1
        else:
            unassigned_explorations.append(record)

    relations = snapshot.get("catalog_relations") or []
    related_ids = {str(value) for relation in relations
                   for value in (relation.get("source_id"), relation.get("target_id")) if value}
    exploration_tasks = {str(item.get("task_id")) for item in exploration_rows if item.get("task_id")}
    exploration_branches = {str(item.get("branch")) for item in exploration_rows if item.get("branch")}
    file_rows: list[dict] = []
    unassigned_files: list[dict] = []
    for item in snapshot.get("catalog_items") or []:
        tags = {str(tag).casefold() for tag in item.get("tags") or []}
        item_id = str(item.get("item_id") or "")
        task_id = str(item.get("task_id") or "")
        branch = str(item.get("branch") or "")
        linked = item_id in related_ids or task_id in exploration_tasks or branch in exploration_branches
        if not item.get("path") or item.get("status") == "missing" or not (linked or tags & KEY_FILE_TAGS):
            continue
        version = task_versions.get(task_id)
        if not version and branch in exploration_branches:
            version = next((row["version"] for row in exploration_rows if row.get("branch") == branch), None)
        record = {
            "id": f"file:{item_id}", "item_id": item_id, "version": version,
            "label": item.get("title") or item_id, "path": item.get("path"),
            "kind": item.get("kind"), "tags": list(item.get("tags") or []),
            "relation_count": sum(item_id in {str(rel.get("source_id")), str(rel.get("target_id"))} for rel in relations),
        }
        if version in by_version:
            file_rows.append(record)
            by_version[version]["file_count"] += 1
        else:
            unassigned_files.append(record)

    displayed_files: list[dict] = []
    for version in (row["version"] for row in version_rows):
        members = [item for item in file_rows if item["version"] == version]
        if len(members) <= 5:
            displayed_files.extend(members)
        else:
            displayed_files.append({
                "id": f"file-group:{version}", "version": version,
                "label": f"关键文件 {len(members)}", "kind": "file-group", "items": members,
            })

    edges: list[dict] = []
    for left, right in zip(version_rows, version_rows[1:]):
        edges.append({"source": left["id"], "target": right["id"], "kind": "version"})
    for item in exploration_rows:
        edges.append({"source": f"version:{item['version']}", "target": item["id"], "kind": "derived"})
    for item in displayed_files:
        edges.append({"source": f"version:{item['version']}", "target": item["id"], "kind": "derived"})
    visible_files = {item.get("item_id"): item["id"] for item in displayed_files if item.get("item_id")}
    for relation in relations:
        source, target = visible_files.get(relation.get("source_id")), visible_files.get(relation.get("target_id"))
        if source and target:
            edges.append({"source": source, "target": target, "kind": "explicit",
                          "relation": relation.get("relation_type")})
    return {
        "versions": version_rows, "explorations": exploration_rows,
        "files": displayed_files, "edges": edges,
        "unassigned": {"explorations": unassigned_explorations, "files": unassigned_files},
    }


def web_snapshot(snapshot: dict) -> dict:
    """Attach the active research views without altering the source snapshot."""
    projected = dict(snapshot)
    projected["research"] = {
        "timeline": version_timeline(snapshot),
        "evidence_matrix": evidence_matrix(snapshot),
    }
    projected["status_glossary"] = glossary_snapshot()
    return projected
