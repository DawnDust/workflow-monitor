"""Durable active-task sidecar independent from the rebuildable SQLite projection."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path


ACTIVE_TASK_FORMAT = 2
SUPPORTED_ACTIVE_TASK_FORMATS = (1, 2)


class ActiveTaskError(RuntimeError):
    pass


def active_task_path(state_dir: Path) -> Path:
    return state_dir / "active-task.json"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checksum(body: dict) -> str:
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def save_active_state(
    state_dir: Path,
    record: dict,
    *,
    state_updated: bool = False,
    decisions_added: int = 0,
    phase: str = "active",
    finish: dict | None = None,
) -> dict:
    body = {
        "format": ACTIVE_TASK_FORMAT,
        "phase": phase,
        "record": record,
        "checkpoint_recorded": bool(state_updated),
        "decisions_added": int(decisions_added),
        "finish": finish,
    }
    envelope = {**body, "checksum": _checksum(body)}
    atomic_write_json(active_task_path(state_dir), envelope)
    return envelope


def load_active_state(state_dir: Path) -> dict | None:
    path = active_task_path(state_dir)
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ActiveTaskError(f"活动任务 sidecar 无法读取: {exc}") from exc
    checksum = envelope.pop("checksum", None)
    if envelope.get("format") not in SUPPORTED_ACTIVE_TASK_FORMATS or checksum != _checksum(envelope):
        raise ActiveTaskError("活动任务 sidecar 校验失败；请运行 task recover")
    if envelope.get("format") == 1:
        envelope["checkpoint_recorded"] = bool(envelope.get("state_updated"))
    envelope["state_updated"] = bool(envelope.get("checkpoint_recorded"))
    envelope["checksum"] = checksum
    return envelope


def delete_active_state(state_dir: Path) -> None:
    active_task_path(state_dir).unlink(missing_ok=True)


def active_row_matches(database: Path, state_dir: Path) -> bool:
    state = load_active_state(state_dir)
    if state is None:
        return True
    record = state["record"]
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT task_id, started_at, branch, record_json, state_updated, decisions_added FROM active_tasks"
        ).fetchall()
    finally:
        connection.close()
    expected = (
        record["task_id"], record["started_at"], record["git"]["branch"],
        _canonical(record), int(state["checkpoint_recorded"]), int(state["decisions_added"]),
    )
    return len(rows) == 1 and tuple(rows[0]) == expected


def restore_active_row(database: Path, state_dir: Path, *, force: bool = False) -> bool:
    state = load_active_state(state_dir)
    if state is None:
        return False
    record = state["record"]
    connection = sqlite3.connect(database)
    try:
        with connection:
            rows = connection.execute("SELECT task_id FROM active_tasks").fetchall()
            if not force and len(rows) > 1:
                raise ActiveTaskError(
                    "检测到多个活动任务；拒绝自动覆盖，请运行 task recover"
                )
            if not force and rows and rows[0][0] != record["task_id"]:
                raise ActiveTaskError(
                    "活动任务 sidecar 与 SQLite 不一致；请运行 task recover"
                )
            if force:
                connection.execute("DELETE FROM active_tasks")
            connection.execute(
                """INSERT INTO active_tasks VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(task_id) DO UPDATE SET
                     started_at=excluded.started_at,
                     branch=excluded.branch,
                     record_json=excluded.record_json,
                     state_updated=excluded.state_updated,
                     decisions_added=excluded.decisions_added""",
                (
                    record["task_id"], record["started_at"], record["git"]["branch"],
                    _canonical(record), int(state["checkpoint_recorded"]), int(state["decisions_added"]),
                ),
            )
    finally:
        connection.close()
    return True


def task_age_seconds(state: dict, now: datetime) -> float | None:
    value = state.get("record", {}).get("started_at")
    if not value:
        return None
    clean = str(value).split("（", 1)[0].split("(", 1)[0]
    try:
        started = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return max(0.0, (now.replace(tzinfo=None) - started).total_seconds())
