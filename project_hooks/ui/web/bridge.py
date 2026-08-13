"""Local WebView2 host and allowlisted bridge for the Web Dashboard."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from ... import DISPLAY_NAME, __version__
from ...core.catalog import render_context_markdown
from ...infrastructure.system.dashboard_actions import export_bundle, report_bug as open_dashboard_bug
from ...infrastructure.system.diagnostics import diagnostics_status
from ...infrastructure.system.external_navigation import open_directory, reveal_file
from ...infrastructure.git.build_identity import build_identity
from .projections import web_snapshot
from ...infrastructure.system.resource_layout import RESOURCE_DIRECTORIES


WEBVIEW2_DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
WEB_ASSET_PACKAGE = Path("project_hooks") / "ui" / "web" / "assets"
ALLOWED_OPEN_ROOTS = ("resources", "diagnostics-export")


def asset_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / WEB_ASSET_PACKAGE
    return Path(__file__).resolve().parent / "assets"


def webview2_runtime_version() -> str | None:
    """Return an installed Evergreen Runtime version without starting WebView2."""
    if platform.system() != "Windows":
        return None
    try:
        import winreg
    except ImportError:
        return None
    clients = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    )
    for hive, key_name in clients:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                value, _ = winreg.QueryValueEx(key, "pv")
            if value and value != "0.0.0.0":
                return str(value)
        except OSError:
            continue
    # The Runtime can also be installed as the Edge WebView application.
    for hive, base in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
                       (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients")):
        try:
            with winreg.OpenKey(hive, base) as parent:
                count = winreg.QueryInfoKey(parent)[0]
                for index in range(count):
                    with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as key:
                        name = str(winreg.QueryValueEx(key, "name")[0]).lower()
                        version = str(winreg.QueryValueEx(key, "pv")[0])
                        if "webview2" in name and version != "0.0.0.0":
                            return version
        except OSError:
            continue
    return None


def _token(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


class WebDashboardBridge:
    """Small explicit API surface exposed to JavaScript by pywebview."""

    def __init__(
        self, provider, *, diagnostic_checks: Callable[[], dict] | None = None,
        refresh_seconds: float = 3.0,
    ):
        self.provider = provider
        self.project_root = provider.project_root.resolve()
        self.diagnostic_checks = diagnostic_checks or (lambda: {"status": "not-requested"})
        self.refresh_seconds = max(0.0, float(refresh_seconds))
        self._condition = threading.Condition()
        self._refreshing = False
        self._last_snapshot: dict | None = None
        self._last_token: str | None = None
        self._changed_sections: list[str] = []

    @staticmethod
    def _ok(data: object = None) -> dict:
        return {"ok": True, "data": data, "error": None}

    @staticmethod
    def _error(exc: Exception | str, *, code: str = "request_failed") -> dict:
        return {"ok": False, "data": None, "error": {"code": code, "message": str(exc)}}

    def ready(self) -> dict:
        marker = os.environ.get("WORKFLOW_MONITOR_BRIDGE_READY_FILE")
        if marker:
            try:
                Path(marker).touch(exist_ok=True)
            except OSError:
                pass
        return self._ok({
            "bridge": "ready", "version": __version__,
            "build_identity": build_identity(),
            "refresh_seconds": self.refresh_seconds,
        })

    def bootstrap(self) -> dict:
        return self.refresh(None, True)

    def refresh(self, state_token: str | None = None, force_full: bool = False) -> dict:
        """Coalesce concurrent refresh calls and preserve the last good snapshot."""
        with self._condition:
            if self._refreshing:
                self._condition.wait_for(lambda: not self._refreshing, timeout=30)
                if self._last_snapshot is not None:
                    return self._refresh_response(state_token, force_full)
            self._refreshing = True
        try:
            source = self.provider.load()
            service = getattr(self.provider, "action_service", None)
            if service is not None:
                matrix = service.availability_matrix()
                source["action_matrix"] = {
                    "state_token": matrix.state_token,
                    "state": matrix.state,
                    "actions": [{
                        "action_id": action.action_id, "label": action.label,
                        "category": action.category, "status": action.status,
                        "missing_fields": list(action.missing_fields),
                        "availability": action.availability.as_dict(),
                    } for action in matrix.actions],
                }
            loaded = web_snapshot(source)
            next_token = _token(loaded)
            with self._condition:
                previous = self._last_snapshot or {}
                self._changed_sections = [
                    key for key in loaded if _token(previous.get(key)) != _token(loaded.get(key))
                ]
                self._last_snapshot = loaded
                self._last_token = next_token
            return self._refresh_response(state_token, force_full)
        except Exception as exc:
            if self._last_snapshot is not None:
                return {
                    "ok": False,
                    "data": {"state_token": self._last_token, "snapshot": self._last_snapshot,
                             "stale": True},
                    "error": {"code": "refresh_failed", "message": str(exc)},
                }
            return self._error(exc, code="refresh_failed")
        finally:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()

    def _refresh_response(self, state_token: str | None, force_full: bool) -> dict:
        unchanged = bool(not force_full and state_token and state_token == self._last_token)
        return self._ok({
            "state_token": self._last_token,
            "snapshot": None if unchanged else self._last_snapshot,
            "unchanged": unchanged,
            "changed_sections": [] if unchanged else self._changed_sections,
            "stale": False,
        })

    def check_updates(self) -> dict:
        try:
            return self._ok(self.provider.check_for_updates())
        except Exception as exc:
            return self._error(exc, code="update_check_failed")

    def refresh_software_delivery(self, release: dict | None = None) -> dict:
        if not isinstance(release, dict):
            return self._error("release must be an object", code="invalid_parameters")
        try:
            return self._ok(self.provider.refresh_software_delivery(release))
        except Exception as exc:
            return self._error(exc, code="software_refresh_failed")

    def export_diagnostics(self, filename: str | None = None) -> dict:
        safe_name = filename or f"workflow-monitor-diagnostics-{datetime.now():%Y%m%d-%H%M%S}.zip"
        if Path(safe_name).name != safe_name or not safe_name.lower().endswith(".zip"):
            return self._error("filename must be a ZIP file name without directories", code="invalid_path")
        try:
            folder = self.project_root / "diagnostics-export"
            folder.mkdir(parents=True, exist_ok=True)
            return self._ok(export_bundle(self.project_root, folder / safe_name, self.diagnostic_checks))
        except Exception as exc:
            return self._error(exc, code="diagnostic_export_failed")

    def report_bug(self) -> dict:
        try:
            incident_id = diagnostics_status(self.project_root).get("latest_incident_id")
            return self._ok({"opened": open_dashboard_bug(incident_id=incident_id),
                             "incident_id": incident_id})
        except Exception as exc:
            return self._error(exc, code="report_bug_failed")

    def open_resource_directory(self, relative: str) -> dict:
        allowed = {item.relative_path for item in RESOURCE_DIRECTORIES}
        if relative not in allowed:
            return self._error("unknown standard resource directory", code="invalid_path")
        candidate = (self.project_root / relative).resolve()
        try:
            rel = candidate.relative_to(self.project_root)
        except ValueError:
            return self._error("path is outside the project", code="invalid_path")
        if not candidate.is_dir():
            return self._error("resource directory does not exist", code="missing_path")
        try:
            open_directory(candidate)
            return self._ok({"path": rel.as_posix()})
        except OSError as exc:
            return self._error(exc, code="open_failed")

    def reveal_resource_file(self, item_id: str) -> dict:
        if not isinstance(item_id, str) or not item_id.strip():
            return self._error("item_id is required", code="invalid_parameters")
        snapshot = self._last_snapshot or web_snapshot(self.provider.load())
        item = next((value for value in snapshot.get("catalog_items") or []
                     if value.get("item_id") == item_id), None)
        if item is None:
            return self._error("catalog item is not registered", code="not_found")
        relative = item.get("path")
        if not isinstance(relative, str):
            return self._error("catalog item has no file path", code="missing_path")
        candidate = (self.project_root / relative).resolve()
        try:
            rel = candidate.relative_to(self.project_root)
        except ValueError:
            return self._error("path is outside the project", code="invalid_path")
        if not rel.parts or rel.parts[0] != "resources":
            return self._error("catalog file is outside resources", code="invalid_path")
        if not candidate.is_file() and not candidate.is_dir():
            return self._error("catalog path does not exist", code="missing_path")
        try:
            if candidate.is_dir():
                open_directory(candidate)
            else:
                reveal_file(candidate)
            return self._ok({"item_id": item_id, "path": rel.as_posix()})
        except OSError as exc:
            return self._error(exc, code="open_failed")

    def copy_context(self, item_ids: list[str] | None = None) -> dict:
        if item_ids is not None and (not isinstance(item_ids, list)
                                     or any(not isinstance(value, str) for value in item_ids)):
            return self._error("item_ids must be a string array", code="invalid_parameters")
        snapshot = self._last_snapshot or web_snapshot(self.provider.load())
        requested = set(item_ids or [])
        items = [item for item in snapshot.get("catalog_items") or []
                 if not requested or item.get("item_id") in requested]
        relations = [relation for relation in snapshot.get("catalog_relations") or []
                     if not requested or relation.get("source_id") in requested
                     or relation.get("target_id") in requested]
        return self._ok({"text": render_context_markdown(items, relations)})

def launch_web_dashboard(
    provider, refresh_seconds: float = 3.0,
    *,
    diagnostic_checks: Callable[[], dict],
    shown_callback: Callable[[], None] | None = None,
) -> None:
    """Launch the local HTML application in an Edge WebView2 window."""
    runtime = webview2_runtime_version()
    if not runtime:
        raise RuntimeError(
            "Microsoft Edge WebView2 Runtime 未安装。可从官方页面安装 Evergreen Runtime："
            f"{WEBVIEW2_DOWNLOAD_URL}"
        )
    index = asset_root() / "index.html"
    if not index.is_file():
        raise RuntimeError(f"Web Dashboard 静态资源缺失：{index}")
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("pywebview 未安装，无法启动 Web Dashboard") from exc
    bridge = WebDashboardBridge(
        provider, diagnostic_checks=diagnostic_checks,
        refresh_seconds=refresh_seconds,
    )
    user_data = provider.project_root / ".project_hooks" / "webview2"
    user_data.mkdir(parents=True, exist_ok=True)
    page_url = index.as_uri()
    requested_theme = os.environ.get("WORKFLOW_MONITOR_THEME")
    if requested_theme in {"light", "dark"}:
        page_url += f"#theme={requested_theme}"
    window = webview.create_window(
        DISPLAY_NAME, page_url, js_api=bridge, width=1280, height=800,
        min_size=(960, 640), text_select=True,
    )
    if shown_callback is not None:
        window.events.shown += shown_callback
    webview.start(gui="edgechromium", debug=False, storage_path=str(user_data), private_mode=False)
