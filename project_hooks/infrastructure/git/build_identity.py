"""Runtime build identity for source checkouts and frozen distributions."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

SOFTWARE_SOURCE_PATHS = (
    "project_hooks", "scripts", "tests", ".github/workflows", ".githooks",
    "requirements-dev.txt", ".coveragerc", ".gitattributes", ".gitignore",
    "AGENTS.md", "README.md", "CHANGELOG.md", "maintenance/README.md",
)


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root, text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _untracked_digests(root: Path) -> list[str]:
    try:
        completed = subprocess.run(
            [
                "git", "ls-files", "--others", "--exclude-standard", "-z", "--",
                *SOFTWARE_SOURCE_PATHS,
            ],
            cwd=root,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    values: list[str] = []
    for raw_path in completed.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        path = root / relative
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "non-file"
        except OSError:
            digest = "unreadable"
        values.append(f"{relative}\0{digest}")
    return sorted(values)


def repository_source_identity(root: Path) -> dict:
    """Fingerprint the current Git worktree, including untracked file contents."""
    root = root.resolve()
    commit = _git(root, "rev-parse", "HEAD")
    status = _git(
        root, "status", "--porcelain=v1", "--untracked-files=all", "--",
        *SOFTWARE_SOURCE_PATHS,
    )
    diff = _git(root, "diff", "--binary", "HEAD", "--", *SOFTWARE_SOURCE_PATHS) or ""
    legacy_material = f"{commit or 'unknown'}\n{status or ''}\n{diff}"
    legacy_tree = hashlib.sha256(legacy_material.encode("utf-8")).hexdigest()
    material = legacy_material + "\nuntracked-content-v2\n" + "\n".join(_untracked_digests(root))
    return {
        "source_commit": commit,
        "source_tree": hashlib.sha256(material.encode("utf-8", errors="surrogateescape")).hexdigest(),
        "legacy_source_tree": legacy_tree,
        "source_tree_algorithm": "git-worktree-v2",
        "dirty": bool(status),
    }


def exe_matches_repository(root: Path, identity: dict | None = None) -> bool | None:
    """Compare an embedded EXE source snapshot with a software source checkout."""
    tracked_source = _git(root, "ls-files", "--error-unmatch", "project_hooks/__init__.py")
    if tracked_source is None:
        return None
    executable = identity or build_identity()
    expected = executable.get("source_tree")
    if not expected:
        return None
    repository = repository_source_identity(root)
    actual = (
        repository["source_tree"]
        if executable.get("source_tree_algorithm") == "git-worktree-v2"
        else repository["legacy_source_tree"]
    )
    return str(expected) == str(actual)


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
    repository = repository_source_identity(root)
    tree = repository["source_tree"]
    return {
        "build_id": f"source-{tree[:12]}",
        "source_commit": repository["source_commit"],
        "source_tree": tree,
        "source_tree_algorithm": repository["source_tree_algorithm"],
        "built_at": None,
        "dirty": repository["dirty"],
        "mode": "source",
    }


def build_warning(project_build_id: str | None) -> str | None:
    current = build_identity()["build_id"]
    if project_build_id and project_build_id != current:
        return f"同一版本检测到不同构建：当前 {current}，项目记录 {project_build_id}"
    return None
