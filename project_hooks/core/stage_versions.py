"""Branch-local stage versions; never choose a winner by timestamp."""

import hashlib
import json


def stage_versions(events: list[dict], source: str) -> list[dict]:
    stages = {}
    seen = set()
    for event in events:
        if event.get("event_id") in seen:
            continue
        seen.add(event.get("event_id"))
        kind = event.get("event_type")
        payload = event.get("payload") or {}
        stage_id = payload.get("stage_id")
        if kind == "stage.started":
            stages[stage_id] = {**payload, "status": "active", "evidence": [], "event_ids": []}
        stage = stages.get(stage_id)
        if not stage or kind not in {"stage.started", "stage.updated", "stage.revised", "stage.state_changed"}:
            continue
        for key, value in payload.items():
            if key != "evidence":
                stage[key] = value
        for evidence in payload.get("evidence", []):
            if evidence not in stage["evidence"]:
                stage["evidence"].append(evidence)
        stage["event_ids"].append(event["event_id"])
    return [{**stage, "sources": [source], "version": hashlib.sha256(
        json.dumps(stage["event_ids"]).encode()).hexdigest()[:16]} for stage in stages.values()]


def merge_versions(versions: list[dict]) -> list[dict]:
    result = {}
    for stage in versions:
        key = (stage["stage_id"], stage["version"])
        if key not in result:
            result[key] = {**stage, "sources": list(stage["sources"])}
        else:
            result[key]["sources"] = sorted(set(result[key]["sources"] + stage["sources"]))
    return list(result.values())


def conflicting_stages(versions: list[dict]) -> list[str]:
    conflicts = set()
    for left in versions:
        for right in versions:
            if left["stage_id"] != right["stage_id"]:
                continue
            a, b = set(left["event_ids"]), set(right["event_ids"])
            if not a <= b and not b <= a:
                conflicts.add(left["stage_id"])
    return sorted(conflicts)


def annotate_parallel_conflicts(context: dict, project: dict) -> None:
    """Keep integrated divergent judgments visible until a version-bound decision."""
    pending = [item for item in project.get("conflicts", []) if item.get("requires_user_decision") and item.get("status") == "pending"]
    context["parallel_conflicts"] = pending
    local_versions = {item["stage_id"]: set(item["event_ids"]) for item in context.get("stage_versions", []) if "working-tree" in item.get("sources", [])}
    for stage in context.get("stages", []):
        conflicts = [item for item in pending if item["object"] == "stage:" + stage["stage_id"]]
        if not conflicts:
            continue
        stage["parallel_versions"] = [source for item in conflicts for source in item["sources"]]
        source_events = [set(source["version"].get("event_ids", [])) for source in stage["parallel_versions"]]
        if source_events and all(source_events) and set().union(*source_events) <= local_versions.get(stage["stage_id"], set()):
            stage["unconfirmed_summary"] = stage.get("summary")
            stage["summary"] = "并行研究修订待用户确认；请比较来源版本"
            stage["judgment_status"] = "pending_user_decision"
            for state_name in ("state", "overview_state"):
                state = context.get(state_name)
                if state and state.get("judgment") and state.get("judgment_status") != "pending_user_decision":
                    state["unconfirmed_judgment"] = state["judgment"]
                    state["judgment"] = "并行研究修订待用户确认"
                    state["judgment_status"] = "pending_user_decision"
