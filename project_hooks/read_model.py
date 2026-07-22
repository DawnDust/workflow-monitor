"""Shared read-only projections for CLI output and the desktop dashboard."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable

from .store import SCHEMA_VERSION, ensure_database, journal_hash, rows


class ReadModelError(RuntimeError):
    pass


class MaintenanceReadModel:
    def __init__(self, database: Path, journal: Path, branch_provider: Callable[[], str], repo: Path | None = None):
        self.database_path = database
        self.journal_path = journal
        self.branch_provider = branch_provider
        self.repo_path = repo or journal.parent.parent
        self._timeline_signature: str | None = None
        self._timeline_cache: dict | None = None

    def _git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments], cwd=self.repo_path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise ReadModelError(f"读取 Git 时间线失败: {detail or '未知 Git 错误'}")
        return completed.stdout

    @staticmethod
    def _branch_name(ref: str) -> str | None:
        ref = ref.strip()
        if not ref or ref.endswith("/HEAD"):
            return None
        if ref.startswith("refs/heads/"):
            return ref.removeprefix("refs/heads/")
        if ref.startswith("refs/remotes/"):
            return ref.removeprefix("refs/remotes/")
        return ref

    def _git_signature(self) -> str:
        refs = self._git(
            "for-each-ref", "--format=%(refname)%00%(objectname)", "refs/heads", "refs/remotes",
        )
        head = self._git("rev-parse", "--verify", "HEAD").strip()
        return f"{head}\n{refs}\n{journal_hash(self.journal_path)}"

    def _event_commit_map(self) -> dict[str, list[str]]:
        output = self._git(
            "log", "--all", "--reverse", "--format=@@PROJECT_HOOKS_COMMIT:%H",
            "--patch", "--unified=0", "--no-color", "--no-ext-diff", "--",
            self.journal_path.relative_to(self.repo_path).as_posix(),
        )
        current: str | None = None
        result: dict[str, list[str]] = {}
        for line in output.splitlines():
            if line.startswith("@@PROJECT_HOOKS_COMMIT:"):
                current = line.split(":", 1)[1].strip()
                continue
            if current is None or not line.startswith("+{"):
                continue
            try:
                event_id = json.loads(line[1:]).get("event_id")
            except json.JSONDecodeError:
                continue
            if event_id and current not in result.setdefault(event_id, []):
                result[event_id].append(current)
        return result

    @staticmethod
    def _lane_priority(name: str, default: str) -> tuple[int, str]:
        if name == default:
            return 2, name
        if name.startswith(("research/", "experiment/", "sandbox/", "archive/")):
            return 0, name
        return 1, name

    def _build_timeline(self, connection: sqlite3.Connection) -> dict:
        signature = self._git_signature()
        if signature == self._timeline_signature and self._timeline_cache is not None:
            return self._timeline_cache

        separator = "\x1f"
        raw = self._git(
            "log", "--all", "--topo-order", "--reverse", "--date=iso-strict",
            f"--format=%H{separator}%P{separator}%aI{separator}%an{separator}%s",
        )
        commits: list[dict] = []
        by_hash: dict[str, dict] = {}
        for line in raw.splitlines():
            parts = line.split(separator, 4)
            if len(parts) != 5:
                continue
            commit_hash, parent_text, occurred_at, author, subject = parts
            commit = {
                "hash": commit_hash,
                "short_hash": commit_hash[:8],
                "parents": parent_text.split() if parent_text else [],
                "occurred_at": occurred_at,
                "author": author,
                "subject": subject,
                "refs": [],
                "lane": "其他",
                "events": [],
                "task_ids": [],
                "event_branches": [],
            }
            commits.append(commit)
            by_hash[commit_hash] = commit

        refs_output = self._git(
            "for-each-ref", "--format=%(refname)%00%(objectname)", "refs/heads", "refs/remotes",
        )
        ref_entries: list[tuple[str, str, bool]] = []
        for line in refs_output.splitlines():
            if "\x00" not in line:
                continue
            ref, tip = line.split("\x00", 1)
            branch = self._branch_name(ref)
            if not branch or tip not in by_hash:
                continue
            ref_entries.append((branch, tip, ref.startswith("refs/remotes/")))

        local_tips = {branch: tip for branch, tip, remote in ref_entries if not remote}
        remote_groups: dict[str, set[str]] = {}
        for branch, tip, remote in ref_entries:
            if remote and "/" in branch:
                remote_groups.setdefault(branch.split("/", 1)[1], set()).add(tip)

        branch_tips: dict[str, str] = {}
        for branch, tip, remote in ref_entries:
            if remote and "/" in branch:
                suffix = branch.split("/", 1)[1]
                if local_tips.get(suffix) == tip or (suffix not in local_tips and len(remote_groups[suffix]) == 1):
                    branch = suffix
            branch_tips.setdefault(branch, tip)
            if branch not in by_hash[tip]["refs"]:
                by_hash[tip]["refs"].append(branch)

        event_rows = rows(
            connection,
            "SELECT event_id, occurred_at, event_type, branch, task_id, payload_json FROM events ORDER BY occurred_at, event_id",
        )
        event_by_id: dict[str, dict] = {}
        for event in event_rows:
            event["payload"] = json.loads(event.pop("payload_json"))
            event_by_id[event["event_id"]] = event
        for event_id, hashes in self._event_commit_map().items():
            event = event_by_id.get(event_id)
            if event is None:
                continue
            for commit_hash in hashes:
                commit = by_hash.get(commit_hash)
                if commit is None:
                    continue
                commit["events"].append(event)
                if event.get("task_id") and event["task_id"] not in commit["task_ids"]:
                    commit["task_ids"].append(event["task_id"])
                if event["branch"] not in commit["event_branches"]:
                    commit["event_branches"].append(event["branch"])

        default = "main" if "main" in branch_tips else (next(iter(branch_tips), "main"))

        def ancestry(start: str) -> set[str]:
            found: set[str] = set()
            pending = [start]
            while pending:
                item = pending.pop()
                if item in found or item not in by_hash:
                    continue
                found.add(item)
                pending.extend(by_hash[item]["parents"])
            return found

        default_ancestry = ancestry(branch_tips[default]) if default in branch_tips else set()
        claimed: set[str] = set()
        for branch in sorted(branch_tips, key=lambda name: self._lane_priority(name, default)):
            current = branch_tips[branch]
            while current in by_hash and current not in claimed:
                if branch != default and current in default_ancestry:
                    break
                by_hash[current]["lane"] = branch
                claimed.add(current)
                parents = by_hash[current]["parents"]
                if not parents:
                    break
                current = parents[0]
        for commit_hash in default_ancestry:
            if commit_hash in by_hash and commit_hash not in claimed:
                by_hash[commit_hash]["lane"] = default
                claimed.add(commit_hash)

        lane_names = {commit["lane"] for commit in commits}
        lane_names.update(branch_tips)
        lane_names.update(event["branch"] for event in event_rows)
        if all(commit["lane"] != "其他" for commit in commits):
            lane_names.discard("其他")
        lanes = sorted(lane_names, key=lambda name: self._lane_priority(name, default))
        if default in lanes:
            lanes.remove(default)
            lanes.insert(0, default)
        edges = [
            {"parent": parent, "child": commit["hash"]}
            for commit in commits for parent in commit["parents"] if parent in by_hash
        ]
        result = {
            "status": "passed",
            "default_branch": default,
            "lanes": lanes,
            "commits": commits,
            "edges": edges,
        }
        self._timeline_signature = signature
        self._timeline_cache = result
        return result

    def timeline(self) -> dict:
        connection, _ = self._connection()
        try:
            return self._build_timeline(connection)
        finally:
            connection.close()

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
            timeline_error = None
            try:
                timeline = self._build_timeline(connection)
            except (OSError, ReadModelError, subprocess.SubprocessError) as exc:
                timeline = {"status": "unavailable", "lanes": [], "commits": [], "edges": []}
                timeline_error = str(exc)
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
                "timeline": timeline,
                "timeline_error": timeline_error,
            }
        finally:
            connection.close()
