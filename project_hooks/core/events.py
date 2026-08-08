"""Pure event protocol, validation, identity, and serialization rules."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone as fixed_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import StoreError


SCHEMA_VERSION = 3
SUPPORTED_EVENT_SCHEMA_VERSIONS = (1, 2, 3)


def resolve_timezone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Shanghai":
            return fixed_timezone(timedelta(hours=8), name)
        if name in {"UTC", "Etc/UTC"}:
            return fixed_timezone.utc
        raise StoreError(f"系统缺少时区数据 {name}；请安装 tzdata 或改用 Asia/Shanghai/UTC")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def new_event(event_type: str, *, branch: str, task_id: str | None, payload: dict,
              timezone: str, event_id: str | None = None,
              occurred_at: str | None = None) -> dict:
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
    if not all(isinstance(event[key], str) and event[key]
               for key in ("event_id", "event_type", "occurred_at", "branch")):
        raise StoreError(f"{where}包含空或非法标识字段")
    if event["task_id"] is not None and not isinstance(event["task_id"], str):
        raise StoreError(f"{where} task_id 必须是字符串或 null")
    if not isinstance(event["payload"], dict):
        raise StoreError(f"{where} payload 必须是对象")
    return event
