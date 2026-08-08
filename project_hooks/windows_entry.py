"""Windows portable executable entrypoint and first-run bootstrap."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from project_hooks import DISPLAY_NAME, EXECUTABLE_NAME, __version__
from project_hooks.app_icon import apply_window_icon
from project_hooks.launcher import (
    PORTABLE_ROOT_ENV,
    configure_utf8_stdio,
    is_frozen,
    run_selected_executable,
)


CONFIG_PATH = Path(".codex/project-maintenance-workflow.json")


def initialize_project(*args, **kwargs):
    """Lazy compatibility wrapper kept patchable by distribution tests."""
    from project_hooks.project_manager import initialize_project as implementation

    return implementation(*args, **kwargs)


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
    if executable.name.lower() != EXECUTABLE_NAME:
        raise PortableBootstrapError(
            f"便携启动文件必须命名为 {EXECUTABLE_NAME}；当前名称为 {executable.name}。\n\n"
            "请重命名后重新双击。"
        )
    if not _directory_is_bootstrap_safe(root, executable):
        raise PortableBootstrapError(
            "当前目录不是空文件夹，且尚未初始化 Workflow Monitor。\n\n"
            f"请把 {EXECUTABLE_NAME} 放入一个空文件夹后双击，"
            "或在命令行中对现有 Git 仓库显式执行 init。"
        )
    if not _git_available():
        raise PortableBootstrapError(
            "未找到 Git。请先安装 Git for Windows，并确认 git 命令已加入 PATH，然后重新双击。"
        )
    git_created = not (root / ".git").exists()
    try:
        from project_hooks.cli import check_repository, set_project_root

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
        apply_window_icon(root, tk)
        root.withdraw()
        messagebox.showerror(DISPLAY_NAME, message, parent=root)
        root.destroy()
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, DISPLAY_NAME, 0x10)
        except Exception:
            pass


def run_portable_dashboard(root: Path | None = None, refresh_seconds: float = 3.0) -> int:
    project = (root or portable_root()).resolve()
    import tkinter as tk
    from tkinter import ttk

    # Map a real window before importing the larger CLI/read-model graph or
    # performing first-run Git initialization.  Frozen one-file extraction is
    # then the only unavoidable delay visible to the user.
    root_window = tk.Tk()
    apply_window_icon(root_window, tk)
    root_window.title(DISPLAY_NAME)
    root_window.geometry("560x150")
    root_window.minsize(460, 130)
    ttk.Label(
        root_window, text=DISPLAY_NAME, font=("TkDefaultFont", 13, "bold"),
    ).pack(anchor="w", padx=18, pady=(18, 5))
    ttk.Label(
        root_window, text="正在读取项目并准备只读工作流视图…",
    ).pack(anchor="w", padx=18, pady=(0, 12))
    root_window.update_idletasks()
    root_window.update()
    try:
        prepare_portable_project(project)
        from project_hooks.cli import (
            check_repository, classify_branch, dashboard_action_service, read_model, set_project_root,
        )
        from project_hooks.dashboard import DashboardDataProvider, launch_dashboard

        set_project_root(project)
        # The Dashboard itself explains version/install/health blockers and
        # keeps read-only diagnostics/update checks available.
        check_repository(raise_on_error=False)
        for child in root_window.winfo_children():
            child.destroy()
        provider = DashboardDataProvider(
            read_model(), classify_branch, None,
            action_service=dashboard_action_service(),
        )
        launch_dashboard(provider, refresh_seconds, root_factory=lambda: root_window)
        return 0
    except Exception:
        if root_window.winfo_exists():
            root_window.destroy()
        raise


def _dashboard_project(args: list[str], default: Path) -> Path:
    """Resolve the Dashboard project without importing the full CLI graph."""
    try:
        index = args.index("--project")
    except ValueError:
        return default
    if index + 1 >= len(args):
        return default
    return Path(args[index + 1]).resolve()


def _dashboard_refresh_seconds(args: list[str]) -> float:
    try:
        index = args.index("--refresh-seconds")
        return max(0.0, float(args[index + 1]))
    except (ValueError, IndexError):
        return 3.0


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
    if args and args[0] == "dashboard":
        # The normal CLI parser imports most of the application before command
        # dispatch.  Route Dashboard startup here so its loading window is
        # mapped first; Dashboard-specific options are applied by the UI's
        # shared refresh scheduler after startup.
        try:
            return run_portable_dashboard(
                _dashboard_project(args, project), _dashboard_refresh_seconds(args),
            )
        except Exception as exc:
            from project_hooks.diagnostics import execution_mode, format_failure, record_failure
            from project_hooks.store import SCHEMA_VERSION

            record = record_failure(
                project, exc, command="dashboard.startup", application_version=__version__,
                schema_version=SCHEMA_VERSION, execution_mode=execution_mode(),
            )
            show_error(format_failure(record))
            return 1
    if args:
        from project_hooks.cli import main as cli_main

        if "--project" not in args:
            args = ["--project", str(project), *args]
        return cli_main(args)
    try:
        return run_portable_dashboard(project)
    except Exception as exc:
        from project_hooks.diagnostics import execution_mode, format_failure, record_failure
        from project_hooks.store import SCHEMA_VERSION

        record = record_failure(
            project, exc, command="dashboard.startup", application_version=__version__,
            schema_version=SCHEMA_VERSION, execution_mode=execution_mode(),
        )
        show_error(format_failure(record))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
