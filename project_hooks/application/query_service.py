"""Presentation-neutral Dashboard query orchestration."""

from __future__ import annotations

from typing import Callable

from ..core.lifecycle import active_task_warning
from ..core.stage_versions import annotate_parallel_conflicts


class WorkbenchQueryService:
    def __init__(
        self,
        *,
        snapshot_loader: Callable[[str | None], dict],
        classifier: Callable[[str], dict],
        diagnostics_loader: Callable[[], dict],
        branch: str | None = None,
        project_loader: Callable[[], dict | None] | None = None,
    ):
        self.snapshot_loader = snapshot_loader
        self.classifier = classifier
        self.diagnostics_loader = diagnostics_loader
        self.branch = branch
        self.project_loader = project_loader

    def load(self) -> dict:
        project = self.project_loader() if self.project_loader else None
        snapshot = self.snapshot_loader(self.branch)
        snapshot["classification"] = self.classifier(snapshot["branch"])
        active = snapshot.get("context", {}).get("active_task")
        snapshot["active_task_warning"] = active_task_warning(active)
        snapshot["diagnostics"] = self.diagnostics_loader()
        if project:
            local = snapshot.get("context", {}).get("active_task")
            if not project["enabled"] and local:
                task = {**local, "status": "active",
                        "current_step": (snapshot["context"].get("state") or {}).get("current_step")}
                project["workflows"][0].update(current_task=task, last_task=task, task_count=1)
                project["tasks"] = [task]
            snapshot["project_monitor"] = project
            annotate_parallel_conflicts(snapshot.get("context", {}), project)
        return snapshot
