"""Project maintenance command implementation backed by SQLite and JSONL events."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from .errors import WorkflowError
from .parser import build_parser
from ... import __version__
from ...infrastructure.persistence.active_task import (
    ActiveTaskError,
    delete_active_state,
    load_active_state,
    restore_active_row,
    save_active_state,
)
from .catalog_commands import (
    CatalogError,
    CatalogRuntime,
    catalog_command,
    configure_catalog_parser,
    decode_item,
)
from ...infrastructure.system.diagnostics import (
    cleanup_resolved_diagnostics,
    diagnostics_status,
    execution_mode,
    export_diagnostics,
    format_failure,
    open_bug_report,
    record_failure,
    resolve_diagnostic,
)
from ...infrastructure.git import client as git_ops
from ...infrastructure.system.health import active_task_errors
from ...infrastructure.persistence.read_model import (
    MaintenanceReadModel,
    ReadModelError,
    action_overview_text,
    git_state_summary,
)
from ...infrastructure.persistence.store import (
    SCHEMA_VERSION,
    StoreError,
    canonical_json,
    connect,
    ensure_database,
    journal_hash,
    load_events,
    new_event,
    rebuild,
    record_events,
    resolve_timezone,
    validate_event,
)
from ...infrastructure.persistence.database import (
    DatabaseError,
    ProjectDatabase,
    repository,
    verification_snapshot,
)
from ...infrastructure.system.project_manager import (
    HOOK_TEMPLATE,
    INSTALLATION_PATH,
    ProjectManagerError,
    apply_project_update,
    initialize_project,
    installation_record,
    write_json,
)
from ...infrastructure.system.resource_layout import catalog_consistency_errors, ensure_resource_directories
from ...infrastructure.system.updater import UpdateError, check_latest_update, run_update, version_report
from ...infrastructure.persistence.transaction import MutationLockError, mutation_lock, read_writer_lock
from ...infrastructure.system.runtime import is_frozen
from ...infrastructure.git.build_identity import build_identity
from ...application.action_service import ActionProgress, WorkflowActionService, action_spec
from ...composition import build_action_service
from ...application.workbench_service import (
    EXTERNAL_TOOL_KINDS,
    KIND_LABELS,
    ORIGIN_LABELS,
    PURPOSE_LABELS,
    WorkbenchError,
    external_tools_from_events,
    legacy_item_type_taxonomy,
    normalize_external_tool,
    normalize_workbench_item,
    sha256_file,
    validate_item_id,
    validate_tool_id,
    workbench_consistency_errors,
    workbench_items_from_events,
)
from ...infrastructure.system.workbench_packages import (
    WORKBENCH_LOCAL,
    export_package,
    import_package,
    inspect_package_for_project,
    remove_imported_package,
    workbench_package_consistency_errors,
)
from ...core.branches import classify_branch as classify_branch_with_policy


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_MARKER = Path(".codex/project-maintenance-workflow.json")
ROOT = Path.cwd().resolve()
MARKER = ROOT / PROJECT_MARKER
TASK_ID_RE = re.compile(r"^\d{8}_[a-z0-9][a-z0-9_-]*_\d{3}$")
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
STAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MANAGED_HOOK_MARKER = "# project-maintenance-hooks managed"
TRACKED_HOOKS_DIR = ".githooks"
STATIC_READ_ORDER = [
    "maintenance/README.md",
]
DEFAULT_BRANCH_POLICY = {
    "default_branch": "main",
    "exploration_types": ["research", "experiment", "sandbox"],
    "archive_prefix": "archive",
    "success_integration": "squash_pr",
    "require_user_merge_confirmation": True,
}
DEFAULT_STORE = {
    "engine": "sqlite",
    "database": ".project_hooks/maintenance.sqlite3",
    "journal": "maintenance/events.jsonl",
    "schema_version": SCHEMA_VERSION,
}
STATE_ARGUMENTS = (
    "goal", "judgment", "breakpoint", "blocker", "status", "main_goal_version",
)
DAILY_HELP = """\
usage: workflow-monitor [-h] [--help-all] [--project PATH] {context,start,end,diagnostics} ...

项目内维护入口。无参数运行时显示行动概览。

日常命令:
  context     读取完整动态上下文
  start       开始任务生命周期
  end         完成任务生命周期
  diagnostics 查看或导出本地脱敏诊断

首次使用:
  .\\workflow-monitor.exe init .
  .\\workflow-monitor.exe check

软件升级:
  .\\workflow-monitor.exe update

使用 --help-all 查看全部高级命令；使用 <命令> --help 查看参数。
"""
FULL_HELP = """\
usage: workflow-monitor [-h] [--help-all] [--project PATH] <command> ...

日常命令:
  context, start, end, diagnostics

仓库维护:
  init, install, update, version, check, status, branch-status

状态与记录:
  project, stage, state, history, decisions, decision, explorations, catalog, workbench

探索流程:
  attempt, exploration, prepare-pr, archive-attempt

数据维护:
  db, diagnostics

内部 Git Hook 命令不显示；所有既有公开命令保持兼容。
使用 <命令> --help 查看详细参数。
"""


def clean_text(value: str | None, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise WorkflowError(f"{label}不能为空")
    if len(text) > maximum:
        raise WorkflowError(f"{label}最多允许 {maximum} 个字符")
    return text


def discover_project_root(start: Path | None = None) -> Path | None:
    """Find the closest project marker without depending on package location."""
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / PROJECT_MARKER).is_file():
            return candidate
    development = PACKAGE_ROOT.parent
    return development if (development / PROJECT_MARKER).is_file() else None


def set_project_root(path: Path) -> None:
    global ROOT, MARKER
    ROOT = path.resolve()
    MARKER = ROOT / PROJECT_MARKER


def config() -> dict:
    if not MARKER.is_file():
        raise WorkflowError("缺少 .codex/project-maintenance-workflow.json")
    data = json.loads(MARKER.read_text(encoding="utf-8"))
    data.setdefault("timezone", "Asia/Shanghai")
    data.setdefault("state_dir", ".project_hooks")
    data.setdefault("core_read_order", STATIC_READ_ORDER)
    data.setdefault("context_command", ".\\workflow-monitor.exe context --format markdown")
    data.setdefault("maintenance_store", DEFAULT_STORE)
    data.setdefault("git_auto_commit", {"enabled": True, "eligible_task_sizes": ["large"]})
    data.setdefault("branch_policy", DEFAULT_BRANCH_POLICY)
    return data


def installed_project_version() -> str | None:
    path = ROOT / INSTALLATION_PATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("application_version") or data.get("core_version")
        return str(value) if value else None
    except (OSError, ValueError):
        return None


def command_is_read_only(args: argparse.Namespace) -> bool:
    if args.command in {None, "version", "context", "check", "status", "branch-status",
                        "history", "decisions", "explorations", "dashboard", "diagnostics"}:
        return True
    if args.command == "update" and args.check:
        return True
    if args.command == "attempt" and args.attempt_command == "show":
        return True
    if args.command == "task" and args.task_command == "recover":
        return False
    if args.command == "project" and args.project_command == "show":
        return True
    if args.command == "stage" and args.stage_command in {"list", "show"}:
        return True
    if args.command == "db" and args.db_command in {"status", "verify"}:
        return True
    if args.command == "catalog" and (
        args.catalog_command in {"list", "show", "context"}
        or (args.catalog_command == "migrate-layout" and args.dry_run)
    ):
        return True
    if args.command == "workbench" and args.workbench_command == "external":
        return args.external_command in {"list", "show"}
    if args.command == "workbench" and args.workbench_command == "item":
        return args.item_command in {"list", "show"}
    if args.command == "workbench" and args.workbench_command == "package":
        return args.package_command == "inspect"
    return False


def assert_project_version_compatible(args: argparse.Namespace) -> None:
    installed = installed_project_version()
    if (installed and installed != __version__ and args.command != "_apply-update"
            and not command_is_read_only(args)):
        raise WorkflowError(
            f"项目模板版本为 {installed}，当前 EXE 为 {__version__}；"
            "写操作前请运行 `.\\workflow-monitor.exe update`"
        )


def state_dir() -> Path:
    return ROOT / config()["state_dir"]


def database_path() -> Path:
    return ROOT / config()["maintenance_store"]["database"]


def journal_path() -> Path:
    return ROOT / config()["maintenance_store"]["journal"]


def database() -> ProjectDatabase:
    try:
        return repository(ensure_database(database_path(), journal_path()))
    except (DatabaseError, StoreError) as exc:
        raise WorkflowError(str(exc)) from exc


def read_model() -> MaintenanceReadModel:
    return MaintenanceReadModel(database_path(), journal_path(), current_branch)


def timestamp() -> str:
    timezone = config()["timezone"]
    return datetime.now(resolve_timezone(timezone)).strftime(f"%Y-%m-%d %H:%M:%S（{timezone}）")


def emit(event_type: str, *, branch: str, task_id: str | None, payload: dict,
         event_id: str | None = None, occurred_at: str | None = None) -> dict:
    return new_event(
        event_type, branch=branch, task_id=task_id, payload=payload,
        timezone=config()["timezone"], event_id=event_id, occurred_at=occurred_at,
    )


def persist(events: list[dict]) -> None:
    try:
        record_events(database_path(), journal_path(), state_dir(), events)
    except (DatabaseError, StoreError) as exc:
        raise WorkflowError(str(exc)) from exc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot() -> dict[str, dict]:
    ignored_dirs = {".git", ".project_hooks", "__pycache__", ".pytest_cache", "node_modules", ".venv"}
    result: dict[str, dict] = {}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in ignored_dirs]
        for name in files:
            path = Path(base) / name
            rel = path.relative_to(ROOT).as_posix()
            stat = path.stat()
            item = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            if stat.st_size <= 8 * 1024 * 1024:
                item["sha256"] = sha256(path)
            result[rel] = item
    return result


def changed(before: dict, after: dict) -> list[str]:
    return sorted({*before, *after} - {p for p in before.keys() & after.keys() if before[p] == after[p]})


def run_git(args: list[str], *, env: dict | None = None, check: bool = True):
    return git_ops.run(ROOT, args, env=env, check=check)


def is_git_repo() -> bool:
    return git_ops.is_repository(ROOT)


def git_dirty_paths() -> list[str]:
    return git_ops.dirty_paths(ROOT)


def current_branch() -> str:
    if not is_git_repo():
        raise WorkflowError("当前目录不是 Git 仓库")
    branch = git_ops.current_branch(ROOT)
    if branch is None:
        raise WorkflowError("当前处于 detached HEAD，不能运行分支维护生命周期")
    return branch


def branch_policy() -> dict:
    policy = config()["branch_policy"]
    if policy != DEFAULT_BRANCH_POLICY:
        raise WorkflowError("branch_policy 与项目维护协议不一致")
    return policy


def classify_branch(branch: str) -> dict:
    return classify_branch_with_policy(branch, branch_policy())


def ref_exists(ref: str) -> bool:
    return run_git(["show-ref", "--verify", "--quiet", ref], check=False).returncode == 0


def assert_main_matches_origin() -> None:
    default = branch_policy()["default_branch"]
    if ref_exists(f"refs/remotes/origin/{default}"):
        if run_git(["rev-parse", default]).stdout.strip() != run_git(["rev-parse", f"origin/{default}"]).stdout.strip():
            raise WorkflowError(f"本地 {default} 与已知 origin/{default} 不一致；请先同步后重试")


def markdown_link_errors() -> list[str]:
    errors: list[str] = []
    folder = ROOT / "maintenance"
    for path in folder.rglob("*.md") if folder.is_dir() else []:
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            clean = target.strip().strip("<>")
            if not clean or clean.startswith(("#", "http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / unquote(clean.split("#", 1)[0])).resolve()
            if not resolved.exists():
                errors.append(f"Markdown 断链: {path.relative_to(ROOT).as_posix()} -> {clean}")
    return errors


def check_repository(
    *, raise_on_error: bool = False, include_catalog_consistency: bool = True,
) -> list[str]:
    cfg = config()
    errors: list[str] = []
    if cfg.get("version") != 3:
        errors.append("项目维护配置必须为 version=3")
    if cfg.get("core_read_order") != STATIC_READ_ORDER:
        errors.append("core_read_order 与 SQLite 工作流不一致")
    store = cfg.get("maintenance_store")
    if not isinstance(store, dict) or any(store.get(key) != value for key, value in DEFAULT_STORE.items()):
        errors.append("maintenance_store 与 SQLite 工作流不一致")
    installed = installed_project_version()
    if installed and installed != __version__:
        errors.append(f"项目模板版本 {installed} 与当前 EXE {__version__} 不一致；请运行 .\\workflow-monitor.exe update")
    try:
        branch_policy()
    except WorkflowError as exc:
        errors.append(str(exc))
    for rel in [*STATIC_READ_ORDER, "maintenance/README.md", "maintenance/events.jsonl",
                INSTALLATION_PATH.as_posix(), f"{TRACKED_HOOKS_DIR}/pre-commit"]:
        if not (ROOT / rel).is_file():
            errors.append(f"缺少维护文件: {rel}")
    for rel in ("workbench/local", "workbench/imported"):
        if not (ROOT / rel).is_dir():
            errors.append(f"缺少工作台目录: {rel}")
    for rel in ("maintenance/current_task.md", "maintenance/change_archive.md", "maintenance/decision_log.md", "maintenance/exploration_log.md"):
        if (ROOT / rel).exists():
            errors.append(f"旧动态维护文件仍存在: {rel}")
    errors.extend(markdown_link_errors())
    connection = None
    try:
        connection = database()
        integrity = connection.integrity()
        errors.extend(active_task_errors(connection.active_task_ids()))
        catalog_items = [
            decode_item(row) for row in connection.catalog_items()
        ]
        if integrity != "ok":
            errors.append(f"SQLite integrity_check: {integrity}")
        if include_catalog_consistency:
            errors.extend(catalog_consistency_errors(ROOT, catalog_items))
        events = load_events(journal_path())
        errors.extend(workbench_consistency_errors(ROOT, workbench_items_from_events(events)))
        errors.extend(workbench_package_consistency_errors(ROOT, events))
    except (WorkflowError, DatabaseError) as exc:
        errors.append(f"维护数据库检查失败: {exc}")
    finally:
        if connection is not None:
            connection.close()
    if errors and raise_on_error:
        raise WorkflowError("项目维护检查失败:\n- " + "\n- ".join(errors))
    return errors


def active_row(connection: ProjectDatabase | None = None) -> dict | None:
    owned = connection is None
    connection = connection or database()
    row = connection.latest_active_task()
    if owned:
        connection.close()
    return row


def read_active() -> dict:
    try:
        state = load_active_state(state_dir())
    except ActiveTaskError as exc:
        raise WorkflowError(str(exc)) from exc
    if state is not None:
        record = json.loads(json.dumps(state["record"], ensure_ascii=False))
        record["state_updated"] = bool(state["state_updated"])
        record["decisions_added"] = int(state["decisions_added"])
        record["recovery_phase"] = state["phase"]
        record["finish"] = state.get("finish")
        return record
    connection = database()
    row = active_row(connection)
    connection.close()
    if row is None:
        raise WorkflowError("没有活动任务")
    record = json.loads(row["record_json"])
    record["state_updated"] = bool(row["state_updated"])
    record["decisions_added"] = int(row["decisions_added"])
    return record


def save_active(
    record: dict,
    *,
    state_updated: int = 0,
    decisions_added: int = 0,
    phase: str = "active",
    finish: dict | None = None,
) -> None:
    save_active_state(
        state_dir(), record, state_updated=bool(state_updated),
        decisions_added=decisions_added, phase=phase, finish=finish,
    )
    connection = database()
    connection.upsert_active_task(record, canonical_json(record), state_updated, decisions_added)
    connection.close()


def update_active_flags(*, state_updated: bool = False, decision_added: bool = False) -> None:
    record = read_active()
    next_state_updated = bool(record["state_updated"] or state_updated)
    next_decisions = int(record["decisions_added"]) + int(decision_added)
    save_active_state(
        state_dir(), {key: value for key, value in record.items()
                     if key not in {"state_updated", "decisions_added", "recovery_phase", "finish"}},
        state_updated=next_state_updated,
        decisions_added=next_decisions,
        phase=record.get("recovery_phase", "active"),
        finish=record.get("finish"),
    )
    connection = database()
    connection.update_active_flags(
        record["task_id"], state_updated=state_updated, decision_added=decision_added,
    )
    connection.close()


def delete_active(task_id: str) -> None:
    connection = database()
    connection.delete_active_task(task_id)
    connection.close()
    delete_active_state(state_dir())


def assert_active_branch(record: dict) -> str:
    branch = current_branch()
    expected = record["git"]["branch"]
    if branch != expected:
        raise WorkflowError(f"任务启动于分支 {expected}，当前分支为 {branch}；请切回原分支")
    return branch


def get_attempt(branch: str) -> dict | None:
    try:
        return read_model().attempt(branch)
    except ReadModelError as exc:
        raise WorkflowError(str(exc)) from exc


def stable_write_context() -> tuple[dict, str]:
    record = read_active()
    branch = assert_active_branch(record)
    default = branch_policy()["default_branch"]
    if branch != default or record["git"]["track"] != "stable":
        raise WorkflowError(f"项目资料和阶段只能在 {default} 的 stable 活动任务中更新")
    return record, branch


def decode_stage(row: dict) -> dict:
    item = dict(row)
    item["acceptance"] = json.loads(item.pop("acceptance_json"))
    item["evidence"] = json.loads(item.pop("evidence_json"))
    return item


def active_stage(connection: ProjectDatabase) -> dict | None:
    row = connection.active_stage()
    return decode_stage(row) if row else None


def project_command(args: argparse.Namespace) -> dict | str:
    connection = database()
    try:
        row = connection.project_profile(branch_policy()["default_branch"])
        current = row if row else {
            "branch": branch_policy()["default_branch"], "description": "", "big_goal": "",
            "updated_at": None, "task_id": None, "event_id": None,
        }
    finally:
        connection.close()
    if args.project_command == "show":
        if args.format == "json":
            return current
        return (
            "# 项目资料\n\n"
            f"- 项目描述：{current.get('description') or '未设置'}\n"
            f"- 大目标：{current.get('big_goal') or '未设置'}\n"
            f"- 更新时间：{current.get('updated_at') or '未设置'}\n"
        )
    record, branch = stable_write_context()
    description = clean_text(args.description, "项目描述", 500)
    big_goal = clean_text(args.big_goal, "大目标", 500)
    if description is None and big_goal is None:
        raise WorkflowError("project update 至少提供 --description 或 --big-goal")
    payload = {
        "description": description if description is not None else current.get("description", ""),
        "big_goal": big_goal if big_goal is not None else current.get("big_goal", ""),
    }
    persist([emit("project.profile_updated", branch=branch, task_id=record["task_id"], payload=payload)])
    return payload


def stage_command(args: argparse.Namespace) -> dict | list[dict] | str:
    connection = database()
    try:
        if args.stage_command == "list":
            items = [decode_stage(row) for row in connection.stages(args.limit)]
            if args.format == "json":
                return items
            lines = ["# 项目阶段", ""]
            lines.extend(
                f"- {item['sequence']}. `{item['stage_id']}`｜{item['title']}｜{item['status']}｜"
                f"{item['current_step'] or '未记录当前步骤'}"
                for item in items
            )
            if not items:
                lines.append("没有阶段记录。")
            return "\n".join(lines) + "\n"
        if args.stage_command == "show":
            if args.stage_id:
                row = connection.stage(args.stage_id)
            else:
                row = connection.active_stage()
            if row is None:
                raise WorkflowError("找不到指定阶段" if args.stage_id else "当前没有 active 阶段")
            item = decode_stage(row)
            if args.format == "json":
                return item
            acceptance = "\n".join(f"- {value}" for value in item["acceptance"]) or "- 未设置"
            evidence = "\n".join(f"- {value}" for value in item["evidence"]) or "- 无"
            return (
                f"# {item['title']}\n\n- ID：`{item['stage_id']}`\n- 状态：{item['status']}\n"
                f"- 目标：{item['goal']}\n- 当前步骤：{item['current_step'] or '未设置'}\n"
                f"- 下一步：{item['next_step'] or '未设置'}\n- 阻塞：{item['blocker'] or '无。'}\n\n"
                f"## 验收条件\n\n{acceptance}\n\n## 总结\n\n{item['summary'] or '未设置'}\n\n"
                f"## 证据\n\n{evidence}\n"
            )
    finally:
        connection.close()

    record, branch = stable_write_context()
    connection = database()
    try:
        if args.stage_command == "start":
            if not STAGE_ID_RE.fullmatch(args.stage_id):
                raise WorkflowError("stage_id 只能使用小写字母、数字和连字符")
            if connection.stage(args.stage_id) is not None:
                raise WorkflowError(f"阶段已存在: {args.stage_id}")
            existing = active_stage(connection)
            if existing:
                raise WorkflowError(f"已有 active 阶段: {existing['stage_id']}")
            title = clean_text(args.title, "阶段标题", 100)
            goal = clean_text(args.goal, "阶段目标", 500)
            acceptance = [clean_text(item, "验收条件", 1000) for item in args.acceptance]
            sequence = connection.next_stage_sequence()
            payload = {"stage_id": args.stage_id, "sequence": sequence, "title": title,
                       "goal": goal, "acceptance": acceptance}
            persist([emit("stage.started", branch=branch, task_id=record["task_id"], payload=payload)])
            return payload | {"status": "active"}

        row = connection.stage(args.stage_id)
        if row is None:
            raise WorkflowError(f"找不到阶段: {args.stage_id}")
        stage = decode_stage(row)
        if stage["status"] in {"completed", "cancelled"}:
            raise WorkflowError(f"{stage['status']} 阶段不能再更新")
        fields = {
            "summary": clean_text(args.summary, "阶段总结", 2000),
            "current_step": clean_text(args.current_step, "当前步骤", 500),
            "next_step": clean_text(args.next_step, "下一步", 500),
            "blocker": clean_text(args.blocker, "阶段阻塞", 500),
        }
        evidence = [clean_text(item, "阶段证据", 1000) for item in (args.evidence or [])]
        requested = {key: value for key, value in fields.items() if value is not None}
        if evidence:
            requested["evidence"] = evidence
        status = args.status
        if status is None and not requested:
            raise WorkflowError("stage update 至少提供一个更新字段或 --status")
        if status == "active" and stage["status"] != "paused":
            raise WorkflowError("只有 paused 阶段可以恢复为 active")
        if status == "active":
            other = active_stage(connection)
            if other and other["stage_id"] != stage["stage_id"]:
                raise WorkflowError(f"已有 active 阶段: {other['stage_id']}")
        final_summary = requested.get("summary", stage["summary"])
        final_evidence = [*stage["evidence"], *evidence]
        if status == "completed":
            projected = read_model().attempts_across_branches()
            local_attempts = connection.attempt_states()
            by_branch = {item["branch"]: item for item in [*projected, *local_attempts]}
            active_branches = sorted(
                item["branch"] for item in by_branch.values()
                if item.get("stage_id") == stage["stage_id"] and item.get("state") == "active"
            )
            if active_branches:
                raise WorkflowError("阶段仍有 active 探索: " + ", ".join(active_branches))
            if not final_summary or not final_evidence:
                raise WorkflowError("completed 阶段必须填写总结并至少记录一项证据")
        events = []
        if requested:
            events.append(emit("stage.updated", branch=branch, task_id=record["task_id"],
                               payload={"stage_id": stage["stage_id"], **requested}))
        if status is not None and status != stage["status"]:
            events.append(emit("stage.state_changed", branch=branch, task_id=record["task_id"],
                               payload={"stage_id": stage["stage_id"], "status": status}))
        if not events:
            raise WorkflowError("阶段内容和状态均未变化")
        persist(events)
        return {"stage_id": stage["stage_id"], **requested,
                "status": status or stage["status"]}
    finally:
        connection.close()


def start_task(args: argparse.Namespace) -> dict:
    if active_row() is not None:
        raise WorkflowError("已有活动任务，请先执行 status/end")
    if not TASK_ID_RE.fullmatch(args.task_id):
        raise WorkflowError("task_id 必须为 YYYYMMDD_<slug>_NNN")
    original_branch = current_branch()
    classification = classify_branch(original_branch)
    requested_track, requested_topic = args.track, args.topic
    # A stable repair task must be able to start while legacy or unindexed resources exist;
    # the normal check and task end still require the inconsistency to be repaired.
    repairing_on_main = (
        classification["kind"] == "stable" and (requested_track or "stable") == "stable"
    )
    check_repository(
        raise_on_error=True,
        include_catalog_consistency=not repairing_on_main,
    )
    created_branch = False
    head_result = run_git(["rev-parse", "--verify", "HEAD"], check=False)
    base_head = head_result.stdout.strip() if head_result.returncode == 0 else None
    if classification["kind"] == "stable":
        requested_track = requested_track or "stable"
        if requested_track == "stable":
            if requested_topic:
                raise WorkflowError("stable 任务不能使用 --topic")
        else:
            if not base_head:
                raise WorkflowError("首次探索前需要先完成一个 stable 周期并建立 Git 基线提交")
            if not requested_topic or not TOPIC_RE.fullmatch(requested_topic):
                raise WorkflowError("探索 topic 只能使用小写字母、数字和连字符")
            dirty = git_dirty_paths()
            if dirty:
                raise WorkflowError("自动创建探索分支前工作树必须干净: " + ", ".join(dirty))
            assert_main_matches_origin()
            branch = f"{requested_track}/{requested_topic}"
            if ref_exists(f"refs/heads/{branch}") or ref_exists(f"refs/remotes/origin/{branch}") or get_attempt(branch):
                raise WorkflowError(f"探索分支或记录已存在: {branch}")
            run_git(["switch", "-c", branch])
            created_branch = True
            classification = classify_branch(branch)
    elif classification["kind"] == "exploration":
        requested_track = requested_track or classification["track"]
        if requested_track != classification["track"] or (requested_topic and requested_topic != classification["topic"]):
            raise WorkflowError(f"start 参数与当前探索分支 {original_branch} 不一致")
        if not get_attempt(original_branch):
            raise WorkflowError(f"当前探索分支缺少数据库尝试记录: {original_branch}")
    else:
        raise WorkflowError(f"不支持在分支 {original_branch} 启动任务")
    branch = current_branch()
    record = {
        "version": 2,
        "task_id": args.task_id,
        "started_at": timestamp(),
        "declaration": {"kind": args.kind, "scope": args.scope, "out_of_scope": args.out_of_scope,
                        "acceptance": args.acceptance, "task_size": args.task_size, "git_commit": args.git_commit},
        "baseline": snapshot(),
        "git": {"is_repo": True, "dirty_paths": git_dirty_paths(), "head": base_head,
                "branch": branch, "base_branch": branch_policy()["default_branch"], "base_head": base_head,
                "track": classification["track"], "topic": classification["topic"]},
    }
    events = [emit("task.started", branch=branch, task_id=args.task_id, payload=record["declaration"])]
    if classification["kind"] == "exploration" and created_branch:
        connection = database()
        try:
            linked_stage = active_stage(connection)
        finally:
            connection.close()
        events.append(emit("attempt.started", branch=branch, task_id=args.task_id, payload={
            "attempt_id": args.task_id, "track": classification["track"], "topic": classification["topic"],
            "base_commit": base_head, "goal": args.scope, "acceptance": args.acceptance,
            "stage_id": linked_stage["stage_id"] if linked_stage else None,
        }))
    elif classification["kind"] == "exploration":
        attempt = get_attempt(branch)
        if attempt and attempt["state"] != "active":
            events.append(emit("attempt.state_changed", branch=branch, task_id=args.task_id, payload={
                "attempt_id": attempt["attempt_id"], "state": "active",
            }))
    try:
        save_active(record, phase="starting")
        persist(events)
        save_active(record, phase="active")
    except Exception:
        try:
            delete_active(args.task_id)
        except Exception:
            pass
        if created_branch:
            run_git(["switch", original_branch], check=False)
            run_git(["branch", "-D", branch], check=False)
        raise
    return {"task_id": args.task_id, "started_at": record["started_at"], "branch": branch, "track": classification["track"]}


def task_status() -> dict:
    row = active_row()
    if row is None:
        return {"active": False, "checks": check_repository()}
    record = read_active()
    branch = assert_active_branch(record)
    return {
        "active": True, "task_id": record["task_id"], "started_at": record["started_at"],
        "branch": branch, "track": record["git"]["track"],
        "changed_paths": changed(record["baseline"], snapshot()),
        "state_updated": record["state_updated"], "decisions_added": record["decisions_added"],
        "missing_updates": [] if record["state_updated"] else ["project state update"],
        "checks": check_repository(),
    }


def task_recover(args: argparse.Namespace) -> dict:
    try:
        state = load_active_state(state_dir())
    except ActiveTaskError as exc:
        raise WorkflowError(str(exc)) from exc
    if state is None:
        row = active_row()
        if row is None:
            raise WorkflowError("没有可恢复的活动任务")
        record = json.loads(row["record_json"])
        save_active_state(
            state_dir(), record, state_updated=bool(row["state_updated"]),
            decisions_added=int(row["decisions_added"]), phase="active",
        )
        state = load_active_state(state_dir())
    assert state is not None
    record = state["record"]
    restore_active_row(database_path(), state_dir(), force=True)
    events = load_events(journal_path())
    finished = [
        event for event in events
        if event.get("task_id") == record["task_id"] and event["event_type"] == "task.finished"
    ]
    if args.skip_auto_commit:
        if not args.reason:
            raise WorkflowError("--skip-auto-commit 必须同时提供 --reason")
        if state["phase"] != "finishing" and not finished:
            raise WorkflowError("只有完成事件已写入但自动提交未完成时才能跳过自动提交")
        pending = state.get("finish") or {}
        if pending.get("events"):
            persist(pending["events"])
        persist([emit(
            "task.recovery.recorded", branch=record["git"]["branch"],
            task_id=record["task_id"], payload={
                "action": "skip-auto-commit", "reason": args.reason,
            },
        )])
        delete_active(record["task_id"])
        return {
            "status": "recovered",
            "task_id": record["task_id"],
            "auto_commit": "skipped",
            "reason": args.reason,
        }
    if state["phase"] == "starting":
        if not any(
            event.get("task_id") == record["task_id"] and event["event_type"] == "task.started"
            for event in events
        ):
            event = emit(
                "task.started", branch=record["git"]["branch"], task_id=record["task_id"],
                payload=record["declaration"],
                event_id=hashlib.sha256(
                    f"{record['task_id']}:recovered-start".encode("utf-8")
                ).hexdigest()[:32],
            )
            persist([event])
        save_active(
            record, state_updated=int(state["state_updated"]),
            decisions_added=state["decisions_added"], phase="active",
        )
        state["phase"] = "active"
    suggestion = (
        "使用与首次请求完全相同的参数重新运行 end；若自动提交持续失败，运行 "
        "task recover --skip-auto-commit --reason <原因>"
        if state["phase"] == "finishing"
        else "继续当前任务；写入后运行 fast，结束前运行 full，再运行 end"
    )
    return {
        "status": "recovered",
        "task_id": record["task_id"],
        "phase": state["phase"],
        "branch": record["git"]["branch"],
        "next_safe_action": suggestion,
    }


def task_abandon(args: argparse.Namespace) -> dict:
    record = read_active()
    branch = assert_active_branch(record)
    event = emit(
        "task.finished", branch=branch, task_id=record["task_id"],
        event_id=hashlib.sha256(
            f"{record['task_id']}:abandoned".encode("utf-8")
        ).hexdigest()[:32],
        payload={
            "summary": record["declaration"]["scope"],
            "evidence": "",
            "result": "abandoned",
            "route": "unchanged",
            "methods_action": "reviewed-no-change",
            "main_goal": "unchanged",
            "note": args.reason,
            "attempt_state": None,
            "started_at": record["started_at"],
        },
    )
    persist([event])
    delete_active(record["task_id"])
    return {
        "status": "abandoned",
        "task_id": record["task_id"],
        "reason": args.reason,
        "files_preserved": True,
        "branch_preserved": branch,
    }


def project_state_payload(record: dict, args: argparse.Namespace, branch: str) -> dict:
    connection = database()
    current = connection.project_state(branch, fallback_to_main=True)
    base = current or {}
    connection.close()
    next_steps = args.next if args.next is not None else json.loads(base.get("next_steps_json", "[]"))
    if len(next_steps) > 3:
        raise WorkflowError("state update 最多允许三个 --next")
    payload = {
        "status": args.status or base.get("status") or "进行中",
        "main_goal_version": args.main_goal_version or base.get("main_goal_version") or "v1",
        "goal": args.goal or base.get("goal") or record["declaration"]["scope"],
        "judgment": args.judgment or base.get("judgment") or args.breakpoint or record["declaration"]["scope"],
        "breakpoint": args.breakpoint or base.get("breakpoint") or record["declaration"]["scope"],
        "next_steps": next_steps,
        "blocker": args.blocker if args.blocker is not None else base.get("blocker", "无。"),
    }
    return payload


def state_arguments_requested(args: argparse.Namespace) -> bool:
    return any(getattr(args, name, None) is not None for name in STATE_ARGUMENTS) or args.next is not None


def state_update(args: argparse.Namespace) -> dict:
    record = read_active()
    branch = assert_active_branch(record)
    payload = project_state_payload(record, args, branch)
    persist([emit("project_state.updated", branch=branch, task_id=record["task_id"], payload=payload)])
    update_active_flags(state_updated=True)
    return payload


def decision_add(args: argparse.Namespace) -> dict:
    record = read_active()
    branch = assert_active_branch(record)
    unique = hashlib.sha256(f"{branch}:{timestamp()}:{os.getpid()}".encode("utf-8")).hexdigest()[:8]
    decision_id = args.id or f"D-{datetime.now().strftime('%Y%m%d')}-{unique}"
    payload = {"decision_id": decision_id, "decision": args.decision, "alternatives": args.alternatives,
               "basis": args.basis, "reopen_condition": args.reopen_condition}
    persist([emit("decision.recorded", branch=branch, task_id=record["task_id"], payload=payload)])
    update_active_flags(decision_added=True)
    return payload


def attempt_update(args: argparse.Namespace) -> dict:
    record = read_active()
    branch = assert_active_branch(record)
    attempt = get_attempt(branch)
    if not attempt:
        raise WorkflowError("当前活动任务不属于探索尝试")
    if (args.hypothesis is None and args.conclusion is None and not args.evidence
            and args.current_step is None and args.progress is None and args.next_step is None):
        raise WorkflowError("attempt update 至少提供一个更新字段")
    payload = {
        "attempt_id": attempt["attempt_id"],
        "hypothesis": clean_text(args.hypothesis, "探索假设", 2000),
        "conclusion": clean_text(args.conclusion, "探索结论", 2000),
        "evidence": [clean_text(item, "探索证据", 1000) for item in (args.evidence or [])],
        "current_step": clean_text(args.current_step, "探索当前步骤", 500),
        "progress": clean_text(args.progress, "探索进展", 2000),
        "next_step": clean_text(args.next_step, "探索下一步", 500),
    }
    persist([emit("attempt.updated", branch=branch, task_id=record["task_id"], payload=payload)])
    return get_attempt(branch) or {}


def pre_commit_check() -> None:
    check_repository(raise_on_error=True)
    if active_row() is not None:
        record = read_active()
        assert_active_branch(record)
        if not record["state_updated"]:
            raise WorkflowError("活动任务尚未更新结构化项目状态")


def install_git_hook(force: bool = False) -> str:
    ensure_resource_directories(ROOT)
    connection = database()
    connection.close()
    if not is_git_repo():
        return "非 Git 仓库，已初始化数据库并跳过 pre-commit 安装"
    configured = run_git(["config", "--local", "--get", "core.hooksPath"], check=False).stdout.strip()
    if configured and configured != TRACKED_HOOKS_DIR and not force:
        raise WorkflowError(f"本仓库已有 core.hooksPath={configured}；使用 --force 前请先人工合并")
    hook = ROOT / TRACKED_HOOKS_DIR / "pre-commit"
    content = HOOK_TEMPLATE
    if hook.exists():
        old = hook.read_text(encoding="utf-8", errors="replace")
        if old != content and MANAGED_HOOK_MARKER not in old and not force:
            raise WorkflowError("已有非本工作流管理的 pre-commit；使用 --force 前请先人工合并")
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(content, encoding="utf-8", newline="\n")
    try:
        hook.chmod(0o755)
    except OSError:
        pass
    run_git(["config", "--local", "core.hooksPath", TRACKED_HOOKS_DIR])
    installation = ROOT / INSTALLATION_PATH
    if not installation.exists():
        write_json(installation, installation_record(ROOT, __version__))
    return f"已启用仓库内 pre-commit，并初始化 SQLite: {database_path()}"


def auto_commit(record: dict, paths: list[str], result: str, message: str | None) -> dict:
    declaration, cfg = record["declaration"], config()["git_auto_commit"]
    mode = declaration["git_commit"]
    eligible = cfg.get("enabled", True) and declaration["task_size"] in cfg.get("eligible_task_sizes", ["large"])
    if mode == "never" or not eligible:
        return {"status": "not-requested"}
    overlap = sorted(set(paths) & set(record["git"]["dirty_paths"]))
    if overlap:
        detail = "任务修改了启动前已脏文件: " + ", ".join(overlap)
        if mode == "always":
            raise WorkflowError(detail)
        return {"status": "skipped", "reason": detail}
    commit_paths = [path for path in paths if not path.startswith(".project_hooks/")]
    if not commit_paths:
        return {"status": "skipped", "reason": "没有任务归属文件"}
    git_dir = Path(run_git(["rev-parse", "--git-dir"]).stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (ROOT / git_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="project-hooks-index-"))
    env, temp_index = os.environ.copy(), temp_dir / "index"
    env["GIT_INDEX_FILE"] = str(temp_index)
    try:
        if run_git(["rev-parse", "--verify", "HEAD"], check=False).returncode == 0:
            run_git(["read-tree", "HEAD"], env=env)
        else:
            run_git(["read-tree", "--empty"], env=env)
        run_git(["add", "-A", "--", *commit_paths], env=env)
        staged = run_git(["diff", "--cached", "--name-only"], env=env).stdout.splitlines()
        if not staged:
            return {"status": "skipped", "reason": "任务路径没有可提交差异"}
        commit_message = message or f"maint({declaration['kind']}): {record['task_id']}"
        pre_commit_check()
        completed = run_git(
            ["-c", "core.hooksPath=.git/no-hooks", "commit", "-m", commit_message],
            env=env, check=False,
        )
        if completed.returncode != 0:
            raise WorkflowError("自动提交失败: " + (completed.stderr or completed.stdout).strip())
        run_git(["reset", "--quiet", "HEAD", "--", *staged])
        return {"status": "committed", "commit": run_git(["rev-parse", "HEAD"]).stdout.strip(),
                "message": commit_message, "paths": staged, "result": result}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _base_active_record(record: dict) -> dict:
    return {
        key: value for key, value in record.items()
        if key not in {"state_updated", "decisions_added", "recovery_phase", "finish"}
    }


def _finish_signature(args: argparse.Namespace) -> str:
    values = {
        name: getattr(args, name, None)
        for name in (
            "task_id", "result", "route", "methods_action", "main_goal", "note",
            "evidence", "commit_message", "attempt_state", *STATE_ARGUMENTS, "next",
        )
    }
    return hashlib.sha256(canonical_json(values).encode("utf-8")).hexdigest()


def _fixed_finish_event_ids(task_id: str, events: list[dict]) -> None:
    for index, event in enumerate(events):
        material = f"{task_id}:finish:{index}:{event['event_type']}"
        event["event_id"] = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def stage_update_reminder(record: dict) -> dict | None:
    connection = database()
    try:
        stage = active_stage(connection)
    finally:
        connection.close()
    if not stage or stage.get("task_id") == record.get("task_id"):
        return None
    return {
        "code": "stage-not-updated",
        "message": "本任务没有更新当前阶段；请确认阶段进展、当前步骤和下一步是否仍然准确",
        "stage_id": stage["stage_id"],
        "stage_updated_at": stage.get("updated_at"),
        "next_safe_command": f".\\workflow-monitor.exe stage update {stage['stage_id']} ...",
    }


def finish_task(args: argparse.Namespace) -> dict:
    record = read_active()
    if args.task_id != record["task_id"]:
        raise WorkflowError(f"活动任务是 {record['task_id']}，不是 {args.task_id}")
    branch = assert_active_branch(record)
    signature = _finish_signature(args)
    pending_finish = record.get("finish") if record.get("recovery_phase") == "finishing" else None
    if pending_finish is not None:
        if pending_finish.get("signature") != signature:
            raise WorkflowError(
                "该任务正在重试 end，参数与首次结束请求不同；请使用相同参数重试，"
                "或运行 task recover --skip-auto-commit --reason <原因>"
            )
        events = pending_finish["events"]
        finish_values = pending_finish["values"]
        persist(events)
        paths = changed(record["baseline"], snapshot())
        report = {
            "task_id": args.task_id,
            "started_at": record["started_at"],
            "finished_at": pending_finish["finished_at"],
            **finish_values,
            "changed_paths": paths,
        }
        report["git"] = auto_commit(record, paths, finish_values["result"], args.commit_message)
        reminder = stage_update_reminder(record)
        if reminder:
            report["warnings"] = [reminder]
        delete_active(args.task_id)
        return report
    check_repository(raise_on_error=True)
    final_state_requested = state_arguments_requested(args)
    if not record["state_updated"] and not final_state_requested:
        raise WorkflowError("结束前必须执行 state update")
    if args.route == "changed" and record["decisions_added"] < 1:
        raise WorkflowError("路线发生变化时必须在本任务执行 decision add")
    attempt = get_attempt(branch) if record["git"]["track"] != "stable" else None
    events: list[dict] = []
    if final_state_requested:
        events.append(emit(
            "project_state.updated",
            branch=branch,
            task_id=args.task_id,
            payload=project_state_payload(record, args, branch),
        ))
    if attempt:
        if not args.attempt_state:
            raise WorkflowError("探索任务结束时必须显式使用 --attempt-state")
        if args.attempt_state == "validated" and (not attempt["hypothesis"] or not attempt["evidence"] or not attempt["conclusion"]):
            raise WorkflowError("validated 尝试必须填写 hypothesis、evidence 和 conclusion")
        archive = f"archive/{attempt['track']}/{attempt['topic']}" if args.attempt_state in {"negative", "inconclusive", "paused"} else None
        events.append(emit("attempt.state_changed", branch=branch, task_id=args.task_id, payload={
            "attempt_id": attempt["attempt_id"], "state": args.attempt_state, "archive_branch": archive,
        }))
        if args.attempt_state == "validated":
            events.append(emit("exploration.recorded", branch=branch, task_id=args.task_id, payload={
                "exploration_id": branch,
                "branch": branch, "goal": attempt["goal"], "result": "validated",
                "evidence": "; ".join(attempt["evidence"]), "disposition_ref": "pending PR",
            }))
    elif args.attempt_state:
        raise WorkflowError("stable 任务不能使用 --attempt-state")
    evidence = "; ".join(args.evidence or [])
    events.append(emit("task.finished", branch=branch, task_id=args.task_id, payload={
        "summary": record["declaration"]["scope"], "evidence": evidence, "result": args.result,
        "route": args.route, "methods_action": args.methods_action, "main_goal": args.main_goal,
        "note": args.note, "attempt_state": args.attempt_state, "started_at": record["started_at"],
    }))
    _fixed_finish_event_ids(args.task_id, events)
    finished_at = timestamp()
    finish_values = {
        "result": args.result,
        "route": args.route,
        "methods_action": args.methods_action,
        "main_goal": args.main_goal,
        "note": args.note,
        "attempt_state": args.attempt_state,
    }
    finish_state = {
        "signature": signature,
        "events": events,
        "values": finish_values,
        "finished_at": finished_at,
    }
    save_active(
        _base_active_record(record),
        state_updated=int(record["state_updated"] or final_state_requested),
        decisions_added=record["decisions_added"],
        phase="finishing",
        finish=finish_state,
    )
    persist(events)
    if final_state_requested:
        record["state_updated"] = True
    paths = changed(record["baseline"], snapshot())
    report = {"task_id": args.task_id, "started_at": record["started_at"], "finished_at": finished_at,
              **finish_values,
              "changed_paths": paths}
    report["git"] = auto_commit(record, paths, args.result, args.commit_message)
    reminder = stage_update_reminder(record)
    if reminder:
        report["warnings"] = [reminder]
    delete_active(args.task_id)
    return report


def branch_status() -> dict:
    branch, active = current_branch(), None
    row = active_row()
    if row:
        record = read_active()
        active = {"task_id": record["task_id"], "started_branch": record["git"]["branch"],
                  "branch_matches": record["git"]["branch"] == branch}
    attempt = get_attempt(branch)
    return {"branch": branch, "classification": classify_branch(branch), "attempt": attempt,
            "active_task": active, "policy": branch_policy()}


def assert_no_active_task() -> None:
    if active_row() is not None:
        raise WorkflowError("该命令要求没有活动任务；请先完成当前生命周期")


def assert_clean_worktree() -> None:
    dirty = git_dirty_paths()
    if dirty:
        raise WorkflowError("工作树必须干净: " + ", ".join(dirty))


def has_finished_receipt(branch: str, attempt_state: str) -> bool:
    connection = database()
    found = connection.has_finished_receipt(branch, attempt_state)
    connection.close()
    return found


def prepare_pr() -> dict:
    assert_no_active_task()
    check_repository(raise_on_error=True)
    assert_clean_worktree()
    branch = current_branch()
    attempt = get_attempt(branch)
    if classify_branch(branch)["kind"] != "exploration" or not attempt:
        raise WorkflowError("prepare-pr 只能在有效探索分支运行")
    if attempt["state"] != "validated" or not all((attempt["hypothesis"], attempt["evidence"], attempt["conclusion"])):
        raise WorkflowError("只有证据完整的 validated 尝试可以准备 PR")
    if not has_finished_receipt(branch, "validated"):
        raise WorkflowError("缺少 validated 生命周期回执")
    assert_main_matches_origin()
    default = branch_policy()["default_branch"]
    if run_git(["merge-base", "--is-ancestor", default, "HEAD"], check=False).returncode != 0:
        raise WorkflowError(f"探索分支未基于最新 {default}")
    body = (f"## Summary\n\n{attempt['goal']}\n\n## Evidence\n\n" + "\n".join(f"- {item}" for item in attempt["evidence"]) +
            "\n\n## Integration\n\n- [ ] User explicitly confirmed merge\n- Merge method: Squash\n")
    return {"ready": True, "branch": branch, "base": default, "merge_method": "squash_pr",
            "requires_user_confirmation": True, "pr": {"title": f"[{attempt['track']}] {attempt['topic']}", "body": body},
            "network_actions_performed": False}


def archive_attempt() -> dict:
    assert_no_active_task()
    check_repository(raise_on_error=True)
    assert_clean_worktree()
    branch = current_branch()
    attempt = get_attempt(branch)
    if classify_branch(branch)["kind"] != "exploration" or not attempt:
        raise WorkflowError("archive-attempt 只能在有效探索分支运行")
    if attempt["state"] not in {"negative", "inconclusive", "paused"} or not has_finished_receipt(branch, attempt["state"]):
        raise WorkflowError("尝试必须先通过 end 记录 negative、inconclusive 或 paused")
    target = f"archive/{attempt['track']}/{attempt['topic']}"
    if ref_exists(f"refs/heads/{target}") or ref_exists(f"refs/remotes/origin/{target}"):
        raise WorkflowError(f"归档分支已存在或与远程冲突: {target}")
    persist([emit("attempt.archived", branch=branch, task_id=None, payload={"attempt_id": attempt["attempt_id"], "archive_branch": target})])
    run_git(["branch", "-m", target])
    return {"archived": True, "from": branch, "branch": target, "network_actions_performed": False,
            "next": [f"提交 maintenance/events.jsonl 后执行 git push -u origin {target}",
                     f"回到 main 的稳定任务运行 project_hooks exploration import {target}"]}


def context_data(branch: str | None = None) -> dict:
    try:
        result = read_model().context(branch)
        result["recent_decisions"] = read_model().records("decisions", 5)
        return result
    except ReadModelError as exc:
        raise WorkflowError(str(exc)) from exc


def markdown_context(data: dict) -> str:
    state = data.get("overview_state") or data.get("state") or {}
    profile = data.get("project_profile") or {}
    stage = data.get("current_stage") or {}
    lines = ["# 动态维护上下文", "", f"- 当前分支：`{data['branch']}`",
             f"- 当前状态：{state.get('status', '未设置')}", f"- 主目标版本：{state.get('main_goal_version', '未设置')}",
             "", "## 项目资料", "", f"- 项目描述：{profile.get('description') or '未设置'}",
             f"- 大目标：{profile.get('big_goal') or '未设置'}",
             "", "## 当前大阶段", "", f"- 阶段：{stage.get('title') or '未设置'}",
             f"- 阶段目标：{stage.get('goal') or '未设置'}",
             f"- 阶段进展：{stage.get('summary') or '未设置'}",
             f"- 当前步骤：{stage.get('current_step') or '未设置'}",
             f"- 下一步：{stage.get('next_step') or '未设置'}",
             f"- 阶段阻塞：{stage.get('blocker') or '无。'}",
             "", "## 当前工作目标", "", state.get("goal") or "未设置", "", "## 当前判决", "",
             state.get("judgment") or "未设置", "", "## 工作断点", "", state.get("breakpoint") or "未设置",
             "", "## 真实断点", "", git_state_summary(data.get("git_state") or {}),
             "", "## 接下来三步", ""]
    for index, item in enumerate(state.get("next_steps", []), 1):
        lines.append(f"{index}. {item}")
    if not state.get("next_steps"):
        lines.append("无。")
    lines += ["", "## 当前阻塞", "", state.get("blocker") or "无。", "", "## 当前探索", ""]
    for attempt in data.get("active_attempts") or []:
        lines.append(
            f"- `{attempt['branch']}`｜当前：{attempt.get('current_step') or '未设置'}｜"
            f"下一步：{attempt.get('next_step') or '未设置'}｜阶段：{attempt.get('stage_id') or '未归属阶段'}"
        )
    if not data.get("active_attempts"):
        lines.append("无。")
    lines += ["", "## 最近交接", ""]
    for item in data["recent_handoffs"]:
        lines.append(f"- {item['occurred_at']}｜{item['task']}｜{item['result']}｜主目标：{item['main_goal_change']}")
    if not data["recent_handoffs"]:
        lines.append("无。")
    return "\n".join(lines) + "\n"


def query_output(kind: str, limit: int) -> list[dict]:
    try:
        result = read_model().records(kind, limit)
        if kind == "history":
            for item in result:
                item.pop("payload_json", None)
        return result
    except ReadModelError as exc:
        raise WorkflowError(str(exc)) from exc


def render_records(records: list[dict], fmt: str) -> str | list[dict]:
    if fmt == "json":
        return records
    lines: list[str] = []
    for record in records:
        lines.append("- " + " | ".join(f"{key}: {value}" for key, value in record.items()))
    return "\n".join(lines) + ("\n" if lines else "")


def catalog_write_context() -> tuple[dict, str]:
    record = read_active()
    return record, assert_active_branch(record)


def next_catalog_ingest_task_id() -> str:
    date = datetime.now(resolve_timezone(config()["timezone"])).strftime("%Y%m%d")
    prefix = f"{date}_catalog_ingest_"
    connection = database()
    try:
        used = connection.task_ids_like(prefix)
    finally:
        connection.close()
    for index in range(1, 1000):
        candidate = f"{prefix}{index:03d}"
        if candidate not in used:
            return candidate
    raise WorkflowError("当天的 catalog ingest 自动任务编号已用尽")


def catalog_auto_start(filename: str) -> dict:
    classification = classify_branch(current_branch())
    if classification["kind"] != "stable":
        raise WorkflowError("探索分支上没有活动任务；请先手工 start，再运行 catalog ingest")
    task_id = next_catalog_ingest_task_id()
    return start_task(argparse.Namespace(
        task_id=task_id,
        kind="analysis",
        scope=f"登记科研资料 {filename}",
        out_of_scope="修改科研内容或自动提交",
        acceptance=[f"{filename} 已进入科研资料索引"],
        task_size="small",
        git_commit="never",
        track="stable",
        topic=None,
    ))


def catalog_auto_finish(success: bool, note: str, evidence: list[str]) -> dict:
    record = read_active()
    return finish_task(argparse.Namespace(
        task_id=record["task_id"],
        result="completed" if success else "failed",
        route="unchanged",
        methods_action="reviewed-no-change",
        main_goal="unchanged",
        note=note,
        evidence=evidence,
        commit_message=None,
        attempt_state=None,
        goal=None,
        judgment=None,
        breakpoint=note,
        blocker=None,
        status=None,
        main_goal_version=None,
        next=None,
    ))


def catalog_runtime() -> CatalogRuntime:
    return CatalogRuntime(
        root=ROOT,
        database=database,
        emit=emit,
        persist=persist,
        write_context=catalog_write_context,
        timestamp=timestamp,
        has_active_task=lambda: active_row() is not None,
        auto_start=catalog_auto_start,
        auto_finish=catalog_auto_finish,
    )


def exploration_import(args: argparse.Namespace) -> dict:
    record = read_active()
    if current_branch() != "main" or record["git"]["track"] != "stable":
        raise WorkflowError("exploration import 必须在 main 的稳定任务中运行")
    result = run_git(["show", f"{args.archive_branch}:maintenance/events.jsonl"], check=False)
    if result.returncode != 0:
        raise WorkflowError(f"无法读取归档分支事件日志: {args.archive_branch}")
    branch_events: list[dict] = []
    for number, line in enumerate(result.stdout.splitlines(), 1):
        if line.strip():
            try:
                branch_events.append(validate_event(json.loads(line), line=number))
            except (json.JSONDecodeError, StoreError) as exc:
                raise WorkflowError(str(exc)) from exc
    starts = [event for event in branch_events if event["event_type"] == "attempt.started"]
    states = [event for event in branch_events if event["event_type"] == "attempt.state_changed"]
    archives = [event for event in branch_events if event["event_type"] == "attempt.archived" and event["payload"].get("archive_branch") == args.archive_branch]
    if not starts or not states or not archives:
        raise WorkflowError("归档分支缺少完整的尝试开始、判决或归档事件")
    start, state = starts[-1], states[-1]
    updates = [event for event in branch_events if event["event_type"] == "attempt.updated" and event["payload"].get("attempt_id") == start["payload"]["attempt_id"]]
    evidence = [item for event in updates for item in event["payload"].get("evidence", [])]
    payload = {"exploration_id": start["branch"], "branch": args.archive_branch,
               "goal": start["payload"]["goal"], "result": state["payload"]["state"],
               "evidence": "; ".join(evidence), "disposition_ref": args.archive_branch}
    persist([emit("exploration.recorded", branch="main", task_id=record["task_id"], payload=payload)])
    return payload


def legacy_rows(path: Path) -> list[list[str]]:
    if not path.is_file():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or re.match(r"^\|\s*:?-+", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] not in {"探索简单总结", "ID", "分支", "任务时间（Asia/Shanghai）"}:
            result.append(cells)
    return result


def legacy_event_id(source: str, content: str) -> str:
    return f"legacy-{source}-{hashlib.sha256(content.encode('utf-8')).hexdigest()[:24]}"


def section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def migrate_legacy(delete_legacy: bool) -> dict:
    events: list[dict] = []
    change_file = ROOT / "maintenance/change_archive.md"
    for row in legacy_rows(change_file):
        if len(row) >= 5:
            summary, task_id, occurred_at, evidence, result = row[:5]
            events.append(emit("task.finished", branch="main", task_id=task_id, occurred_at=occurred_at,
                               event_id=legacy_event_id("change", "|".join(row)), payload={
                                   "summary": summary, "evidence": evidence, "result": result,
                                   "route": "legacy", "note": result, "legacy_source": "change_archive.md",
                               }))
    decision_file = ROOT / "maintenance/decision_log.md"
    for row in legacy_rows(decision_file):
        if len(row) >= 6:
            decision_id, occurred_at, decision, alternatives, basis, reopen = row[:6]
            events.append(emit("decision.recorded", branch="main", task_id=None, occurred_at=occurred_at,
                               event_id=legacy_event_id("decision", decision_id), payload={
                                   "decision_id": decision_id, "decision": decision, "alternatives": alternatives,
                                   "basis": basis, "reopen_condition": reopen, "legacy_source": "decision_log.md",
                               }))
    current_file = ROOT / "maintenance/current_task.md"
    if current_file.is_file():
        text = current_file.read_text(encoding="utf-8")
        last = re.search(r"\*\*最后更新\*\*：([^\n]+)", text)
        status = re.search(r"\*\*当前状态\*\*：([^\n]+)", text)
        version = re.search(r"\*\*主目标版本\*\*：([^\n]+)", text)
        next_text = section(text, "接下来三步")
        next_steps = [re.sub(r"^\d+\.\s*", "", line).strip() for line in next_text.splitlines() if re.match(r"^\d+\.", line)]
        occurred = last.group(1).strip() if last else timestamp()
        payload = {"status": status.group(1).strip() if status else "未知", "main_goal_version": version.group(1).strip() if version else "v1",
                   "goal": section(text, "当前主目标"), "judgment": section(text, "当前判决"),
                   "breakpoint": section(text, "真实断点"), "next_steps": next_steps, "blocker": section(text, "当前阻塞")}
        events.append(emit("project_state.updated", branch="main", task_id=None, occurred_at=occurred,
                           event_id=legacy_event_id("state", canonical_json(payload)), payload=payload))
        for row in legacy_rows(current_file):
            if len(row) >= 4:
                events.append(emit("handoff.recorded", branch="main", task_id=None, occurred_at=row[0],
                                   event_id=legacy_event_id("handoff", "|".join(row)), payload={
                                       "task": row[1], "result": row[2], "main_goal_change": row[3],
                                   }))
    exploration_file = ROOT / "maintenance/exploration_log.md"
    for row in legacy_rows(exploration_file):
        if len(row) >= 5:
            events.append(emit("exploration.recorded", branch="main", task_id=None,
                               occurred_at="1970-01-01 00:00:00（Asia/Shanghai）",
                               event_id=legacy_event_id("exploration", "|".join(row)), payload={
                                   "branch": row[0], "goal": row[1], "result": row[2], "evidence": row[3], "disposition_ref": row[4],
                               }))
    history_files = sorted((state_dir() / "history").glob("*.json")) if (state_dir() / "history").is_dir() else []
    for path in history_files:
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"旧历史 JSON 损坏: {path.name}: {exc}") from exc
        start, end = receipt.get("start", {}), receipt.get("end", {})
        payload = {"summary": start.get("declaration", {}).get("scope", path.stem),
                   "evidence": canonical_json(end.get("git", {})), "result": end.get("result", "unknown"),
                   "route": end.get("route", "legacy"), "note": end.get("note", ""),
                   "started_at": start.get("started_at"), "legacy_source": path.name}
        events.append(emit("task.receipt_imported", branch=start.get("git", {}).get("branch") or "main",
                           task_id=start.get("task_id") or path.stem, occurred_at=end.get("finished_at") or start.get("started_at") or timestamp(),
                           event_id=legacy_event_id("receipt", path.name), payload=payload))
    persist(events)
    legacy_active = state_dir() / "active.json"
    if legacy_active.is_file() and active_row() is None:
        record = json.loads(legacy_active.read_text(encoding="utf-8"))
        record["version"] = 2
        record["git"].setdefault("branch", current_branch())
        record["git"].setdefault("track", classify_branch(record["git"]["branch"])["track"])
        record["git"].setdefault("topic", classify_branch(record["git"]["branch"])["topic"])
        save_active(record)
        persist([emit("task.started", branch=record["git"]["branch"], task_id=record["task_id"],
                      event_id=legacy_event_id("active", record["task_id"]), occurred_at=record["started_at"], payload=record["declaration"])])
    connection = database()
    counts = connection.table_counts(
        ("events", "task_archive", "decisions", "handoffs", "project_state", "explorations")
    )
    connection.close()
    if delete_legacy:
        for path in (current_file, change_file, decision_file, exploration_file):
            if path.exists():
                path.unlink()
        attempts = ROOT / "maintenance/attempts"
        if attempts.is_dir():
            shutil.rmtree(attempts)
        for path in history_files:
            path.unlink()
        history_dir = state_dir() / "history"
        if history_dir.is_dir() and not any(history_dir.iterdir()):
            history_dir.rmdir()
        if legacy_active.exists():
            legacy_active.unlink()
    return {"migrated_events": len(events), "legacy_history_files": len(history_files), "counts": counts,
            "legacy_deleted": delete_legacy}


def db_command(args: argparse.Namespace) -> dict:
    if args.db_command == "rebuild":
        if active_row() is not None:
            raise WorkflowError("活动任务期间不能手工重建数据库，以免丢失本地文件基线")
        for path in (database_path(), Path(str(database_path()) + "-wal"), Path(str(database_path()) + "-shm")):
            if path.exists():
                path.unlink()
        connection = repository(rebuild(database_path(), journal_path()))
        count = connection.event_count()
        connection.close()
        return {"status": "rebuilt", "events": count}
    if args.db_command == "migrate":
        return migrate_legacy(args.delete_legacy)
    connection = database()
    integrity = connection.integrity()
    event_count = connection.event_count()
    stored = connection.journal_hash()
    connection.close()
    output = {"status": "passed" if integrity == "ok" else integrity, "schema_version": SCHEMA_VERSION,
              "events": event_count, "journal_hash": journal_hash(journal_path()),
              "database_matches_journal": bool(stored and stored == journal_hash(journal_path()))}
    if args.db_command == "verify" and (integrity != "ok" or not output["database_matches_journal"]):
        raise WorkflowError("数据库完整性验证失败")
    return output


def diagnostic_check_snapshot() -> dict:
    """Collect check/db-verify evidence without creating or rebuilding the database."""
    errors: list[str] = []
    try:
        cfg = config()
        if cfg.get("version") != 3:
            errors.append("workflow_config_version")
        if cfg.get("core_read_order") != STATIC_READ_ORDER:
            errors.append("core_read_order")
        if cfg.get("maintenance_store") != DEFAULT_STORE:
            errors.append("maintenance_store")
    except Exception:
        errors.append("workflow_config_unreadable")
    for relative in (*STATIC_READ_ORDER, "maintenance/events.jsonl", INSTALLATION_PATH.as_posix()):
        if not (ROOT / relative).is_file():
            errors.append(f"missing:{relative}")

    database_result: dict = {"status": "missing", "database_matches_journal": False}
    path = database_path()
    if path.is_file():
        try:
            actual_hash = journal_hash(journal_path())
            database_result = verification_snapshot(path, actual_hash)
        except Exception as exc:
            database_result = {"status": "failed", "error_type": type(exc).__name__}
    git_result = {"relation": "unknown", "branch_kind": "unknown"}
    try:
        state = read_model().git_state(branch_policy()["default_branch"])
        git_result = {
            "relation": state.get("relation") or "unknown",
            "branch_kind": classify_branch(state.get("branch") or "").get("kind") or "unknown",
        }
    except Exception:
        pass
    return {
        "check": {"status": "passed" if not errors else "failed", "errors": errors},
        "db_verify": database_result,
        "git": git_result,
    }


def diagnostics_command(args: argparse.Namespace) -> dict:
    if args.diagnostics_command == "status":
        return diagnostics_status(ROOT)
    if args.diagnostics_command == "resolve":
        return resolve_diagnostic(
            ROOT, args.fingerprint, reason=args.reason, application_version=__version__,
        )
    if args.diagnostics_command == "cleanup":
        return cleanup_resolved_diagnostics(ROOT)
    output = args.output or (
        ROOT / "diagnostics-export" /
        f"workflow-monitor-diagnostics-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    )
    result = export_diagnostics(
        ROOT,
        output,
        application_version=__version__,
        schema_version=SCHEMA_VERSION,
        execution_mode=execution_mode(),
        checks=diagnostic_check_snapshot,
    )
    try:
        relative = output.resolve().relative_to(ROOT).as_posix()
        ignored = run_git(["check-ignore", "--quiet", "--", relative], check=False).returncode == 0
        if not ignored:
            result["warning"] = "诊断包位于项目内且未被 Git 忽略；提交前请移出或加入忽略规则"
    except (OSError, ValueError):
        pass
    if args.open_issue:
        result["issue_opened"] = open_bug_report(
            incident_id=result.get("latest_incident_id"), version=__version__,
        )
    return result


def external_tools() -> list[dict]:
    return external_tools_from_events(load_events(journal_path()))


def workbench_items() -> list[dict]:
    return workbench_items_from_events(load_events(journal_path()))


def _external_tool_text(items: list[dict]) -> str:
    lines = ["# 外置工作台工具", ""]
    if not items:
        lines.append("尚未登记外置工具。")
    for item in items:
        lines.append(
            f"- `{item['tool_id']}`｜{item['name']}｜{item['kind']}｜{item['status']}｜{item['purpose']}"
        )
    return "\n".join(lines) + "\n"


def _workbench_item_text(items: list[dict]) -> str:
    lines = ["# 科研工作台索引", ""]
    if not items:
        lines.append("尚未登记工作台条目。")
    for item in items:
        source = ORIGIN_LABELS.get(item.get("origin"), item.get("origin") or "未知")
        package = f"｜{item.get('package_id')} {item.get('package_version')}" if item.get("package_id") else ""
        purposes = "、".join(PURPOSE_LABELS[value] for value in item.get("purposes", [])) or "未指定用途"
        review = "｜待复核" if item.get("review_state") == "needs_review" else ""
        lines.append(
            f"- `{item['item_id']}`｜{KIND_LABELS.get(item['kind'], item['kind'])}｜{purposes}"
            f"｜{item['title']}｜{item['status']}｜{source}{package}{review}｜{item['summary']}"
        )
    return "\n".join(lines) + "\n"


def _package_text(package: dict) -> str:
    lines = [
        "# 工作台包预览", "",
        f"- 包：`{package['package_id']}`",
        f"- 名称：{package['name']}",
        f"- 版本：{package['version']}",
        f"- 包格式：v{package['schema_version']}（来源 v{package.get('source_schema_version', package['schema_version'])}）",
        f"- 作者：{package['author']}",
        f"- 说明：{package.get('description') or '未提供'}",
        f"- 许可证：{package.get('license') or '未注明'}",
        f"- 内容哈希：`{package['package_sha256']}`", "", "## 条目", "",
    ]
    if package.get("project_status"):
        lines.insert(8, f"- 当前项目：{package['project_status']}｜目标 `{package.get('destination', '')}`")
    actions = {item.get("item_id"): item.get("action") for item in package.get("item_actions") or []}
    for item in package.get("items") or []:
        original = item.get("original_item_id") or item.get("item_id")
        action = f"｜{actions.get(original)}" if actions.get(original) else ""
        purposes = "、".join(PURPOSE_LABELS[value] for value in item.get("purposes", [])) or "未指定用途"
        lines.append(f"- `{original}`｜{KIND_LABELS.get(item['kind'], item['kind'])}｜{purposes}{action}｜{item['title']}｜{item['summary']}")
    return "\n".join(lines) + "\n"


def workbench_command(args: argparse.Namespace) -> dict | list[dict] | str:
    if args.workbench_command == "external":
        items = external_tools()
        by_id = {item["tool_id"]: item for item in items}
        command = args.external_command
        if command == "list":
            return items if args.format == "json" else _external_tool_text(items)
        tool_id = validate_tool_id(args.tool_id)
        current = by_id.get(tool_id)
        if command == "show":
            if current is None:
                raise WorkflowError("没有找到该外置工具")
            return current if args.format == "json" else _external_tool_text([current])
        record, branch = stable_write_context()
        if command == "add":
            if current is not None:
                raise WorkflowError("该外置工具已经存在；请使用 update")
            payload = normalize_external_tool({
                "tool_id": tool_id, "name": args.name, "kind": args.kind,
                "purpose": args.purpose, "usage_hint": args.usage_hint,
                "reference": args.reference, "status": "active",
            })
            event_type = "workbench.external_upserted"
        elif command == "update":
            if current is None:
                raise WorkflowError("没有找到该外置工具；请先 add")
            supplied = {key: value for key, value in {
                "tool_id": tool_id, "name": args.name, "kind": args.kind,
                "purpose": args.purpose, "usage_hint": args.usage_hint,
                "reference": args.reference,
            }.items() if value is not None}
            if set(supplied) == {"tool_id"}:
                raise WorkflowError("update 至少提供一个要修改的字段")
            payload = normalize_external_tool(supplied, current=current)
            event_type = "workbench.external_upserted"
        else:
            if current is None:
                raise WorkflowError("没有找到该外置工具")
            payload = {"tool_id": tool_id, "status": {"pause": "paused", "restore": "active", "retire": "retired"}[command], "note": clean_text(args.note, "说明", 500) or ""}
            event_type = "workbench.external_status_changed"
        persist([emit(event_type, branch=branch, task_id=record["task_id"], payload=payload)])
        return payload

    if args.workbench_command == "item":
        items = workbench_items()
        by_id = {item["item_id"]: item for item in items}
        command = args.item_command
        if command == "list":
            legacy_filter = legacy_item_type_taxonomy(args.legacy_item_type) if args.legacy_item_type else None
            kind_filter = args.kind or (legacy_filter or {}).get("kind")
            purpose_filters = set(args.purposes or (legacy_filter or {}).get("purposes") or [])
            selected = [item for item in items if (
                (not kind_filter or item["kind"] == kind_filter)
                and (not purpose_filters or purpose_filters.intersection(item.get("purposes", [])))
                and (not args.status or item["status"] == args.status)
            )]
            return selected if args.format == "json" else _workbench_item_text(selected)
        item_id = validate_item_id(args.item_id)
        current = by_id.get(item_id)
        if command == "show":
            if current is None:
                raise WorkflowError("没有找到该工作台条目")
            return current if args.format == "json" else _workbench_item_text([current])
        record, branch = stable_write_context()
        if command == "add":
            if current is not None:
                raise WorkflowError("该工作台条目已经存在；请使用 update")
            relative = Path(str(args.path).replace("\\", "/"))
            if not relative.as_posix().startswith(WORKBENCH_LOCAL.as_posix() + "/"):
                raise WorkflowError("本地条目 Markdown 必须位于 workbench/local")
            content = (ROOT / relative).resolve()
            try:
                content.relative_to((ROOT / WORKBENCH_LOCAL).resolve())
            except ValueError as exc:
                raise WorkflowError("本地条目 Markdown 路径越界") from exc
            if not content.is_file():
                raise WorkflowError("没有找到本地条目 Markdown")
            if bool(args.kind) == bool(args.legacy_item_type):
                raise WorkflowError("add 必须且只能提供 --kind；旧脚本可改用单独的 --type")
            taxonomy = ({"kind": args.kind, "purposes": args.purposes or []}
                        if args.kind else {**legacy_item_type_taxonomy(args.legacy_item_type), "source_schema_version": 2})
            payload = normalize_workbench_item({
                "item_id": item_id, **taxonomy, "title": args.title,
                "summary": args.summary, "path": relative.as_posix(),
                "content_sha256": sha256_file(content), "tags": args.tags or [],
                "reference": args.reference, "status": "active", "origin": "local",
            })
            event_type = "workbench.entry_upserted"
        elif command == "update":
            if current is None:
                raise WorkflowError("没有找到该工作台条目；请先 add")
            if current.get("legacy_external"):
                raise WorkflowError("旧版外置条目请使用 workbench external update")
            imported_metadata_only = current.get("origin") == "imported"
            if imported_metadata_only and any((args.title, args.summary, args.path, args.reference, args.refresh_content)):
                raise WorkflowError("导入条目只允许复核主类型、科研用途、标签和复核状态；不能修改包内正文或来源")
            if args.kind and args.legacy_item_type:
                raise WorkflowError("update 不能同时提供 --kind 和弃用的 --type")
            taxonomy = {}
            if args.kind:
                taxonomy["kind"] = args.kind
            elif args.legacy_item_type:
                taxonomy = {**legacy_item_type_taxonomy(args.legacy_item_type), "source_schema_version": 2}
            supplied = {key: value for key, value in {
                "item_id": item_id, **taxonomy, "purposes": args.purposes,
                "title": args.title, "summary": args.summary, "path": args.path,
                "tags": args.tags, "reference": args.reference,
                "review_state": args.review_state, "review_note": args.review_note,
            }.items() if value is not None}
            if args.review_state == "reviewed" and args.review_note is None:
                supplied["review_note"] = ""
            if len(supplied) == 1 and not args.refresh_content:
                raise WorkflowError("update 至少提供一个修改字段或 --refresh-content")
            if not imported_metadata_only:
                path_value = supplied.get("path") or current.get("path")
                content = (ROOT / str(path_value)).resolve()
                try:
                    content.relative_to((ROOT / WORKBENCH_LOCAL).resolve())
                except ValueError as exc:
                    raise WorkflowError("本地条目 Markdown 路径越界") from exc
                if not content.is_file():
                    raise WorkflowError("没有找到本地条目 Markdown")
                supplied["content_sha256"] = sha256_file(content)
            payload = normalize_workbench_item(supplied, current=current)
            event_type = "workbench.entry_upserted"
        else:
            if current is None:
                raise WorkflowError("没有找到该工作台条目")
            payload = {"item_id": item_id, "status": {"pause": "paused", "restore": "active", "retire": "retired"}[command], "note": clean_text(args.note, "说明", 500) or ""}
            event_type = "workbench.entry_status_changed"
        persist([emit(event_type, branch=branch, task_id=record["task_id"], payload=payload)])
        return payload

    if args.workbench_command == "package":
        command = args.package_command
        if command == "inspect":
            package = inspect_package_for_project(ROOT, Path(args.archive))
            current_ids = {item["item_id"] for item in workbench_items()}
            package["item_actions"] = [{
                "item_id": item["original_item_id"],
                "action": "update" if item["item_id"] in current_ids else "add",
            } for item in package["items"]]
            return package if args.format == "json" else _package_text(package)
        if command == "export":
            by_id = {item["item_id"]: item for item in workbench_items()}
            missing = [item_id for item_id in args.item_ids if item_id not in by_id]
            if missing:
                raise WorkflowError("没有找到工作台条目: " + ", ".join(missing))
            return export_package(
                ROOT, [by_id[item_id] for item_id in args.item_ids], package_id=args.package_id,
                name=args.name, version=args.version, author=args.author,
                description=args.description or "", license_name=args.license_name or "",
            )
        record, branch = stable_write_context()
        result = import_package(ROOT, Path(args.archive))
        if result["status"] == "unchanged":
            return {key: value for key, value in result.items() if key != "items"}
        payload = {
            "package_id": result["package_id"], "name": result["name"],
            "version": result["version"], "author": result["author"],
            "source_schema_version": result.get("source_schema_version", 2),
            "description": result.get("description", ""), "license": result.get("license", ""),
            "package_sha256": result["package_sha256"], "destination": result["destination"],
            "items": result["items"],
        }
        try:
            persist([emit("workbench.package_imported", branch=branch, task_id=record["task_id"], payload=payload)])
        except Exception:
            remove_imported_package(ROOT, result)
            raise
        return payload
    raise WorkflowError("未知工作台命令")


def _next_dashboard_task_id() -> str:
    date = datetime.now(resolve_timezone(config()["timezone"])).strftime("%Y%m%d")
    prefix = f"{date}_dashboard_"
    connection = database()
    try:
        used = connection.task_ids_like(prefix)
    finally:
        connection.close()
    for index in range(1, 1000):
        candidate = f"{prefix}{index:03d}"
        if candidate not in used:
            return candidate
    raise WorkflowError("当天的 Dashboard 自动任务编号已用尽")


def dashboard_action_state() -> dict:
    """Return the small mutable-state projection used by Dashboard actions."""
    branch = current_branch()
    classification = classify_branch(branch)
    writer = read_writer_lock(state_dir())
    sidecar = None
    try:
        sidecar = load_active_state(state_dir())
    except ActiveTaskError as exc:
        sidecar = {"phase": "invalid", "error": str(exc)}
    active: dict | None = None
    changed_paths: list[str] = []
    worktree_snapshot: dict = {}
    try:
        record = read_active()
        worktree_snapshot = snapshot()
        changed_paths = changed(record.get("baseline", {}), worktree_snapshot)
        active = {
            "task_id": record["task_id"],
            "started_at": record["started_at"],
            "branch": record.get("git", {}).get("branch"),
            "track": record.get("git", {}).get("track"),
            "kind": record.get("declaration", {}).get("kind"),
            "scope": record.get("declaration", {}).get("scope"),
            "state_updated": bool(record.get("state_updated")),
            "decisions_added": int(record.get("decisions_added") or 0),
            "base_head": record.get("git", {}).get("base_head"),
        }
    except WorkflowError as exc:
        if "没有活动任务" not in str(exc):
            sidecar = sidecar or {"phase": "invalid", "error": str(exc)}
    dirty = git_dirty_paths()
    if not worktree_snapshot:
        head_result = run_git(["rev-parse", "--verify", "HEAD"], check=False)
        worktree_material = {
            "head": head_result.stdout.strip() if head_result.returncode == 0 else None,
            "dirty": dirty,
        }
    else:
        worktree_material = {
            path: worktree_snapshot.get(path)
            for path in changed_paths
        }
    git_state = MaintenanceReadModel(
        database_path(), journal_path(), lambda: branch,
    ).git_state(branch_policy()["default_branch"])
    attempt = get_attempt(branch) if classification["kind"] == "exploration" else None
    installed = installed_project_version()
    hook = run_git(["config", "--local", "--get", "core.hooksPath"], check=False).stdout.strip()
    try:
        health_errors = check_repository()
    except Exception as exc:
        health_errors = [str(exc)]
    connection = database()
    try:
        last_completed = connection.last_completed_task()
    finally:
        connection.close()
    return {
        "application_version": __version__,
        "build_identity": build_identity(),
        "project_version": installed,
        "installed": hook == TRACKED_HOOKS_DIR,
        "frozen": is_frozen(),
        "branch": branch,
        "classification": classification,
        "active_task": active,
        "sidecar": sidecar,
        "writer_lock": writer,
        "changed_paths": changed_paths,
        "dirty_paths": dirty,
        "worktree_signature": hashlib.sha256(canonical_json(worktree_material).encode("utf-8")).hexdigest(),
        "journal_hash": journal_hash(journal_path()),
        "git": git_state,
        "attempt": attempt,
        "health_errors": health_errors,
        "last_completed": last_completed,
    }


def _action_namespace(fields: dict, **defaults) -> argparse.Namespace:
    values = {**defaults, **fields}
    for key, value in list(values.items()):
        if value == "":
            values[key] = None
    return argparse.Namespace(**values)


def dashboard_execute_action(
    action_id: str,
    fields: dict,
    progress: callable,
) -> dict | str | list[dict]:
    """Execute a validated action in-process through the existing workflow functions."""
    progress(ActionProgress(action_id, "dispatch", "正在调用共享工作流服务", 35))
    if action_id == "task.start":
        values = dict(fields)
        requested_task_id = values.pop("_task_id", None)
        return start_task(_action_namespace(
            values,
            task_id=requested_task_id or _next_dashboard_task_id(), out_of_scope=None, topic=None,
            task_size="small", git_commit="auto", track="stable",
        ))
    if action_id == "state.update":
        return state_update(_action_namespace(
            fields, goal=None, judgment=None, breakpoint=None, blocker=None,
            status=None, main_goal_version=None, next=None,
        ))
    if action_id == "decision.add":
        return decision_add(_action_namespace(fields, id=None))
    if action_id == "attempt.update":
        return attempt_update(_action_namespace(
            fields, hypothesis=None, evidence=None, conclusion=None,
            current_step=None, progress=None, next_step=None,
        ))
    if action_id == "task.finish":
        record = read_active()
        values = dict(fields)
        values.pop("writer_stopped", None)
        requested_task_id = values.pop("_task_id", None)
        return finish_task(_action_namespace(
            values,
            task_id=requested_task_id or record["task_id"], evidence=None, commit_message=None,
            attempt_state=None, goal=None, judgment=None, breakpoint=None,
            blocker=None, status=None, main_goal_version=None, next=None,
        ))
    if action_id == "task.recover":
        return task_recover(_action_namespace({}, skip_auto_commit=False, reason=None))
    if action_id == "task.recover_skip_commit":
        return task_recover(_action_namespace(fields, skip_auto_commit=True))
    if action_id == "task.abandon":
        return task_abandon(_action_namespace(fields))
    if action_id == "project.update":
        return project_command(_action_namespace(fields, project_command="update", description=None, big_goal=None))
    if action_id == "stage.start":
        return stage_command(_action_namespace(fields, stage_command="start"))
    if action_id == "stage.update":
        return stage_command(_action_namespace(
            fields, stage_command="update", summary=None, current_step=None,
            next_step=None, blocker=None, evidence=None, status=None,
        ))
    if action_id == "exploration.prepare_pr":
        return prepare_pr()
    if action_id == "exploration.archive":
        return archive_attempt()
    if action_id == "exploration.import":
        return exploration_import(_action_namespace(fields))
    catalog_defaults = {
        "id": None, "kind": None, "title": None, "summary": None, "path": None,
        "entrypoint": None, "source": None, "tag": None, "meta": None, "status": None,
        "clear_path": False, "clear_summary": False, "clear_source": False,
        "clear_tags": False, "clear_metadata": False, "note": None,
        "root": None, "dry_run": False, "name": None, "query": None,
        "related_to": None, "all": False, "add_tag": None, "remove_tag": None,
    }
    catalog_names = {
        "catalog.add": "add", "catalog.update": "update", "catalog.archive": "archive",
        "catalog.restore": "restore", "catalog.link": "link", "catalog.unlink": "unlink",
        "catalog.scan": "scan", "catalog.ingest": "ingest",
        "catalog.bulk_update": "bulk-update", "catalog.migrate": "migrate-layout",
    }
    if action_id in catalog_names:
        values = {**catalog_defaults, **fields, "catalog_command": catalog_names[action_id]}
        if action_id == "catalog.update":
            values["item_id"] = fields["item_id"]
        return catalog_command(argparse.Namespace(**values), catalog_runtime())
    if action_id.startswith("workbench.external."):
        command = action_id.rsplit(".", 1)[1]
        values = {
            "workbench_command": "external",
            "external_command": command,
            "tool_id": fields.get("tool_id"),
            "name": fields.get("name"),
            "kind": fields.get("kind") or None,
            "purpose": fields.get("purpose"),
            "usage_hint": fields.get("usage_hint"),
            "reference": fields.get("reference"),
            "note": fields.get("note"),
        }
        return workbench_command(argparse.Namespace(**values))
    if action_id == "health.check":
        check_repository(raise_on_error=True)
        return {"status": "passed"}
    if action_id == "db.verify":
        return db_command(_action_namespace({}, db_command="verify", delete_legacy=False))
    if action_id == "db.rebuild":
        return db_command(_action_namespace({}, db_command="rebuild", delete_legacy=False))
    if action_id == "db.migrate":
        return db_command(_action_namespace(fields, db_command="migrate", delete_legacy=False))
    if action_id == "install":
        return {"status": install_git_hook(bool(fields.get("force")))}
    if action_id == "update.check":
        return check_latest_update(ROOT)
    if action_id == "update.apply":
        return run_update(ROOT)
    raise WorkflowError(f"未实现的 Dashboard 动作：{action_id}")


def dashboard_action_service() -> WorkflowActionService:
    return build_action_service(
        ROOT, state_provider=dashboard_action_state, executor=dashboard_execute_action,
    )


def cli_shared_action(args: argparse.Namespace) -> tuple[str, dict] | None:
    """Map compatible CLI mutations to the same in-process action executor as Dashboard."""
    action_id: str | None = None
    if args.command == "start":
        action_id = "task.start"
    elif args.command == "end":
        action_id = "task.finish"
    elif args.command == "state":
        action_id = "state.update"
    elif args.command == "decision":
        action_id = "decision.add"
    elif args.command == "attempt" and args.attempt_command == "update":
        action_id = "attempt.update"
    elif args.command == "project" and args.project_command == "update":
        action_id = "project.update"
    elif args.command == "stage" and args.stage_command in {"start", "update"}:
        action_id = f"stage.{args.stage_command}"
    elif args.command == "task":
        if args.task_command == "abandon":
            action_id = "task.abandon"
        elif args.skip_auto_commit:
            action_id = "task.recover_skip_commit"
        else:
            action_id = "task.recover"
    elif args.command == "prepare-pr":
        action_id = "exploration.prepare_pr"
    elif args.command == "archive-attempt":
        action_id = "exploration.archive"
    elif args.command == "exploration":
        action_id = "exploration.import"
    elif args.command == "workbench" and args.workbench_command == "external" and args.external_command not in {"list", "show"}:
        action_id = f"workbench.external.{args.external_command}"
    if action_id is None:
        return None
    names = {field.name for field in action_spec(action_id).fields}
    fields = {
        name: getattr(args, name)
        for name in names
        if hasattr(args, name) and getattr(args, name) is not None
    }
    if args.command in {"start", "end"}:
        fields["_task_id"] = args.task_id
    if action_id == "task.finish":
        fields["writer_stopped"] = True
    return action_id, fields
