"""Project configuration and installation manifest path resolution."""

from __future__ import annotations

import json
from pathlib import Path


PRIMARY_CONFIG_DIR = Path(".workflow-monitor")
LEGACY_CONFIG_DIR = Path(".codex")

PRIMARY_CONFIG_PATH = Path(".workflow-monitor/workflow.json")
LEGACY_CONFIG_PATH = Path(".codex/project-maintenance-workflow.json")

PRIMARY_INSTALLATION_PATH = Path(".workflow-monitor/installation.json")
LEGACY_INSTALLATION_PATH = Path(".codex/project-maintenance-installation.json")

CONFIG_CANDIDATES = (PRIMARY_CONFIG_PATH, LEGACY_CONFIG_PATH)
INSTALLATION_CANDIDATES = (PRIMARY_INSTALLATION_PATH, LEGACY_INSTALLATION_PATH)


def resolve_config_relative(root: Path) -> Path:
    """Return the active relative workflow config path, defaulting to primary."""
    for relative in CONFIG_CANDIDATES:
        if (root / relative).is_file():
            return relative
    return PRIMARY_CONFIG_PATH


def resolve_config_path(root: Path) -> Path:
    """Resolve the absolute workflow configuration file path for a project."""
    return root / resolve_config_relative(root)


def resolve_installation_relative(root: Path) -> Path:
    """Return the active relative installation manifest path, defaulting to primary."""
    for relative in INSTALLATION_CANDIDATES:
        if (root / relative).is_file():
            return relative
    return PRIMARY_INSTALLATION_PATH


def resolve_installation_path(root: Path) -> Path:
    """Resolve the absolute installation manifest path for a project."""
    return root / resolve_installation_relative(root)


def has_project_config(root: Path) -> bool:
    """Return True when either primary or legacy workflow config exists."""
    return any((root / relative).is_file() for relative in CONFIG_CANDIDATES)


def read_project_config(root: Path) -> dict:
    """Load and decode the project workflow configuration JSON."""
    return json.loads(resolve_config_path(root).read_text(encoding="utf-8"))
