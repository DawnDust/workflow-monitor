"""Git-backed clone and worktree identities, independent of checkout layout."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .client import run


@dataclass(frozen=True)
class RepositoryContext:
    root: Path
    common_dir: Path
    git_dir: Path

    @property
    def shared_dir(self) -> Path:
        return self.common_dir / "workflow-monitor"

    @property
    def worktree_id(self) -> str:
        return "main" if self.git_dir == self.common_dir else self.git_dir.name


def checkout_environment():
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX", "PROJECT_HOOKS_PORTABLE_ROOT"):
        env.pop(name, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def repository_context(root: Path) -> RepositoryContext:
    root = root.resolve()
    directory = root / ".git"
    if directory.is_dir() and (directory / "HEAD").is_file() and (directory / "objects").is_dir() and not (directory / "commondir").exists():
        git_dir = directory.resolve()
        return RepositoryContext(root, git_dir, git_dir)
    marker = directory.stat()
    return _repository_context(root, (marker.st_ino, marker.st_mtime_ns, marker.st_size))


@lru_cache(maxsize=256)
def _repository_context(root: Path, marker_signature: tuple) -> RepositoryContext:
    """Cache identities only while the checkout's Git marker is unchanged."""
    def resolve(argument: str) -> Path:
        result = run(root, ["rev-parse", argument], env=checkout_environment(), check=False)
        if result.returncode:
            raise RuntimeError("无法解析 Git 工作树身份: " + result.stderr.strip())
        value = Path(result.stdout.strip())
        return (root / value).resolve() if not value.is_absolute() else value.resolve()
    return RepositoryContext(root, resolve("--git-common-dir"), resolve("--git-dir"))


def list_worktrees(root: Path) -> list[dict]:
    output = run(root, ["-c", "core.quotePath=false", "worktree", "list", "--porcelain", "-z"], env=checkout_environment()).stdout
    result, item = [], {}
    for field in output.split("\0"):
        if not field:
            if item:
                result.append(item)
                item = {}
            continue
        key, _, value = field.partition(" ")
        item[key] = value or True
    if item:
        result.append(item)
    for item in result:
        item["path"] = str(Path(item["worktree"]).resolve())
        item["branch"] = str(item.get("branch", "")).removeprefix("refs/heads/")
    return result


def workflow_command(root: Path, arguments: list[str]) -> tuple[list[str], dict]:
    env = checkout_environment()
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        source = str(Path(__file__).resolve().parents[3])
        env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
        command = [sys.executable, "-m", "project_hooks"]
    return [*command, "--project", str(root), *arguments], env


def invoke_workflow(root: Path, arguments: list[str], *, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    command, env = workflow_command(root, arguments)
    env.update(extra_env or {})
    return subprocess.run(command, cwd=root, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True)
