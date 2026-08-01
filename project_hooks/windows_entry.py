"""Windows portable executable entrypoint and first-run bootstrap."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from project_hooks import __version__
from project_hooks.diagnostics import execution_mode, format_failure, record_failure
from project_hooks.store import SCHEMA_VERSION

from project_hooks.cli import check_repository, classify_branch, read_model, set_project_root
from project_hooks.dashboard import DashboardDataProvider, launch_dashboard
from project_hooks.launcher import (
    PORTABLE_ROOT_ENV,
    configure_utf8_stdio,
    is_frozen,
    run_selected_executable,
)
from project_hooks.project_manager import CONFIG_PATH, initialize_project


class PortableBootstrapError(RuntimeError):
    pass


BOOTSTRAP_PATHS = (
    Path(".codex"),
    Path(".githooks"),
    Path(".project_hooks"),
    Path("maintenance"),
    Path("AGENTS.md"),
    Path(".gitignore"),
    Path(".gitattributes"),
)


def portable_root() -> Path:
    override = os.environ.get(PORTABLE_ROOT_ENV)
    return Path(override).resolve() if override else Path(sys.executable).resolve().parent


def _directory_is_bootstrap_safe(root: Path, executable: Path) -> bool:
    for entry in root.iterdir():
        if entry.name == ".git":
            continue
        try:
            if entry.samefile(executable):
                continue
        except OSError:
            pass
        return False
    return True


def _git_available() -> bool:
    try:
        result = subprocess.run(
            ["git", "--version"], text=True, encoding="utf-8",
            capture_output=True, check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _remove_created_paths(root: Path, *, remove_git: bool) -> None:
    targets = list(BOOTSTRAP_PATHS)
    if remove_git:
        targets.append(Path(".git"))
    for relative in targets:
        target = (root / relative).resolve()
        if target.parent != root:
            continue
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            try:
                target.unlink()
            except OSError:
                pass


def prepare_portable_project(root: Path, executable: Path | None = None) -> bool:
    """Ensure a portable project exists; return True when initialized now."""
    root = root.resolve()
    executable = (executable or Path(sys.executable)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / CONFIG_PATH).is_file():
        return False
    if executable.name.lower() != "project-hooks.exe":
        raise PortableBootstrapError(
            f"便携启动文件必须命名为 project-hooks.exe；当前名称为 {executable.name}。\n\n"
            "请重命名后重新双击。"
        )
    if not _directory_is_bootstrap_safe(root, executable):
        raise PortableBootstrapError(
            "当前目录不是空文件夹，且尚未初始化 project-hooks。\n\n"
            "请把 project-hooks.exe 放入一个空文件夹后双击，"
            "或在命令行中对现有 Git 仓库显式执行 init。"
        )
    if not _git_available():
        raise PortableBootstrapError(
            "未找到 Git。请先安装 Git for Windows，并确认 git 命令已加入 PATH，然后重新双击。"
        )
    git_created = not (root / ".git").exists()
    try:
        if git_created:
            completed = subprocess.run(
                ["git", "init", "-b", "main"], cwd=root, text=True,
                encoding="utf-8", capture_output=True, check=False,
            )
            if completed.returncode != 0:
                raise PortableBootstrapError(
                    completed.stderr.strip() or completed.stdout.strip() or "Git 仓库初始化失败"
                )
        initialize_project(root, __version__)
        set_project_root(root)
        check_repository(raise_on_error=True)
        return True
    except Exception:
        _remove_created_paths(root, remove_git=git_created)
        raise


def show_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("project-hooks", message, parent=root)
        root.destroy()
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "project-hooks", 0x10)
        except Exception:
            pass


def run_portable_dashboard(root: Path | None = None) -> int:
    project = (root or portable_root()).resolve()
    prepare_portable_project(project)
    set_project_root(project)
    check_repository(raise_on_error=True)
    provider = DashboardDataProvider(read_model(), classify_branch, None)
    launch_dashboard(provider, 3.0)
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    if not is_frozen():
        from project_hooks.launcher import main as launcher_main

        return launcher_main(args)
    project = portable_root()
    delegated = run_selected_executable(args, portable_root=project)
    if delegated is not None:
        return delegated
    if args:
        from project_hooks.cli import main as cli_main

        if "--project" not in args:
            args = ["--project", str(project), *args]
        return cli_main(args)
    try:
        return run_portable_dashboard(project)
    except Exception as exc:
        record = record_failure(
            project, exc, command="dashboard.startup", application_version=__version__,
            schema_version=SCHEMA_VERSION, execution_mode=execution_mode(),
        )
        show_error(format_failure(record))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
