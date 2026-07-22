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
    "maintenance/branch_workflow.md",
    "maintenance/workflow_spec.md",
]
DEFAULT_BRANCH_POLICY = {
    "default_branch": "main",
    "exploration_types": ["research", "experiment", "sandbox"],
    "archive_prefix": "archive",
    "success_integration": "squash_pr",
    "require_user_merge_confirmation": True,
}
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ATTEMPT_STATES = ("active", "validated", "negative", "inconclusive", "paused")


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
    data.setdefault("branch_policy", DEFAULT_BRANCH_POLICY)
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


def current_branch() -> str:
    if not is_git_repo():
        raise WorkflowError("当前目录不是 Git 仓库")
    result = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    branch = result.stdout.strip()
    if result.returncode != 0 or not branch:
        raise WorkflowError("当前处于 detached HEAD，不能运行分支维护生命周期")
    return branch


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
    if (
        len(parts) == 3
        and parts[0] == policy["archive_prefix"]
        and parts[1] in policy["exploration_types"]
        and TOPIC_RE.fullmatch(parts[2])
    ):
        return {"kind": "archive", "track": parts[1], "topic": parts[2]}
    return {"kind": "unsupported", "track": None, "topic": None}


def ref_exists(ref: str) -> bool:
    return run_git(["show-ref", "--verify", "--quiet", ref], check=False).returncode == 0


def assert_main_matches_origin() -> None:
    default = branch_policy()["default_branch"]
    remote_ref = f"refs/remotes/origin/{default}"
    if ref_exists(remote_ref):
        local_head = run_git(["rev-parse", default]).stdout.strip()
        remote_head = run_git(["rev-parse", f"origin/{default}"]).stdout.strip()
        if local_head != remote_head:
            raise WorkflowError(f"本地 {default} 与已知 origin/{default} 不一致；请先同步后重试")


def assert_active_branch(record: dict) -> str:
    branch = current_branch()
    expected = record.get("git", {}).get("branch")
    if expected and branch != expected:
        raise WorkflowError(f"任务启动于分支 {expected}，当前分支为 {branch}；请切回原分支")
    return branch


def attempt_files() -> list[Path]:
    folder = ROOT / "maintenance/attempts"
    return sorted(path for path in folder.glob("*.md") if path.name != "README.md") if folder.is_dir() else []


def attempt_for_branch(branch: str) -> Path | None:
    marker = f"- Branch: `{branch}`"
    matches = [path for path in attempt_files() if marker in path.read_text(encoding="utf-8")]
    if len(matches) > 1:
        raise WorkflowError(f"分支 {branch} 对应多个尝试记录")
    return matches[0] if matches else None


def attempt_state(path: Path) -> str | None:
    match = re.search(r"^- State: `([^`]+)`\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def update_attempt_state(path: Path, state: str, track: str, topic: str) -> None:
    text = path.read_text(encoding="utf-8")
    if not re.search(r"^- State: `[^`]+`\s*$", text, re.MULTILINE):
        raise WorkflowError(f"尝试记录缺少处置状态: {path.relative_to(ROOT).as_posix()}")
    text = re.sub(r"^- State: `[^`]+`\s*$", f"- State: `{state}`", text, count=1, flags=re.MULTILINE)
    archive = f"{branch_policy()['archive_prefix']}/{track}/{topic}" if state in {"negative", "inconclusive", "paused"} else "pending"
    if re.search(r"^- Archive branch: `[^`]+`\s*$", text, re.MULTILINE):
        text = re.sub(r"^- Archive branch: `[^`]+`\s*$", f"- Archive branch: `{archive}`", text, count=1, flags=re.MULTILINE)
    path.write_text(text, encoding="utf-8")


def section_is_filled(text: str, heading: str) -> bool:
    match = re.search(rf"^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if not match:
        return False
    value = match.group(1).strip()
    return bool(value and value not in {"- Pending", "Pending", "待填写"})


def validate_attempt_evidence(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    missing = [heading for heading in ("Hypothesis", "Evidence", "Conclusion") if not section_is_filled(text, heading)]
    if missing:
        raise WorkflowError("validated 尝试必须填写: " + ", ".join(missing))


def completed_attempt_receipt(branch: str, attempt_rel: str, state: str) -> dict | None:
    folder = state_dir() / "history"
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.json"), reverse=True):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        start = receipt.get("start", {})
        end = receipt.get("end", {})
        git = start.get("git", {})
        if git.get("branch") == branch and git.get("attempt_path") == attempt_rel and end.get("attempt_state") == state:
            return receipt
    return None


def create_attempt(path: Path, *, task_id: str, branch: str, track: str, base_head: str, scope: str, acceptance: list[str]) -> None:
    criteria = "\n".join(f"- {item}" for item in acceptance)
    content = f"""# Exploration attempt: {task_id}

- Task ID: `{task_id}`
- Branch: `{branch}`
- Type: `{track}`
- Base commit: `{base_head}`

## Goal

{scope}

## Hypothesis

- Pending

## Acceptance criteria

{criteria}

## Evidence

- Pending

## Conclusion

- Pending

## Disposition

- State: `active`
- PR: `pending`
- Archive branch: `pending`
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
    try:
        branch_policy()
    except WorkflowError as exc:
        errors.append(str(exc))
    for rel in [
        *DEFAULT_READ_ORDER,
        "maintenance/README.md",
        "maintenance/exploration_log.md",
        "maintenance/attempts/README.md",
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
    if not is_git_repo():
        raise WorkflowError("项目维护生命周期要求 Git 仓库")
    original_branch = current_branch()
    classification = classify_branch(original_branch)
    policy = branch_policy()
    requested_track = args.track
    requested_topic = args.topic
    created_branch = False
    attempt_path: Path | None = None
    base_branch = policy["default_branch"]
    base_head = run_git(["rev-parse", "HEAD"]).stdout.strip()

    if classification["kind"] == "stable":
        requested_track = requested_track or "stable"
        if requested_track == "stable":
            if requested_topic:
                raise WorkflowError("stable 任务不能使用 --topic")
        else:
            if requested_track not in policy["exploration_types"]:
                raise WorkflowError("未知探索类型")
            if not requested_topic or not TOPIC_RE.fullmatch(requested_topic):
                raise WorkflowError("探索 topic 只能使用小写字母、数字和连字符")
            dirty = git_dirty_paths()
            if dirty:
                raise WorkflowError("自动创建探索分支前工作树必须干净: " + ", ".join(dirty))
            assert_main_matches_origin()
            branch = f"{requested_track}/{requested_topic}"
            if ref_exists(f"refs/heads/{branch}") or ref_exists(f"refs/remotes/origin/{branch}"):
                raise WorkflowError(f"探索分支已存在或与远程冲突: {branch}")
            if attempt_for_branch(branch) is not None:
                raise WorkflowError(f"已有尝试记录占用分支名: {branch}")
            run_git(["switch", "-c", branch])
            created_branch = True
            classification = classify_branch(branch)
    elif classification["kind"] == "exploration":
        requested_track = requested_track or classification["track"]
        if requested_track != classification["track"]:
            raise WorkflowError(f"--track 与当前探索分支 {original_branch} 不一致")
        if requested_topic and requested_topic != classification["topic"]:
            raise WorkflowError(f"--topic 与当前探索分支 {original_branch} 不一致")
    else:
        raise WorkflowError(f"不支持在分支 {original_branch} 启动任务")

    branch = current_branch()
    if classification["kind"] == "exploration":
        attempt_path = attempt_for_branch(branch)
        if attempt_path is None:
            if not created_branch:
                raise WorkflowError(f"当前探索分支缺少尝试记录: {branch}")
            attempt_path = ROOT / "maintenance/attempts" / f"{args.task_id}.md"

    baseline = snapshot()
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
        "baseline": baseline,
        "git": {
            "is_repo": True,
            "dirty_paths": git_dirty_paths(),
            "head": run_git(["rev-parse", "HEAD"]).stdout.strip(),
            "branch": branch,
            "base_branch": base_branch,
            "base_head": base_head,
            "track": classification["track"],
            "topic": classification["topic"],
            "attempt_path": attempt_path.relative_to(ROOT).as_posix() if attempt_path else None,
        },
    }
    try:
        if attempt_path is not None and not attempt_path.exists():
            create_attempt(
                attempt_path,
                task_id=args.task_id,
                branch=branch,
                track=classification["track"],
                base_head=base_head,
                scope=args.scope,
                acceptance=args.acceptance,
            )
        write_json(active_path(), record)
    except Exception:
        if attempt_path is not None and created_branch and attempt_path.exists():
            attempt_path.unlink()
        if created_branch:
            run_git(["switch", original_branch], check=False)
            run_git(["branch", "-D", branch], check=False)
        raise
    return {"active": str(active_path()), "task_id": args.task_id, "started_at": record["started_at"]}


def task_status() -> dict:
    if not active_path().is_file():
        return {"active": False, "checks": check_repository()}
    record = read_active()
    branch = assert_active_branch(record)
    paths = changed(record["baseline"], snapshot())
    required = ["maintenance/change_archive.md", "maintenance/current_task.md"]
    missing = [path for path in required if path not in paths]
    return {
        "active": True,
        "task_id": record["task_id"],
        "started_at": record["started_at"],
        "branch": branch,
        "track": record["git"].get("track", "stable"),
        "attempt_path": record["git"].get("attempt_path"),
        "changed_paths": paths,
        "required_updates": required,
        "missing_updates": missing,
        "checks": check_repository(),
    }


def pre_commit_check() -> None:
    check_repository(raise_on_error=True)
    if active_path().is_file():
        assert_active_branch(read_active())
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
    assert_active_branch(record)
    check_repository(raise_on_error=True)
    track = record["git"].get("track", "stable")
    attempt_rel = record["git"].get("attempt_path")
    paths = changed(record["baseline"], snapshot())
    for required in ("maintenance/change_archive.md", "maintenance/current_task.md"):
        if required not in paths:
            raise WorkflowError(f"结束前必须实际更新 {required}")
    if args.route == "changed" and "maintenance/decision_log.md" not in paths:
        raise WorkflowError("路线发生变化时必须更新 maintenance/decision_log.md")
    if track == "stable":
        if args.attempt_state:
            raise WorkflowError("stable 任务不能使用 --attempt-state")
    else:
        if not args.attempt_state:
            raise WorkflowError("探索任务结束时必须显式使用 --attempt-state")
        if not attempt_rel:
            raise WorkflowError("探索任务缺少尝试记录路径")
        attempt = ROOT / attempt_rel
        if not attempt.is_file():
            raise WorkflowError(f"探索任务缺少尝试记录: {attempt_rel}")
        if args.attempt_state == "validated":
            validate_attempt_evidence(attempt)
            if "maintenance/exploration_log.md" not in paths:
                raise WorkflowError("validated 探索结束前必须更新 maintenance/exploration_log.md")
        update_attempt_state(attempt, args.attempt_state, track, record["git"]["topic"])
    paths = changed(record["baseline"], snapshot())
    if attempt_rel and attempt_rel not in paths:
        raise WorkflowError(f"探索任务结束前必须实际更新 {attempt_rel}")
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
        "attempt_state": args.attempt_state,
        "changed_paths": paths,
    }
    report["git"] = auto_commit(record, paths, args.result, args.commit_message)
    history = state_dir() / "history" / f"{args.task_id}.json"
    write_json(history, {"start": record, "end": report})
    active_path().unlink()
    return report


def branch_status() -> dict:
    branch = current_branch()
    classification = classify_branch(branch)
    attempt = attempt_for_branch(branch) if classification["kind"] == "exploration" else None
    active = None
    if active_path().is_file():
        record = read_active()
        started_branch = record.get("git", {}).get("branch") or branch
        active = {
            "task_id": record["task_id"],
            "started_branch": started_branch,
            "branch_matches": started_branch == branch,
        }
    return {
        "branch": branch,
        "classification": classification,
        "attempt_path": attempt.relative_to(ROOT).as_posix() if attempt else None,
        "attempt_state": attempt_state(attempt) if attempt else None,
        "active_task": active,
        "policy": branch_policy(),
    }


def assert_no_active_task() -> None:
    if active_path().exists():
        raise WorkflowError("该命令要求没有活动任务；请先完成当前生命周期")


def assert_clean_worktree() -> None:
    dirty = git_dirty_paths()
    if dirty:
        raise WorkflowError("工作树必须干净: " + ", ".join(dirty))


def prepare_pr() -> dict:
    assert_no_active_task()
    check_repository(raise_on_error=True)
    assert_clean_worktree()
    branch = current_branch()
    classification = classify_branch(branch)
    if classification["kind"] != "exploration":
        raise WorkflowError("prepare-pr 只能在有效探索分支运行")
    attempt = attempt_for_branch(branch)
    if attempt is None:
        raise WorkflowError(f"探索分支缺少尝试记录: {branch}")
    state = attempt_state(attempt)
    if state != "validated":
        raise WorkflowError(f"只有 validated 尝试可以准备 PR；当前状态为 {state or 'unknown'}")
    validate_attempt_evidence(attempt)
    attempt_rel = attempt.relative_to(ROOT).as_posix()
    if completed_attempt_receipt(branch, attempt_rel, "validated") is None:
        raise WorkflowError("缺少 validated 生命周期回执；必须通过 end 完成归档后再准备 PR")
    assert_main_matches_origin()
    default = branch_policy()["default_branch"]
    ancestor = run_git(["merge-base", "--is-ancestor", default, "HEAD"], check=False)
    if ancestor.returncode != 0:
        raise WorkflowError(f"探索分支未基于最新 {default}；请在探索分支完成整理和冲突解决")
    text = attempt.read_text(encoding="utf-8")
    goal = re.search(r"^## Goal\s*$\n(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL)
    title = f"[{classification['track']}] {classification['topic']}"
    body = (
        f"## Summary\n\n{goal.group(1).strip() if goal else classification['topic']}\n\n"
        f"## Evidence\n\nSee `{attempt.relative_to(ROOT).as_posix()}`.\n\n"
        "## Integration\n\n- [ ] User explicitly confirmed merge\n- Merge method: Squash\n"
    )
    return {
        "ready": True,
        "branch": branch,
        "base": default,
        "merge_method": branch_policy()["success_integration"],
        "requires_user_confirmation": branch_policy()["require_user_merge_confirmation"],
        "pr": {"title": title, "body": body},
        "network_actions_performed": False,
    }


def archive_attempt() -> dict:
    assert_no_active_task()
    check_repository(raise_on_error=True)
    assert_clean_worktree()
    branch = current_branch()
    classification = classify_branch(branch)
    if classification["kind"] != "exploration":
        raise WorkflowError("archive-attempt 只能在有效探索分支运行")
    attempt = attempt_for_branch(branch)
    if attempt is None:
        raise WorkflowError(f"探索分支缺少尝试记录: {branch}")
    state = attempt_state(attempt)
    if state not in {"negative", "inconclusive", "paused"}:
        raise WorkflowError(f"只有 negative、inconclusive 或 paused 尝试可以归档；当前状态为 {state or 'unknown'}")
    attempt_rel = attempt.relative_to(ROOT).as_posix()
    if completed_attempt_receipt(branch, attempt_rel, state) is None:
        raise WorkflowError(f"缺少 {state} 生命周期回执；必须通过 end 完成归档后再改名")
    target = f"{branch_policy()['archive_prefix']}/{classification['track']}/{classification['topic']}"
    if ref_exists(f"refs/heads/{target}") or ref_exists(f"refs/remotes/origin/{target}"):
        raise WorkflowError(f"归档分支已存在或与远程冲突: {target}")
    run_git(["branch", "-m", target])
    return {
        "archived": True,
        "from": branch,
        "branch": target,
        "attempt_state": state,
        "network_actions_performed": False,
        "next": [
            f"git push -u origin {target}",
            f"确认归档分支已推送后，按需显式删除旧远程分支 {branch}",
            "切回 main 并启动 stable 治理任务，将结论和归档分支写入 maintenance/exploration_log.md",
        ],
    }


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
    start.add_argument("--track", choices=("stable", "research", "experiment", "sandbox"))
    start.add_argument("--topic")
    sub.add_parser("status", help="显示活动任务和未满足义务")
    sub.add_parser("branch-status", help="显示当前分支、尝试记录与分支策略")
    sub.add_parser("prepare-pr", help="验证成功探索并生成 Squash PR 标题与正文")
    sub.add_parser("archive-attempt", help="将失败、暂停或不可判决探索改名为归档分支")
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
    end.add_argument("--attempt-state", choices=ATTEMPT_STATES)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "start":
            output = start_task(args)
        elif args.command == "status":
            output = task_status()
        elif args.command == "branch-status":
            output = branch_status()
        elif args.command == "prepare-pr":
            output = prepare_pr()
        elif args.command == "archive-attempt":
            output = archive_attempt()
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
