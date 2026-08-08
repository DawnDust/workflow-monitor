"""Git process boundary used by lifecycle and health services."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run(root: Path, args: list[str], *, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=60, env=env, check=check,
    )


def is_repository(root: Path) -> bool:
    try:
        return run(root, ["rev-parse", "--is-inside-work-tree"]).stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def dirty_paths(root: Path) -> list[str]:
    if not is_repository(root):
        return []
    entries = run(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]).stdout.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            if index < len(entries) and entries[index]:
                paths.add(entries[index].replace("\\", "/"))
                index += 1
        paths.add(path.replace("\\", "/"))
    return sorted(paths)


def current_branch(root: Path) -> str | None:
    if not is_repository(root):
        return None
    result = run(root, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def process_environment() -> dict:
    return os.environ.copy()
