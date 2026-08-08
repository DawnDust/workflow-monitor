"""Privacy-preserving local diagnostics independent from the maintenance store."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import tempfile
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
LEDGER_MAX_RECORDS = 200
PROTECTED_CATEGORIES = {"internal_error", "data_integrity", "external_dependency"}
AUTO_CLEAN_CATEGORIES = {"validation", "conflict"}
NON_DIAGNOSTIC_CATEGORIES = {"validation", "conflict"}
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
        return "conflict", "PH-C200", "先运行 `.\\workflow-monitor.exe status`，确认冲突后再重试。"
    if any(word in message for word in (
        "integrity", "database", "sqlite", "数据库", "事件 id 内容冲突", "日志", "schema",
    )) or "database" in name:
        return "data_integrity", "PH-D300", "运行 `.\\workflow-monitor.exe db verify`，不要先删除数据库或日志。"
    if name in {"readmodelerror", "storeerror", "jsondecodeerror"}:
        return "data_integrity", "PH-D300", "运行 `.\\workflow-monitor.exe db verify`，不要先删除数据库或日志。"
    if name == "updateerror" or isinstance(exc, OSError):
        return "external_dependency", "PH-X400", "检查本地文件或外部服务状态后重试；持续失败时导出诊断。"
    expected_names = {
        "workflowerror", "catalogerror", "projectmanagererror", "valueerror",
        "actionblockederror",
    }
    if name in expected_names or isinstance(exc, ValueError):
        return "validation", "PH-E100", "按错误提示修正输入或前置条件后重试。"
    return "internal_error", "PH-I500", "导出诊断包并将事件编号提交给开发者。"


def is_recordable_failure(exc: BaseException) -> bool:
    """Return whether a failure is actionable as a persisted software diagnostic."""
    category, _code, _suggestion = classify_failure(exc)
    return category not in NON_DIAGNOSTIC_CATEGORIES


def _fingerprint(category: str, exc: BaseException, summary: str) -> str:
    normalized = _VOLATILE.sub("#", summary.lower())
    source = f"{category}|{type(exc).__name__}|{normalized}".encode("utf-8", "replace")
    return hashlib.sha256(source).hexdigest()[:16]


def diagnostic_directory(project_root: Path) -> Path:
    return project_root / ".project_hooks" / "diagnostics"


def _record_files(directory: Path) -> list[Path]:
    return [*(directory / f"events.{index}.jsonl" for index in range(HISTORY_FILES, 0, -1)),
            directory / "events.jsonl"]


def _ledger_path(project_root: Path) -> Path:
    return diagnostic_directory(project_root) / "ledger.jsonl"


def load_ledger(project_root: Path) -> list[dict]:
    path = _ledger_path(project_root)
    if not path.is_file():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records[-LEDGER_MAX_RECORDS:]


def _atomic_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def append_ledger(project_root: Path, record: dict) -> None:
    """Append a bounded, path-free receipt without affecting the primary operation."""
    try:
        directory = diagnostic_directory(project_root)
        with _LOCK:
            with event_lock(directory, timeout=1.0):
                records = load_ledger(project_root)
                records.append(record)
                _atomic_jsonl(_ledger_path(project_root), records[-LEDGER_MAX_RECORDS:])
    except Exception:
        pass


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
    record["recorded"] = is_recordable_failure(exc)
    if record["recorded"]:
        append_record(project_root, record)
    return record


def format_failure(record: dict) -> str:
    lines = [f"[{record['code']}] {record['summary']}"]
    if record.get("recorded", True):
        lines.append(f"事件编号：{record['incident_id']}")
    lines.append(f"建议：{record['suggestion']}")
    return "\n".join(lines)


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


def diagnostics_overview(project_root: Path, *, application_version: str) -> dict:
    """Return fingerprint-grouped issues plus export and cleanup receipts for the UI."""
    records = load_records(project_root)
    ledger = load_ledger(project_root)
    exported: dict[str, dict] = {}
    resolved: set[str] = set()
    cleanups: list[dict] = []
    for entry in ledger:
        kind = entry.get("type")
        if kind == "bundle_exported":
            for incident_id in entry.get("incident_ids") or []:
                exported[str(incident_id)] = entry
        elif kind == "fingerprint_resolved":
            resolved.add(str(entry.get("fingerprint") or ""))
        elif kind in {"records_auto_cleaned", "records_cleaned"}:
            cleanups.append(entry)
    grouped: dict[str, list[dict]] = {}
    for record in records:
        fingerprint = str(record.get("fingerprint") or record.get("incident_id") or "unknown")
        grouped.setdefault(fingerprint, []).append(record)
    issues: list[dict] = []
    for fingerprint, items in grouped.items():
        latest = items[-1]
        incident_ids = [str(item.get("incident_id") or "") for item in items]
        export_entries = [exported[item] for item in incident_ids if item in exported]
        category = str(latest.get("category") or "unknown")
        versions = sorted({str(item.get("application_version") or "unknown") for item in items})
        current = application_version in versions
        is_resolved = fingerprint in resolved
        if is_resolved:
            status = "resolved"
        elif category in PROTECTED_CATEGORIES and not current:
            status = "old_version_protected"
        elif current:
            status = "current"
        else:
            status = "old_version"
        last_export = max(export_entries, key=lambda item: str(item.get("exported_at") or "")) if export_entries else None
        issues.append({
            "fingerprint": fingerprint,
            "category": category,
            "code": latest.get("code") or "PH-UNKNOWN",
            "summary": latest.get("summary") or "未知诊断",
            "suggestion": latest.get("suggestion") or "请检查诊断详情。",
            "count": len(items),
            "incident_ids": incident_ids,
            "versions": versions,
            "latest_at": latest.get("occurred_at"),
            "status": status,
            "protected": category in PROTECTED_CATEGORIES,
            "exported": bool(export_entries),
            "last_export_id": last_export.get("export_id") if last_export else None,
            "last_exported_at": last_export.get("exported_at") if last_export else None,
        })
    issues.sort(key=lambda item: str(item.get("latest_at") or ""), reverse=True)
    return {
        "status": "available",
        "records": len(records),
        "issues": issues,
        "cleanups": cleanups[-20:][::-1],
        "exports": [item for item in ledger if item.get("type") == "bundle_exported"][-20:][::-1],
        "automatic_upload": False,
    }


def resolve_diagnostic(
    project_root: Path, fingerprint: str, *, reason: str, application_version: str,
) -> dict:
    records = [item for item in load_records(project_root) if item.get("fingerprint") == fingerprint]
    if not records:
        raise ValueError("没有找到该诊断指纹")
    append_ledger(project_root, {
        "type": "fingerprint_resolved",
        "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "application_version": application_version,
        "fingerprint": fingerprint,
        "reason": sanitize_text(reason, project_root=project_root)[:500],
    })
    return {"status": "resolved", "fingerprint": fingerprint, "records": len(records)}


def _prune_records(project_root: Path, fingerprints: set[str], *, receipt_type: str, metadata: dict) -> dict:
    directory = diagnostic_directory(project_root)
    removed: list[dict] = []
    try:
        with _LOCK:
            with event_lock(directory, timeout=1.0):
                records = load_records(project_root)
                kept = []
                for record in records:
                    if str(record.get("fingerprint") or "") in fingerprints:
                        removed.append(record)
                    else:
                        kept.append(record)
                for path in _record_files(directory):
                    path.unlink(missing_ok=True)
                if kept:
                    _atomic_jsonl(directory / "events.jsonl", kept)
        receipt = {
            "type": receipt_type,
            "cleaned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "records": len(removed),
            "fingerprints": sorted({str(item.get("fingerprint") or "") for item in removed}),
            **metadata,
        }
        append_ledger(project_root, receipt)
        return {"status": "cleaned", **receipt}
    except Exception as exc:
        return {"status": "failed", "error_type": type(exc).__name__, "records": 0, **metadata}


def cleanup_resolved_diagnostics(project_root: Path) -> dict:
    resolved = {
        str(item.get("fingerprint") or "") for item in load_ledger(project_root)
        if item.get("type") == "fingerprint_resolved"
    }
    return _prune_records(project_root, resolved, receipt_type="records_cleaned", metadata={})


def cleanup_after_update(project_root: Path, *, from_version: str, to_version: str) -> dict:
    """Best-effort graded cleanup after a fully successful project migration."""
    fingerprints = {
        str(record.get("fingerprint") or "")
        for record in load_records(project_root)
        if record.get("application_version") != to_version
        and record.get("category") in AUTO_CLEAN_CATEGORIES
    }
    return _prune_records(
        project_root, fingerprints, receipt_type="records_auto_cleaned",
        metadata={"from_version": from_version, "to_version": to_version},
    )


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
    export_id = uuid.uuid4().hex[:12]
    incident_ids = [str(record.get("incident_id") or "") for record in records]
    fingerprints = sorted({str(record.get("fingerprint") or "") for record in records})
    categories: dict[str, int] = {}
    for record in records:
        category = str(record.get("category") or "unknown")
        categories[category] = categories.get(category, 0) + 1
    report = (
        "# Workflow Monitor 脱敏诊断报告\n\n"
        f"- 导出时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"- 应用版本：{application_version}\n"
        f"- 导出批次：{export_id}\n"
        f"- 诊断记录：{len(records)}\n"
        f"- 故障指纹：{len(fingerprints)}\n"
        f"- 最近事件编号：{latest.get('incident_id') if latest else '无'}\n"
        "- 自动上传：否\n\n"
        "此文件包不包含维护 SQLite、完整事件日志、项目文件、环境变量或 Git 远程地址。"
    )
    manifest = {
        "format": "project-hooks-diagnostics",
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "export_id": export_id,
        "incident_ids": incident_ids,
        "fingerprints": fingerprints,
        "categories": categories,
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
    exported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    append_ledger(project_root, {
        "type": "bundle_exported",
        "export_id": export_id,
        "exported_at": exported_at,
        "bundle_name": sanitize_text(output.name, project_root=project_root),
        "records": len(records),
        "incident_ids": incident_ids,
        "fingerprints": fingerprints,
        "categories": categories,
    })
    return {
        "status": "exported",
        "export_id": export_id,
        "output": str(output),
        "records": len(records),
        "latest_incident_id": latest.get("incident_id") if latest else None,
        "automatic_upload": False,
    }


def execution_mode() -> str:
    return "frozen" if getattr(sys, "frozen", False) else "source"
from .build_identity import build_identity
