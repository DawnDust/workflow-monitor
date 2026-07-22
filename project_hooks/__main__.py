"""Deterministic project-local maintenance lifecycle and Git gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / ".codex/project-maintenance-workflow.json"
TASK_ID_RE = re.compile(r"^\d{8}_[a-z0-9][a-z0-9_-]*_\d{3}$")
MANAGED_HOOK_MARKER = "# project-maintenance-hooks managed"
TRACKED_HOOKS_DIR = ".githooks"
DEFAULT_READ_ORDER = [
    "maintenance/current_task.md",
    "maintenance/project_context.md",
    "maintenance/change_archive.md",
    "maintenance/decision_log.md",
    "maintenance/workflow_spec.md",
]


class WorkflowError(RuntimeError):
    pass


def config() -> dict:
    if not MARKER.is_file():
        raise WorkflowError("缺少 .codex/project-maintenance-workflow.json")
    data = json.loads(MARKER.read_text(encoding="utf-8"))
    data.setdefault("timezone", "Asia/Shanghai")
    data.setdefault("state_dir", ".project_hooks")
    data.setdefault("core_read_order", DEFAULT_READ_ORDER)
    data.setdefault("git_auto_commit", {"enabled": True, "eligible_task_sizes": ["large"]})
    return data


def state_dir() -> Path:
    return ROOT / config()["state_dir"]


def active_path() -> Path:
    return state_dir() / "active.json"


def timestamp() -> str:
    return datetime.now(ZoneInfo(config()["timezone"])).strftime("%Y-%m-%d %H:%M:%S（Asia/Shanghai）")


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
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, timeout=60,
        env=env, check=check,
    )


def is_git_repo() -> bool:
    try:
        return run_git(["rev-parse", "--is-inside-work-tree"]).stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def git_dirty_paths() -> list[str]:
    if not is_git_repo():
        return []
    result = run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    entries = result.stdout.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        path = entry[3:]
        if "R" in status or "C" in status:
            if index < len(entries) and entries[index]:
                paths.add(entries[index].replace("\\", "/"))
                index += 1
        paths.add(path.replace("\\", "/"))
    return sorted(paths)


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
    errors = []
    if not cfg.get("enabled", True):
        errors.append("项目维护工作流被禁用")
    if cfg.get("core_read_order") != DEFAULT_READ_ORDER:
        errors.append("core_read_order 与 v1 项目维护协议不一致")
    for rel in [
        *DEFAULT_READ_ORDER,
        "maintenance/README.md",
        "project_hooks/__main__.py",
        f"{TRACKED_HOOKS_DIR}/pre-commit",
    ]:
        if not (ROOT / rel).is_file():
            errors.append(f"缺少维护文件: {rel}")
    current = ROOT / "maintenance/current_task.md"
    if current.is_file():
        text = current.read_text(encoding="utf-8")
        for heading in ("当前主目标", "当前判决", "真实断点", "接下来三步", "当前阻塞", "最近交接"):
            if heading not in text:
                errors.append(f"当前任务缺少栏目: {heading}")
        rows = sum(1 for line in text.split("最近交接", 1)[-1].splitlines() if line.startswith("| ")) - 2
        if rows > 5:
            errors.append(f"最近交接有 {rows} 项，最多保留 5 项")
    errors.extend(markdown_link_errors())
    if errors and raise_on_error:
        raise WorkflowError("项目维护检查失败:\n- " + "\n- ".join(errors))
    return errors


def read_active() -> dict:
    path = active_path()
    if not path.is_file():
        raise WorkflowError("没有活动任务")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def start_task(args: argparse.Namespace) -> dict:
    check_repository(raise_on_error=True)
    if active_path().exists():
        raise WorkflowError("已有活动任务，请先执行 status/end")
    if not TASK_ID_RE.fullmatch(args.task_id):
        raise WorkflowError("task_id 必须为 YYYYMMDD_<slug>_NNN")
    if not args.scope or not args.acceptance:
        raise WorkflowError("start 必须声明 --scope 与至少一个 --acceptance")
    record = {
        "version": 1,
        "task_id": args.task_id,
        "started_at": timestamp(),
        "declaration": {
            "kind": args.kind,
            "scope": args.scope,
            "out_of_scope": args.out_of_scope,
            "acceptance": args.acceptance,
            "task_size": args.task_size,
            "git_commit": args.git_commit,
        },
        "baseline": snapshot(),
        "git": {
            "is_repo": is_git_repo(),
            "dirty_paths": git_dirty_paths(),
            "head": run_git(["rev-parse", "HEAD"]).stdout.strip() if is_git_repo() else None,
        },
    }
    write_json(active_path(), record)
    return {"active": str(active_path()), "task_id": args.task_id, "started_at": record["started_at"]}


def task_status() -> dict:
    if not active_path().is_file():
        return {"active": False, "checks": check_repository()}
    record = read_active()
    paths = changed(record["baseline"], snapshot())
    required = ["maintenance/change_archive.md", "maintenance/current_task.md"]
    missing = [path for path in required if path not in paths]
    return {
        "active": True,
        "task_id": record["task_id"],
        "started_at": record["started_at"],
        "changed_paths": paths,
        "required_updates": required,
        "missing_updates": missing,
        "checks": check_repository(),
    }


def pre_commit_check() -> None:
    check_repository(raise_on_error=True)
    if active_path().is_file():
        status = task_status()
        if status["missing_updates"]:
            raise WorkflowError("活动任务尚未完成归档: " + ", ".join(status["missing_updates"]))


def install_git_hook(force: bool = False) -> str:
    if not is_git_repo():
        return "非 Git 仓库，已跳过 pre-commit 安装"
    configured = run_git(["config", "--local", "--get", "core.hooksPath"], check=False)
    old_path = configured.stdout.strip()
    if old_path and old_path != TRACKED_HOOKS_DIR and not force:
        raise WorkflowError(
            f"本仓库已有 core.hooksPath={old_path}；使用 --force 前请先人工合并"
        )
    hook = ROOT / TRACKED_HOOKS_DIR / "pre-commit"
    content = f"#!/bin/sh\n{MANAGED_HOOK_MARKER}\npython -m project_hooks pre-commit\n"
    hook_is_current = False
    if hook.exists():
        old = hook.read_text(encoding="utf-8", errors="replace")
        if old == content:
            hook_is_current = True
        elif MANAGED_HOOK_MARKER not in old and not force:
            raise WorkflowError("已有非本工作流管理的 pre-commit；使用 --force 前请先人工合并")
    if not hook_is_current:
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(content, encoding="utf-8", newline="\n")
        try:
            hook.chmod(0o755)
        except OSError:
            pass
    run_git(["config", "--local", "core.hooksPath", TRACKED_HOOKS_DIR])
    return f"已启用仓库内 pre-commit: {hook}（core.hooksPath={TRACKED_HOOKS_DIR}）"


def auto_commit(record: dict, paths: list[str], result: str, message: str | None) -> dict:
    declaration = record["declaration"]
    cfg = config()["git_auto_commit"]
    mode = declaration["git_commit"]
    eligible = cfg.get("enabled", True) and declaration["task_size"] in cfg.get("eligible_task_sizes", ["large"])
    if mode == "never" or not eligible or not record["git"]["is_repo"]:
        return {"status": "not-requested"}
    overlap = sorted(set(paths) & set(record["git"]["dirty_paths"]))
    if overlap:
        detail = "任务修改了启动前已脏文件: " + ", ".join(overlap)
        if mode == "always":
            raise WorkflowError(detail)
        return {"status": "skipped", "reason": detail}
    commit_paths = [p for p in paths if not p.startswith(".project_hooks/")]
    if not commit_paths:
        return {"status": "skipped", "reason": "没有任务归属文件"}
    git_dir_text = run_git(["rev-parse", "--git-dir"]).stdout.strip()
    git_dir = Path(git_dir_text)
    if not git_dir.is_absolute():
        git_dir = (ROOT / git_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="project-hooks-index-"))
    temp_index = temp_dir / "index"
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(temp_index)
    try:
        run_git(["read-tree", "HEAD"], env=env)
        run_git(["add", "-A", "--", *commit_paths], env=env)
        staged = run_git(["diff", "--cached", "--name-only"], env=env).stdout.splitlines()
        if not staged:
            return {"status": "skipped", "reason": "任务路径没有可提交差异"}
        commit_message = message or f"maint({declaration['kind']}): {record['task_id']}"
        completed = run_git(["commit", "-m", commit_message], env=env, check=False)
        if completed.returncode != 0:
            raise WorkflowError("自动提交失败: " + (completed.stderr or completed.stdout).strip())
        return {
            "status": "committed",
            "commit": run_git(["rev-parse", "HEAD"]).stdout.strip(),
            "message": commit_message,
            "paths": staged,
            "result": result,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def finish_task(args: argparse.Namespace) -> dict:
    record = read_active()
    if args.task_id != record["task_id"]:
        raise WorkflowError(f"活动任务是 {record['task_id']}，不是 {args.task_id}")
    check_repository(raise_on_error=True)
    paths = changed(record["baseline"], snapshot())
    for required in ("maintenance/change_archive.md", "maintenance/current_task.md"):
        if required not in paths:
            raise WorkflowError(f"结束前必须实际更新 {required}")
    if args.route == "changed" and "maintenance/decision_log.md" not in paths:
        raise WorkflowError("路线发生变化时必须更新 maintenance/decision_log.md")
    finished_at = timestamp()
    report = {
        "task_id": args.task_id,
        "started_at": record["started_at"],
        "finished_at": finished_at,
        "result": args.result,
        "route": args.route,
        "methods_action": args.methods_action,
        "main_goal": args.main_goal,
        "note": args.note,
        "changed_paths": paths,
    }
    report["git"] = auto_commit(record, paths, args.result, args.commit_message)
    history = state_dir() / "history" / f"{args.task_id}.json"
    write_json(history, {"start": record, "end": report})
    active_path().unlink()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project-hooks")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="记录基线并打开维护任务")
    start.add_argument("task_id")
    start.add_argument("--kind", required=True, choices=("code", "docs", "review", "governance", "analysis", "design", "test", "other"))
    start.add_argument("--scope", required=True)
    start.add_argument("--out-of-scope")
    start.add_argument("--acceptance", action="append", required=True)
    start.add_argument("--task-size", choices=("small", "large"), default="small")
    start.add_argument("--git-commit", choices=("auto", "always", "never"), default="auto")
    sub.add_parser("status", help="显示活动任务和未满足义务")
    sub.add_parser("check", help="检查项目维护结构")
    install = sub.add_parser("install", help="安装仓库本地 pre-commit")
    install.add_argument("--force", action="store_true")
    sub.add_parser("pre-commit", help=argparse.SUPPRESS)
    end = sub.add_parser("end", help="验证归档并关闭任务")
    end.add_argument("task_id")
    end.add_argument("--result", required=True, choices=("completed", "blocked", "failed", "indeterminate"))
    end.add_argument("--route", required=True, choices=("changed", "unchanged"))
    end.add_argument("--methods-action", required=True, choices=("updated", "reviewed-no-change"))
    end.add_argument("--main-goal", required=True, choices=("changed", "unchanged"))
    end.add_argument("--note", required=True)
    end.add_argument("--commit-message")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "start":
            output = start_task(args)
        elif args.command == "status":
            output = task_status()
        elif args.command == "check":
            check_repository(raise_on_error=True)
            output = {"status": "passed"}
        elif args.command == "install":
            output = {"status": install_git_hook(args.force)}
        elif args.command == "pre-commit":
            pre_commit_check()
            output = {"status": "passed"}
        else:
            output = finish_task(args)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, WorkflowError) as exc:
        print(str(exc), file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
