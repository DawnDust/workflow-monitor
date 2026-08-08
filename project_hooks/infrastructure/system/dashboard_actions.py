"""Explicit user-triggered Dashboard actions, separate from read-only data views."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ... import __version__
from .diagnostics import execution_mode, export_diagnostics, open_bug_report
from ..persistence.store import SCHEMA_VERSION


def export_bundle(project_root: Path, output: Path, checks: Callable[[], dict]) -> dict:
    return export_diagnostics(
        project_root,
        output,
        application_version=__version__,
        schema_version=SCHEMA_VERSION,
        execution_mode=execution_mode(),
        checks=checks,
    )


def report_bug(*, incident_id: str | None) -> bool:
    return open_bug_report(incident_id=incident_id, version=__version__)
