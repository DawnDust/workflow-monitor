"""Presentation-neutral data provider for the local Web workbench."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__
from .diagnostics import diagnostics_overview
from .read_model import MaintenanceReadModel
from .store import load_events
from .updater import check_latest_update, refresh_software_delivery, software_delivery_report
from .workbench import external_tools_from_events
from .workflow_actions import WorkflowActionService


def active_task_warning(active: dict | None, now: datetime | None = None) -> dict | None:
    if not active or not active.get("started_at"):
        return None
    clean = str(active["started_at"]).split("（", 1)[0].split("(", 1)[0].strip()
    try:
        started = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    age_hours = max(0.0, ((now or datetime.now()) - started).total_seconds() / 3600)
    if age_hours >= 24 * 7:
        return {"level": "red", "age_hours": round(age_hours, 1), "message": "活动任务已超过 7 天，请恢复或明确放弃"}
    if age_hours >= 24:
        return {"level": "yellow", "age_hours": round(age_hours, 1), "message": "活动任务已超过 24 小时，请确认是否继续"}
    return None


class WebWorkbenchDataProvider:
    def __init__(self, model: MaintenanceReadModel, classifier: Callable[[str], dict],
                 branch: str | None = None, action_service: WorkflowActionService | None = None):
        self.model = model
        self.classifier = classifier
        self.branch = branch
        self.action_service = action_service

    @property
    def project_root(self) -> Path:
        return self.model.database_path.parent.parent

    def load(self) -> dict:
        snapshot = self.model.dashboard_snapshot(self.branch)
        snapshot["classification"] = self.classifier(snapshot["branch"])
        snapshot["active_task_warning"] = active_task_warning(snapshot.get("context", {}).get("active_task"))
        events = load_events(self.model.journal_path)
        snapshot["external_tools"] = external_tools_from_events(events)
        snapshot["diagnostics"] = diagnostics_overview(self.project_root, application_version=__version__)
        return snapshot

    def check_for_updates(self) -> dict:
        return check_latest_update(self.project_root)

    def refresh_software_delivery(self, release: dict) -> dict:
        return refresh_software_delivery(self.project_root, release)

    def check_software_delivery(self) -> dict:
        return software_delivery_report(self.project_root)
