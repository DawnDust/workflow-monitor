"""Allowlisted Windows shell navigation used by trusted UI adapters."""

from __future__ import annotations

import subprocess
from pathlib import Path


def open_directory(path: Path) -> None:
    subprocess.Popen(["explorer.exe", str(path)])


def reveal_file(path: Path) -> None:
    subprocess.Popen(["explorer.exe", f"/select,{path}"])
