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
