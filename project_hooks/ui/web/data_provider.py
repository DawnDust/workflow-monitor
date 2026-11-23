"""Presentation-neutral data provider for the local Dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ...application.query_service import WorkbenchQueryService
from ...application.action_service import WorkflowActionService


class WebWorkbenchDataProvider:
    def __init__(self, project_root: Path, queries: WorkbenchQueryService, *,
                 action_service: WorkflowActionService | None = None,
                 update_checker: Callable[[], dict],
                 software_delivery_refresher: Callable[[dict], dict],
                 software_delivery_checker: Callable[[], dict],
                 workflow_loader: Callable[[str, str | None], dict] | None = None,
                 workflow_content_loader: Callable[[str], dict] | None = None,
                 dashboard_loader: Callable | None = None):
        self._project_root = project_root.resolve()
        self.queries = queries
        self.action_service = action_service
        self.update_checker = update_checker
        self.software_delivery_refresher = software_delivery_refresher
        self.software_delivery_checker = software_delivery_checker
        self.workflow_loader = workflow_loader
        self.workflow_content_loader = workflow_content_loader
        self.dashboard_loader = dashboard_loader

    @property
    def project_root(self) -> Path:
        return self._project_root

    def load(self) -> dict:
        return self.queries.load()

    def load_view(self, view: str, *, force: bool = False) -> dict:
        if self.dashboard_loader is None:
            return self.load()
        return self.dashboard_loader(view, force=force)

    def workflow(self, workflow_id: str, task_id: str | None) -> dict:
        if self.workflow_loader is None:
            raise RuntimeError("工作流详情不可用")
        return self.workflow_loader(workflow_id, task_id)

    def workflow_content(self, workflow_id: str) -> dict:
        if self.workflow_content_loader is None:
            raise RuntimeError("工作流内容不可用")
        return self.workflow_content_loader(workflow_id)

    def check_for_updates(self) -> dict:
        return self.update_checker()

    def refresh_software_delivery(self, release: dict) -> dict:
        return self.software_delivery_refresher(release)

    def check_software_delivery(self) -> dict:
        return self.software_delivery_checker()
