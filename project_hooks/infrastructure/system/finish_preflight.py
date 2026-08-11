"""Assemble task finish facts once for CLI and read-model consumers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ...core.lifecycle import finish_preflight
from .verification import verify_receipts, work_content_fingerprint


def assemble_finish_preflight(
    root: Path,
    record: dict,
    task_events: Iterable[dict],
    *,
    linked_stage_id: str | None,
    changed_paths: list[str],
    health_errors: list[str] | None = None,
    stage_review_result: str | None = None,
    checkpoint_override: str | None = None,
) -> dict:
    """Return the canonical finish preflight plus its verification and input facts."""
    events = list(task_events)
    checkpoints = [event for event in events if event["event_type"] == "task.checkpointed"]
    checkpoint_status = checkpoint_override or "missing"
    if checkpoint_override is None and checkpoints:
        recorded = checkpoints[-1].get("payload", {}).get("workspace_fingerprint")
        if not recorded:
            checkpoint_status = "legacy-unknown"
        elif recorded == work_content_fingerprint(root):
            checkpoint_status = "fresh"
        else:
            checkpoint_status = "stale"

    verification = verify_receipts(
        root,
        record["task_id"],
        profile=record["declaration"].get("verification_profile", "auto"),
        changed_paths=changed_paths,
    )
    stage_changed = any(
        event["event_type"] in {"stage.started", "stage.updated", "stage.state_changed"}
        and event.get("payload", {}).get("stage_id") == linked_stage_id
        for event in events
    )
    facts = {
        "checkpoint_status": checkpoint_status,
        "verification": verification,
        "health_errors": health_errors or [],
        "linked_stage_id": linked_stage_id,
        "stage_changed": stage_changed,
        "stage_review": stage_review_result,
        "decisions_added": sum(
            event["event_type"] == "decision.recorded" for event in events
        ),
        "project_updated": any(
            event["event_type"] == "project.profile_updated" for event in events
        ),
    }
    result = finish_preflight(facts)
    result["verification"] = verification
    result["facts"] = facts
    return result
