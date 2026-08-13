"""Presentation-neutral Dashboard query orchestration."""

from __future__ import annotations

from typing import Callable

from ..core.lifecycle import active_task_warning


class WorkbenchQueryService:
    def __init__(
        self,
        *,
        snapshot_loader: Callable[[str | None], dict],
        classifier: Callable[[str], dict],
        diagnostics_loader: Callable[[], dict],
        branch: str | None = None,
    ):
        self.snapshot_loader = snapshot_loader
        self.classifier = classifier
        self.diagnostics_loader = diagnostics_loader
        self.branch = branch

    def load(self) -> dict:
        snapshot = self.snapshot_loader(self.branch)
        snapshot["classification"] = self.classifier(snapshot["branch"])
        active = snapshot.get("context", {}).get("active_task")
        snapshot["active_task_warning"] = active_task_warning(active)
        snapshot["diagnostics"] = self.diagnostics_loader()
        return snapshot
