"""Shared read-only projections for CLI output and the desktop dashboard."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

from .store import SCHEMA_VERSION, ensure_database, journal_hash, rows


class ReadModelError(RuntimeError):
    pass


class MaintenanceReadModel:
    def __init__(self, database: Path, journal: Path, branch_provider: Callable[[], str]):
        self.database_path = database
        self.journal_path = journal
        self.branch_provider = branch_provider

    def _connection(self) -> tuple[sqlite3.Connection, bool]:
        rebuilt = not self.database_path.exists()
        if not rebuilt:
            probe = None
            try:
                probe = sqlite3.connect(self.database_path, timeout=2)
                stored = probe.execute("SELECT value FROM meta WHERE key='journal_hash'").fetchone()
                integrity = probe.execute("PRAGMA quick_check").fetchone()
                rebuilt = not stored or stored[0] != journal_hash(self.journal_path) or not integrity or integrity[0] != "ok"
            except sqlite3.DatabaseError:
                rebuilt = True
            finally:
                if probe is not None:
                    probe.close()
        try:
            return ensure_database(self.database_path, self.journal_path), rebuilt
        except (OSError, sqlite3.DatabaseError, RuntimeError) as exc:
            raise ReadModelError(str(exc)) from exc

    @staticmethod
    def _attempt(connection: sqlite3.Connection, branch: str) -> dict | None:
        row = connection.execute("SELECT * FROM attempts WHERE branch=? OR archive_branch=? LIMIT 1", (branch, branch)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["acceptance"] = json.loads(result.pop("acceptance_json"))
        result["evidence"] = [item["evidence"] for item in rows(
            connection,
            "SELECT evidence FROM attempt_evidence WHERE attempt_id=? ORDER BY occurred_at, event_id",
            (result["attempt_id"],),
        )]
        return result

    @staticmethod
    def _context(connection: sqlite3.Connection, branch: str) -> dict:
        state = connection.execute("SELECT * FROM project_state WHERE branch=?", (branch,)).fetchone()
        state_branch = branch
        if state is None and branch != "main":
            state = connection.execute("SELECT * FROM project_state WHERE branch='main'").fetchone()
            state_branch = "main"
        handoffs = rows(
            connection,
            "SELECT occurred_at, task, result, main_goal_change FROM handoffs WHERE branch=? ORDER BY occurred_at DESC, event_id DESC LIMIT 5",
            (branch,),
        )
        if not handoffs and branch != "main":
            handoffs = rows(
                connection,
                "SELECT occurred_at, task, result, main_goal_change FROM handoffs WHERE branch='main' ORDER BY occurred_at DESC, event_id DESC LIMIT 5",
            )
        state_data = dict(state) if state else None
        if state_data:
            state_data["next_steps"] = json.loads(state_data.pop("next_steps_json"))
            state_data["source_branch"] = state_branch
        active = connection.execute("SELECT * FROM active_tasks ORDER BY started_at DESC LIMIT 1").fetchone()
        active_data = None
        if active:
            active_data = {
                "task_id": active["task_id"],
                "started_at": active["started_at"],
                "branch": active["branch"],
                "state_updated": bool(active["state_updated"]),
                "decisions_added": active["decisions_added"],
            }
        return {"branch": branch, "state": state_data, "recent_handoffs": handoffs, "active_task": active_data}

    def context(self, branch: str | None = None) -> dict:
        branch = branch or self.branch_provider()
        connection, _ = self._connection()
        try:
            return self._context(connection, branch)
        finally:
            connection.close()

    def attempt(self, branch: str | None = None) -> dict | None:
        branch = branch or self.branch_provider()
        connection, _ = self._connection()
        try:
            return self._attempt(connection, branch)
        finally:
            connection.close()

    def records(self, kind: str, limit: int = 20) -> list[dict]:
        connection, _ = self._connection()
        try:
            if kind == "history":
                return rows(connection, "SELECT occurred_at, task_id, summary, evidence, result, branch, payload_json FROM task_archive ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            if kind == "decisions":
                return rows(connection, "SELECT decision_id, occurred_at, decision, alternatives, basis, reopen_condition, branch FROM decisions ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            if kind == "explorations":
                return rows(connection, "SELECT branch, occurred_at, goal, result, evidence, disposition_ref FROM explorations ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            if kind == "events":
                values = rows(connection, "SELECT event_id, occurred_at, event_type, branch, task_id, payload_json FROM events ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
                for value in values:
                    value["payload"] = json.loads(value.pop("payload_json"))
                return values
            raise ReadModelError(f"未知查询类型: {kind}")
        finally:
            connection.close()

    def dashboard_snapshot(self, branch: str | None = None, *, limit: int = 1000) -> dict:
        branch = branch or self.branch_provider()
        connection, rebuilt = self._connection()
        try:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            context = self._context(connection, branch)
            history = rows(connection, "SELECT occurred_at, task_id, summary, evidence, result, branch, payload_json FROM task_archive ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            decisions = rows(connection, "SELECT decision_id, occurred_at, decision, alternatives, basis, reopen_condition, branch FROM decisions ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            explorations = rows(connection, "SELECT branch, occurred_at, goal, result, evidence, disposition_ref FROM explorations ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            events = rows(connection, "SELECT event_id, occurred_at, event_type, branch, task_id, payload_json FROM events ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            for event in events:
                event["payload"] = json.loads(event.pop("payload_json"))
            return {
                "branch": branch,
                "health": {
                    "status": "passed" if integrity == "ok" else integrity,
                    "schema_version": SCHEMA_VERSION,
                    "events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                    "journal_hash": journal_hash(self.journal_path),
                    "rebuilt": rebuilt,
                },
                "context": context,
                "history": history,
                "decisions": decisions,
                "explorations": explorations,
                "attempt": self._attempt(connection, branch),
                "events": events,
            }
        finally:
            connection.close()
