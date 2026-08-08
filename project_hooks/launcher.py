"""Stable launcher for the project-local Windows executable runtime."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import EXECUTABLE_NAME, __version__


ACTIVE_ENV = "PROJECT_HOOKS_EXE_ACTIVE"
PORTABLE_ROOT_ENV = "PROJECT_HOOKS_PORTABLE_ROOT"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def configure_utf8_stdio() -> None:
    """Keep CLI output deterministic on Windows legacy code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def runtime_root(project_root: Path) -> Path:
    return project_root.resolve() / ".project_hooks" / "runtime"


def selected_executable(project_root: Path) -> Path | None:
    """Return the selected project-local executable when it is usable."""
    runtime = runtime_root(project_root)
    pointer = runtime / "current.json"
    if not pointer.is_file():
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
        selected = str(data["version"])
        executable = Path(str(data.get("executable") or (
            runtime / "executables" / selected / EXECUTABLE_NAME
        ))).resolve()
        selected_key = tuple(int(part) for part in selected.split("."))
        bundled_key = tuple(int(part) for part in __version__.split("."))
    except (KeyError, OSError, ValueError, TypeError):
        return None
    if selected_key < bundled_key or not executable.is_file():
        return None
    try:
        if executable.samefile(Path(sys.executable)):
            return None
    except OSError:
        pass
    return executable


def run_selected_executable(args: list[str], *, portable_root: Path) -> int | None:
    """Delegate the stable project EXE to its selected project-local version."""
    if not is_frozen() or os.environ.get(ACTIVE_ENV):
        return None
    executable = selected_executable(portable_root)
    if executable is None:
        return None
    env = os.environ.copy()
    env[ACTIVE_ENV] = "1"
    env[PORTABLE_ROOT_ENV] = str(portable_root.resolve())
    completed = subprocess.run([str(executable), *args], env=env, check=False)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    """Source-tree development entrypoint; releases use windows_entry.main."""
    configure_utf8_stdio()
    from .cli import main as cli_main

    return cli_main(list(sys.argv[1:] if argv is None else argv))
