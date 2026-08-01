"""Privacy-preserving local diagnostics independent from the maintenance store."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import threading
import uuid
import webbrowser
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from .store import event_lock


DIAGNOSTIC_SCHEMA_VERSION = 1
MAX_RECORDS = 500
MAX_BYTES = 2 * 1024 * 1024
HISTORY_FILES = 3
ISSUE_URL = "https://github.com/DawnDust/project-maintenance-template/issues/new"
_LOCK = threading.RLock()

_WINDOWS_PATH = re.compile(r"(?i)(?:[a-z]:\\|\\\\)[^\s\r\n\"']+")
_POSIX_HOME = re.compile(r"(?<![\w.])/(?:home|users)/[^/\s]+(?:/[^\s\r\n\"']*)?", re.I)
_URL_SECRET = re.compile(r"(?i)(https?://[^\s?#]+)(?:[?#][^\s]*)")
_SECRET = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization)\b\s*[:=]\s*([^\s,;]+)"
)
_LONG_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b")
_VOLATILE = re.compile(r"\b(?:[0-9a-f]{8,}|\d{4,})\b", re.I)


def sanitize_text(value: object, *, project_root: Path | None = None) -> str:
    """Remove paths and likely credentials while keeping a useful short summary."""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    roots = [Path.home()]
    if project_root is not None:
        roots.append(project_root.resolve())
    for root in roots:
        rendered = str(root)
        if rendered:
            text = re.sub(re.escape(rendered), "<redacted-path>", text, flags=re.I)
    text = _WINDOWS_PATH.sub("<redacted-path>", text)
    text = _POSIX_HOME.sub("<redacted-path>", text)
    text = _URL_SECRET.sub(r"\1?<redacted>", text)
    text = _SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = _LONG_TOKEN.sub("<redacted-token>", text)
    return text[:1000] or "未知错误"


def classify_failure(exc: BaseException) -> tuple[str, str, str]:
    """Return category, stable error code and a safe next action."""
    message = str(exc).lower()
    name = type(exc).__name__.lower()
    if any(word in message for word in ("冲突", "conflict", "分叉", "切换分支", "拒绝覆盖")):
        return "conflict", "PH-C200", "先运行 `.\\project-hooks.exe status`，确认冲突后再重试。"
    if any(word in message for word in (
        "integrity", "database", "sqlite", "数据库", "事件 id 内容冲突", "日志", "schema",
    )) or "database" in name:
        return "data_integrity", "PH-D300", "运行 `.\\project-hooks.exe db verify`，不要先删除数据库或日志。"
    expected_names = {
        "workflowerror", "catalogerror", "dashboarderror", "projectmanagererror",
        "readmodelerror", "storeerror", "updateerror", "valueerror", "jsondecodeerror",
    }
    if name in expected_names or isinstance(exc, (ValueError, OSError)):
        return "validation", "PH-E100", "按错误提示修正输入或前置条件后重试。"
    return "internal_error", "PH-I500", "导出诊断包并将事件编号提交给开发者。"


def _fingerprint(category: str, exc: BaseException, summary: str) -> str:
    normalized = _VOLATILE.sub("#", summary.lower())
    source = f"{category}|{type(exc).__name__}|{normalized}".encode("utf-8", "replace")
    return hashlib.sha256(source).hexdigest()[:16]


def diagnostic_directory(project_root: Path) -> Path:
    return project_root / ".project_hooks" / "diagnostics"


def _record_files(directory: Path) -> list[Path]:
    return [*(directory / f"events.{index}.jsonl" for index in range(HISTORY_FILES, 0, -1)),
            directory / "events.jsonl"]


def _rotate_if_needed(path: Path, incoming_size: int) -> None:
    if not path.exists():
        return
    try:
        record_count = sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line)
        should_rotate = record_count >= MAX_RECORDS or path.stat().st_size + incoming_size > MAX_BYTES
    except OSError:
        return
    if not should_rotate:
        return
    oldest = path.with_name(f"events.{HISTORY_FILES}.jsonl")
    oldest.unlink(missing_ok=True)
    for index in range(HISTORY_FILES - 1, 0, -1):
        source = path.with_name(f"events.{index}.jsonl")
        if source.exists():
            source.replace(path.with_name(f"events.{index + 1}.jsonl"))
    path.replace(path.with_name("events.1.jsonl"))


def append_record(project_root: Path, record: dict) -> None:
    """Best-effort append. Diagnostics must never break the original command."""
    try:
        encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        directory = diagnostic_directory(project_root)
        with _LOCK:
            with event_lock(directory, timeout=1.0):
                path = directory / "events.jsonl"
                _rotate_if_needed(path, len(encoded))
                with path.open("ab") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
    except Exception:
        pass


def create_failure_record(
    project_root: Path,
    exc: BaseException,
    *,
    command: str,
    application_version: str,
    schema_version: int,
    execution_mode: str,
    git_state: dict | None = None,
) -> dict:
    category, code, suggestion = classify_failure(exc)
    summary = sanitize_text(exc, project_root=project_root)
    relation = (git_state or {}).get("relation")
    branch_kind = (git_state or {}).get("branch_kind")
    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "incident_id": uuid.uuid4().hex[:12],
        "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "application_version": application_version,
        "maintenance_schema_version": schema_version,
        "execution_mode": execution_mode,
        "command": re.sub(r"[^a-z0-9_.-]", "", command.lower())[:80] or "unknown",
        "category": category,
        "code": code,
        "exception_type": type(exc).__name__,
        "summary": summary,
        "fingerprint": _fingerprint(category, exc, summary),
        "suggestion": suggestion,
        "git": {"relation": relation or "unknown", "branch_kind": branch_kind or "unknown"},
    }


def record_failure(project_root: Path, exc: BaseException, **metadata) -> dict:
    record = create_failure_record(project_root, exc, **metadata)
    append_record(project_root, record)
    return record


def format_failure(record: dict) -> str:
    return (
        f"[{record['code']}] {record['summary']}\n"
        f"事件编号：{record['incident_id']}\n"
        f"建议：{record['suggestion']}"
    )


def load_records(project_root: Path) -> list[dict]:
    records: list[dict] = []
    directory = diagnostic_directory(project_root)
    for path in _record_files(directory):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records[-MAX_RECORDS:]


def diagnostics_status(project_root: Path) -> dict:
    records = load_records(project_root)
    categories: dict[str, int] = {}
    for record in records:
        category = str(record.get("category", "unknown"))
        categories[category] = categories.get(category, 0) + 1
    return {
        "status": "available",
        "records": len(records),
        "categories": categories,
        "latest_incident_id": records[-1].get("incident_id") if records else None,
        "automatic_upload": False,
    }


def bug_report_url(*, incident_id: str | None = None, version: str | None = None) -> str:
    body = (
        "## 复现步骤\n\n1. \n\n## 预期结果\n\n\n## 实际结果\n\n\n"
        f"## 诊断信息\n\n- 版本：{version or '请填写'}\n"
        f"- 事件编号：{incident_id or '请填写'}\n\n"
        "请检查诊断 ZIP 后，将其拖到此 Issue。ZIP 不会被程序自动上传。"
    )
    return ISSUE_URL + "?" + urlencode({"labels": "bug", "title": "[Bug] ", "body": body})


def open_bug_report(*, incident_id: str | None = None, version: str | None = None) -> bool:
    return bool(webbrowser.open(bug_report_url(incident_id=incident_id, version=version)))


def export_diagnostics(
    project_root: Path,
    output: Path,
    *,
    application_version: str,
    schema_version: int,
    execution_mode: str,
    checks: Callable[[], dict] | None = None,
) -> dict:
    """Export a strict allowlist bundle. Project data and the maintenance journal are excluded."""
    records = load_records(project_root)
    environment = {
        "diagnostic_schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "application_version": application_version,
        "maintenance_schema_version": schema_version,
        "execution_mode": execution_mode,
        "operating_system": platform.system(),
        "operating_system_release": platform.release(),
        "python_version": platform.python_version(),
        "automatic_upload": False,
        "build_identity": build_identity(),
    }
    check_result: dict
    try:
        check_result = checks() if checks is not None else {"status": "not_requested"}
    except Exception as exc:
        check_result = {"status": "failed", "summary": sanitize_text(exc, project_root=project_root)}
    latest = records[-1] if records else None
    report = (
        "# project-hooks 脱敏诊断报告\n\n"
        f"- 导出时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"- 应用版本：{application_version}\n"
        f"- 诊断记录：{len(records)}\n"
        f"- 最近事件编号：{latest.get('incident_id') if latest else '无'}\n"
        "- 自动上传：否\n\n"
        "此文件包不包含维护 SQLite、完整事件日志、项目文件、环境变量或 Git 远程地址。"
    )
    manifest = {
        "format": "project-hooks-diagnostics",
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "files": ["manifest.json", "report.md", "environment.json", "checks.json", "diagnostics.jsonl"],
    }
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.md", report)
        archive.writestr("environment.json", json.dumps(environment, ensure_ascii=False, indent=2) + "\n")
        archive.writestr("checks.json", json.dumps(check_result, ensure_ascii=False, indent=2) + "\n")
        archive.writestr(
            "diagnostics.jsonl",
            "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        )
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {
        "status": "exported",
        "output": str(output),
        "records": len(records),
        "latest_incident_id": latest.get("incident_id") if latest else None,
        "automatic_upload": False,
    }


def execution_mode() -> str:
    return "frozen" if getattr(sys, "frozen", False) else "source"
from .build_identity import build_identity
