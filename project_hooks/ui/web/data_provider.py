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
                 software_delivery_checker: Callable[[], dict]):
        self._project_root = project_root.resolve()
        self.queries = queries
        self.action_service = action_service
        self.update_checker = update_checker
        self.software_delivery_refresher = software_delivery_refresher
        self.software_delivery_checker = software_delivery_checker

    @property
    def project_root(self) -> Path:
        return self._project_root

    def load(self) -> dict:
        return self.queries.load()

    def check_for_updates(self) -> dict:
        return self.update_checker()

    def refresh_software_delivery(self, release: dict) -> dict:
        return self.software_delivery_refresher(release)

    def check_software_delivery(self) -> dict:
        return self.software_delivery_checker()
