"""Project maintenance command implementation backed by SQLite and JSONL events."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from . import __version__
from .catalog import (
    CatalogError,
    CatalogRuntime,
    catalog_command,
    configure_catalog_parser,
)
from .dashboard import DashboardDataProvider, DashboardError, launch_dashboard
from .read_model import (
    MaintenanceReadModel,
    ReadModelError,
    action_overview_text,
    git_state_summary,
)
from .store import (
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
from .project_manager import (
    HOOK_TEMPLATE,
    INSTALLATION_PATH,
    ProjectManagerError,
    apply_project_update,
    initialize_project,
    installation_record,
    write_json,
)
from .updater import UpdateError, run_update, version_report


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_MARKER = Path(".codex/project-maintenance-workflow.json")
ROOT = Path.cwd().resolve()
MARKER = ROOT / PROJECT_MARKER
TASK_ID_RE = re.compile(r"^\d{8}_[a-z0-9][a-z0-9_-]*_\d{3}$")
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
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
ATTEMPT_STATES = ("active", "validated", "negative", "inconclusive", "paused")
STATE_ARGUMENTS = (
    "goal", "judgment", "breakpoint", "blocker", "status", "main_goal_version",
)
DAILY_HELP = """\
usage: project-hooks [-h] [--help-all] [--project PATH] {context,start,end,dashboard} ...

项目内维护入口。无参数运行时显示行动概览。

日常命令:
  context     读取完整动态上下文
  start       开始任务生命周期
  end         完成任务生命周期
  dashboard   打开只读管理窗口

首次使用:
  .\\project-hooks.exe init .
  .\\project-hooks.exe check

软件升级:
  .\\project-hooks.exe update

使用 --help-all 查看全部高级命令；使用 <命令> --help 查看参数。
"""
FULL_HELP = """\
usage: project-hooks [-h] [--help-all] [--project PATH] <command> ...

日常命令:
  context, start, end, dashboard

仓库维护:
  init, install, update, version, check, status, branch-status

状态与记录:
  state, history, decisions, decision, explorations, catalog

探索流程:
  attempt, exploration, prepare-pr, archive-attempt

数据维护:
  db

内部 Git Hook 命令不显示；所有既有公开命令保持兼容。
使用 <命令> --help 查看详细参数。
"""


class WorkflowError(RuntimeError):
    pass


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
    data.setdefault("context_command", ".\\project-hooks.exe context --format markdown")
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
                        "history", "decisions", "explorations", "dashboard", "update"}:
        return True
    if args.command == "attempt" and args.attempt_command == "show":
        return True
    if args.command == "db" and args.db_command in {"status", "verify"}:
        return True
    if args.command == "catalog" and args.catalog_command in {"list", "show", "context"}:
        return True
    return False


def assert_project_version_compatible(args: argparse.Namespace) -> None:
    installed = installed_project_version()
    if (installed and installed != __version__ and args.command != "_apply-update"
            and not command_is_read_only(args)):
        raise WorkflowError(
            f"项目模板版本为 {installed}，当前 EXE 为 {__version__}；"
            "写操作前请运行 `.\\project-hooks.exe update`"
        )


def state_dir() -> Path:
    return ROOT / config()["state_dir"]


def database_path() -> Path:
    return ROOT / config()["maintenance_store"]["database"]


def journal_path() -> Path:
    return ROOT / config()["maintenance_store"]["journal"]


def database() -> sqlite3.Connection:
    try:
        return ensure_database(database_path(), journal_path())
    except (sqlite3.DatabaseError, StoreError) as exc:
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
    except (sqlite3.DatabaseError, StoreError) as exc:
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


def run_git(args: list[str], *, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                          capture_output=True, timeout=60, env=env, check=check)


def is_git_repo() -> bool:
    try:
        return run_git(["rev-parse", "--is-inside-work-tree"]).stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def git_dirty_paths() -> list[str]:
    if not is_git_repo():
        return []
    entries = run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all"]).stdout.split("\0")
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


def current_branch() -> str:
    if not is_git_repo():
        raise WorkflowError("当前目录不是 Git 仓库")
    result = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise WorkflowError("当前处于 detached HEAD，不能运行分支维护生命周期")
    return result.stdout.strip()


def branch_policy() -> dict:
    policy = config()["branch_policy"]
    if policy != DEFAULT_BRANCH_POLICY:
        raise WorkflowError("branch_policy 与项目维护协议不一致")
    return policy


def classify_branch(branch: str) -> dict:
    policy = branch_policy()
    if branch == policy["default_branch"]:
        return {"kind": "stable", "track": "stable", "topic": None}
    parts = branch.split("/")
    if len(parts) == 2 and parts[0] in policy["exploration_types"] and TOPIC_RE.fullmatch(parts[1]):
        return {"kind": "exploration", "track": parts[0], "topic": parts[1]}
    if len(parts) == 3 and parts[0] == policy["archive_prefix"] and parts[1] in policy["exploration_types"] and TOPIC_RE.fullmatch(parts[2]):
        return {"kind": "archive", "track": parts[1], "topic": parts[2]}
    return {"kind": "unsupported", "track": None, "topic": None}


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


def check_repository(*, raise_on_error: bool = False) -> list[str]:
    cfg = config()
    errors: list[str] = []
    if cfg.get("version") != 2:
        errors.append("项目维护配置必须为 version=2")
    if cfg.get("core_read_order") != STATIC_READ_ORDER:
        errors.append("core_read_order 与 SQLite 工作流不一致")
    store = cfg.get("maintenance_store")
    if not isinstance(store, dict) or any(store.get(key) != value for key, value in DEFAULT_STORE.items()):
        errors.append("maintenance_store 与 SQLite 工作流不一致")
    installed = installed_project_version()
    if installed and installed != __version__:
        errors.append(f"项目模板版本 {installed} 与当前 EXE {__version__} 不一致；请运行 .\\project-hooks.exe update")
    try:
        branch_policy()
    except WorkflowError as exc:
        errors.append(str(exc))
    for rel in [*STATIC_READ_ORDER, "maintenance/README.md", "maintenance/events.jsonl",
                INSTALLATION_PATH.as_posix(), f"{TRACKED_HOOKS_DIR}/pre-commit"]:
        if not (ROOT / rel).is_file():
            errors.append(f"缺少维护文件: {rel}")
    for rel in ("maintenance/current_task.md", "maintenance/change_archive.md", "maintenance/decision_log.md", "maintenance/exploration_log.md"):
        if (ROOT / rel).exists():
            errors.append(f"旧动态维护文件仍存在: {rel}")
    errors.extend(markdown_link_errors())
    try:
        connection = database()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        connection.close()
        if integrity != "ok":
            errors.append(f"SQLite integrity_check: {integrity}")
    except (WorkflowError, sqlite3.DatabaseError) as exc:
        errors.append(f"维护数据库检查失败: {exc}")
    if errors and raise_on_error:
        raise WorkflowError("项目维护检查失败:\n- " + "\n- ".join(errors))
    return errors


def active_row(connection: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    owned = connection is None
    connection = connection or database()
    row = connection.execute("SELECT * FROM active_tasks ORDER BY started_at DESC LIMIT 1").fetchone()
    if owned:
        connection.close()
    return row


def read_active() -> dict:
    connection = database()
    row = active_row(connection)
    connection.close()
    if row is None:
        raise WorkflowError("没有活动任务")
    record = json.loads(row["record_json"])
    record["state_updated"] = bool(row["state_updated"])
    record["decisions_added"] = int(row["decisions_added"])
    return record


def save_active(record: dict, *, state_updated: int = 0, decisions_added: int = 0) -> None:
    connection = database()
    with connection:
        connection.execute(
            "INSERT INTO active_tasks VALUES (?, ?, ?, ?, ?, ?)",
            (record["task_id"], record["started_at"], record["git"]["branch"], canonical_json(record), state_updated, decisions_added),
        )
    connection.close()


def update_active_flags(*, state_updated: bool = False, decision_added: bool = False) -> None:
    record = read_active()
    connection = database()
    with connection:
        if state_updated:
            connection.execute("UPDATE active_tasks SET state_updated=1 WHERE task_id=?", (record["task_id"],))
        if decision_added:
            connection.execute("UPDATE active_tasks SET decisions_added=decisions_added+1 WHERE task_id=?", (record["task_id"],))
    connection.close()


def delete_active(task_id: str) -> None:
    connection = database()
    with connection:
        connection.execute("DELETE FROM active_tasks WHERE task_id=?", (task_id,))
    connection.close()


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


def start_task(args: argparse.Namespace) -> dict:
    check_repository(raise_on_error=True)
    if active_row() is not None:
        raise WorkflowError("已有活动任务，请先执行 status/end")
    if not TASK_ID_RE.fullmatch(args.task_id):
        raise WorkflowError("task_id 必须为 YYYYMMDD_<slug>_NNN")
    original_branch = current_branch()
    classification = classify_branch(original_branch)
    requested_track, requested_topic = args.track, args.topic
    created_branch = False
    base_head = run_git(["rev-parse", "HEAD"]).stdout.strip()
    if classification["kind"] == "stable":
        requested_track = requested_track or "stable"
        if requested_track == "stable":
            if requested_topic:
                raise WorkflowError("stable 任务不能使用 --topic")
        else:
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
        "git": {"is_repo": True, "dirty_paths": git_dirty_paths(), "head": run_git(["rev-parse", "HEAD"]).stdout.strip(),
                "branch": branch, "base_branch": branch_policy()["default_branch"], "base_head": base_head,
                "track": classification["track"], "topic": classification["topic"]},
    }
    events = [emit("task.started", branch=branch, task_id=args.task_id, payload=record["declaration"])]
    if classification["kind"] == "exploration" and created_branch:
        events.append(emit("attempt.started", branch=branch, task_id=args.task_id, payload={
            "attempt_id": args.task_id, "track": classification["track"], "topic": classification["topic"],
            "base_commit": base_head, "goal": args.scope, "acceptance": args.acceptance,
        }))
    try:
        save_active(record)
        persist(events)
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


def project_state_payload(record: dict, args: argparse.Namespace, branch: str) -> dict:
    connection = database()
    current = connection.execute("SELECT * FROM project_state WHERE branch=?", (branch,)).fetchone()
    if current is None and branch != "main":
        current = connection.execute("SELECT * FROM project_state WHERE branch='main'").fetchone()
    base = dict(current) if current else {}
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
    if args.hypothesis is None and args.conclusion is None and not args.evidence:
        raise WorkflowError("attempt update 至少提供一个更新字段")
    payload = {"attempt_id": attempt["attempt_id"], "hypothesis": args.hypothesis,
               "conclusion": args.conclusion, "evidence": args.evidence or []}
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
        run_git(["read-tree", "HEAD"], env=env)
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


def finish_task(args: argparse.Namespace) -> dict:
    record = read_active()
    if args.task_id != record["task_id"]:
        raise WorkflowError(f"活动任务是 {record['task_id']}，不是 {args.task_id}")
    branch = assert_active_branch(record)
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
    persist(events)
    if final_state_requested:
        update_active_flags(state_updated=True)
        record["state_updated"] = True
    paths = changed(record["baseline"], snapshot())
    report = {"task_id": args.task_id, "started_at": record["started_at"], "finished_at": timestamp(),
              "result": args.result, "route": args.route, "methods_action": args.methods_action,
              "main_goal": args.main_goal, "note": args.note, "attempt_state": args.attempt_state,
              "changed_paths": paths}
    report["git"] = auto_commit(record, paths, args.result, args.commit_message)
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
    found = connection.execute(
        "SELECT 1 FROM task_archive WHERE branch=? AND json_extract(payload_json, '$.attempt_state')=? LIMIT 1",
        (branch, attempt_state),
    ).fetchone() is not None
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
    lines = ["# 动态维护上下文", "", f"- 当前分支：`{data['branch']}`",
             f"- 当前状态：{state.get('status', '未设置')}", f"- 主目标版本：{state.get('main_goal_version', '未设置')}",
             "", "## 当前主目标", "", state.get("goal") or "未设置", "", "## 当前判决", "",
             state.get("judgment") or "未设置", "", "## 工作断点", "", state.get("breakpoint") or "未设置",
             "", "## 真实断点", "", git_state_summary(data.get("git_state") or {}),
             "", "## 接下来三步", ""]
    for index, item in enumerate(state.get("next_steps", []), 1):
        lines.append(f"{index}. {item}")
    if not state.get("next_steps"):
        lines.append("无。")
    lines += ["", "## 当前阻塞", "", state.get("blocker") or "无。", "", "## 最近交接", ""]
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
        used = {
            row[0] for row in connection.execute(
                "SELECT DISTINCT task_id FROM events WHERE task_id LIKE ? AND task_id IS NOT NULL",
                (prefix + "%",),
            ).fetchall()
        }
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
    payload = {"branch": args.archive_branch, "goal": start["payload"]["goal"], "result": state["payload"]["state"],
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
    counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("events", "task_archive", "decisions", "handoffs", "project_state", "explorations")}
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
        connection = rebuild(database_path(), journal_path())
        count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        connection.close()
        return {"status": "rebuilt", "events": count}
    if args.db_command == "migrate":
        return migrate_legacy(args.delete_legacy)
    connection = database()
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    stored = connection.execute("SELECT value FROM meta WHERE key='journal_hash'").fetchone()
    connection.close()
    output = {"status": "passed" if integrity == "ok" else integrity, "schema_version": SCHEMA_VERSION,
              "events": event_count, "journal_hash": journal_hash(journal_path()),
              "database_matches_journal": bool(stored and stored[0] == journal_hash(journal_path()))}
    if args.db_command == "verify" and (integrity != "ok" or not output["database_matches_journal"]):
        raise WorkflowError("数据库完整性验证失败")
    return output


def non_negative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是数字") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("必须大于或等于 0")
    return number


def add_state_arguments(parser: argparse.ArgumentParser) -> None:
    for name in ("goal", "judgment", "breakpoint", "blocker", "status", "main-goal-version"):
        parser.add_argument(f"--{name}")
    parser.add_argument("--next", action="append")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project-hooks", add_help=False)
    parser.add_argument("-h", "--help", action="store_true", dest="basic_help")
    parser.add_argument("--help-all", action="store_true")
    parser.add_argument("--project", type=Path)
    sub = parser.add_subparsers(dest="command")
    init = sub.add_parser("init")
    init.add_argument("path", nargs="?", type=Path, default=Path("."))
    update = sub.add_parser("update")
    update.add_argument("--check", action="store_true")
    update.add_argument("--to")
    update.add_argument("--manifest-url", help=argparse.SUPPRESS)
    sub.add_parser("version")
    apply_update = sub.add_parser("_apply-update", help=argparse.SUPPRESS)
    apply_update.add_argument("--target-version", required=True)
    start = sub.add_parser("start")
    start.add_argument("task_id")
    start.add_argument("--kind", required=True, choices=("code", "docs", "review", "governance", "analysis", "design", "test", "other"))
    start.add_argument("--scope", required=True)
    start.add_argument("--out-of-scope")
    start.add_argument("--acceptance", action="append", required=True)
    start.add_argument("--task-size", choices=("small", "large"), default="small")
    start.add_argument("--git-commit", choices=("auto", "always", "never"), default="auto")
    start.add_argument("--track", choices=("stable", "research", "experiment", "sandbox"))
    start.add_argument("--topic")
    sub.add_parser("status")
    sub.add_parser("branch-status")
    sub.add_parser("prepare-pr")
    sub.add_parser("archive-attempt")
    sub.add_parser("check")
    install = sub.add_parser("install")
    install.add_argument("--force", action="store_true")
    sub.add_parser("pre-commit", help=argparse.SUPPRESS)
    context = sub.add_parser("context")
    context.add_argument("--format", choices=("markdown", "json"), default="markdown")
    dashboard = sub.add_parser("dashboard", help="打开只读 SQLite 维护数据窗口")
    dashboard.add_argument("--refresh-seconds", type=non_negative_float, default=3.0)
    dashboard.add_argument("--branch")
    state = sub.add_parser("state")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    state_update_parser = state_sub.add_parser("update")
    add_state_arguments(state_update_parser)
    decision = sub.add_parser("decision")
    decision_sub = decision.add_subparsers(dest="decision_command", required=True)
    decision_add_parser = decision_sub.add_parser("add")
    decision_add_parser.add_argument("--id")
    decision_add_parser.add_argument("--decision", required=True)
    decision_add_parser.add_argument("--alternatives", required=True)
    decision_add_parser.add_argument("--basis", required=True)
    decision_add_parser.add_argument("--reopen-condition", required=True)
    attempt = sub.add_parser("attempt")
    attempt_sub = attempt.add_subparsers(dest="attempt_command", required=True)
    attempt_sub.add_parser("show")
    attempt_update_parser = attempt_sub.add_parser("update")
    attempt_update_parser.add_argument("--hypothesis")
    attempt_update_parser.add_argument("--evidence", action="append")
    attempt_update_parser.add_argument("--conclusion")
    for name in ("history", "decisions", "explorations"):
        query = sub.add_parser(name)
        query.add_argument("--limit", type=int, default=20)
        query.add_argument("--format", choices=("markdown", "json"), default="markdown")
    exploration = sub.add_parser("exploration")
    exploration_sub = exploration.add_subparsers(dest="exploration_command", required=True)
    exploration_import_parser = exploration_sub.add_parser("import")
    exploration_import_parser.add_argument("archive_branch")
    configure_catalog_parser(sub)
    db = sub.add_parser("db")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("status")
    db_sub.add_parser("verify")
    db_sub.add_parser("rebuild")
    migrate = db_sub.add_parser("migrate")
    migrate.add_argument("--delete-legacy", action="store_true")
    end = sub.add_parser("end")
    end.add_argument("task_id")
    end.add_argument("--result", required=True, choices=("completed", "blocked", "failed", "indeterminate"))
    end.add_argument("--route", required=True, choices=("changed", "unchanged"))
    end.add_argument("--methods-action", required=True, choices=("updated", "reviewed-no-change"))
    end.add_argument("--main-goal", required=True, choices=("changed", "unchanged"))
    end.add_argument("--note", required=True)
    end.add_argument("--evidence", action="append")
    end.add_argument("--commit-message")
    end.add_argument("--attempt-state", choices=ATTEMPT_STATES)
    add_state_arguments(end)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root: Path | None
        if args.command == "init":
            root = (args.project or args.path).resolve()
        else:
            root = args.project.resolve() if args.project else discover_project_root()
        if root is not None:
            set_project_root(root)
            assert_project_version_compatible(args)
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
                "当前目录不在 project-hooks 科研项目中；请先运行 `.\\project-hooks.exe init .`，"
                "或使用 `--project <path>`"
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
                    "项目维护尚未安装。请先运行 `.\\project-hooks.exe install`，"
                    "再运行 `.\\project-hooks.exe check`。"
                )
            output = action_overview_text(context_data())
        elif args.command == "start": output = start_task(args)
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
        elif args.command == "dashboard":
            provider = DashboardDataProvider(read_model(), classify_branch, args.branch)
            launch_dashboard(provider, args.refresh_seconds)
            output = {"status": "closed"}
        elif args.command == "state": output = state_update(args)
        elif args.command == "decision": output = decision_add(args)
        elif args.command == "attempt": output = get_attempt(current_branch()) if args.attempt_command == "show" else attempt_update(args)
        elif args.command in {"history", "decisions", "explorations"}:
            output = render_records(query_output(args.command, args.limit), args.format)
        elif args.command == "exploration": output = exploration_import(args)
        elif args.command == "catalog": output = catalog_command(args, catalog_runtime())
        elif args.command == "db": output = db_command(args)
        else: output = finish_task(args)
        if isinstance(output, str): print(output, end="")
        else: print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError, subprocess.SubprocessError,
            CatalogError, DashboardError, ProjectManagerError, ReadModelError, StoreError,
            UpdateError, WorkflowError) as exc:
        print(str(exc), file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
