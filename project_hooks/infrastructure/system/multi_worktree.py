"""Clone-local worktree orchestration, migration and project monitoring."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from ... import __version__
from ...core.events import canonical_json, new_event, resolve_timezone, validate_event
from ...core.branches import classify_branch
from ..git import client as git
from ..git.worktrees import invoke_workflow, list_worktrees, repository_context
from ..persistence.active_task import load_active_state, atomic_write_json
from ..persistence.project_registry import OCCUPIED, REGISTRY_SCHEMA, ProjectRegistry, RegistryError, registry_for, now, legacy_active_rows
from ..persistence.store import load_events, append_events, sync_registry_events
from ..persistence.transaction import mutation_lock, _pid_alive


MARKER = ".codex/project-maintenance-workflow.json"
RESERVATION_ENV = "WORKFLOW_MONITOR_RESERVATION"
TOPIC = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def settings(root):
    return json.loads((Path(root) / MARKER).read_text(encoding="utf-8"))


def decode_state(row):
    state = json.loads(row["state_json"])
    state["phase"] = row["status"]
    if state.get("record"):
        state["record"]["_registry_revision"] = row["revision"]
    return state


def scan_legacy(root):
    trees, candidates, conflicts, ids = list_worktrees(root), [], [], set()
    event_sources, journals = {}, []
    for tree in trees:
        path = Path(tree["path"])
        if not (path / MARKER).is_file():
            conflicts.append({"kind": "unavailable" if not path.is_dir() else "uninstalled", "path": str(path)})
            continue
        try:
            config = settings(path)
            state_dir = path / config.get("state_dir", ".project_hooks")
            journal = path / config.get("maintenance_store", {}).get("journal", "maintenance/events.jsonl")
            events = load_events(journal)
            journals.append({"path": str(path), "events": events})
            for event in events:
                source = event_sources.setdefault(event["event_id"], {})
                source.setdefault(canonical_json(event), []).append(str(path))
            ids.update(e["task_id"] for e in events if e.get("task_id"))
            sidecar = load_active_state(state_dir)
            db = path / config.get("maintenance_store", {}).get("database", ".project_hooks/maintenance.sqlite3")
            rows = legacy_active_rows(db)
            if len(rows) > 1:
                raise RegistryError("同树存在多个活动任务")
            if sidecar and rows and canonical_json(sidecar["record"]) != canonical_json(json.loads(rows[0][0])):
                raise RegistryError("sidecar 与维护数据库状态矛盾")
            state = sidecar or ({"record": json.loads(rows[0][0]), "phase": "active", "checkpoint_recorded": bool(rows[0][1])} if rows else None)
            if state:
                task_id = state["record"]["task_id"]
                ids.add(task_id)
                if state["record"]["git"]["branch"] != tree["branch"]:
                    raise RegistryError("活动任务分支与检出分支不一致")
                candidates.append({"path": str(path), "state": state, "task_id": task_id})
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            conflicts.append({"kind": "legacy_state", "path": str(path), "reason": str(exc)})
    by_id = {}
    for item in candidates:
        by_id.setdefault(item["task_id"], []).append(item)
    duplicates = {key for key, items in by_id.items() if len(items) > 1}
    for task_id in duplicates:
        conflicts.append({"kind": "duplicate_task_id", "task_id": task_id, "paths": [i["path"] for i in by_id[task_id]]})
    bad_events = set()
    for event_id, versions in event_sources.items():
        if len(versions) > 1:
            bad_events.add(event_id)
            conflicts.append({"kind": "event_content", "event_id": event_id, "paths": sorted({p for paths in versions.values() for p in paths})})
    return {"worktrees": trees, "tasks": [item for item in candidates if item["task_id"] not in duplicates], "conflicts": conflicts, "used_ids": sorted(ids), "journals": journals, "conflicting_event_ids": bad_events}


def install_driver(root):
    context = repository_context(root)
    # Git invokes this command from its own temporary merge context. Keep the
    # executable/source location outside any disposable task checkout.
    if getattr(sys, "frozen", False):
        from .runtime import shared_entrypoint
        executable = shared_entrypoint(root)
        prefix = shell_quote(executable.as_posix())
    else:
        source = Path(__file__).resolve().parents[3]
        runtime = context.shared_dir / "runtime" / "source"
        runtime.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source / "project_hooks", runtime / "project_hooks", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        launcher = context.shared_dir / "runtime" / "events_merge.py"
        launcher.write_text("import sys\nsys.path.insert(0," + repr(str(runtime)) + ")\nfrom project_hooks.infrastructure.system.multi_worktree import merge_driver_main\nraise SystemExit(merge_driver_main(sys.argv[1:]))\n", encoding="utf-8")
        prefix = '"' + Path(sys.executable).as_posix() + '" "' + launcher.as_posix() + '"'
    driver = prefix + ' events-merge "%O" "%A" "%B"' if getattr(sys, "frozen", False) else prefix + ' "%O" "%A" "%B"'
    git.run(root, ["config", "--local", "merge.workflow-events.name", "Workflow Monitor event identity merge"])
    git.run(root, ["config", "--local", "merge.workflow-events.driver", driver])
    git.run(root, ["config", "--local", "merge.workflow-events.recursive", "binary"])
    # Clone-local attributes also cover existing branches without rewriting them.
    attributes = context.common_dir / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    original = attributes.read_bytes() if attributes.exists() else b""
    line = b"maintenance/events.jsonl merge=workflow-events\n"
    if line not in original:
        with attributes.open("ab") as handle:
            handle.write((b"\n" if original and not original.endswith(b"\n") else b"") + line)


def shell_quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"


def install_shared_hooks(root):
    from .runtime import shared_entrypoint
    context = repository_context(root)
    hooks = context.shared_dir / "hooks"
    configured = git.run(root, ["config", "--local", "--get", "core.hooksPath"], check=False).stdout.strip()
    if configured and configured not in {".githooks", hooks.as_posix(), str(hooks)}:
        raise RegistryError("项目有自定义提交钩子，请先处置: " + configured)
    hooks.mkdir(parents=True, exist_ok=True)
    environment = ""
    if getattr(sys, "frozen", False):
        command = shell_quote(shared_entrypoint(root).as_posix())
    else:
        environment = f"export PYTHONPATH={shell_quote(Path(__file__).resolve().parents[3].as_posix())}\n"
        command = f"{shell_quote(Path(sys.executable).as_posix())} -m project_hooks"
    hook = hooks / "pre-commit"
    hook.write_text(
        '#!/bin/sh\n# Workflow Monitor managed\n'
        'root=$(git rev-parse --show-toplevel) || exit 1\n'
        f'{environment}exec {command} --project "$root" pre-commit\n',
        encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    git.run(root, ["config", "--local", "core.hooksPath", hooks.as_posix()])


def register_legacy_workflows(registry, primary, trees):
    policy = settings(primary)["branch_policy"]
    by_branch = {item["branch"]: Path(item["path"]) for item in trees if item.get("branch") and not item.get("bare")}
    registry.register_workflow(workflow_id="main", branch="main", track="stable", path=primary,
                               worktree_id=repository_context(primary).worktree_id)
    events = registry.events()
    archived = {event["payload"].get("attempt_id"): event["payload"].get("archive_branch")
                for event in events if event["event_type"] == "attempt.archived"}
    for event in events:
        if event["event_type"] != "attempt.started":
            continue
        payload = event["payload"]
        archive = archived.get(payload["attempt_id"])
        path = by_branch.get(archive or event["branch"])
        registry.register_workflow(workflow_id=payload["attempt_id"], branch=event["branch"],
                                   track=payload["track"], topic=payload["topic"], path=path,
                                   worktree_id=repository_context(path).worktree_id if path and path.is_dir() else None,
                                   base_commit=payload.get("base_commit"), archive_branch=archive)
    known = {item["branch"] for item in registry.workflows()}
    for branch, path in by_branch.items():
        classification = classify_branch(branch, policy)
        if branch not in known and classification["kind"] == "exploration" and (path / ".git").is_file():
            registry.register_workflow(workflow_id=registry.attempt_id(branch) or classification.get("task_id") or branch,
                                       branch=branch, track=classification["track"], topic=classification["topic"],
                                       path=path, worktree_id=repository_context(path).worktree_id)


def initialize_unified(root):
    context = repository_context(root)
    registry = ProjectRegistry.create(context.shared_dir / "registry.sqlite3")
    registry.import_events(root, load_events(root / settings(root)["maintenance_store"]["journal"]))
    register_legacy_workflows(registry, root, list_worktrees(root))
    install_driver(root)
    install_shared_hooks(root)
    registry.configure(primary_root=str(root), unified=True, enabled=True)


def enable(root, *, apply=False):
    context = repository_context(root)
    with ExitStack() as stack:
        if apply:
            stack.enter_context(mutation_lock(context.shared_dir, command="worktree.enable", timeout=10))
            for item in sorted(list_worktrees(root), key=lambda t: t["path"].casefold()):
                path = Path(item["path"])
                if (path / MARKER).is_file():
                    stack.enter_context(mutation_lock(path / settings(path).get("state_dir", ".project_hooks"), command="worktree.migrate", timeout=10))
        scan = scan_legacy(root)
        public = {"worktrees": scan["worktrees"], "tasks": [{"task_id": i["task_id"], "path": i["path"], "branch": i["state"]["record"]["git"]["branch"], "phase": i["state"].get("phase", "active")} for i in scan["tasks"]], "conflicts": scan["conflicts"]}
        if not apply:
            return {"mode": "preview", **public}
        registry = ProjectRegistry.create(context.shared_dir / "registry.sqlite3")
        if registry.setting("unified"):
            return {"mode": "enabled", **public, "sync_failures": sync_registry_events(registry)}
        if registry.schema < REGISTRY_SCHEMA:
            raise RegistryError("请从主项目升级并迁移原工作树")
        registry.configure(enabled=True, migration_in_progress=True)
        primary = next((item["path"] for item in scan["worktrees"] if not item.get("bare")), str(root))
        registry.configure(primary_root=registry.setting("primary_root") or primary)
        registry.reserve_history(scan["used_ids"])
        registry.migration_conflicts(scan["conflicts"])
        for journal in scan["journals"]:
            registry.import_events(Path(journal["path"]), [e for e in journal["events"] if e["event_id"] not in scan["conflicting_event_ids"]])
        for item in scan["tasks"]:
            registry.import_task(Path(item["path"]), item["state"])
        register_legacy_workflows(registry, Path(primary), scan["worktrees"])
        install_driver(root)
        registry.configure(enabled=True)
        for item in scan["worktrees"]:
            path = Path(item["path"])
            if not (path / MARKER).is_file():
                continue
            with mutation_lock(path / settings(path).get("state_dir", ".project_hooks"), command="worktree.migrate", timeout=10):
                config = settings(path)
                config["multi_worktree"] = {"minimum_event_schema": 6}
                config["maintenance_store"]["schema_version"] = 6
                atomic_write_json(path / MARKER, config)
                if not any(e["event_type"] == "workflow.multi_worktree_enabled" for e in load_events(path / config.get("maintenance_store", {}).get("journal", "maintenance/events.jsonl"))):
                    event = new_event("workflow.multi_worktree_enabled", branch=item["branch"] or "main", task_id=None, payload={"registry_schema": 1}, timezone=config.get("timezone", "Asia/Shanghai"))
                    registry.record_events(path, [event])
        failures = sync_registry_events(registry)
        registry.configure(migration_in_progress=False)
        return {"mode": "enabled", **public, "sync_failures": failures}


def assert_write_allowed(root, *, allow_pending=False):
    registry = registry_for(root)
    if not registry:
        return
    if registry.schema < REGISTRY_SCHEMA:
        raise RegistryError("旧版注册表仍可查看，请从主项目升级后再写入")
    if registry.setting("migration_in_progress"):
        raise RegistryError("项目迁移尚未完成；请重新预览并执行 worktree enable --apply")
    sync_registry_events(registry)
    identity = repository_context(root).worktree_id
    if not allow_pending and any(item["worktree_id"] == identity for item in registry.pending()):
        raise RegistryError("本树状态已经保存，但审计同步尚未完成；请先 events sync 或恢复来源路径")
    current = str(root.resolve()).casefold()
    for item in registry.migration_conflicts():
        paths = item.get("paths", []) + ([item["path"]] if item.get("path") else [])
        if current in {str(Path(p).resolve()).casefold() for p in paths}:
            raise RegistryError("本工作树有待处置迁移冲突: " + canonical_json(item))


def start(root, args, raw_args):
    registry = registry_for(root)
    selected = getattr(args, "workflow", None)
    if not registry:
        if selected or getattr(args, "predecessor", None):
            raise RegistryError("请先将项目升级为统一工作流模式")
        return None
    assert_write_allowed(root, allow_pending=args.track in {"research", "experiment", "sandbox"})
    reservation_id = os.environ.get(RESERVATION_ENV)
    if reservation_id:
        registry.bind(root, args.task_id, reservation_id)
        return None
    if args.task_id and not re.fullmatch(r"(?:\d{8}-\d{3,}|\d{8}_[a-z0-9][a-z0-9_-]*_\d{3})", args.task_id):
        raise RegistryError("任务 ID 格式无效")
    branch = git.run(root, ["branch", "--show-current"]).stdout.strip()
    identity = repository_context(root).worktree_id
    primary = Path(registry.setting("primary_root"))
    workflow = registry.workflow(selected) if selected else None
    if selected and not workflow:
        raise RegistryError("工作流不存在: " + selected)
    if not selected and identity != "main":
        workflow = next((item for item in registry.workflows()
                         if item["worktree_id"] == identity and item["branch"] == branch), None)
    if identity != "main" and (not workflow or workflow["worktree_id"] != identity):
        raise RegistryError("当前目录不属于所选工作流")
    if identity == "main" and branch not in {"", "main"}:
        raise RegistryError("请在主工作树检出 main 后启动任务")
    if workflow and (workflow["status"] != "open" or not workflow["path"]):
        raise RegistryError("工作流已归档、待归档或没有已登记工作区")
    track = workflow["track"] if workflow else (args.track or "stable")
    if workflow and (args.track and args.track != track or args.topic and args.topic != workflow["topic"]):
        raise RegistryError("启动参数与工作流轨道或主题不一致")
    predecessor = getattr(args, "predecessor", None)
    if predecessor and (workflow or track == "stable" or (registry.workflow(predecessor) or {}).get("status") != "archived"):
        raise RegistryError("--predecessor 仅用于新探索并须引用已归档工作流")
    if not workflow and track != "stable" and (not args.topic or len(args.topic) > 40 or not TOPIC.fullmatch(args.topic)):
        raise RegistryError("topic 必须为 1–40 个小写 ASCII 字母、数字及分段连字符")
    if track == "stable" and args.topic:
        raise RegistryError("stable 任务不能使用 --topic")
    if registry.setting("unified") and not workflow and track != "stable":
        directory = primary / ".worktrees"
        if directory.resolve().parent != primary.resolve() or directory.is_symlink() or directory.is_junction():
            raise RegistryError("工作区管理目录被重定向: " + str(directory))
    config = settings(root)
    day = datetime.now(resolve_timezone(config.get("timezone", "Asia/Shanghai"))).strftime("%Y%m%d")
    with mutation_lock(repository_context(root).shared_dir, command="worktree.create", timeout=30):
        target = Path(workflow["path"]) if workflow else root
        if workflow and not owns_checkout(root, workflow):
            raise RegistryError("工作区不可用或分支身份不符，请先恢复")
        base_result = git.run(target, ["rev-parse", "HEAD^{commit}" if workflow else "main^{commit}"], check=False)
        base = base_result.stdout.strip() if base_result.returncode == 0 else None
        if track != "stable":
            if not base:
                raise RegistryError("首次探索前需要先建立 main 基线提交")
            validate_exploration_start(target, args, base, working_tree=bool(workflow))
        task = registry.reserve(root, day, track=track, topic=args.topic,
                                workflow_id=workflow["workflow_id"] if workflow else None,
                                requested_id=args.task_id, base_commit=base, predecessor_id=predecessor)
        explicit_id = args.task_id
        args.task_id = task["task_id"]
        forwarded = strip_project_argument(raw_args)
        index = forwarded.index("start")
        if not explicit_id:
            forwarded.insert(index + 1, task["task_id"])
        registry.prepare(task["task_id"], forwarded, base)
        if Path(task["path"]).resolve() == root.resolve():
            registry.bind(root, task["task_id"], task["token"])
            return None
        target = Path(task["path"])
        if not workflow:
            occupied = next((t for t in list_worktrees(root) if t["branch"] == task["branch"]), None)
            if occupied:
                raise RegistryError("分支已在工作树检出: " + occupied["path"])
            refs = ["refs/heads/", "refs/remotes/origin/"]
            if target.exists() or any(git.run(root, ["show-ref", "--verify", "--quiet", prefix + task["branch"]],
                                              check=False).returncode == 0 for prefix in refs):
                raise RegistryError("目标目录或分支已占用。预留任务：" + task["task_id"])
            target.parent.mkdir(parents=True, exist_ok=True)
            git.run(root, ["worktree", "add", "-b", task["branch"], str(target), base])
            prepare_checkout(target)
    child = invoke_workflow(target, forwarded, extra_env={RESERVATION_ENV: task["token"]})
    if child.returncode:
        raise RegistryError("目标 start 中断，保留任务以便恢复: " + child.stderr)
    output = json.loads(child.stdout)
    return {**output, "workflow_id": task["workflow_id"], "worktree": str(target), "base_commit": base,
            "entrypoint": str(primary / "workflow-monitor.exe"), "next": "使用项目入口 --project <工作区路径> 继续"}


def strip_project_argument(arguments):
    if arguments and arguments[0] == "--project":
        return list(arguments[2:])
    if arguments and arguments[0].startswith("--project="):
        return list(arguments[1:])
    return list(arguments)


def prepare_checkout(target):
    target_config = settings(target)
    target_config["multi_worktree"] = {"minimum_event_schema": 6}
    target_config["maintenance_store"]["schema_version"] = 6
    atomic_write_json(target / MARKER, target_config)
    installed = invoke_workflow(target, ["install"])
    if installed.returncode:
        raise RegistryError("目标安装中断，保留任务以便恢复: " + installed.stderr)


def owns_checkout(root, workflow):
    if not workflow.get("path"):
        return False
    path = Path(workflow["path"])
    try:
        context = repository_context(path)
        return (context.common_dir == repository_context(root).common_dir
                and context.worktree_id == workflow["worktree_id"]
                and git.run(path, ["branch", "--show-current"]).stdout.strip() == workflow["branch"])
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False


def validate_exploration_start(root, args, base, *, working_tree=False):
    from ...core.stage_versions import stage_versions
    config = settings(root)
    relative = config.get("maintenance_store", {}).get("journal", "maintenance/events.jsonl")
    journal = git.run(root, ["show", f"{base}:{relative}"], check=False)
    if journal.returncode:
        raise RegistryError("已提交的 main 缺少维护基线，请先完成安装与提交")
    events = (load_events(root / relative) if working_tree else
              [validate_event(json.loads(line)) for line in journal.stdout.splitlines() if line.strip()])
    stages = stage_versions(events, "base")
    active = [item for item in stages if item.get("status") == "active"]
    choice = getattr(args, "stage", "auto")
    if choice == "new":
        new_stage = getattr(args, "new_stage", None)
        if not isinstance(new_stage, dict) or not all(new_stage.get(name) for name in ("title", "goal", "acceptance")) or not isinstance(new_stage["acceptance"], list) or set(new_stage) - {"title", "goal", "acceptance", "stage_id"}:
            raise RegistryError("新篇章需要 title、goal 和 acceptance 列表")
        if active and not (isinstance(getattr(args, "previous_stage", None), dict) and args.previous_stage.get("status") == "paused" and args.previous_stage.get("reason")):
            raise RegistryError("基线存在活动篇章，创建新篇章前需要明确暂停原因")
    elif choice == "none" or (choice == "auto" and not active):
        if not getattr(args, "without_stage_reason", None):
            raise RegistryError("探索需要已提交的活动篇章，或 --stage new，或 --stage none --without-stage-reason")
    elif choice != "auto" and not any(item["stage_id"] == choice for item in active):
        raise RegistryError("指定篇章在已提交基线中不存在或未激活")


def recover_start(root, task):
    registry = registry_for(root)
    state = json.loads(task["state_json"])
    if not state.get("request_args") or not task.get("token"):
        raise RegistryError("启动记录缺少恢复参数；保留记录，请明确处置")
    path = Path(task["path"])
    with mutation_lock(repository_context(root).shared_dir, command="worktree.recover"):
        if not path.exists():
            checked = next((t for t in list_worktrees(root) if t["branch"] == task["branch"]), None)
            if checked:
                raise RegistryError("原分支已在其他位置检出: " + checked["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            exists = git.run(root, ["show-ref", "--verify", "--quiet", "refs/heads/" + task["branch"]], check=False).returncode == 0
            arguments = ["worktree", "add", str(path), task["branch"]] if exists else ["worktree", "add", "-b", task["branch"], str(path), state["base_commit"]]
            git.run(root, arguments)
        if repository_context(path).common_dir != repository_context(root).common_dir or git.run(path, ["branch", "--show-current"]).stdout.strip() != task["branch"]:
            raise RegistryError("目标路径的仓库或分支不匹配")
        if task["branch"] != "main":
            prepare_checkout(path)
    result = invoke_workflow(path, state["request_args"], extra_env={RESERVATION_ENV: task["token"]})
    if result.returncode:
        raise RegistryError(result.stderr)
    return {**json.loads(result.stdout), "worktree": str(path), "recovered": True}


def change_lifecycle(root, task_id, status, *, reason, evidence=None, next_steps=None):
    registry = registry_for(root)
    task = registry.task(task_id)
    if not task or task["status"] not in OCCUPIED:
        raise RegistryError("任务不存在或已经结束")
    if status == "active" and task["status"] not in {"paused", "blocked"}:
        raise RegistryError("只有暂停或阻塞任务使用 resume；中断的启动或收尾请使用 recover")
    if status in {"paused", "blocked"} and task["status"] not in {"active", "paused", "blocked"}:
        raise RegistryError("启动或收尾尚未完成，请先 recover")
    if status in {"paused", "blocked"} and (not reason or not next_steps):
        raise RegistryError("暂停或阻塞需要原因与恢复条件")
    event = new_event("task.lifecycle_changed", branch=task["branch"], task_id=task_id, payload={"status": status, "reason": reason, "evidence": evidence or [], "next": next_steps or []}, timezone=settings(root).get("timezone", "Asia/Shanghai"))
    events = [event]
    attempt_id = registry.attempt_id(task["branch"])
    if attempt_id and (status == "paused" or (status == "active" and task["status"] == "paused")):
        events.append(new_event("attempt.state_changed", branch=task["branch"], task_id=task_id, payload={"attempt_id": attempt_id, "state": "paused" if status == "paused" else "active"}, timezone=settings(root).get("timezone", "Asia/Shanghai")))
    registry.record_events(Path(task["path"]), events, worktree_id=task["worktree_id"])
    sync_registry_events(registry)
    return {"task_id": task_id, "state": status, "worktree": task["path"]}


def assert_integratable(root, branch):
    data = snapshot(root, sync=False)
    if not data:
        return
    pending = [c for c in data["conflicts"] if c["requires_user_decision"] and c["status"] == "pending" and any(s["branch"] == branch for s in c["sources"])]
    if pending:
        raise RegistryError("相关研究分歧尚待用户确认: " + ", ".join(c["conflict_id"] for c in pending))


def process_identity(pid):
    if not _pid_alive(pid):
        raise RegistryError("绑定的执行进程不存在")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise RegistryError("无法读取执行进程启动标识")
        try:
            created, exited, kernel_time, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user)):
                raise RegistryError("无法读取执行进程启动标识")
            marker = (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            kernel.CloseHandle(handle)
    else:
        marker = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    return {"pid": pid, "started": str(marker)}


def snapshot(root, *, sync=True, registry=None):
    registry = registry or registry_for(root)
    if not registry:
        return None
    failures = sync_registry_events(registry) if sync else []
    trees = list_worktrees(root)
    by_path = {str(Path(t["path"]).resolve()).casefold(): t for t in trees}
    workflows = {item["workflow_id"]: item for item in registry.workflows()}
    tasks = []
    checked_paths = {}
    for row in registry.tasks():
        path = Path(row["path"])
        tree = by_path.get(str(path.resolve()).casefold())
        state = decode_state(row)
        record = state.get("record", {})
        issues = []
        workspace, dirty = "unknown", None
        if not path.is_dir():
            issues.append("路径失效")
        elif not tree:
            issues.append("Git 已移除或工作树已移动")
        else:
            try:
                if tree["branch"] != row["branch"]:
                    issues.append("检出分支与任务不符")
                if path not in checked_paths:
                    changes = git.run(path, ["status", "--porcelain", "-z", "--untracked-files=normal"], check=False)
                    checked_paths[path] = None if changes.returncode else bool(changes.stdout)
                dirty = checked_paths[path]
                if dirty is None:
                    issues.append("无法读取工作区状态")
                else:
                    workspace = "dirty" if dirty else "clean"
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                issues.append("无法读取工作区状态: " + str(exc))
        if row["status"] in OCCUPIED:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["updated_at"])).total_seconds()
            if age > 24 * 3600:
                issues.append("24 小时未更新")
            if row.get("owner_json"):
                owner = json.loads(row["owner_json"])
                try:
                    if process_identity(owner["pid"])["started"] != owner["started"]:
                        issues.append("执行进程已退出或 PID 已复用")
                except (OSError, RuntimeError, ValueError):
                    issues.append("执行进程状态待核实")
        checkpoint = record.get("checkpoint", {})
        workflow = workflows.get(row.get("workflow_id"), {})
        if workflow.get("status") == "archived":
            issues = []
        tasks.append({"task_id": row["task_id"], "workflow_id": row.get("workflow_id"), "kind": record.get("declaration", {}).get("kind"), "track": record.get("git", {}).get("track"), "worktree_id": row["worktree_id"], "worktree": row["path"], "branch": row["branch"], "status": row["status"], "started_at": row["started_at"], "updated_at": row["updated_at"], "current_step": checkpoint.get("current_step", ""), "checkpoint": checkpoint, "workspace": workspace, "dirty": dirty, "verification": "状态待核实" if issues else "normal", "issues": issues, "baseline_commit": record.get("git", {}).get("base_head")})
    from .worktree_conflicts import detect_conflicts
    conflicts = detect_conflicts(registry, tasks)
    return {"enabled": True, "tasks": tasks, "worktrees": trees, "conflicts": conflicts, "migration_in_progress": bool(registry.setting("migration_in_progress")), "migration_conflicts": registry.migration_conflicts(), "sync_failures": failures, "pending_events": len(registry.pending())}


def remove(root, task_id, *, confirmed=False):
    registry = registry_for(root)
    task = registry.task(task_id)
    if not task:
        raise RegistryError("任务不存在")
    if task["status"] in OCCUPIED:
        raise RegistryError("工作树关联未结束任务；请恢复并结束，或经用户确认放弃")
    path = Path(task["path"])
    if path.is_dir() and registry.active(path):
        raise RegistryError("工作区仍有另一条未结束任务")
    if task["worktree_id"] == "main":
        raise RegistryError("不能移除主工作树")
    if any(item["worktree_id"] == task["worktree_id"] for item in registry.pending()):
        raise RegistryError("审计事件尚未同步；请先 events sync")
    if git.run(path, ["status", "--porcelain", "--untracked-files=all"]).stdout:
        raise RegistryError("工作树存在未提交内容；请先保存并明确处置")
    if not confirmed:
        return {"task_id": task_id, "worktree": str(path), "confirmation_required": True}
    with mutation_lock(repository_context(root).shared_dir, command="worktree.remove"):
        git.run(root, ["worktree", "remove", str(path)])
    return {"removed": True, "task_id": task_id, "worktree": str(path)}


def merge_events(base: Path, ours: Path, theirs: Path):
    base_bytes, ours_bytes = base.read_bytes(), ours.read_bytes()
    base_events = load_events(base)
    our_events, their_events = load_events(ours), load_events(theirs)
    for side in (our_events, their_events):
        by_id = {e["event_id"]: canonical_json(e) for e in side}
        if any(by_id.get(e["event_id"]) != canonical_json(e) for e in base_events):
            raise RegistryError("共同基线事件被删除或修改，拒绝自动合并")
    if base_bytes and not ours_bytes.startswith(base_bytes):
        raise RegistryError("当前侧历史前缀已改变，拒绝自动合并")
    with tempfile.TemporaryDirectory(prefix="workflow-event-merge-") as directory:
        append_events(ours, Path(directory), their_events)
    return {"merged": True}


def merge_driver_main(arguments):
    try:
        merge_events(*(Path(value) for value in arguments))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
