"""Preview and resume the one-time move to project-owned workspaces."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import ExitStack
from pathlib import Path

from ...core.events import canonical_json
from ..config_paths import has_project_config, resolve_config_path, resolve_installation_path
from ..git import client as git
from ..git.worktrees import list_worktrees, repository_context
from ..persistence.active_task import atomic_write_json
from ..persistence.project_registry import OCCUPIED, ProjectRegistry, RegistryError
from ..persistence.transaction import mutation_lock
from . import multi_worktree as service


def legacy_hook_conflicts(context, configured, trees):
    paths = [context.common_dir / "hooks"] if not configured else [Path(tree["path"]) / configured for tree in trees]
    conflicts = []
    for directory in paths:
        if not directory.is_dir():
            continue
        for hook in sorted(directory.iterdir()):
            if not hook.is_file() or hook.name.endswith(".sample"):
                continue
            if configured == ".githooks" and hook.name == "pre-commit" and not hook.is_symlink():
                installation = resolve_installation_path(directory.parent)
                if installation.is_file():
                    record = json.loads(installation.read_text(encoding="utf-8"))
                    expected = record.get("managed_files", {}).get(".githooks/pre-commit", {})
                    content = hook.read_bytes().replace(b"\r\n", b"\n")
                    hashes = {hashlib.sha256(data).hexdigest() for data in (content, content.replace(b"\n", b"\r\n"))}
                    if not expected.get("customized") and expected.get("hash") in hashes:
                        continue
            conflicts.append({"path": str(hook), "reason": "自定义 Git 钩子需要先保留或迁移"})
    return conflicts


def plan_migration(root):
    root = root.resolve()
    context = repository_context(root)
    registry_path = context.shared_dir / "registry.sqlite3"
    registry = ProjectRegistry(registry_path) if registry_path.is_file() else None
    journal = context.shared_dir / "layout-migration.json"
    previous = json.loads(journal.read_text(encoding="utf-8")) if journal.is_file() else {}
    scan = service.scan_legacy(root)
    blockers = list(scan["conflicts"])
    for task in scan["tasks"]:
        blockers.append({"path": task["path"], "reason": "存在未结束任务", "task_id": task["task_id"]})
    for task in registry.tasks() if registry else []:
        if task["status"] in OCCUPIED:
            blockers.append({"path": task["path"], "reason": "存在未结束任务", "task_id": task["task_id"]})
    configured = git.run(root, ["config", "--local", "--get", "core.hooksPath"], check=False).stdout.strip()
    if configured not in {"", ".githooks", (context.shared_dir / "hooks").as_posix()}:
        blockers.append({"path": configured, "reason": "自定义提交钩子"})
    elif configured in {"", ".githooks"}:
        blockers.extend(legacy_hook_conflicts(context, configured, scan["worktrees"]))
    directory = root / ".worktrees"
    if directory.resolve().parent != root or directory.is_symlink() or directory.is_junction() or (directory.exists() and not directory.is_dir()):
        blockers.append({"path": str(directory), "reason": "工作区根目录被重定向或占用"})
    moves = list(previous.get("moves", []))
    runtime = context.shared_dir / "runtime"
    known_executables = [root / "workflow-monitor.exe", runtime / "workflow-monitor.exe"]
    known_hashes = {hashlib.sha256(file.read_bytes()).hexdigest()
                    for file in known_executables if file.is_file()}
    planned = {item["worktree_id"] for item in moves}
    destinations = {str(Path(item["destination"])).casefold() for item in moves}
    for tree in scan["worktrees"]:
        path = Path(tree["path"])
        if path == root or not has_project_config(path):
            continue
        identity = repository_context(path).worktree_id
        destination = directory / path.name
        if identity in planned or path.parent == directory:
            continue
        submodules = git.run(path, ["ls-files", "--stage"], check=False).stdout
        if tree.get("locked") or tree.get("prunable") or any(line.startswith("160000 ") for line in submodules.splitlines()):
            blockers.append({"path": str(path), "reason": "工作区已锁定、失效或包含子模块"})
        if destination.exists() or str(destination).casefold() in destinations:
            blockers.append({"path": str(destination), "reason": "迁移目标已占用"})
        destinations.add(str(destination).casefold())
        executable = path / "workflow-monitor.exe"
        digest = hashlib.sha256(executable.read_bytes()).hexdigest() if executable.is_file() else None
        tracked = git.run(path, ["ls-files", "--", "workflow-monitor.exe"], check=False).stdout
        moves.append({"source": str(path), "destination": str(destination), "branch": tree["branch"],
                      "worktree_id": identity, "legacy_executable": digest,
                      "retire_executable": bool(digest in known_hashes and not tracked and not executable.is_symlink())})
    return {"moves": moves, "blockers": blockers, "required": bool(moves or not (registry and registry.setting("unified")))}


def migrate_project(root):
    root = root.resolve()
    context = repository_context(root)
    with mutation_lock(context.shared_dir, command="worktree.migrate", timeout=20):
        if context.worktree_id != "main" or git.run(root, ["branch", "--show-current"]).stdout.strip() != "main":
            raise RegistryError("从检出 main 的主工作区应用迁移")
        plan = plan_migration(root)
        if plan["blockers"]:
            raise RegistryError("工作区迁移受阻: " + canonical_json(plan["blockers"]))
        if not plan["required"]:
            return plan
        scan = service.scan_legacy(root)
        with ExitStack() as locks:
            held = {}
            paths = sorted((Path(item["path"]) for item in scan["worktrees"]), key=str)
            for path in paths:
                if has_project_config(path):
                    state_dir = service.settings(path).get("state_dir", ".project_hooks")
                    lock = locks.enter_context(mutation_lock(path / state_dir, command="worktree.migrate", timeout=1))
                    if path != root:
                        held[str(path)] = (state_dir, lock["token"])
            plan = plan_migration(root)
            if plan["blockers"]:
                raise RegistryError("工作区迁移受阻: " + canonical_json(plan["blockers"]))
            return apply_plan(root, context, plan, service.scan_legacy(root), held)


def apply_plan(root, context, plan, scan, held):
    state_file = context.shared_dir / "layout-migration.json"
    registry = ProjectRegistry.create(context.shared_dir / "registry.sqlite3")
    registry.configure(primary_root=str(root), migration_in_progress=True, enabled=True)
    atomic_write_json(state_file, {"moves": plan["moves"]})
    registry.upgrade_schema()
    registry.reserve_history(scan["used_ids"])
    for source in scan["journals"]:
        registry.import_events(Path(source["path"]), source["events"])
    service.register_legacy_workflows(registry, root, scan["worktrees"])
    ensure_local_exclusion(context)
    for item in plan["moves"]:
        source, destination = Path(item["source"]), Path(item["destination"])
        if destination.resolve().parent != (root / ".worktrees").resolve():
            raise RegistryError("迁移目标不在项目工作区目录")
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            result = git.run(root, ["worktree", "move", str(source), str(destination)], check=False)
            if result.returncode:
                raise RegistryError("工作区移动失败，原任务和迁移记录已保留: " + result.stderr.strip())
            state_dir, lock_id = held[str(source)]
            moved_lock = destination / state_dir / "mutation.lock"
            if json.loads(moved_lock.read_text(encoding="utf-8")).get("token") == lock_id:
                moved_lock.unlink()
        actual = repository_context(destination)
        branch = git.run(destination, ["branch", "--show-current"]).stdout.strip()
        if actual.common_dir != context.common_dir or actual.worktree_id != item["worktree_id"] or branch != item["branch"]:
            raise RegistryError("迁移后工作区身份与预览不一致: " + str(destination))
        registry.relocate_worktree(actual.worktree_id, destination)
        config = service.settings(destination)
        if config["maintenance_store"].get("schema_version") != 6:
            config["maintenance_store"]["schema_version"] = 6
            atomic_write_json(resolve_config_path(destination), config)
    service.install_driver(root)
    service.install_shared_hooks(root)
    failures = service.sync_registry_events(registry)
    if failures:
        raise RegistryError("迁移后审计同步失败: " + canonical_json(failures))
    retained = retire_legacy_executables(context, plan["moves"])
    registry.configure(unified=True, migration_in_progress=False)
    state_file.unlink(missing_ok=True)
    return {**plan, "status": "completed", "retained_executables": retained}


def ensure_local_exclusion(context):
    exclude = context.common_dir / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    existing = exclude.read_bytes() if exclude.is_file() else b""
    if b"/.worktrees/" not in existing.splitlines():
        with exclude.open("ab") as stream:
            if existing and not existing.endswith(b"\n"):
                stream.write(b"\n")
            stream.write(b"/.worktrees/\n")


def retire_legacy_executables(context, moves):
    """Move identified legacy copies to a shared rollback area once per digest."""
    backup = context.shared_dir / "runtime" / "retired"
    retained = []
    for item in moves:
        executable = Path(item["destination"]) / "workflow-monitor.exe"
        if not executable.is_file():
            continue
        content = executable.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if not item.get("retire_executable") or digest != item["legacy_executable"] or executable.is_symlink():
            retained.append(str(executable))
            continue
        destination = backup / (digest + ".exe")
        backup.mkdir(parents=True, exist_ok=True)
        try:
            if destination.is_file():
                if destination.read_bytes() != content:
                    raise OSError("retired executable digest collision")
                executable.unlink()
            else:
                os.replace(executable, destination)
        except OSError:
            retained.append(str(executable))
    return retained
