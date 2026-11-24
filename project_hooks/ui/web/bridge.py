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
from .projections import web_snapshot


WEBVIEW2_DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
WEB_ASSET_PACKAGE = Path("project_hooks") / "ui" / "web" / "assets"
ALLOWED_OPEN_ROOTS = ("resources", "diagnostics-export")
PHYSICS_ROADMAP_PATH = Path("maintenance") / "PHYSICS_RESEARCH_ROADMAP.md"
UNDERSTANDING_GUIDE_PATH = Path("maintenance") / "RESEARCH_UNDERSTANDING_GUIDE.md"


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
        refresh_seconds: float = 3.0, project_root: Path | None = None,
    ):
        self._provider = provider if project_root is None else None
        self._provider_factory = provider if project_root is not None else None
        self._provider_lock = threading.Lock()
        self.project_root = (project_root or provider.project_root).resolve()
        self.diagnostic_checks = diagnostic_checks or (lambda: {"status": "not-requested"})
        self.refresh_seconds = max(0.0, float(refresh_seconds))
        self._condition = threading.Condition()
        self._refreshing = False
        self._last_snapshot: dict | None = None
        self._last_token: str | None = None
        self._changed_sections: list[str] = []
        self._view_lock = threading.Lock()
        self._view_locks: dict[str, threading.Lock] = {}
        self._views: dict[str, tuple] = {}

    @property
    def provider(self):
        if self._provider is None:
            with self._provider_lock:
                if self._provider is None:
                    self._provider = self._provider_factory()
        return self._provider

    @staticmethod
    def _ok(data: object = None) -> dict:
        return {"ok": True, "data": data, "error": None}

    @staticmethod
    def _error(exc: Exception | str, *, code: str = "request_failed") -> dict:
        return {"ok": False, "data": None, "error": {"code": code, "message": str(exc)}}

    def ready(self) -> dict:
        from ...infrastructure.git.build_identity import build_identity, display_version
        from ...infrastructure.system.build_notes import read_build_notes
        marker = os.environ.get("WORKFLOW_MONITOR_BRIDGE_READY_FILE")
        if marker:
            try:
                Path(marker).touch(exist_ok=True)
            except OSError:
                pass
        return self._ok({
            "bridge": "ready", "version": __version__,
            "display_version": display_version(__version__),
            "build_identity": build_identity(),
            "refresh_seconds": self.refresh_seconds,
            "physics_roadmap_available": self._physics_roadmap_path() is not None,
            "research_understanding_guide_available": self._guide_path(UNDERSTANDING_GUIDE_PATH) is not None,
            "test_build_notes_available": read_build_notes()["available"],
        })

    def _physics_roadmap_path(self) -> Path | None:
        return self._guide_path(PHYSICS_ROADMAP_PATH)

    def _guide_path(self, relative: Path) -> Path | None:
        try:
            maintenance_root = (self.project_root / "maintenance").resolve()
            maintenance_root.relative_to(self.project_root)
            candidate = (self.project_root / relative).resolve()
            candidate.relative_to(maintenance_root)
        except (OSError, ValueError):
            return None
        return candidate if candidate.is_file() else None

    def open_physics_research_roadmap(self) -> dict:
        from ...infrastructure.system.external_navigation import open_file_default
        """Open only the project's fixed, optional physics research guide."""
        candidate = self._physics_roadmap_path()
        if candidate is None:
            return self._error("the physics research roadmap is unavailable", code="missing_path")
        try:
            open_file_default(candidate)
            relative = candidate.relative_to(self.project_root).as_posix()
            return self._ok({"path": relative})
        except OSError as exc:
            return self._error(exc, code="open_failed")

    def open_research_understanding_guide(self) -> dict:
        from ...infrastructure.system.external_navigation import open_file_default
        candidate = self._guide_path(UNDERSTANDING_GUIDE_PATH)
        if candidate is None:
            return self._error("the research understanding guide is unavailable", code="missing_path")
        try:
            open_file_default(candidate)
            return self._ok({"path": candidate.relative_to(self.project_root).as_posix()})
        except OSError as exc:
            return self._error(exc, code="open_failed")

    def get_test_build_notes(self) -> dict:
        from ...infrastructure.system.build_notes import read_build_notes
        return self._ok(read_build_notes())

    def bootstrap(self) -> dict:
        return self.refresh(None, True)

    def get_project_snapshot(self) -> dict:
        return self.refresh(None, True)

    def get_workflow_snapshot(self, workflow_id: str, task_id: str | None = None) -> dict:
        try:
            return self._ok(self.provider.workflow(workflow_id, task_id))
        except (OSError, ValueError, RuntimeError) as exc:
            return self._error(exc, code="workflow_unavailable")

    def get_workflow_content(self, workflow_id: str) -> dict:
        try:
            return self._ok(self.provider.workflow_content(workflow_id))
        except (OSError, ValueError, RuntimeError) as exc:
            return self._error(exc, code="workflow_unavailable")

    def refresh_view(self, view: str, state_token: str | None = None, force: bool = False) -> dict:
        from ...infrastructure.system.dashboard_views import DASHBOARD_VIEWS
        if not isinstance(view, str) or view not in DASHBOARD_VIEWS:
            return self._error("unknown Dashboard view", code="invalid_parameters")
        with self._view_lock:
            view_lock = self._view_locks.setdefault(view, threading.Lock())
        with view_lock:
            previous, previous_token, previous_time = self._views.get(view, ({}, None, None))
            try:
                loaded = self.provider.load_view(view, force=force)
                if view in {"workflow", "settings"}:
                    matrix = self._action_matrix()
                    if matrix is not None:
                        loaded["action_matrix"] = matrix
                loaded = web_snapshot(loaded)
                snapshot_revision = _token(loaded)
                changed = [key for key in loaded if _token(previous.get(key)) != _token(loaded.get(key))]
                stamp = datetime.now().astimezone().isoformat(timespec="seconds")
                self._views[view] = loaded, snapshot_revision, stamp
                return self._ok({"view": view, "state_token": snapshot_revision,
                                 "snapshot": loaded if force or snapshot_revision != state_token else None,
                                 "changed_sections": changed, "updated_at": stamp, "stale": False})
            except Exception as exc:
                reply = self._error(exc, code="refresh_failed")
                reply["data"] = {"view": view, "state_token": previous_token, "snapshot": previous or None,
                                 "updated_at": previous_time, "stale": True}
                return reply

    def _action_matrix(self) -> dict | None:
        service = getattr(self.provider, "action_service", None)
        if service is None:
            return None
        matrix = service.availability_matrix()
        return {
            "state_token": matrix.state_token, "state": matrix.state,
            "actions": [{"action_id": item.action_id, "label": item.label, "category": item.category,
                         "status": item.status, "missing_fields": list(item.missing_fields),
                         "availability": item.availability.as_dict()} for item in matrix.actions],
        }

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
            matrix = self._action_matrix()
            if matrix is not None:
                source["action_matrix"] = matrix
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
        from ...infrastructure.system.dashboard_actions import export_bundle
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
        from ...infrastructure.system.diagnostics import diagnostics_status
        from ...infrastructure.system.dashboard_actions import report_bug as open_dashboard_bug
        try:
            incident_id = diagnostics_status(self.project_root).get("latest_incident_id")
            return self._ok({"opened": open_dashboard_bug(incident_id=incident_id),
                             "incident_id": incident_id})
        except Exception as exc:
            return self._error(exc, code="report_bug_failed")

    def _resource_snapshot(self) -> dict:
        if self._last_snapshot is not None:
            return self._last_snapshot
        if hasattr(self.provider, "load_view"):
            return self._views.get("resources", (None,))[0] or self.provider.load_view("resources")
        return self.provider.load()

    def open_resource_directory(self, relative: str) -> dict:
        from ...infrastructure.system.resource_layout import RESOURCE_DIRECTORIES
        from ...infrastructure.system.external_navigation import open_directory
        allowed = {"resources", *(item.relative_path for item in RESOURCE_DIRECTORIES)}
        snapshot = self._resource_snapshot()
        allowed.update(folder["path"] for folder in snapshot.get("catalog_folders") or []
                       if folder.get("status") != "archived")
        allowed.update(item["path"] for item in snapshot.get("catalog_items") or []
                       if item.get("status") != "archived" and item.get("path")
                       and (item.get("metadata") or {}).get("entry_type") == "bundle")
        if relative not in allowed:
            return self._error("unknown standard resource directory", code="invalid_path")
        candidate = (self.project_root / relative).resolve()
        try:
            rel = candidate.relative_to(self.project_root)
            candidate.relative_to(self.project_root / "resources")
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
        from ...infrastructure.system.external_navigation import open_directory, reveal_file
        if not isinstance(item_id, str) or not item_id.strip():
            return self._error("item_id is required", code="invalid_parameters")
        snapshot = self._resource_snapshot()
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
        from ...core.catalog import render_context_markdown
        if item_ids is not None and (not isinstance(item_ids, list)
                                     or any(not isinstance(value, str) for value in item_ids)):
            return self._error("item_ids must be a string array", code="invalid_parameters")
        snapshot = self._resource_snapshot()
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
    project_root: Path | None = None,
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
        refresh_seconds=refresh_seconds, project_root=project_root,
    )
    user_data = bridge.project_root / ".project_hooks" / "webview2"
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
