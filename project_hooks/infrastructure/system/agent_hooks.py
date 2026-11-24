"""Antigravity lifecycle hook handler (SessionStart and PreToolUse guards)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable, TextIO


_DESTRUCTIVE_TARGET_RE = re.compile(
    r"(?:maintenance[\\/]events\.jsonl|\.project_hooks[\\/]active-task(?:\.json)?|"
    r"\.project_hooks[\\/]maintenance\.sqlite3(?:-wal|-shm)?|"
    r"\.git[\\/]workflow-monitor[\\/]registry\.sqlite3(?:-wal|-shm)?)",
    re.IGNORECASE,
)
_DESTRUCTIVE_OP_RE = re.compile(
    r"(?:\b(?:rm|rmdir|del|erase|remove-item|ri|clear-content|set-content|out-file|"
    r"truncate|sqlite3)\b|>>?)",
    re.IGNORECASE,
)
_WORKFLOW_MONITOR_RE = re.compile(r"\bworkflow-monitor(?:\.exe)?\b", re.IGNORECASE)
_HIGH_RISK_COMMAND_RE = re.compile(
    r"(?:\bgit\s+(?:push|merge)\b|\bgh\s+pr\s+merge\b|"
    r"\btask\s+abandon\b|\bdb\s+rebuild\b)",
    re.IGNORECASE,
)


def find_hook_project(
    payload: dict,
    *,
    project_finder: Callable[[Path], Path | None],
) -> Path | None:
    """Locate the Workflow Monitor project root from hook metadata."""
    candidates: list[str] = []
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        candidates.append(cwd)
    workspace_paths = payload.get("workspacePaths")
    if isinstance(workspace_paths, list):
        candidates.extend(item for item in workspace_paths if isinstance(item, str) and item.strip())
    for raw in candidates:
        try:
            found = project_finder(Path(raw))
        except Exception:
            found = None
        if found is not None:
            return found
    return None


def _resolve_target_path(raw_path: str, payload: dict, project_root: Path) -> Path | None:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate.resolve()
    cwd = payload.get("cwd")
    base = Path(cwd) if isinstance(cwd, str) and cwd.strip() else project_root
    return (base / candidate).resolve()


def _handle_pre_tool_use(payload: dict, project_root: Path) -> dict:
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return {}
    name = str(tool_call.get("name") or "")
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return {}
    if name in {"write_file", "edit_file"}:
        raw_path = args.get("Path") or args.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return {}
        try:
            target = _resolve_target_path(raw_path, payload, project_root)
            resolved_root = project_root.resolve()
        except OSError:
            return {}
        if target is None or not target.is_relative_to(resolved_root):
            return {}
        relative = target.relative_to(resolved_root).as_posix().lower()
        is_protected = (
            relative == "maintenance/events.jsonl"
            or relative.startswith(".git/workflow-monitor/")
            or (
                relative.startswith(".project_hooks/")
                and (".sqlite3" in relative or relative.startswith(".project_hooks/active-task"))
            )
        )
        if is_protected:
            return {
                "decision": "deny",
                "reason": "禁止直接修改维护事件日志、活动状态或 SQLite 数据库；请使用 workflow-monitor.exe 生命周期命令。",
            }
        return {}
    if name == "run_command":
        command = str(args.get("CommandLine") or args.get("command") or "").strip()
        if not command:
            return {}
        if (
            not _WORKFLOW_MONITOR_RE.search(command)
            and _DESTRUCTIVE_TARGET_RE.search(command)
            and _DESTRUCTIVE_OP_RE.search(command)
        ):
            return {
                "decision": "deny",
                "reason": "禁止通过终端命令直接删除活动状态或改写事件日志/SQLite；请使用 workflow-monitor.exe 命令。",
            }
        if _HIGH_RISK_COMMAND_RE.search(command):
            return {
                "decision": "force_ask",
                "reason": "该命令涉及外部交付或高风险维护操作，按 Workflow Monitor 规范必须获得用户明确确认。",
            }
    return {}


def handle_hook_payload(
    payload: dict,
    *,
    project_finder: Callable[[Path], Path | None],
    context_loader: Callable[[Path], str],
) -> dict:
    """Process one Antigravity hook event payload and return the JSON response."""
    if not isinstance(payload, dict):
        return {}
    project_root = find_hook_project(payload, project_finder=project_finder)
    if project_root is None:
        return {}
    event_name = str(payload.get("hookEventName") or "")
    if event_name == "SessionStart":
        try:
            brief = context_loader(project_root).strip()
        except Exception:
            return {}
        if not brief:
            return {}
        return {"injectSteps": [{"systemMessage": {"systemMessage": brief}}]}
    if event_name == "PreToolUse":
        return _handle_pre_tool_use(payload, project_root)
    return {}


def handle_stdin(
    *,
    project_finder: Callable[[Path], Path | None],
    context_loader: Callable[[Path], str],
    stream: TextIO | None = None,
) -> dict:
    """Read an Antigravity hook payload from stdin with safe empty fallback."""
    source = stream or sys.stdin
    try:
        raw = source.read()
        if not raw or not raw.strip():
            return {}
        payload = json.loads(raw)
    except Exception:
        return {}
    return handle_hook_payload(
        payload,
        project_finder=project_finder,
        context_loader=context_loader,
    )
