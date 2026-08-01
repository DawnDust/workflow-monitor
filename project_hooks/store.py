"""SQLite projections backed by a Git-friendly JSONL event journal."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as fixed_timezone
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .active_task import ActiveTaskError, active_row_matches, active_task_path, restore_active_row
from .transaction import mutation_lock


SCHEMA_VERSION = 3
SUPPORTED_EVENT_SCHEMA_VERSIONS = (1, 2, 3)


class StoreError(RuntimeError):
    pass


def resolve_timezone(name: str):
    """Resolve configured time zones even in minimal Windows Python installs."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Shanghai":
            return fixed_timezone(timedelta(hours=8), name)
        if name in {"UTC", "Etc/UTC"}:
            return fixed_timezone.utc
        raise StoreError(
            f"系统缺少时区数据 {name}；请安装 tzdata 或改用 Asia/Shanghai/UTC"
        )


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def journal_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


def new_event(
    event_type: str,
    *,
    branch: str,
    task_id: str | None,
    payload: dict,
    timezone: str,
    event_id: str | None = None,
    occurred_at: str | None = None,
) -> dict:
    now = datetime.now(resolve_timezone(timezone))
    return {
        "event_id": event_id or f"{now.strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex}",
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "occurred_at": occurred_at or now.strftime(f"%Y-%m-%d %H:%M:%S（{timezone}）"),
        "branch": branch,
        "task_id": task_id,
        "payload": payload,
    }


def validate_event(event: object, *, line: int | None = None) -> dict:
    where = f"第 {line} 行" if line else "事件"
    if not isinstance(event, dict):
        raise StoreError(f"{where}不是 JSON 对象")
    required = {"event_id", "schema_version", "event_type", "occurred_at", "branch", "task_id", "payload"}
    if set(event) != required:
        raise StoreError(f"{where}字段不符合事件协议")
    if event["schema_version"] not in SUPPORTED_EVENT_SCHEMA_VERSIONS:
        supported = ", ".join(str(item) for item in SUPPORTED_EVENT_SCHEMA_VERSIONS)
        raise StoreError(f"{where} schema_version={event['schema_version']}，当前支持 {supported}")
    if not all(isinstance(event[key], str) and event[key] for key in ("event_id", "event_type", "occurred_at", "branch")):
        raise StoreError(f"{where}包含空或非法标识字段")
    if event["task_id"] is not None and not isinstance(event["task_id"], str):
        raise StoreError(f"{where} task_id 必须是字符串或 null")
    if not isinstance(event["payload"], dict):
        raise StoreError(f"{where} payload 必须是对象")
    return event


def load_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events: list[dict] = []
    seen: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            event = validate_event(json.loads(raw), line=line_number)
        except json.JSONDecodeError as exc:
            raise StoreError(f"事件日志第 {line_number} 行不是合法 JSON: {exc.msg}") from exc
        encoded = canonical_json(event)
        previous = seen.get(event["event_id"])
        if previous is not None:
            if previous != encoded:
                raise StoreError(f"事件 ID 内容冲突: {event['event_id']}")
            continue
        seen[event["event_id"]] = encoded
        events.append(event)
    return events


@contextmanager
def event_lock(state_dir: Path, timeout: float = 10.0) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = state_dir / "maintenance.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()} {time.time()}".encode("ascii"))
            os.close(descriptor)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise StoreError("维护数据正被另一个进程占用")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def append_events(path: Path, state_dir: Path, additions: Iterable[dict]) -> list[dict]:
    additions = [validate_event(event) for event in additions]
    with event_lock(state_dir):
        events = load_events(path)
        by_id = {event["event_id"]: canonical_json(event) for event in events}
        for event in additions:
            encoded = canonical_json(event)
            old = by_id.get(event["event_id"])
            if old is not None and old != encoded:
                raise StoreError(f"事件 ID 内容冲突: {event['event_id']}")
            if old is None:
                events.append(event)
                by_id[event["event_id"]] = encoded
        validate_projection(events)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix="events-", suffix=".jsonl", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                for event in events:
                    handle.write(canonical_json(event) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return events


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL, branch TEXT NOT NULL, task_id TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_state (
  branch TEXT PRIMARY KEY, status TEXT, main_goal_version TEXT, goal TEXT, judgment TEXT,
  breakpoint TEXT, next_steps_json TEXT NOT NULL, blocker TEXT, updated_at TEXT NOT NULL, task_id TEXT
);
CREATE TABLE IF NOT EXISTS project_profile (
  branch TEXT PRIMARY KEY, description TEXT NOT NULL, big_goal TEXT NOT NULL,
  updated_at TEXT NOT NULL, task_id TEXT, event_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS stages (
  stage_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE, title TEXT NOT NULL,
  goal TEXT NOT NULL, acceptance_json TEXT NOT NULL, status TEXT NOT NULL,
  summary TEXT NOT NULL, current_step TEXT NOT NULL, next_step TEXT NOT NULL,
  blocker TEXT NOT NULL, evidence_json TEXT NOT NULL, started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, finished_at TEXT, task_id TEXT
);
CREATE TABLE IF NOT EXISTS handoffs (
  event_id TEXT PRIMARY KEY, branch TEXT NOT NULL, task_id TEXT, occurred_at TEXT NOT NULL,
  task TEXT NOT NULL, result TEXT NOT NULL, main_goal_change TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_archive (
  event_id TEXT PRIMARY KEY, branch TEXT NOT NULL, task_id TEXT, occurred_at TEXT NOT NULL,
  summary TEXT NOT NULL, evidence TEXT NOT NULL, result TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
  decision_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, branch TEXT NOT NULL,
  occurred_at TEXT NOT NULL, decision TEXT NOT NULL, alternatives TEXT NOT NULL,
  basis TEXT NOT NULL, reopen_condition TEXT NOT NULL, task_id TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
  attempt_id TEXT PRIMARY KEY, branch TEXT NOT NULL UNIQUE, track TEXT NOT NULL, topic TEXT NOT NULL,
  base_commit TEXT NOT NULL, goal TEXT NOT NULL, acceptance_json TEXT NOT NULL,
  stage_id TEXT, hypothesis TEXT, conclusion TEXT, current_step TEXT, progress TEXT, next_step TEXT,
  state TEXT NOT NULL, pr TEXT, archive_branch TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempt_evidence (
  event_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, occurred_at TEXT NOT NULL, evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS explorations (
  event_id TEXT PRIMARY KEY, branch TEXT NOT NULL, occurred_at TEXT NOT NULL,
  goal TEXT NOT NULL, result TEXT NOT NULL, evidence TEXT NOT NULL, disposition_ref TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS active_tasks (
  task_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, branch TEXT NOT NULL,
  record_json TEXT NOT NULL, state_updated INTEGER NOT NULL DEFAULT 0,
  decisions_added INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS catalog_items (
  item_id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
  path TEXT UNIQUE, status TEXT NOT NULL, tags_json TEXT NOT NULL, source TEXT NOT NULL,
  metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  branch TEXT NOT NULL, task_id TEXT, event_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS catalog_relations (
  relation_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
  relation_type TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, branch TEXT NOT NULL, task_id TEXT, event_id TEXT NOT NULL UNIQUE,
  FOREIGN KEY(source_id) REFERENCES catalog_items(item_id),
  FOREIGN KEY(target_id) REFERENCES catalog_items(item_id)
);
CREATE INDEX IF NOT EXISTS catalog_items_kind_status ON catalog_items(kind, status);
CREATE INDEX IF NOT EXISTS catalog_relations_source ON catalog_relations(source_id);
CREATE INDEX IF NOT EXISTS catalog_relations_target ON catalog_relations(target_id);
"""


PROJECTION_TABLES = (
    "events", "project_state", "project_profile", "stages", "handoffs", "task_archive", "decisions",
    "attempts", "attempt_evidence", "explorations", "catalog_relations", "catalog_items",
)

REQUIRED_SCHEMA_COLUMNS = {
    "meta": {"key", "value"},
    "events": {"event_id", "schema_version", "event_type", "payload_json"},
    "project_profile": {"branch", "description", "big_goal", "event_id"},
    "stages": {
        "stage_id", "sequence", "title", "goal", "acceptance_json", "status",
        "summary", "current_step", "next_step", "blocker", "evidence_json",
    },
    "attempts": {
        "attempt_id", "branch", "stage_id", "current_step", "progress", "next_step", "state",
    },
    "active_tasks": {"task_id", "branch", "record_json", "state_updated", "decisions_added"},
}


def schema_layout_is_current(connection: sqlite3.Connection) -> bool:
    for table, required in REQUIRED_SCHEMA_COLUMNS.items():
        columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not required.issubset(columns):
            return False
    return True


def validate_projection(events: Iterable[dict]) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SCHEMA)
        for event in sorted(events, key=lambda item: (item["occurred_at"], item["event_id"])):
            apply_event(connection, event)
    except sqlite3.DatabaseError as exc:
        raise StoreError(f"事件无法形成一致的数据库投影: {exc}") from exc
    finally:
        connection.close()


def connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.executescript(SCHEMA)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return connection
    except Exception:
        connection.close()
        raise


def apply_event(connection: sqlite3.Connection, event: dict) -> None:
    payload = event["payload"]
    connection.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event["event_id"], event["schema_version"], event["event_type"], event["occurred_at"],
         event["branch"], event["task_id"], canonical_json(payload)),
    )
    kind = event["event_type"]
    if kind == "project_state.updated":
        connection.execute(
            """INSERT INTO project_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(branch) DO UPDATE SET status=excluded.status,
               main_goal_version=excluded.main_goal_version, goal=excluded.goal,
               judgment=excluded.judgment, breakpoint=excluded.breakpoint,
               next_steps_json=excluded.next_steps_json, blocker=excluded.blocker,
               updated_at=excluded.updated_at, task_id=excluded.task_id""",
            (event["branch"], payload.get("status"), payload.get("main_goal_version"), payload.get("goal"),
             payload.get("judgment"), payload.get("breakpoint"), canonical_json(payload.get("next_steps", [])),
             payload.get("blocker"), event["occurred_at"], event["task_id"]),
        )
    elif kind == "project.profile_updated":
        connection.execute(
            """INSERT INTO project_profile VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(branch) DO UPDATE SET description=excluded.description,
               big_goal=excluded.big_goal, updated_at=excluded.updated_at,
               task_id=excluded.task_id, event_id=excluded.event_id""",
            (event["branch"], payload.get("description", ""), payload.get("big_goal", ""),
             event["occurred_at"], event["task_id"], event["event_id"]),
        )
    elif kind == "stage.started":
        connection.execute(
            """INSERT INTO stages (
               stage_id, sequence, title, goal, acceptance_json, status, summary,
               current_step, next_step, blocker, evidence_json, started_at,
               updated_at, finished_at, task_id
               ) VALUES (?, ?, ?, ?, ?, 'active', '', '', '', '', '[]', ?, ?, NULL, ?)""",
            (payload["stage_id"], payload["sequence"], payload["title"], payload["goal"],
             canonical_json(payload["acceptance"]), event["occurred_at"],
             event["occurred_at"], event["task_id"]),
        )
    elif kind == "stage.updated":
        stage_id = payload["stage_id"]
        for key, column in (
            ("summary", "summary"), ("current_step", "current_step"),
            ("next_step", "next_step"), ("blocker", "blocker"),
        ):
            if key in payload:
                connection.execute(
                    f"UPDATE stages SET {column}=?, updated_at=?, task_id=? WHERE stage_id=?",
                    (payload[key], event["occurred_at"], event["task_id"], stage_id),
                )
        if payload.get("evidence"):
            row = connection.execute(
                "SELECT evidence_json FROM stages WHERE stage_id=?", (stage_id,),
            ).fetchone()
            evidence = json.loads(row[0]) if row else []
            evidence.extend(item for item in payload["evidence"] if item not in evidence)
            connection.execute(
                "UPDATE stages SET evidence_json=?, updated_at=?, task_id=? WHERE stage_id=?",
                (canonical_json(evidence), event["occurred_at"], event["task_id"], stage_id),
            )
    elif kind == "stage.state_changed":
        finished_at = event["occurred_at"] if payload["status"] in {"completed", "cancelled"} else None
        connection.execute(
            """UPDATE stages SET status=?, updated_at=?, finished_at=?, task_id=?
               WHERE stage_id=?""",
            (payload["status"], event["occurred_at"], finished_at,
             event["task_id"], payload["stage_id"]),
        )
    elif kind == "handoff.recorded":
        connection.execute(
            "INSERT INTO handoffs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event["event_id"], event["branch"], event["task_id"], event["occurred_at"],
             payload["task"], payload["result"], payload["main_goal_change"]),
        )
    elif kind in {"task.finished", "task.receipt_imported"}:
        connection.execute(
            "INSERT INTO task_archive VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event["event_id"], event["branch"], event["task_id"], event["occurred_at"],
             payload["summary"], payload.get("evidence", ""), payload["result"], canonical_json(payload)),
        )
    elif kind == "decision.recorded":
        connection.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payload["decision_id"], event["event_id"], event["branch"], event["occurred_at"],
             payload["decision"], payload["alternatives"], payload["basis"], payload["reopen_condition"], event["task_id"]),
        )
    elif kind == "attempt.started":
        connection.execute(
            """INSERT INTO attempts (
               attempt_id, branch, track, topic, base_commit, goal, acceptance_json,
               stage_id, hypothesis, conclusion, current_step, progress, next_step,
               state, pr, archive_branch, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL,
                         'active', NULL, NULL, ?, ?)""",
            (payload["attempt_id"], event["branch"], payload["track"], payload["topic"], payload["base_commit"],
             payload["goal"], canonical_json(payload["acceptance"]), payload.get("stage_id"),
             event["occurred_at"], event["occurred_at"]),
        )
    elif kind == "attempt.updated":
        attempt_id = payload["attempt_id"]
        if payload.get("hypothesis") is not None:
            connection.execute("UPDATE attempts SET hypothesis=?, updated_at=? WHERE attempt_id=?", (payload["hypothesis"], event["occurred_at"], attempt_id))
        if payload.get("conclusion") is not None:
            connection.execute("UPDATE attempts SET conclusion=?, updated_at=? WHERE attempt_id=?", (payload["conclusion"], event["occurred_at"], attempt_id))
        for key, column in (
            ("current_step", "current_step"), ("progress", "progress"),
            ("next_step", "next_step"),
        ):
            if payload.get(key) is not None:
                connection.execute(
                    f"UPDATE attempts SET {column}=?, updated_at=? WHERE attempt_id=?",
                    (payload[key], event["occurred_at"], attempt_id),
                )
        for evidence in payload.get("evidence", []):
            connection.execute(
                "INSERT INTO attempt_evidence VALUES (?, ?, ?, ?)",
                (f"{event['event_id']}:{len(connection.execute('SELECT 1 FROM attempt_evidence WHERE event_id LIKE ?', (event['event_id'] + ':%',)).fetchall())}",
                 attempt_id, event["occurred_at"], evidence),
            )
    elif kind == "attempt.state_changed":
        connection.execute(
            "UPDATE attempts SET state=?, pr=COALESCE(?, pr), archive_branch=COALESCE(?, archive_branch), updated_at=? WHERE attempt_id=?",
            (payload["state"], payload.get("pr"), payload.get("archive_branch"), event["occurred_at"], payload["attempt_id"]),
        )
    elif kind == "attempt.archived":
        connection.execute(
            "UPDATE attempts SET archive_branch=?, updated_at=? WHERE attempt_id=?",
            (payload["archive_branch"], event["occurred_at"], payload["attempt_id"]),
        )
    elif kind == "exploration.recorded":
        connection.execute(
            "INSERT INTO explorations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event["event_id"], payload["branch"], event["occurred_at"], payload["goal"],
             payload["result"], payload["evidence"], payload["disposition_ref"]),
        )
    elif kind == "catalog.item_upserted":
        existing = connection.execute(
            "SELECT created_at FROM catalog_items WHERE item_id=?",
            (payload["item_id"],),
        ).fetchone()
        created_at = existing["created_at"] if existing else payload.get("created_at", event["occurred_at"])
        connection.execute(
            """INSERT INTO catalog_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(item_id) DO UPDATE SET kind=excluded.kind, title=excluded.title,
               summary=excluded.summary, path=excluded.path, status=excluded.status,
               tags_json=excluded.tags_json, source=excluded.source,
               metadata_json=excluded.metadata_json, updated_at=excluded.updated_at,
               branch=excluded.branch, task_id=excluded.task_id, event_id=excluded.event_id""",
            (
                payload["item_id"], payload["kind"], payload["title"], payload.get("summary", ""),
                payload.get("path") or None, payload.get("status", "active"),
                canonical_json(payload.get("tags", [])), payload.get("source", ""),
                canonical_json(payload.get("metadata", {})), created_at, event["occurred_at"],
                event["branch"], event["task_id"], event["event_id"],
            ),
        )
    elif kind == "catalog.item_archived":
        connection.execute(
            """UPDATE catalog_items SET status='archived', updated_at=?, branch=?,
               task_id=?, event_id=? WHERE item_id=?""",
            (event["occurred_at"], event["branch"], event["task_id"], event["event_id"], payload["item_id"]),
        )
    elif kind == "catalog.item_restored":
        connection.execute(
            """UPDATE catalog_items SET status='active', updated_at=?, branch=?,
               task_id=?, event_id=? WHERE item_id=?""",
            (event["occurred_at"], event["branch"], event["task_id"], event["event_id"], payload["item_id"]),
        )
    elif kind == "catalog.relation_upserted":
        existing = connection.execute(
            "SELECT created_at FROM catalog_relations WHERE relation_id=?",
            (payload["relation_id"],),
        ).fetchone()
        created_at = existing["created_at"] if existing else payload.get("created_at", event["occurred_at"])
        connection.execute(
            """INSERT INTO catalog_relations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(relation_id) DO UPDATE SET source_id=excluded.source_id,
               target_id=excluded.target_id, relation_type=excluded.relation_type,
               note=excluded.note, updated_at=excluded.updated_at, branch=excluded.branch,
               task_id=excluded.task_id, event_id=excluded.event_id""",
            (
                payload["relation_id"], payload["source_id"], payload["target_id"],
                payload["relation_type"], payload.get("note", ""), created_at,
                event["occurred_at"], event["branch"], event["task_id"], event["event_id"],
            ),
        )
    elif kind == "catalog.relation_removed":
        connection.execute(
            "DELETE FROM catalog_relations WHERE relation_id=?",
            (payload["relation_id"],),
        )


def rebuild(database: Path, journal: Path, *, preserve_active: bool = True) -> sqlite3.Connection:
    events = load_events(journal)
    active_tasks: list[tuple] = []
    recreate = not database.exists()
    if database.exists():
        probe = None
        try:
            probe = sqlite3.connect(database, timeout=2)
            version = probe.execute("PRAGMA user_version").fetchone()[0]
            recreate = version != SCHEMA_VERSION or not schema_layout_is_current(probe)
            table = probe.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_tasks'"
            ).fetchone()
            if preserve_active and table:
                active_tasks = probe.execute(
                    "SELECT task_id, started_at, branch, record_json, state_updated, decisions_added "
                    "FROM active_tasks"
                ).fetchall()
        except sqlite3.DatabaseError:
            active_tasks = []
            recreate = True
        finally:
            if probe is not None:
                probe.close()

    if recreate:
        try:
            for path in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")):
                if path.exists():
                    path.unlink()
        except OSError as exc:
            raise StoreError(f"无法重建维护数据库: {exc}") from exc

    connection = connect(database)
    with connection:
        if recreate and active_tasks:
            connection.executemany(
                "INSERT INTO active_tasks VALUES (?, ?, ?, ?, ?, ?)", active_tasks
            )
        for table in PROJECTION_TABLES:
            connection.execute(f"DELETE FROM {table}")
        if not preserve_active:
            connection.execute("DELETE FROM active_tasks")
        for event in sorted(events, key=lambda item: (item["occurred_at"], item["event_id"])):
            apply_event(connection, event)
        connection.execute(
            "INSERT INTO meta(key, value) VALUES ('journal_hash', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (journal_hash(journal),),
        )
    return connection


def ensure_database(database: Path, journal: Path) -> sqlite3.Connection:
    def restore_sidecar(connection: sqlite3.Connection) -> sqlite3.Connection:
        if not active_task_path(database.parent).is_file():
            return connection
        try:
            if active_row_matches(database, database.parent):
                return connection
        except (ActiveTaskError, sqlite3.DatabaseError):
            pass
        connection.close()
        try:
            with mutation_lock(database.parent, command="active-task.restore"):
                restore_active_row(database, database.parent)
        except ActiveTaskError as exc:
            raise StoreError(str(exc)) from exc
        return connect(database)

    def guarded_rebuild() -> sqlite3.Connection:
        with mutation_lock(database.parent, command="database.rebuild"):
            return restore_sidecar(rebuild(database, journal))

    previous_version = None
    layout_current = False
    if database.exists():
        probe = None
        try:
            probe = sqlite3.connect(database, timeout=2)
            previous_version = probe.execute("PRAGMA user_version").fetchone()[0]
            layout_current = schema_layout_is_current(probe)
        except sqlite3.DatabaseError:
            previous_version = None
        finally:
            if probe is not None:
                probe.close()
    if database.exists() and (previous_version != SCHEMA_VERSION or not layout_current):
        return guarded_rebuild()
    connection = None
    try:
        connection = connect(database)
        stored = connection.execute("SELECT value FROM meta WHERE key='journal_hash'").fetchone()
        if stored is None or stored[0] != journal_hash(journal):
            connection.close()
            return guarded_rebuild()
        return restore_sidecar(connection)
    except sqlite3.DatabaseError:
        if connection is not None:
            connection.close()
        return guarded_rebuild()


def record_events(database: Path, journal: Path, state_dir: Path, events: Iterable[dict]) -> None:
    pending = list(events)
    task_id = next((event.get("task_id") for event in pending if event.get("task_id")), None)
    with mutation_lock(state_dir, command="event.persist", task_id=task_id):
        existed = journal.exists()
        original = journal.read_bytes() if existed else b""
        append_events(journal, state_dir, pending)
        try:
            connection = rebuild(database, journal)
            connection.close()
        except Exception as exc:
            descriptor, temp_name = tempfile.mkstemp(
                prefix="events-rollback-", suffix=".jsonl", dir=journal.parent
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(original)
                    handle.flush()
                    os.fsync(handle.fileno())
                if existed:
                    os.replace(temp_name, journal)
                else:
                    os.unlink(temp_name)
                    journal.unlink(missing_ok=True)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            try:
                rollback = rebuild(database, journal)
                rollback.close()
            except Exception as rollback_exc:
                raise StoreError(
                    f"事件写入失败且数据库回滚失败: {exc}; rollback: {rollback_exc}"
                ) from exc
            raise


def rows(connection: sqlite3.Connection, query: str, parameters: tuple = ()) -> list[dict]:
    return [dict(row) for row in connection.execute(query, parameters).fetchall()]
