"""Project-local executable runtime selection without UI dependencies."""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from ... import EXECUTABLE_NAME, __version__


ACTIVE_ENV = "PROJECT_HOOKS_EXE_ACTIVE"
PORTABLE_ROOT_ENV = "PROJECT_HOOKS_PORTABLE_ROOT"
UPDATE_CHILD_ENV = "PROJECT_HOOKS_UPDATE_CHILD"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def runtime_root(project_root: Path) -> Path:
    from ..git.worktrees import repository_context
    from ..persistence.project_registry import registry_for
    if (project_root / ".git").exists():
        registry = registry_for(project_root)
        if registry and registry.setting("unified"):
            return repository_context(project_root).shared_dir / "runtime"
    return legacy_runtime_root(project_root)


def legacy_runtime_root(project_root: Path) -> Path:
    return project_root.resolve() / ".project_hooks" / "runtime"


def cached_executable(project_root: Path, version: str, build_id: str | None = None, *, legacy=False) -> Path:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("非法缓存版本")
    folder = (legacy_runtime_root(project_root) if legacy else runtime_root(project_root)) / "executables" / version
    if build_id is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", build_id):
            raise ValueError("非法缓存构建 ID")
        folder = folder / "builds" / build_id
    return folder / EXECUTABLE_NAME


def write_pointer(version: str, project_root: Path, executable: Path, *, build_id: str | None = None, legacy=False) -> None:
    from ..git.build_identity import build_identity
    runtime = legacy_runtime_root(project_root) if legacy else runtime_root(project_root)
    pointer_executable = Path(executable).resolve()
    expected = cached_executable(project_root, version, build_id, legacy=legacy).resolve()
    if pointer_executable != expected or not pointer_executable.is_file():
        raise ValueError("更新版本指针只能指向已缓存并校验的版本化 EXE")
    runtime.mkdir(parents=True, exist_ok=True)
    pointer = runtime / "current.json"
    descriptor, name = tempfile.mkstemp(prefix="current-", suffix=".json", dir=runtime)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                {"version": version, "executable": str(pointer_executable), "build_id": build_id,
                 "built_at": build_identity().get("built_at")},
                handle, ensure_ascii=False, indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, pointer)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def selected_executable(project_root: Path) -> Path | None:
    marker = project_root / ".git"
    old_pointer = legacy_runtime_root(project_root) / "current.json"
    if marker.is_dir() and not (marker / "commondir").exists() and not old_pointer.is_file():
        pointer = marker / "workflow-monitor" / "runtime" / "current.json"
        if not pointer.is_file():
            return None
    runtime = runtime_root(project_root)
    pointer = runtime / "current.json"
    legacy = False
    if not pointer.is_file() and runtime != legacy_runtime_root(project_root):
        pointer = legacy_runtime_root(project_root) / "current.json"
        legacy = True
    if not pointer.is_file():
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
        selected = str(data["version"])
        expected = cached_executable(project_root, selected, data.get("build_id"), legacy=legacy).resolve()
        executable = Path(str(data.get("executable") or expected)).resolve()
        if executable != expected:
            return None
        selected_key = tuple(int(part) for part in selected.split("."))
        bundled_key = tuple(int(part) for part in __version__.split("."))
    except (KeyError, OSError, ValueError, TypeError):
        return None
    if selected_key < bundled_key or not executable.is_file():
        return None
    if selected_key == bundled_key:
        from ..git.build_identity import build_identity
        bundled = build_identity()
        if (bundled.get("channel") == "test" and bundled.get("built_at")
                and str(data.get("built_at") or "") < bundled["built_at"]):
            return None
    if data.get("build_id"):
        try:
            digest = (executable.parent / ".exe-sha256").read_text(encoding="ascii").strip()
            if hashlib.sha256(executable.read_bytes()).hexdigest() != digest:
                return None
        except OSError:
            return None
    try:
        if executable.samefile(Path(sys.executable)):
            return None
    except OSError:
        pass
    return executable


def run_selected_executable(args: list[str], *, portable_root: Path) -> int | None:
    if (not is_frozen() or os.environ.get(UPDATE_CHILD_ENV) or "_apply-update" in args
            or "--from-exe" in args or "build-notes" in args):
        return None
    if os.environ.get(ACTIVE_ENV):
        # Version probes set ACTIVE without a launched project's context.
        if not os.environ.get(PORTABLE_ROOT_ENV) or runtime_root(portable_root) == legacy_runtime_root(portable_root):
            return None
    executable = selected_executable(portable_root)
    if executable is None:
        return None
    env = os.environ.copy()
    env[ACTIVE_ENV] = "1"
    env[PORTABLE_ROOT_ENV] = str(portable_root.resolve())
    completed = subprocess.run([str(executable), *args], env=env, check=False)
    return completed.returncode


def shared_entrypoint(root: Path) -> Path:
    """Find the project's stable launcher for hooks and event merges."""
    import shutil
    from ..git.worktrees import list_worktrees
    primary = Path(list_worktrees(root)[0]["path"])
    launcher = primary / EXECUTABLE_NAME
    if not launcher.is_file():
        descriptor, temporary = tempfile.mkstemp(prefix="launcher-", suffix=".tmp", dir=primary)
        os.close(descriptor)
        try:
            shutil.copy2(sys.executable, temporary)
            os.replace(temporary, launcher)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return launcher
