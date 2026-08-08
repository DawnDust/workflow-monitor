"""Pure projections used by the local Web Dashboard.

The functions in this module never read files or mutate workflow state.  They
only transform an existing Dashboard snapshot so the same rules can be tested
without a browser or WebView2 runtime.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

from .status_glossary import glossary_snapshot


EVIDENCE_RELATIONS = frozenset({"supports", "validates", "contradicts"})
RESULT_KINDS = frozenset({"output", "report"})


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


def exploration_comparison(snapshot: dict) -> list[dict]:
    records: list[dict] = []
    attempt = snapshot.get("attempt") or None
    if attempt:
        records.append({
            "id": attempt.get("attempt_id"), "branch": attempt.get("branch"),
            "goal": attempt.get("goal"), "hypothesis": attempt.get("hypothesis"),
            "stage": attempt.get("stage_id"), "current_step": attempt.get("current_step"),
            "evidence": attempt.get("evidence") or [], "result": attempt.get("conclusion"),
            "state": attempt.get("state") or "active", "is_current": True,
        })
    for item in snapshot.get("explorations") or []:
        evidence = item.get("evidence") or ""
        records.append({
            "id": item.get("event_id"), "branch": item.get("branch"),
            "goal": item.get("goal"), "hypothesis": None, "stage": None,
            "current_step": None, "evidence": [evidence] if evidence else [],
            "result": item.get("result"), "state": item.get("result") or "inconclusive",
            "is_current": False,
        })
    return records


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


def web_snapshot(snapshot: dict) -> dict:
    """Attach the three research views without altering the source snapshot."""
    projected = dict(snapshot)
    projected["research"] = {
        "graph": research_graph(snapshot),
        "explorations": exploration_comparison(snapshot),
        "evidence_matrix": evidence_matrix(snapshot),
    }
    projected["status_glossary"] = glossary_snapshot()
    return projected
