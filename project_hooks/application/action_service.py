"""Application service coordinating safe workflow actions."""

from __future__ import annotations

import os
import time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .ports import FailureRecorder, MutationLockFactory
from ..core.actions import *
from ..core.actions import _normalized_fields
from ..core.lifecycle import lifecycle_step
def _block(code: str, message: str, evidence: str = "", next_action: str | None = None) -> ActionBlocker:
    return ActionBlocker(code, message, evidence, next_action)


class WorkflowActionService:
    def __init__(
        self,
        project_root: Path,
        *,
        state_provider: Callable[[], dict[str, Any]],
        executor: Callable[[str, dict[str, Any], Callable[[ActionProgress], None]], Any],
        mutation_lock_factory: MutationLockFactory,
        failure_recorder: FailureRecorder,
        failure_formatter: Callable[[dict[str, Any]], str],
        execution_mode_provider: Callable[[], str],
        application_version: str = "2.0.0",
        schema_version: int = 5,
    ):
        self.project_root = project_root.resolve()
        self.state_provider = state_provider
        self.executor = executor
        self.mutation_lock_factory = mutation_lock_factory
        self.failure_recorder = failure_recorder
        self.failure_formatter = failure_formatter
        self.execution_mode_provider = execution_mode_provider
        self.application_version = application_version
        self.schema_version = schema_version
        self._worktree_signature: str | None = None
        self._worktree_changed_at = time.monotonic()
        self._snapshot_cache: dict[str, Any] | None = None
        self._snapshot_cached_at = 0.0

    def snapshot(self, *, force: bool = False, max_age: float = 0.25) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._snapshot_cache is not None and now - self._snapshot_cached_at < max_age:
            state = deepcopy(self._snapshot_cache)
        else:
            state = self.state_provider()
            self._snapshot_cache = deepcopy(state)
            # The cache age starts after the provider returns.  Git-backed state
            # can take longer than the cache window itself; using the start time
            # made every subsequent action miss the cache during Dashboard boot.
            self._snapshot_cached_at = time.monotonic()
        signature = str(state.get("worktree_signature") or "")
        if self._worktree_signature is None:
            self._worktree_signature = signature
            self._worktree_changed_at = time.monotonic()
        elif signature != self._worktree_signature:
            self._worktree_signature = signature
            self._worktree_changed_at = time.monotonic()
        quiet_seconds = max(0.0, time.monotonic() - self._worktree_changed_at)
        state["worktree_quiet_seconds"] = round(quiet_seconds, 1)
        state["worktree_quiet"] = quiet_seconds >= 3.0
        state["last_worktree_change"] = (
            "至少 3 秒前" if state["worktree_quiet"] else f"{quiet_seconds:.1f} 秒前"
        )
        state["lifecycle_step"] = lifecycle_step(state)
        state["state_token"] = state_token(state)
        return state

    def availability(
        self, action_id: str, fields: dict[str, Any] | None = None, *, force: bool = False,
        state: dict[str, Any] | None = None,
    ) -> ActionAvailability:
        spec = action_spec(action_id)
        state = deepcopy(state) if state is not None else self.snapshot(force=force)
        active = state.get("active_task")
        branch_kind = (state.get("classification") or {}).get("kind")
        writer = state.get("writer_lock") or {}
        blockers: list[ActionBlocker] = []
        warnings: list[ActionBlocker] = []

        if writer.get("status") == "active" and int(writer.get("pid") or 0) != os.getpid():
            blockers.append(_block(
                "WRITER_BUSY", "项目正被另一个写动作占用",
                f"PID={writer.get('pid')}，动作={writer.get('command') or '未知'}", "refresh",
            ))
        if not state.get("installed") and action_id not in {
            "install", "health.check", "db.verify", "update.check", "update.apply"
        }:
            blockers.append(_block(
                "WORKFLOW_NOT_INSTALLED", "当前仓库尚未安装工作流 Hook",
                "core.hooksPath 不是 .githooks", "install",
            ))
        version_mismatch = bool(
            state.get("project_version") and state.get("application_version")
            and state["project_version"] != state["application_version"]
        )
        if version_mismatch and action_id not in {
            "update.check", "update.apply", "health.check", "db.verify", "install"
        }:
            blockers.append(_block(
                "VERSION_MISMATCH", "项目模板与当前 EXE 版本不一致",
                f"项目 {state.get('project_version')} / EXE {state.get('application_version')}", "update.check",
            ))
        if spec.requires_active and not active:
            blockers.append(_block("ACTIVE_TASK_REQUIRED", "该动作需要活动工作周期", "当前没有活动任务", "task.start"))
        if spec.requires_idle and active:
            blockers.append(_block(
                "NO_ACTIVE_TASK_REQUIRED", "该动作要求先完成当前工作周期",
                f"活动任务 {active.get('task_id')}", "task.finish",
            ))
        if spec.requires_stable and branch_kind != "stable":
            blockers.append(_block(
                "STABLE_BRANCH_REQUIRED", "该动作只能在稳定分支执行",
                f"当前分支 {state.get('branch')}（{branch_kind or '未知'}）",
            ))
        if spec.requires_exploration and branch_kind != "exploration":
            blockers.append(_block(
                "EXPLORATION_BRANCH_REQUIRED", "该动作需要探索分支",
                f"当前分支 {state.get('branch')}（{branch_kind or '未知'}）",
            ))
        if spec.requires_frozen and not state.get("frozen"):
            blockers.append(_block("FROZEN_EXE_REQUIRED", "软件更新只能由项目根目录的冻结 EXE 执行"))
        if active and active.get("branch") and active.get("branch") != state.get("branch"):
            blockers.append(_block(
                "ACTIVE_BRANCH_CHANGED", "任务期间分支已经变化",
                f"任务分支 {active.get('branch')} / 当前 {state.get('branch')}", "task.recover",
            ))
        sidecar = state.get("sidecar") or {}
        if sidecar.get("phase") in {"starting", "finishing"} and action_id not in {
            "task.recover", "task.recover_skip_commit", "task.abandon", "health.check", "db.verify"
        }:
            blockers.append(_block(
                "RECOVERY_REQUIRED", "活动任务处于未完成的事务阶段",
                f"阶段 {sidecar.get('phase')}", "task.recover",
            ))
        if action_id == "task.start" and active:
            blockers.append(_block("ACTIVE_TASK_EXISTS", "已有活动工作周期", str(active.get("task_id")), "task.finish"))
        if action_id == "task.recover" and not active and not sidecar:
            blockers.append(_block(
                "NO_RECOVERABLE_TASK", "当前没有可恢复的工作周期",
                "活动任务和恢复 sidecar 均不存在", "refresh",
            ))
        if action_id == "task.recover" and sidecar and sidecar.get("phase") not in {"starting", "finishing", "invalid"}:
            blockers.append(_block(
                "NO_RECOVERY_NEEDED", "当前工作周期不需要恢复",
                f"当前阶段：{sidecar.get('phase') or 'active'}", "state.update",
            ))
        if action_id == "task.recover_skip_commit" and sidecar.get("phase") != "finishing":
            blockers.append(_block(
                "FINISH_RECOVERY_REQUIRED", "仅能在结束阶段自动提交持续失败时跳过提交",
                f"当前恢复阶段：{sidecar.get('phase') or '无'}", "task.recover",
            ))
        if action_id == "attempt.update" and not state.get("attempt"):
            blockers.append(_block("ATTEMPT_REQUIRED", "当前活动任务没有探索记录"))
        if action_id == "task.finish" and active:
            preflight = state.get("finish_preflight")
            for item in (preflight or {}).get("blockers", []):
                blockers.append(_block(
                    str(item.get("code")), str(item.get("message")),
                    next_action=item.get("next_action"),
                ))
            if preflight is None and state.get("health_errors"):
                blockers.append(_block(
                    "HEALTH_CHECK_FAILED", "项目检查尚未通过",
                    "；".join(str(item) for item in state.get("health_errors") or []), "health.check",
                ))
            if fields and not fields.get("writer_stopped"):
                blockers.append(_block("WRITER_CONFIRMATION_REQUIRED", "请确认 AI 和其他编辑器已经停止写入"))
            if fields and not state.get("worktree_quiet", False):
                blockers.append(_block(
                    "WRITE_ACTIVITY_RECENT", "工作树尚未连续 3 秒保持稳定",
                    f"最近变化：{state.get('last_worktree_change') or '刚刚'}", "refresh",
                ))
        if action_id == "update.apply":
            git = state.get("git") or {}
            if state.get("branch") != "main":
                blockers.append(_block("UPDATE_MAIN_REQUIRED", "一键更新只能在 main 执行", str(state.get("branch"))))
            if state.get("dirty_paths"):
                blockers.append(_block(
                    "DIRTY_WORKTREE", "更新前工作树必须干净",
                    ", ".join(state.get("dirty_paths") or []),
                ))
            if git.get("available") and git.get("relation") != "synced":
                blockers.append(_block(
                    "UPSTREAM_NOT_SYNCED", "本地 main 与已知 origin/main 不同步",
                    str(git.get("relation")),
                ))
            if not state.get("latest_update"):
                warnings.append(_block("UPDATE_NOT_CHECKED", "建议先检查最新版本", next_action="update.check"))
        if action_id == "install" and state.get("installed") and not (fields or {}).get("force"):
            warnings.append(_block("ALREADY_INSTALLED", "当前项目已安装工作流；通常无需重复安装"))

        return ActionAvailability(not blockers, blockers, warnings, state["state_token"], spec.confirmation_level)

    def availability_matrix(
        self,
        fields_by_action: dict[str, dict[str, Any]] | None = None,
        *,
        force: bool = False,
        max_age: float = 0.25,
    ) -> ActionAvailabilityMatrix:
        """Calculate every action from one state read for the read-only Dashboard."""
        state = self.snapshot(force=force, max_age=max_age)
        supplied = fields_by_action or {}
        writer = state.get("writer_lock") or {}
        writer_running = writer.get("status") == "active"
        statuses: list[ActionStatus] = []
        for spec in action_specs():
            if not spec.dashboard_visible:
                continue
            fields = supplied.get(spec.action_id) or {}
            availability = self.availability(spec.action_id, fields or None, state=state)
            missing = tuple(
                field_spec.label
                for field_spec in spec.fields
                if field_spec.required
                and field_spec.default in (None, "", [], False)
                and fields.get(field_spec.name) in (None, "", [], False)
            )
            if writer_running and writer.get("command") == spec.action_id:
                status = "running"
            elif availability.blockers:
                status = "blocked"
            elif missing:
                status = "needs_input"
            else:
                status = "available"
            statuses.append(ActionStatus(
                spec.action_id, spec.label, spec.category, status, missing, availability,
            ))
        return ActionAvailabilityMatrix(state, tuple(statuses), state["state_token"])

    def validate_fields(self, action_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        return _normalized_fields(action_spec(action_id), fields)

    def execute(
        self,
        request: ActionRequest,
        *,
        progress: Callable[[ActionProgress], None] | None = None,
        capture_failure: bool = True,
    ) -> ActionResult:
        spec = action_spec(request.action_id)
        callback = progress or (lambda _item: None)
        try:
            fields = self.validate_fields(request.action_id, request.fields)
            availability = self.availability(request.action_id, fields, force=True)
            if request.state_token and request.state_token != availability.state_token:
                raise ActionBlockedError([_block(
                    "ACTION_STATE_CHANGED", "项目状态在表单打开后发生变化，请刷新并重新确认",
                    f"原状态 {request.state_token} / 当前 {availability.state_token}", "refresh",
                )])
            if not availability.enabled:
                raise ActionBlockedError(availability.blockers)
            if spec.confirmation_level == "high" and not request.confirmed:
                raise ActionBlockedError([_block("CONFIRMATION_REQUIRED", "该动作需要明确二次确认")])
            callback(ActionProgress(request.action_id, "preflight", "安全预检通过", 10))
            context = None
            if not spec.read_only:
                context = self.mutation_lock_factory(
                    self.project_root / ".project_hooks",
                    command=request.action_id,
                    task_id=(self.snapshot().get("active_task") or {}).get("task_id"),
                    timeout=0.25,
                )
                context.__enter__()
                locked = self.availability(request.action_id, fields, force=True)
                if request.state_token and request.state_token != locked.state_token:
                    raise ActionBlockedError([_block(
                        "ACTION_STATE_CHANGED", "获得写锁后项目状态发生变化，请刷新后重试",
                        f"原状态 {request.state_token} / 当前 {locked.state_token}", "refresh",
                    )])
                external = [item for item in locked.blockers if item.code != "WRITER_BUSY"]
                if external:
                    raise ActionBlockedError(external)
            try:
                callback(ActionProgress(request.action_id, "execute", "正在执行动作", 45))
                data = self.executor(request.action_id, fields, callback)
            finally:
                if context is not None:
                    context.__exit__(None, None, None)
            callback(ActionProgress(request.action_id, "refresh", "正在刷新项目状态", 90))
            changed: list[str] = []
            warnings: list[dict[str, Any]] = []
            next_actions: list[str] = []
            if isinstance(data, dict):
                raw_changed = data.get("changed") or data.get("changed_paths") or []
                changed = list(raw_changed) if isinstance(raw_changed, list) else []
                raw_warnings = data.get("warnings") or []
                warnings = list(raw_warnings) if isinstance(raw_warnings, list) else []
                raw_next = data.get("next_actions") or []
                next_actions = list(raw_next) if isinstance(raw_next, list) else []
            callback(ActionProgress(request.action_id, "complete", "动作完成", 100))
            return ActionResult("success", f"{spec.label}已完成", changed, warnings, next_actions, data=data)
        except ActionBlockedError as exc:
            if not capture_failure:
                raise
            first = exc.blockers[0] if exc.blockers else _block("ACTION_BLOCKED", str(exc))
            return ActionResult(
                "blocked", first.message,
                warnings=[asdict(item) for item in exc.blockers], code=first.code,
            )
        except Exception as exc:
            if not capture_failure:
                raise
            record = self.failure_recorder(
                self.project_root, exc, command=f"dashboard.{request.action_id}",
                application_version=self.application_version, schema_version=self.schema_version,
                execution_mode=self.execution_mode_provider(),
            )
            return ActionResult(
                "failed", self.failure_formatter(record),
                incident_id=record["incident_id"] if record.get("recorded", True) else None,
                code=record["code"],
            )
