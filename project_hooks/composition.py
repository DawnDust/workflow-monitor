"""Application composition root.

This is the only non-UI module that intentionally knows both application
services and concrete infrastructure adapters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .application.action_service import WorkflowActionService
from .application.query_service import WorkbenchQueryService
from . import __version__
from .infrastructure.persistence.store import SCHEMA_VERSION
from .infrastructure.persistence.transaction import mutation_lock
from .infrastructure.system.diagnostics import (
    diagnostics_overview,
    execution_mode,
    format_failure,
    record_failure,
)
from .infrastructure.system.updater import (
    check_latest_update,
    refresh_software_delivery,
    software_delivery_report,
)
from .ui.web.data_provider import WebWorkbenchDataProvider
from .infrastructure.system.dashboard_views import DashboardViews
from .infrastructure.system.project_search import ProjectSearch
from .infrastructure.system.workflow_views import primary_root, project_snapshot, workflow_snapshot, workflow_content, checkout_model


def build_action_service(
    project_root: Path,
    *,
    state_provider: Callable[[], dict],
    executor: Callable,
) -> WorkflowActionService:
    """Wire the workflow action use case to host-system adapters."""
    return WorkflowActionService(
        project_root,
        state_provider=state_provider,
        executor=executor,
        mutation_lock_factory=mutation_lock,
        failure_recorder=record_failure,
        failure_formatter=format_failure,
        execution_mode_provider=execution_mode,
        application_version=__version__,
        schema_version=SCHEMA_VERSION,
    )


def build_web_data_provider(model, classifier, *, branch=None, action_service=None):
    """Wire the read-only Web provider without allowing UI-to-CLI imports."""
    project_root = primary_root(model.repo_path)
    if project_root != model.repo_path:
        model = checkout_model(project_root, "main")
    def diagnostics():
        return diagnostics_overview(project_root, application_version=__version__)

    views = DashboardViews(project_root, model, ProjectSearch(project_root, model), diagnostics)
    queries = WorkbenchQueryService(
        snapshot_loader=model.dashboard_snapshot,
        classifier=classifier,
        diagnostics_loader=diagnostics,
        branch=branch,
        project_loader=lambda: project_snapshot(project_root, known_primary=True),
    )
    return WebWorkbenchDataProvider(
        project_root,
        queries,
        action_service=action_service,
        update_checker=lambda: check_latest_update(project_root),
        software_delivery_refresher=lambda release: refresh_software_delivery(project_root, release),
        software_delivery_checker=lambda: software_delivery_report(project_root),
        workflow_loader=lambda workflow_id, task_id: workflow_snapshot(project_root, workflow_id, task_id),
        workflow_content_loader=lambda workflow_id: workflow_content(project_root, workflow_id),
        dashboard_loader=views.load,
    )
