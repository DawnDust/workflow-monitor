"""Shared read-only projections for CLI output and the desktop dashboard."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable

from .catalog import decode_item
from .resource_layout import resource_directory_snapshot
from .store import SCHEMA_VERSION, ensure_database, journal_hash, rows


class ReadModelError(RuntimeError):
    pass


GIT_RELATION_LABELS = {
    "synced": "同步",
    "ahead": "领先",
    "behind": "落后",
    "diverged": "分叉",
    "unavailable": "不可用",
}

AUXILIARY_TASK_RE = re.compile(
    r"^\d{8}_(?:publish_.+|record_.+_publication)_\d+$",
    re.IGNORECASE,
)


def git_state_summary(state: dict) -> str:
    branch = state.get("branch") or "未知分支"
    head = state.get("head") or "未知"
    upstream_ref = state.get("upstream_ref") or "origin/main"
    upstream_head = state.get("upstream_head") or "不可用"
    relation = GIT_RELATION_LABELS.get(state.get("relation"), state.get("relation") or "不可用")
    return f"本地 {branch}：{head}\n{upstream_ref}：{upstream_head}\n关系：{relation}"


def compact_text(value: object, fallback: str = "未设置") -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def git_state_inline(state: dict) -> str:
    branch = state.get("branch") or "未知分支"
    head = str(state.get("head") or "未知")
    upstream_ref = state.get("upstream_ref") or "origin/main"
    upstream_head = str(state.get("upstream_head") or "不可用")
    relation = GIT_RELATION_LABELS.get(state.get("relation"), state.get("relation") or "不可用")
    local = head[:8] if head not in {"未知", "不可用"} else head
    upstream = upstream_head[:8] if upstream_head not in {"未知", "不可用"} else upstream_head
    return f"{branch} {local} 与 {upstream_ref} {upstream}：{relation}"


def action_overview_text(context: dict) -> str:
    state = context.get("overview_state") or context.get("state") or {}
    profile = context.get("project_profile") or {}
    stage = context.get("current_stage") or {}
    active = context.get("active_task")
    active_text = (
        f"{active.get('task_id')}（{active.get('branch') or context.get('branch') or '未知分支'}）"
        if active else "无"
    )
    separator = "─" * 32
    description = compact_text(profile.get("description"))
    big_goal = compact_text(profile.get("big_goal"))
    if not profile:
        description += "（请运行 project update 初始化）"
        big_goal += "（请运行 project update 初始化）"
    lines = [
        "项目概览",
        f"项目描述：{description}",
        f"大目标：{big_goal}",
        separator,
        "",
        "当前大阶段",
        f"阶段：{compact_text(stage.get('title'))}",
        f"阶段目标：{compact_text(stage.get('goal'))}",
        f"阶段进展：{compact_text(stage.get('summary'))}",
        f"当前步骤：{compact_text(stage.get('current_step'))}",
        f"下一步：{compact_text(stage.get('next_step'))}",
        f"阶段阻塞：{compact_text(stage.get('blocker'), '无。')}",
        separator,
        "",
        "当前执行",
        f"状态：{compact_text(state.get('status'))}",
        f"活动任务：{active_text}",
        f"当前判决：{compact_text(state.get('judgment'))}",
        f"工作断点：{compact_text(state.get('breakpoint'))}",
        f"当前阻塞：{compact_text(state.get('blocker'), '无。')}",
        "下一步：",
    ]
    steps = state.get("next_steps") or []
    lines.extend(f"{index}. {compact_text(item)}" for index, item in enumerate(steps, 1))
    if not steps:
        lines.append("无。")
    lines.extend([
        f"Git 同步：{git_state_inline(context.get('git_state') or {})}",
    ])
    handoffs = context.get("recent_handoffs") or []
    if handoffs:
        latest = handoffs[0]
        lines.append(
            "最近完成："
            f"{compact_text(latest.get('occurred_at'), '未知时间')}｜"
            f"{compact_text(latest.get('task'), '未知任务')}｜"
            f"{compact_text(latest.get('result'), '未知结果')}"
        )
    else:
        lines.append("最近完成：无。")
    lines.extend([separator, "", "探索概览"])
    attempts = context.get("active_attempts") or []
    if attempts:
        for attempt in attempts:
            lines.append(
                f"- {attempt.get('branch')}｜{compact_text(attempt.get('current_step'))}｜"
                f"{compact_text(attempt.get('next_step'))}｜{attempt.get('updated_at') or '未知时间'}"
            )
    else:
        lines.append("- 无 active 探索。")
    lines.append(separator)
    return "\n".join(lines) + "\n"


def is_auxiliary_task_id(task_id: str | None) -> bool:
    return bool(task_id and AUXILIARY_TASK_RE.fullmatch(task_id))


def is_publication_step(value: object) -> bool:
    text = str(value or "").strip().casefold()
    if not text:
        return False
    if any(token in text for token in ("推送", "远端哈希", "origin/")):
        return True
    if re.search(r"\b(?:commit|push)\b", text):
        return True
    artifact_tokens = ("已验证", "改动", "代码", "文件", "版本", "变更")
    if "提交" in text and any(token in text for token in artifact_tokens):
        return True
    return "发布" in text and any(token in text for token in (*artifact_tokens, "全部"))


class MaintenanceReadModel:
    def __init__(self, database: Path, journal: Path, branch_provider: Callable[[], str], repo: Path | None = None):
        self.database_path = database
        self.journal_path = journal
        self.branch_provider = branch_provider
        self.repo_path = repo or journal.parent.parent

    def _git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments], cwd=self.repo_path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise ReadModelError(f"读取 Git 发布状态失败: {detail or '未知 Git 错误'}")
        return completed.stdout

    def git_state(self, default_branch: str = "main") -> dict:
        branch = self.branch_provider()
        upstream_ref = f"origin/{default_branch}"
        result = {
            "branch": branch,
            "head": None,
            "upstream_ref": upstream_ref,
            "upstream_head": None,
            "relation": "unavailable",
            "ahead": None,
            "behind": None,
            "error": None,
        }
        try:
            result["head"] = self._git("rev-parse", "--verify", "HEAD").strip()
            result["upstream_head"] = self._git(
                "rev-parse", "--verify", f"refs/remotes/{upstream_ref}",
            ).strip()
            counts = self._git(
                "rev-list", "--left-right", "--count", f"HEAD...refs/remotes/{upstream_ref}",
            ).strip().split()
            if len(counts) != 2:
                raise ReadModelError("Git 未返回可识别的领先/落后计数")
            ahead, behind = (int(value) for value in counts)
            result["ahead"], result["behind"] = ahead, behind
            if ahead == 0 and behind == 0:
                result["relation"] = "synced"
            elif ahead > 0 and behind == 0:
                result["relation"] = "ahead"
            elif ahead == 0 and behind > 0:
                result["relation"] = "behind"
            else:
                result["relation"] = "diverged"
        except (ReadModelError, ValueError) as exc:
            result["error"] = str(exc)
        return result

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

    def _publication_projection(self, connection: sqlite3.Connection) -> dict:
        default = "main"
        publication_ref = f"origin/{default}"
        event_commits = self._event_commit_map()
        event_tasks = {
            row["event_id"]: row["task_id"]
            for row in rows(connection, "SELECT event_id, task_id FROM events WHERE task_id IS NOT NULL")
        }
        by_hash: dict[str, dict] = {}
        for event_id, hashes in event_commits.items():
            task_id = event_tasks.get(event_id)
            if not task_id:
                continue
            for commit_hash in hashes:
                commit = by_hash.setdefault(commit_hash, {
                    "hash": commit_hash, "task_ids": [], "parents": [], "published": None,
                })
                if task_id not in commit["task_ids"]:
                    commit["task_ids"].append(task_id)
        available = True
        try:
            published = set(self._git("rev-list", f"refs/remotes/{publication_ref}").splitlines())
        except ReadModelError:
            available = False
            published = set()
        for commit in by_hash.values():
            commit["published"] = commit["hash"] in published if available else None
        return {
            "publication": {"ref": publication_ref, "available": available},
            "commits": list(by_hash.values()),
        }

    def _branch_attempts(self) -> list[dict]:
        try:
            output = self._git(
                "for-each-ref", "--format=%(refname)",
                "refs/heads/research", "refs/heads/experiment", "refs/heads/sandbox",
                "refs/heads/archive", "refs/remotes/origin/research",
                "refs/remotes/origin/experiment", "refs/remotes/origin/sandbox",
                "refs/remotes/origin/archive",
            )
        except ReadModelError:
            return []
        refs: dict[str, str] = {}
        for ref in output.splitlines():
            if ref.startswith("refs/heads/"):
                branch = ref.removeprefix("refs/heads/")
                refs[branch] = ref
            elif ref.startswith("refs/remotes/origin/"):
                branch = ref.removeprefix("refs/remotes/origin/")
                refs.setdefault(branch, ref)
        attempts: dict[str, dict] = {}
        journal_relative = self.journal_path.relative_to(self.repo_path).as_posix()
        for ref in refs.values():
            try:
                text = self._git("show", f"{ref}:{journal_relative}")
            except ReadModelError:
                continue
            events = []
            for raw in text.splitlines():
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
            events.sort(key=lambda item: (item.get("occurred_at", ""), item.get("event_id", "")))
            for event in events:
                payload = event.get("payload") or {}
                kind = event.get("event_type")
                if kind == "attempt.started":
                    attempt_id = payload.get("attempt_id")
                    if not attempt_id:
                        continue
                    attempts[attempt_id] = {
                        "attempt_id": attempt_id, "branch": event.get("branch"),
                        "track": payload.get("track"), "topic": payload.get("topic"),
                        "base_commit": payload.get("base_commit"), "goal": payload.get("goal"),
                        "acceptance": payload.get("acceptance", []), "stage_id": payload.get("stage_id"),
                        "hypothesis": None, "conclusion": None, "current_step": None,
                        "progress": None, "next_step": None, "state": "active", "pr": None,
                        "archive_branch": None, "evidence": [],
                        "created_at": event.get("occurred_at"), "updated_at": event.get("occurred_at"),
                    }
                    continue
                attempt_id = payload.get("attempt_id")
                attempt = attempts.get(attempt_id)
                if not attempt:
                    continue
                if kind == "attempt.updated":
                    for key in ("hypothesis", "conclusion", "current_step", "progress", "next_step"):
                        if payload.get(key) is not None:
                            attempt[key] = payload[key]
                    for evidence in payload.get("evidence", []):
                        if evidence not in attempt["evidence"]:
                            attempt["evidence"].append(evidence)
                elif kind == "attempt.state_changed":
                    attempt["state"] = payload.get("state", attempt["state"])
                    attempt["pr"] = payload.get("pr") or attempt["pr"]
                    attempt["archive_branch"] = payload.get("archive_branch") or attempt["archive_branch"]
                elif kind == "attempt.archived":
                    attempt["archive_branch"] = payload.get("archive_branch")
                attempt["updated_at"] = event.get("occurred_at") or attempt["updated_at"]
        return list(attempts.values())

    def attempts_across_branches(self) -> list[dict]:
        return self._branch_attempts()

    def _connection(self) -> tuple[sqlite3.Connection, bool]:
        rebuilt = not self.database_path.exists()
        if not rebuilt:
            probe = None
            try:
                probe = sqlite3.connect(self.database_path, timeout=2)
                stored = probe.execute("SELECT value FROM meta WHERE key='journal_hash'").fetchone()
                integrity = probe.execute("PRAGMA quick_check").fetchone()
                version = probe.execute("PRAGMA user_version").fetchone()[0]
                rebuilt = (
                    version != SCHEMA_VERSION
                    or not stored
                    or stored[0] != journal_hash(self.journal_path)
                    or not integrity
                    or integrity[0] != "ok"
                )
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

    def _context(self, connection: sqlite3.Connection, branch: str) -> dict:
        state = connection.execute("SELECT * FROM project_state WHERE branch=?", (branch,)).fetchone()
        state_branch = branch
        if state is None and branch != "main":
            state = connection.execute("SELECT * FROM project_state WHERE branch='main'").fetchone()
            state_branch = "main"
        handoff_branch = branch
        archive_rows = rows(
            connection,
            "SELECT event_id, occurred_at, task_id, summary, result, payload_json "
            "FROM task_archive WHERE branch=? ORDER BY occurred_at DESC, event_id DESC LIMIT 20",
            (handoff_branch,),
        )
        legacy_handoffs = rows(
            connection,
            "SELECT event_id, occurred_at, task_id, task, result, main_goal_change "
            "FROM handoffs WHERE branch=? ORDER BY occurred_at DESC, event_id DESC LIMIT 20",
            (handoff_branch,),
        )
        if not archive_rows and not legacy_handoffs and branch != "main":
            handoff_branch = "main"
            archive_rows = rows(
                connection,
                "SELECT event_id, occurred_at, task_id, summary, result, payload_json "
                "FROM task_archive WHERE branch='main' ORDER BY occurred_at DESC, event_id DESC LIMIT 20",
            )
            legacy_handoffs = rows(
                connection,
                "SELECT event_id, occurred_at, task_id, task, result, main_goal_change "
                "FROM handoffs WHERE branch='main' ORDER BY occurred_at DESC, event_id DESC LIMIT 20",
            )
        handoff_by_task: dict[str, dict] = {}
        for item in legacy_handoffs:
            if is_auxiliary_task_id(item.get("task_id")):
                continue
            key = item.get("task_id") or f"legacy:{item['occurred_at']}:{item['task']}"
            handoff_by_task[key] = {
                "event_id": item["event_id"],
                "occurred_at": item["occurred_at"],
                "task_id": item.get("task_id"),
                "task": item["task"],
                "result": item["result"],
                "main_goal_change": item["main_goal_change"],
            }
        for item in archive_rows:
            if is_auxiliary_task_id(item.get("task_id")):
                continue
            payload = json.loads(item["payload_json"])
            key = item.get("task_id") or f"archive:{item['occurred_at']}:{item['summary']}"
            handoff_by_task[key] = {
                "event_id": item["event_id"],
                "occurred_at": item["occurred_at"],
                "task_id": item.get("task_id"),
                "task": item["summary"],
                "result": payload.get("note") or item["result"],
                "main_goal_change": payload.get("main_goal") or "unchanged",
            }
        handoffs = sorted(
            handoff_by_task.values(),
            key=lambda item: (item["occurred_at"], item["event_id"]),
            reverse=True,
        )[:5]
        for item in handoffs:
            item.pop("event_id", None)
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
        profile = connection.execute(
            "SELECT * FROM project_profile WHERE branch='main'"
        ).fetchone()
        stage_rows = connection.execute(
            "SELECT * FROM stages ORDER BY sequence DESC"
        ).fetchall()
        stages = []
        for row in stage_rows:
            item = dict(row)
            item["acceptance"] = json.loads(item.pop("acceptance_json"))
            item["evidence"] = json.loads(item.pop("evidence_json"))
            stages.append(item)
        attempts = []
        for row in connection.execute(
            "SELECT * FROM attempts ORDER BY updated_at DESC, branch"
        ).fetchall():
            item = dict(row)
            item["acceptance"] = json.loads(item.pop("acceptance_json"))
            item["evidence"] = [evidence["evidence"] for evidence in rows(
                connection,
                "SELECT evidence FROM attempt_evidence WHERE attempt_id=? ORDER BY occurred_at, event_id",
                (item["attempt_id"],),
            )]
            attempts.append(item)
        by_attempt = {item["attempt_id"]: item for item in attempts}
        for item in self._branch_attempts():
            current = by_attempt.get(item["attempt_id"])
            if current is None or item.get("updated_at", "") > current.get("updated_at", ""):
                by_attempt[item["attempt_id"]] = item
        attempts = sorted(
            by_attempt.values(), key=lambda item: (item.get("updated_at", ""), item["attempt_id"]),
            reverse=True,
        )
        return {
            "branch": branch,
            "state": state_data,
            "git_state": self.git_state(),
            "recent_handoffs": handoffs,
            "active_task": active_data,
            "project_profile": dict(profile) if profile else None,
            "current_stage": next((item for item in stages if item["status"] == "active"), None),
            "stages": stages,
            "attempts": attempts,
            "active_attempts": [item for item in attempts if item["state"] == "active"],
        }

    def context(self, branch: str | None = None) -> dict:
        branch = branch or self.branch_provider()
        connection, _ = self._connection()
        try:
            context = self._context(connection, branch)
            try:
                publication = self._publication_projection(connection)
                task_details = self._task_details(connection, publication)
            except (OSError, ReadModelError, subprocess.SubprocessError):
                task_details = {}
            return self._apply_overview_state(context, task_details)
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
                return rows(connection, "SELECT event_id, occurred_at, task_id, summary, evidence, result, branch, payload_json FROM task_archive ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            if kind == "decisions":
                return rows(connection, "SELECT event_id, decision_id, occurred_at, decision, alternatives, basis, reopen_condition, branch, task_id FROM decisions ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            if kind == "explorations":
                return rows(connection, "SELECT x.event_id, x.branch, x.occurred_at, x.goal, x.result, x.evidence, x.disposition_ref, e.task_id FROM explorations x JOIN events e ON e.event_id=x.event_id ORDER BY x.occurred_at DESC, x.event_id DESC LIMIT ?", (limit,))
            if kind == "events":
                values = rows(connection, "SELECT event_id, occurred_at, event_type, branch, task_id, payload_json FROM events ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
                for value in values:
                    value["payload"] = json.loads(value.pop("payload_json"))
                return values
            raise ReadModelError(f"未知查询类型: {kind}")
        finally:
            connection.close()

    @staticmethod
    def _search_index(connection: sqlite3.Connection) -> list[dict]:
        result: list[dict] = []

        def add(kind: str, label: str, target: str, record_id: str, occurred_at: str,
                branch: str, title: str, summary: str, values: list[object],
                task_ids: list[str] | None = None) -> None:
            search_text = " ".join(str(value or "") for value in values)
            result.append({
                "kind": kind,
                "kind_label": label,
                "target": target,
                "record_id": record_id,
                "occurred_at": occurred_at,
                "branch": branch,
                "title": title,
                "summary": summary,
                "search_text": search_text,
                "task_ids": task_ids or [],
            })

        task_rows = rows(connection, "SELECT event_id, occurred_at, task_id, summary, evidence, result, branch FROM task_archive")
        for item in task_rows:
            add("task", "任务", "history", item["event_id"], item["occurred_at"], item["branch"],
                item["summary"] or item["task_id"] or "未命名任务", item["result"] or "",
                [item["task_id"], item["summary"], item["evidence"], item["result"], item["branch"]],
                [item["task_id"]] if item["task_id"] else [])

        decision_rows = rows(connection, "SELECT event_id, decision_id, occurred_at, decision, alternatives, basis, reopen_condition, branch, task_id FROM decisions")
        for item in decision_rows:
            add("decision", "决策", "decisions", item["event_id"], item["occurred_at"], item["branch"],
                item["decision"] or item["decision_id"], item["basis"] or "",
                [item["decision_id"], item["decision"], item["alternatives"], item["basis"], item["reopen_condition"], item["branch"], item["task_id"]],
                [item["task_id"]] if item["task_id"] else [])

        exploration_rows = rows(connection, "SELECT x.event_id, x.branch, x.occurred_at, x.goal, x.result, x.evidence, x.disposition_ref, e.task_id FROM explorations x JOIN events e ON e.event_id=x.event_id")
        for item in exploration_rows:
            add("exploration", "探索", "explorations", item["event_id"], item["occurred_at"], item["branch"],
                item["goal"] or item["branch"], item["result"] or "",
                [item["branch"], item["goal"], item["result"], item["evidence"], item["disposition_ref"], item["task_id"]],
                [item["task_id"]] if item["task_id"] else [])

        relation_values: dict[str, list[str]] = {}
        for relation in rows(
            connection,
            "SELECT relation_id, source_id, target_id, relation_type, note FROM catalog_relations",
        ):
            text = " ".join(str(relation.get(key) or "") for key in (
                "relation_id", "source_id", "target_id", "relation_type", "note",
            ))
            relation_values.setdefault(relation["source_id"], []).append(text)
            relation_values.setdefault(relation["target_id"], []).append(text)
        for raw in rows(connection, "SELECT * FROM catalog_items"):
            item = decode_item(raw)
            add(
                "catalog", item["kind_label"], "catalog", item["item_id"],
                item["updated_at"], item["branch"], item["title"], item["summary"],
                [
                    item["item_id"], item["kind"], item["kind_label"], item["title"],
                    item["summary"], item.get("path"), item["status"], item["tags"],
                    item["source"], item["metadata"], item["branch"], item.get("task_id"),
                    *relation_values.get(item["item_id"], []),
                ],
                [item["task_id"]] if item.get("task_id") else [],
            )

        return sorted(
            result,
            key=lambda item: (item["occurred_at"][:19].replace(" ", "T"), item["record_id"]),
            reverse=True,
        )

    @staticmethod
    def _task_details(connection: sqlite3.Connection, publication: dict) -> dict[str, dict]:
        event_rows = rows(
            connection,
            "SELECT event_id, occurred_at, event_type, branch, task_id, payload_json FROM events "
            "WHERE task_id IS NOT NULL ORDER BY occurred_at, event_id",
        )
        events_by_task: dict[str, list[dict]] = {}
        for event in event_rows:
            event["payload"] = json.loads(event.pop("payload_json"))
            events_by_task.setdefault(event["task_id"], []).append(event)

        archives = {
            item["task_id"]: item for item in rows(
                connection,
                "SELECT event_id, occurred_at, task_id, summary, evidence, result, branch, payload_json "
                "FROM task_archive WHERE task_id IS NOT NULL",
            )
        }
        decisions = rows(
            connection,
            "SELECT event_id, decision_id, occurred_at, decision, alternatives, basis, reopen_condition, branch, task_id "
            "FROM decisions WHERE task_id IS NOT NULL",
        )
        explorations = rows(
            connection,
            "SELECT x.event_id, x.branch, x.occurred_at, x.goal, x.result, x.evidence, x.disposition_ref, e.task_id "
            "FROM explorations x JOIN events e ON e.event_id=x.event_id WHERE e.task_id IS NOT NULL",
        )
        decisions_by_task: dict[str, list[dict]] = {}
        explorations_by_task: dict[str, list[dict]] = {}
        commits_by_task: dict[str, list[dict]] = {}
        for item in decisions:
            decisions_by_task.setdefault(item["task_id"], []).append(item)
        for item in explorations:
            explorations_by_task.setdefault(item["task_id"], []).append(item)
        for commit in publication.get("commits", []):
            for task_id in commit.get("task_ids", []):
                commits_by_task.setdefault(task_id, []).append(commit)

        task_ids = set(events_by_task) | set(archives) | set(decisions_by_task) | set(explorations_by_task) | set(commits_by_task)
        details: dict[str, dict] = {}
        for task_id in task_ids:
            task_events = events_by_task.get(task_id, [])
            archive = archives.get(task_id)
            start = next((item for item in task_events if item["event_type"] == "task.started"), None)
            finish = next((item for item in reversed(task_events) if item["event_type"] in {"task.finished", "task.receipt_imported"}), None)
            attempt_start = next((item for item in task_events if item["event_type"] == "attempt.started"), None)
            finish_payload = finish["payload"] if finish else {}
            start_payload = start["payload"] if start else {}
            attempt_payload = attempt_start["payload"] if attempt_start else {}

            evidence: list[str] = []
            if archive and archive.get("evidence"):
                evidence.append(archive["evidence"])
            conclusion = finish_payload.get("note") or ""
            for event in task_events:
                payload = event["payload"]
                values = payload.get("evidence", [])
                if isinstance(values, str):
                    values = [values]
                for value in values:
                    if value and value not in evidence:
                        evidence.append(value)
                if payload.get("conclusion"):
                    conclusion = payload["conclusion"]

            related: list[dict] = []
            for item in decisions_by_task.get(task_id, []):
                related.append({
                    "kind": "decision", "kind_label": "决策", "occurred_at": item["occurred_at"],
                    "title": item["decision"], "summary": item["basis"], "target": "decisions",
                    "record_id": item["event_id"], "task_id": task_id,
                })
            for item in explorations_by_task.get(task_id, []):
                related.append({
                    "kind": "exploration", "kind_label": "探索", "occurred_at": item["occurred_at"],
                    "title": item["goal"], "summary": item["result"], "target": "explorations",
                    "record_id": item["event_id"], "task_id": task_id,
                })
            for event in task_events:
                payload = event["payload"]
                summary = next((payload.get(key) for key in ("summary", "scope", "goal", "decision", "state", "task") if payload.get(key)), "")
                related.append({
                    "kind": "event", "kind_label": "事件", "occurred_at": event["occurred_at"],
                    "title": event["event_type"], "summary": str(summary), "target": "events",
                    "record_id": event["event_id"], "task_id": task_id,
                })
            related.sort(key=lambda item: (item["occurred_at"], item["record_id"]), reverse=True)

            branch = (archive or {}).get("branch") or (task_events[0]["branch"] if task_events else "")
            started_at = start["occurred_at"] if start else finish_payload.get("started_at") or (task_events[0]["occurred_at"] if task_events else "")
            finished_at = (archive or {}).get("occurred_at") or (finish["occurred_at"] if finish else "")
            result = (archive or {}).get("result") or "active"
            task_commits = commits_by_task.get(task_id, [])
            linked_commits = [commit["hash"] for commit in task_commits]
            published_commits = [commit["hash"] for commit in task_commits if commit.get("published")]
            publication_available = publication.get("publication", {}).get("available", False)
            if not finish:
                publication_status = "进行中"
            elif not linked_commits:
                publication_status = "仅记录"
            elif not publication_available:
                publication_status = "未知"
            elif len(published_commits) == len(linked_commits):
                publication_status = "已发布"
            elif published_commits:
                publication_status = "部分发布"
            else:
                publication_status = "待发布"
            details[task_id] = {
                "task_id": task_id,
                "goal": start_payload.get("scope") or (archive or {}).get("summary") or attempt_payload.get("goal") or "",
                "acceptance": start_payload.get("acceptance") or attempt_payload.get("acceptance") or "",
                "started_at": started_at,
                "finished_at": finished_at,
                "branch": branch,
                "result": result,
                "status": publication_status,
                "publication_status": publication_status,
                "is_auxiliary": is_auxiliary_task_id(task_id),
                "parent_task_id": None,
                "auxiliary_tasks": [],
                "route": finish_payload.get("route") or "",
                "conclusion": conclusion,
                "evidence": evidence,
                "related": related,
            }
        return details

    @staticmethod
    def _apply_overview_state(context: dict, task_details: dict[str, dict]) -> dict:
        state = context.get("state")
        if not state:
            context["overview_state"] = None
            context["visible_next_steps"] = []
            context["completed_next_steps"] = []
            context["publication_completed"] = False
            return context
        overview = dict(state)
        recorded_steps = list(state.get("next_steps", []))
        git_state = context.get("git_state") or {}
        source = task_details.get(state.get("task_id") or "", {})
        parent_task_id = source.get("parent_task_id")
        primary = task_details.get(parent_task_id, source) if parent_task_id else source
        task_is_published = primary.get("publication_status") == "已发布"
        if git_state.get("relation") == "synced" and task_is_published:
            completed_steps = [
                step for step in recorded_steps if is_publication_step(step)
            ]
        else:
            completed_steps = []
        visible_steps = [
            step for step in recorded_steps if step not in completed_steps
        ]
        publication_completed = bool(
            not context.get("active_task")
            and context.get("branch") == "main"
            and git_state.get("relation") == "synced"
            and task_is_published
        )
        overview["next_steps"] = visible_steps
        if publication_completed:
            task_name = primary.get("goal") or state.get("goal") or "已验证改动"
            conclusion = primary.get("conclusion") or "任务已完成"
            upstream_ref = git_state.get("upstream_ref") or "origin/main"
            upstream_head = git_state.get("upstream_head") or ""
            overview["status"] = "已完成并发布"
            overview["judgment"] = f"{task_name}已发布到 {upstream_ref}"
            overview["breakpoint"] = (
                f"{conclusion}；发布提交 {upstream_head[:8]}"
                if upstream_head else conclusion
            )
            overview["task_id"] = primary.get("task_id") or state.get("task_id")
        context["overview_state"] = overview
        context["visible_next_steps"] = visible_steps
        context["completed_next_steps"] = completed_steps
        context["publication_completed"] = publication_completed
        return context

    def dashboard_snapshot(self, branch: str | None = None, *, limit: int = 1000) -> dict:
        branch = branch or self.branch_provider()
        connection, rebuilt = self._connection()
        try:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            context = self._context(connection, branch)
            history = rows(connection, "SELECT event_id, occurred_at, task_id, summary, evidence, result, branch, payload_json FROM task_archive ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            decisions = rows(connection, "SELECT event_id, decision_id, occurred_at, decision, alternatives, basis, reopen_condition, branch, task_id FROM decisions ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            explorations = rows(connection, "SELECT x.event_id, x.branch, x.occurred_at, x.goal, x.result, x.evidence, x.disposition_ref, e.task_id FROM explorations x JOIN events e ON e.event_id=x.event_id ORDER BY x.occurred_at DESC, x.event_id DESC LIMIT ?", (limit,))
            events = rows(connection, "SELECT event_id, occurred_at, event_type, branch, task_id, payload_json FROM events ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (limit,))
            for event in events:
                event["payload"] = json.loads(event.pop("payload_json"))
            try:
                publication = self._publication_projection(connection)
            except (OSError, ReadModelError, subprocess.SubprocessError) as exc:
                publication = {"publication": {"ref": "origin/main", "available": False}, "commits": []}
            task_details = self._task_details(connection, publication)
            context = self._apply_overview_state(context, task_details)
            catalog_items = [
                decode_item(item) for item in rows(
                    connection,
                    "SELECT * FROM catalog_items ORDER BY updated_at DESC, item_id",
                )
            ]
            catalog_relations = rows(
                connection,
                "SELECT * FROM catalog_relations ORDER BY updated_at DESC, relation_id",
            )
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
                "search_index": self._search_index(connection),
                "task_details": task_details,
                "catalog_items": catalog_items,
                "catalog_relations": catalog_relations,
                "resource_directories": resource_directory_snapshot(
                    self.database_path.parent.parent, catalog_items
                ),
            }
        finally:
            connection.close()
