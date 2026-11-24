"""Read-only search projection of one branch's saved events."""

import json
import sqlite3

from ...core.events import canonical_json
from .read_model import decode_item, rows
from .store import SCHEMA, apply_event


def search_records(model, events, *, integration_statuses=None, integration_reader=None):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SCHEMA)
        stages = {}
        for event in sorted(events, key=lambda row: row["occurred_at"]):
            apply_event(connection, event)
            stage = event["payload"].get("stage_id")
            if event["event_type"].startswith("stage.") and stage:
                stages[stage] = event
        records = model._search_index(connection, integration_statuses=integration_statuses, integration_reader=integration_reader)
        catalog = {item["item_id"]: decode_item(item) for item in rows(connection, "SELECT * FROM catalog_items")}
        archives = {row["event_id"]: row for row in rows(connection, "SELECT * FROM task_archive")}
        explorations = {row["event_id"]: row for row in model._exploration_rows(connection)}
        for item in records:
            if item["kind"] == "catalog":
                item["detail"] = catalog[item["record_id"]]
                continue
            saved = archives.get(item["record_id"]) if item["kind"] == "task" else explorations.get(item["record_id"])
            if saved:
                payload = json.loads(saved.get("payload_json") or "{}")
                evidence = payload.get("evidence") or saved.get("evidence") or []
                item["detail"] = {"note": payload.get("note") or item["summary"],
                                  "evidence": evidence if isinstance(evidence, list) else [evidence],
                                  "verification": payload.get("verification") or []}
        for item in rows(connection, "SELECT * FROM stages"):
            key = item["stage_id"]
            event = stages.get(key)
            if not event:
                continue
            item["evidence"] = json.loads(item.pop("evidence_json"))
            item["acceptance"] = json.loads(item.pop("acceptance_json"))
            records.append({
                "kind": "stage", "kind_label": "篇章", "target": "research-stages", "record_id": key,
                "occurred_at": event["occurred_at"], "branch": event["branch"], "title": item["title"],
                "summary": item.get("summary") or item.get("goal"), "status": item["status"], "status_code": item["status"],
                "archive_status_code": "unarchived", "merge_status_code": "not_applicable",
                "search_text": canonical_json(item), "detail": item,
            })
        return records
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("无法读取分支的搜索记录: " + str(exc)) from exc
    finally:
        connection.close()
