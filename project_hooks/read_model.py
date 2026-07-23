"""Shared read-only projections for CLI output and the desktop dashboard."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable

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
    active = context.get("active_task")
    active_text = (
        f"{active.get('task_id')}（{active.get('branch') or context.get('branch') or '未知分支'}）"
        if active else "无"
    )
    lines = [
        "项目行动概览",
        f"状态：{compact_text(state.get('status'))}（目标版本：{compact_text(state.get('main_goal_version'))}）",
        f"活动任务：{active_text}",
        f"当前阻塞：{compact_text(state.get('blocker'), '无。')}",
        "下一步：",
    ]
    steps = state.get("next_steps") or []
    lines.extend(f"{index}. {compact_text(item)}" for index, item in enumerate(steps, 1))
    if not steps:
        lines.append("无。")
    lines.extend([
        f"Git 同步：{git_state_inline(context.get('git_state') or {})}",
        f"当前目标：{compact_text(state.get('goal'))}",
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
    return "提交" in text and any(
        token in text for token in ("已验证", "改动", "代码", "文件", "版本", "变更")
    )


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

    @staticmethod
    def _lane_priority(name: str, default: str) -> tuple[int, str]:
        if name == default:
            return 2, name
        if name.startswith(("research/", "experiment/", "sandbox/", "archive/")):
            return 0, name
        return 1, name

    @staticmethod
    def _branch_kind(name: str, default: str) -> str:
        if name == default:
            return "stable"
        prefix = name.split("/", 1)[0]
        return prefix if prefix in {"research", "experiment", "sandbox", "archive"} else "other"

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
        branch_refs: dict[str, list[str]] = {}
        for branch, tip, remote in ref_entries:
            source_ref = branch
            if remote and "/" in branch:
                suffix = branch.split("/", 1)[1]
                if local_tips.get(suffix) == tip or (suffix not in local_tips and len(remote_groups[suffix]) == 1):
                    branch = suffix
            branch_tips.setdefault(branch, tip)
            if source_ref not in branch_refs.setdefault(branch, []):
                branch_refs[branch].append(source_ref)
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
        publication_ref = f"origin/{default}"
        publication_tip = next(
            (tip for branch, tip, remote in ref_entries if remote and branch == publication_ref),
            None,
        )

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
        publication_ancestry = ancestry(publication_tip) if publication_tip else set()
        for commit in commits:
            commit["published"] = commit["hash"] in publication_ancestry if publication_tip else None
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
        branches = []
        for name in lanes:
            tip = branch_tips.get(name)
            kind = self._branch_kind(name, default)
            merged = bool(tip and tip in default_ancestry)
            branches.append({
                "name": name,
                "tip": tip,
                "refs": branch_refs.get(name, []),
                "kind": kind,
                "merged": merged,
                "unmerged": kind in {"research", "experiment", "sandbox"} and bool(tip) and not merged,
            })
        result = {
            "status": "passed",
            "default_branch": default,
            "publication": {
                "ref": publication_ref,
                "tip": publication_tip,
                "available": publication_tip is not None,
            },
            "lanes": lanes,
            "branches": branches,
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
        return {
            "branch": branch,
            "state": state_data,
            "git_state": self.git_state(),
            "recent_handoffs": handoffs,
            "active_task": active_data,
        }

    def context(self, branch: str | None = None) -> dict:
        branch = branch or self.branch_provider()
        connection, _ = self._connection()
        try:
            context = self._context(connection, branch)
            try:
                timeline = self._build_timeline(connection)
                task_details = self._task_details(connection, timeline)
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
    def _search_index(connection: sqlite3.Connection, timeline: dict) -> list[dict]:
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

        for commit in timeline.get("commits", []):
            add("commit", "提交", "timeline", commit["hash"], commit["occurred_at"], commit["lane"],
                commit["subject"], commit["short_hash"],
                [commit["hash"], commit["short_hash"], commit["subject"], commit["author"], commit["lane"],
                 *commit.get("refs", []), *commit.get("task_ids", []), *commit.get("event_branches", [])],
                list(commit.get("task_ids", [])))

        return sorted(
            result,
            key=lambda item: (item["occurred_at"][:19].replace(" ", "T"), item["record_id"]),
            reverse=True,
        )

    @staticmethod
    def _task_details(connection: sqlite3.Connection, timeline: dict) -> dict[str, dict]:
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
        for commit in timeline.get("commits", []):
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
            for commit in commits_by_task.get(task_id, []):
                related.append({
                    "kind": "commit", "kind_label": "提交", "occurred_at": commit["occurred_at"],
                    "title": commit["subject"], "summary": commit["short_hash"], "target": "timeline",
                    "record_id": commit["hash"], "task_id": task_id,
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
            publication_available = timeline.get("publication", {}).get("available", False)
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
                "linked_commits": linked_commits,
                "publication_commits": published_commits,
                "is_auxiliary": is_auxiliary_task_id(task_id),
                "parent_task_id": None,
                "auxiliary_tasks": [],
                "route": finish_payload.get("route") or "",
                "conclusion": conclusion,
                "evidence": evidence,
                "related": related,
            }
        commits_by_hash = {
            commit["hash"]: commit for commit in timeline.get("commits", [])
        }

        def task_time(task_id: str) -> str:
            item = details.get(task_id, {})
            return item.get("finished_at") or item.get("started_at") or ""

        def choose_primary(task_ids: list[str], auxiliary_time: str) -> str | None:
            candidates = [
                task_id for task_id in task_ids
                if task_id in details and not details[task_id]["is_auxiliary"]
            ]
            before = [task_id for task_id in candidates if task_time(task_id) <= auxiliary_time]
            pool = before or candidates
            return max(pool, key=task_time) if pool else None

        for task_id, detail in details.items():
            if not detail["is_auxiliary"]:
                continue
            candidate_ids: list[str] = []
            linked = [
                commits_by_hash[commit_hash]
                for commit_hash in detail["linked_commits"]
                if commit_hash in commits_by_hash
            ]
            for commit in linked:
                candidate_ids.extend(commit.get("task_ids", []))
            parent_task_id = choose_primary(candidate_ids, detail["started_at"])
            if parent_task_id is None:
                pending = [
                    parent
                    for commit in reversed(linked)
                    for parent in commit.get("parents", [])[:1]
                ]
                visited: set[str] = set()
                while pending and parent_task_id is None:
                    commit_hash = pending.pop(0)
                    if commit_hash in visited:
                        continue
                    visited.add(commit_hash)
                    commit = commits_by_hash.get(commit_hash)
                    if commit is None:
                        continue
                    parent_task_id = choose_primary(
                        commit.get("task_ids", []), detail["started_at"],
                    )
                    if parent_task_id is None:
                        pending.extend(commit.get("parents", [])[:1])
            detail["parent_task_id"] = parent_task_id
            if parent_task_id:
                details[parent_task_id]["auxiliary_tasks"].append({
                    "task_id": task_id,
                    "goal": detail["goal"],
                    "started_at": detail["started_at"],
                    "finished_at": detail["finished_at"],
                    "result": detail["result"],
                    "status": detail["status"],
                    "conclusion": detail["conclusion"],
                    "evidence": detail["evidence"],
                    "linked_commits": detail["linked_commits"],
                })
        for detail in details.values():
            detail["auxiliary_tasks"].sort(
                key=lambda item: (item["finished_at"], item["task_id"]),
            )
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
            completed_steps
            and not visible_steps
            and not context.get("active_task")
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
            timeline_error = None
            try:
                timeline = self._build_timeline(connection)
            except (OSError, ReadModelError, subprocess.SubprocessError) as exc:
                timeline = {"status": "unavailable", "lanes": [], "branches": [], "commits": [], "edges": []}
                timeline_error = str(exc)
            task_details = self._task_details(connection, timeline)
            context = self._apply_overview_state(context, task_details)
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
                "search_index": self._search_index(connection, timeline),
                "task_details": task_details,
            }
        finally:
            connection.close()
