"""Presentation-neutral workbench query orchestration."""

from __future__ import annotations

from typing import Callable

from ..core.lifecycle import active_task_warning


class WorkbenchQueryService:
    def __init__(
        self,
        *,
        snapshot_loader: Callable[[str | None], dict],
        classifier: Callable[[str], dict],
        event_loader: Callable[[], list[dict]],
        external_tools_projector: Callable[[list[dict]], list[dict]],
        workbench_items_projector: Callable[[list[dict]], list[dict]],
        diagnostics_loader: Callable[[], dict],
        branch: str | None = None,
    ):
        self.snapshot_loader = snapshot_loader
        self.classifier = classifier
        self.event_loader = event_loader
        self.external_tools_projector = external_tools_projector
        self.workbench_items_projector = workbench_items_projector
        self.diagnostics_loader = diagnostics_loader
        self.branch = branch

    def load(self) -> dict:
        snapshot = self.snapshot_loader(self.branch)
        snapshot["classification"] = self.classifier(snapshot["branch"])
        active = snapshot.get("context", {}).get("active_task")
        snapshot["active_task_warning"] = active_task_warning(active)
        events = self.event_loader()
        snapshot["external_tools"] = self.external_tools_projector(events)
        snapshot["workbench_items"] = self.workbench_items_projector(events)
        snapshot["diagnostics"] = self.diagnostics_loader()
        return snapshot
