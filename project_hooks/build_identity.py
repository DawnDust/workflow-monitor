"""Runtime build identity for source checkouts and frozen distributions."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root, text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _embedded_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "build-info.json"


@lru_cache(maxsize=1)
def build_identity() -> dict:
    path = _embedded_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("build_id"):
            return value
    except (OSError, ValueError):
        pass
    root = Path(__file__).resolve().parents[1]
    commit = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    diff = _git(root, "diff", "--binary", "HEAD") or ""
    tree_material = f"{commit or 'unknown'}\n{status or ''}\n{diff}"
    tree = hashlib.sha256(tree_material.encode("utf-8")).hexdigest()
    return {
        "build_id": f"source-{tree[:12]}",
        "source_commit": commit,
        "source_tree": tree,
        "built_at": None,
        "dirty": bool(status),
        "mode": "source",
    }


def build_warning(project_build_id: str | None) -> str | None:
    current = build_identity()["build_id"]
    if project_build_id and project_build_id != current:
        return f"同一版本检测到不同构建：当前 {current}，项目记录 {project_build_id}"
    return None
