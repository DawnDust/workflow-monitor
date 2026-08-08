"""Append-only projection and validation for external workbench reminders."""

from __future__ import annotations

import re
from typing import Iterable


EXTERNAL_TOOL_KINDS = ("notes", "literature", "computation", "skill", "repository", "other")
EXTERNAL_TOOL_STATUSES = ("active", "paused", "retired")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_ABSOLUTE_PATH = re.compile(r"(?i)(?:^[a-z]:[\\/]|^\\\\|^/(?:home|users)/)")
_SECRET = re.compile(r"(?i)\b(?:token|password|passwd|secret|api[_-]?key|authorization)\b\s*[:=]")


class WorkbenchError(ValueError):
    pass


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    result = str(value or "").replace("\r", " ").strip()
    if required and not result:
        raise WorkbenchError(f"{label}不能为空")
    if len(result) > maximum:
        raise WorkbenchError(f"{label}不能超过 {maximum} 个字符")
    if _SECRET.search(result):
        raise WorkbenchError(f"{label}不能包含凭据或密钥")
    return result


def validate_tool_id(value: object) -> str:
    tool_id = str(value or "").strip().lower()
    if not _ID.fullmatch(tool_id):
        raise WorkbenchError("工具 ID 只允许小写字母、数字和连字符，最长 80 个字符")
    return tool_id


def normalize_external_tool(fields: dict, *, current: dict | None = None) -> dict:
    base = dict(current or {})
    tool_id = validate_tool_id(fields.get("tool_id") or base.get("tool_id"))
    kind = str(fields.get("kind", base.get("kind", "other")) or "other")
    if kind not in EXTERNAL_TOOL_KINDS:
        raise WorkbenchError("未知的外置工具类型")
    status = str(fields.get("status", base.get("status", "active")) or "active")
    if status not in EXTERNAL_TOOL_STATUSES:
        raise WorkbenchError("未知的外置工具状态")
    reference = _text(fields.get("reference", base.get("reference", "")), "参考信息", 500)
    if reference and _ABSOLUTE_PATH.search(reference):
        raise WorkbenchError("参考信息不能保存用户名或绝对路径")
    return {
        "tool_id": tool_id,
        "name": _text(fields.get("name", base.get("name")), "名称", 120, required=True),
        "kind": kind,
        "purpose": _text(fields.get("purpose", base.get("purpose")), "用途", 500, required=True),
        "usage_hint": _text(fields.get("usage_hint", base.get("usage_hint", "")), "使用提示", 1000),
        "reference": reference,
        "status": status,
    }


def external_tools_from_events(events: Iterable[dict]) -> list[dict]:
    tools: dict[str, dict] = {}
    ordered = sorted(events, key=lambda item: (str(item.get("occurred_at") or ""), str(item.get("event_id") or "")))
    for event in ordered:
        kind = event.get("event_type")
        payload = event.get("payload") or {}
        if kind == "workbench.external_upserted":
            try:
                record = normalize_external_tool(payload)
            except WorkbenchError:
                continue
            record.update({
                "updated_at": event.get("occurred_at"),
                "task_id": event.get("task_id"),
                "event_id": event.get("event_id"),
            })
            tools[record["tool_id"]] = record
        elif kind == "workbench.external_status_changed":
            tool_id = str(payload.get("tool_id") or "")
            status = str(payload.get("status") or "")
            if tool_id in tools and status in EXTERNAL_TOOL_STATUSES:
                tools[tool_id] = {
                    **tools[tool_id],
                    "status": status,
                    "status_note": str(payload.get("note") or ""),
                    "updated_at": event.get("occurred_at"),
                    "task_id": event.get("task_id"),
                    "event_id": event.get("event_id"),
                }
    return sorted(tools.values(), key=lambda item: (item.get("kind", ""), item.get("name", "").casefold()))
