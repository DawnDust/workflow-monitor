"""Allowlisted Windows shell navigation used by trusted UI adapters."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_directory(path: Path) -> None:
    subprocess.Popen(["explorer.exe", str(path)])


def reveal_file(path: Path) -> None:
    subprocess.Popen(["explorer.exe", f"/select,{path}"])


def open_file_default(path: Path) -> None:
    """Open one already validated file with the operating system's default app."""
    value = str(path)
    if os.name == "nt":
        opener = getattr(os, "startfile", None)
        if opener is None:
            raise OSError("system file opener is unavailable")
        opener(value)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", value])
    else:
        subprocess.Popen(["xdg-open", value])
