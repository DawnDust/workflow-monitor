"""Stable launcher for selecting an installed, versioned project-hooks core."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__


ACTIVE_ENV = "PROJECT_HOOKS_CORE_ACTIVE"


def cache_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "project-maintenance-workflow"
    return Path.home() / ".cache" / "project-maintenance-workflow"


def selected_core() -> Path | None:
    pointer = cache_root() / "current.json"
    if not pointer.is_file():
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
        selected = str(data["version"])
        core = cache_root() / "cores" / selected
    except (KeyError, OSError, ValueError, TypeError):
        return None
    try:
        selected_key = tuple(int(part) for part in selected.split("."))
        bundled_key = tuple(int(part) for part in __version__.split("."))
        if selected_key < bundled_key:
            return None
    except ValueError:
        return None
    return core if (core / "project_hooks" / "__init__.py").is_file() else None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    core = None if os.environ.get(ACTIVE_ENV) else selected_core()
    if core is not None:
        env = os.environ.copy()
        env[ACTIVE_ENV] = "1"
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(core) + (os.pathsep + existing if existing else "")
        completed = subprocess.run(
            [sys.executable, "-m", "project_hooks", *args],
            env=env,
            check=False,
        )
        return completed.returncode
    from .cli import main as cli_main
    return cli_main(args)
