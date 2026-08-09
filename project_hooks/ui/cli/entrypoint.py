"""CLI process entrypoint and top-level error boundary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ... import __version__
from .commands import *
from .parser import build_parser

def main(argv: list[str] | None = None) -> int:
    raw_args = list(os.sys.argv[1:] if argv is None else argv)
    args: argparse.Namespace | None = None
    root: Path | None = None
    writer_context = None
    shared_action: tuple[str, dict] | None = None
    try:
        args = build_parser().parse_args(raw_args)
        if args.command == "init":
            root = (args.project or args.path).resolve()
        else:
            root = args.project.resolve() if args.project else discover_project_root()
        if root is not None:
            set_project_root(root)
            assert_project_version_compatible(args)
            shared_action = cli_shared_action(args)
            if not command_is_read_only(args):
                lock_state_dir = root / ".project_hooks" if args.command == "init" else state_dir()
                writer_context = mutation_lock(
                    lock_state_dir,
                    command=".".join(filter(None, [args.command, getattr(args, f"{args.command}_command", None)])),
                    task_id=getattr(args, "task_id", None),
                    timeout=0.25,
                )
                writer_context.__enter__()
        if args.basic_help:
            output = DAILY_HELP
        elif args.help_all:
            output = FULL_HELP
        elif args.command == "version":
            output = version_report(root)
        elif args.command == "init":
            output = initialize_project(ROOT, __version__)
        elif root is None:
            raise WorkflowError(
                "当前目录不在 Workflow Monitor 科研项目中；请先运行 `.\\workflow-monitor.exe init .`，"
                "或使用 `--project <path>`"
            )
        elif shared_action is not None:
            output = dashboard_execute_action(
                shared_action[0], shared_action[1], lambda _progress: None,
            )
        elif args.command == "update":
            output = run_update(
                ROOT,
                target_version=args.to,
                check_only=args.check,
                manifest_url=args.manifest_url,
            )
        elif args.command == "_apply-update":
            if args.target_version != __version__:
                raise WorkflowError(
                    f"核心版本 {__version__} 与迁移目标 {args.target_version} 不一致"
                )
            output = apply_project_update(ROOT, args.target_version)
        elif args.command is None:
            configured = run_git(["config", "--local", "--get", "core.hooksPath"], check=False).stdout.strip()
            if configured != TRACKED_HOOKS_DIR:
                raise WorkflowError(
                    "项目维护尚未安装。请先运行 `.\\workflow-monitor.exe install`，"
                    "再运行 `.\\workflow-monitor.exe check`。"
                )
            output = action_overview_text(context_data())
        elif args.command == "start": output = start_task(args)
        elif args.command == "task":
            output = task_recover(args) if args.task_command == "recover" else task_abandon(args)
        elif args.command == "status": output = task_status()
        elif args.command == "branch-status": output = branch_status()
        elif args.command == "prepare-pr": output = prepare_pr()
        elif args.command == "archive-attempt": output = archive_attempt()
        elif args.command == "check":
            check_repository(raise_on_error=True); output = {"status": "passed"}
        elif args.command == "install": output = {"status": install_git_hook(args.force)}
        elif args.command == "pre-commit": pre_commit_check(); output = {"status": "passed"}
        elif args.command == "context":
            data = context_data(); output = data if args.format == "json" else markdown_context(data)
        elif args.command == "diagnostics": output = diagnostics_command(args)
        elif args.command == "state": output = state_update(args)
        elif args.command == "project": output = project_command(args)
        elif args.command == "stage": output = stage_command(args)
        elif args.command == "decision": output = decision_add(args)
        elif args.command == "attempt": output = get_attempt(current_branch()) if args.attempt_command == "show" else attempt_update(args)
        elif args.command in {"history", "decisions", "explorations"}:
            output = render_records(query_output(args.command, args.limit), args.format)
        elif args.command == "exploration": output = exploration_import(args)
        elif args.command == "workbench": output = workbench_command(args)
        elif args.command == "catalog": output = catalog_command(args, catalog_runtime())
        elif args.command == "db": output = db_command(args)
        else: output = finish_task(args)
        if isinstance(output, str): print(output, end="")
        else: print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        command_parts = [str((args.command if args is not None else (raw_args[0] if raw_args else None)) or "overview")]
        for attribute in ("diagnostics_command", "db_command", "catalog_command", "project_command",
                          "stage_command", "attempt_command", "exploration_command", "task_command",
                          "workbench_command", "external_command", "item_command", "package_command"):
            value = getattr(args, attribute, None) if args is not None else None
            if value:
                command_parts.append(str(value))
        metadata = {
            "command": ".".join(command_parts),
            "application_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "execution_mode": execution_mode(),
        }
        try:
            state = read_model().git_state(branch_policy()["default_branch"])
            metadata["git_state"] = {
                "relation": state.get("relation") or "unknown",
                "branch_kind": classify_branch(state.get("branch") or "").get("kind") or "unknown",
            }
        except Exception:
            pass
        try:
            record = record_failure((root or ROOT).resolve(), exc, **metadata)
            message = format_failure(record)
        except Exception:
            message = str(exc)
        print(message, file=os.sys.stderr)
        return 1
    finally:
        if writer_context is not None:
            writer_context.__exit__(None, None, None)
