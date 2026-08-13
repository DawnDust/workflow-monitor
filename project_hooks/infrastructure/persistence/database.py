"""Named SQLite repository operations kept behind the persistence boundary."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any


DatabaseError = sqlite3.DatabaseError
Connection = sqlite3.Connection
Row = sqlite3.Row


class ProjectDatabase:
    """Small repository facade that prevents SQL and connections leaking outward."""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def close(self) -> None:
        self._connection.close()

    def integrity(self) -> str:
        return str(self._connection.execute("PRAGMA integrity_check").fetchone()[0])

    def active_task_ids(self) -> list[str]:
        rows = self._connection.execute(
            "SELECT task_id FROM active_tasks ORDER BY started_at, task_id"
        ).fetchall()
        return [str(row[0]) for row in rows]

    def latest_active_task(self) -> dict | None:
        row = self._connection.execute(
            "SELECT * FROM active_tasks ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def upsert_active_task(
        self, record: dict, record_json: str, state_updated: int,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """INSERT INTO active_tasks VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(task_id) DO UPDATE SET
                     started_at=excluded.started_at,
                     branch=excluded.branch,
                     record_json=excluded.record_json,
                     state_updated=excluded.state_updated""",
                (record["task_id"], record["started_at"], record["git"]["branch"],
                 record_json, state_updated),
            )

    def update_active_flags(
        self, task_id: str, *, state_updated: bool,
    ) -> None:
        with self._connection:
            if state_updated:
                self._connection.execute(
                    "UPDATE active_tasks SET state_updated=1 WHERE task_id=?", (task_id,),
                )

    def delete_active_task(self, task_id: str) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM active_tasks WHERE task_id=?", (task_id,))

    def catalog_item(self, item_id: str) -> dict | None:
        row = self._connection.execute(
            "SELECT * FROM catalog_items WHERE item_id=?", (item_id,),
        ).fetchone()
        return dict(row) if row else None

    def catalog_item_by_path(self, path: str) -> dict | None:
        row = self._connection.execute(
            "SELECT * FROM catalog_items WHERE path=?", (path,),
        ).fetchone()
        return dict(row) if row else None

    def catalog_items(self) -> list[dict]:
        return [dict(row) for row in self._connection.execute(
            "SELECT * FROM catalog_items ORDER BY updated_at DESC, item_id"
        ).fetchall()]

    def catalog_relations(self) -> list[dict]:
        return [dict(row) for row in self._connection.execute(
            "SELECT * FROM catalog_relations ORDER BY updated_at DESC, relation_id"
        ).fetchall()]

    def catalog_item_exists(self, item_id: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM catalog_items WHERE item_id=?", (item_id,),
        ).fetchone() is not None

    def catalog_path_exists(self, path: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM catalog_items WHERE path=?", (path,),
        ).fetchone() is not None

    def conflicting_catalog_path(self, path: str, item_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT item_id FROM catalog_items WHERE path=? AND item_id<>?", (path, item_id),
        ).fetchone()
        return str(row[0]) if row else None

    def catalog_relation_exists(self, relation_id: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM catalog_relations WHERE relation_id=?", (relation_id,),
        ).fetchone() is not None

    def project_profile(self, branch: str) -> dict | None:
        row = self._connection.execute(
            "SELECT * FROM project_profile WHERE branch=?", (branch,),
        ).fetchone()
        return dict(row) if row else None

    def stages(self, limit: int | None = None) -> list[dict]:
        rows = self._connection.execute(
            "SELECT * FROM stages ORDER BY sequence DESC"
        ).fetchall()
        values = [dict(row) for row in rows]
        return values if limit is None else values[:limit]

    def stage(self, stage_id: str) -> dict | None:
        row = self._connection.execute(
            "SELECT * FROM stages WHERE stage_id=?", (stage_id,),
        ).fetchone()
        return dict(row) if row else None

    def active_stage(self) -> dict | None:
        row = self._connection.execute(
            "SELECT * FROM stages WHERE status='active' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def next_stage_sequence(self) -> int:
        return int(self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM stages"
        ).fetchone()[0])

    def attempt_states(self) -> list[dict]:
        return [dict(row) for row in self._connection.execute(
            "SELECT stage_id, branch, state FROM attempts"
        ).fetchall()]

    def project_state(self, branch: str, *, fallback_to_main: bool = False) -> dict | None:
        row = self._connection.execute(
            "SELECT * FROM project_state WHERE branch=?", (branch,),
        ).fetchone()
        if row is None and fallback_to_main and branch != "main":
            row = self._connection.execute(
                "SELECT * FROM project_state WHERE branch='main'"
            ).fetchone()
        return dict(row) if row else None

    def has_finished_receipt(self, branch: str, attempt_state: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM attempts WHERE (branch=? OR archive_branch=?) AND state=? "
            "AND EXISTS (SELECT 1 FROM task_archive WHERE task_archive.branch=attempts.branch) LIMIT 1",
            (branch, branch, attempt_state),
        ).fetchone() is not None

    def task_ids_like(self, prefix: str) -> set[str]:
        rows = self._connection.execute(
            "SELECT DISTINCT task_id FROM events WHERE task_id LIKE ? AND task_id IS NOT NULL",
            (prefix + "%",),
        ).fetchall()
        return {str(row[0]) for row in rows}

    def table_counts(self, tables: Iterable[str]) -> dict[str, int]:
        allowed = {"events", "task_archive", "handoffs", "project_state", "explorations"}
        names = list(tables)
        if any(name not in allowed for name in names):
            raise ValueError("unsupported projection table")
        return {
            name: int(self._connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in names
        }

    def event_count(self) -> int:
        return int(self._connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def journal_hash(self) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM meta WHERE key='journal_hash'"
        ).fetchone()
        return str(row[0]) if row else None

    def last_completed_task(self) -> dict | None:
        row = self._connection.execute(
            "SELECT task_id, occurred_at, summary, result FROM task_archive "
            "ORDER BY occurred_at DESC, event_id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def repository(connection: sqlite3.Connection) -> ProjectDatabase:
    return ProjectDatabase(connection)


def integrity_check(connection: sqlite3.Connection) -> str:
    return ProjectDatabase(connection).integrity()


def latest_active_task_id(path: Path) -> str | None:
    if not path.is_file():
        return None
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT task_id FROM active_tasks ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return str(row[0]) if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        connection.close()


def verification_snapshot(path: Path, expected_journal_hash: str) -> dict:
    connection = None
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        stored = connection.execute("SELECT value FROM meta WHERE key='journal_hash'").fetchone()
        return {
            "status": "passed" if integrity == "ok" else "failed",
            "integrity": integrity,
            "events": event_count,
            "database_matches_journal": bool(stored and stored[0] == expected_journal_hash),
        }
    finally:
        if connection is not None:
            connection.close()
