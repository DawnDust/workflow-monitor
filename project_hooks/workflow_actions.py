"""Shared, typed workflow actions for CLI and the interactive Dashboard.

The module deliberately knows nothing about Tkinter or argparse.  A caller
provides a read-only state function and a trusted in-process executor; the
service adds availability checks, stale-state protection, the project writer
lock, progress reporting and privacy-preserving failure capture.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import __version__
from .diagnostics import execution_mode, format_failure, record_failure
from .store import SCHEMA_VERSION, canonical_json
from .transaction import mutation_lock


AI_FORM_SCHEMA = "project-hooks.dashboard-form/v1"


@dataclass(frozen=True)
class ActionField:
    name: str
    label: str
    kind: str = "text"
    required: bool = False
    choices: tuple[str, ...] = ()
    natural_text: bool = False
    maximum: int = 2000
    help: str = ""
    default: Any = None


@dataclass(frozen=True)
class ActionSpec:
    action_id: str
    label: str
    category: str
    fields: tuple[ActionField, ...] = ()
    confirmation_level: str = "none"
    requires_active: bool = False
    requires_idle: bool = False
    requires_stable: bool = False
    requires_exploration: bool = False
    requires_frozen: bool = False
    read_only: bool = False
    description: str = ""
    dashboard_visible: bool = True


@dataclass(frozen=True)
class ActionBlocker:
    code: str
    message: str
    evidence: str = ""
    next_action: str | None = None


@dataclass
class ActionRequest:
    action_id: str
    fields: dict[str, Any] = field(default_factory=dict)
    state_token: str | None = None
    confirmed: bool = False


@dataclass
class ActionAvailability:
    enabled: bool
    blockers: list[ActionBlocker]
    warnings: list[ActionBlocker]
    state_token: str
    confirmation_level: str

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["blockers"] = [asdict(item) for item in self.blockers]
        value["warnings"] = [asdict(item) for item in self.warnings]
        return value


@dataclass(frozen=True)
class ActionStatus:
    """Read-only Dashboard projection for one workflow action."""

    action_id: str
    label: str
    category: str
    status: str
    missing_fields: tuple[str, ...]
    availability: ActionAvailability


@dataclass(frozen=True)
class ActionAvailabilityMatrix:
    """All action states calculated from one immutable project snapshot."""

    state: dict[str, Any]
    actions: tuple[ActionStatus, ...]
    state_token: str


@dataclass(frozen=True)
class ActionProgress:
    action_id: str
    phase: str
    message: str
    percent: int


@dataclass
class ActionResult:
    status: str
    summary: str
    changed: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    incident_id: str | None = None
    data: Any = None
    code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ActionProtocolError(ValueError):
    pass


class ActionBlockedError(RuntimeError):
    def __init__(self, blockers: Iterable[ActionBlocker]):
        self.blockers = list(blockers)
        super().__init__("；".join(f"[{item.code}] {item.message}" for item in self.blockers))


def _f(
    name: str,
    label: str,
    kind: str = "text",
    *,
    required: bool = False,
    choices: tuple[str, ...] = (),
    natural: bool = False,
    maximum: int = 2000,
    help: str = "",
    default: Any = None,
) -> ActionField:
    return ActionField(name, label, kind, required, choices, natural, maximum, help, default)


ACTION_SPECS: dict[str, ActionSpec] = {
    "task.start": ActionSpec(
        "task.start", "开始工作周期", "生命周期",
        (
            _f("kind", "任务类型", "choice", required=True,
               choices=("code", "docs", "review", "governance", "analysis", "design", "test", "other"),
               default="code"),
            _f("scope", "任务目标", "multiline", required=True, natural=True, maximum=2000),
            _f("out_of_scope", "不包含内容", "multiline", natural=True, maximum=2000),
            _f("acceptance", "验收条件（每行一项）", "list", required=True, natural=True, maximum=1000),
            _f("task_size", "任务规模", "choice", choices=("small", "large"), default="small"),
            _f("git_commit", "自动提交", "choice", choices=("auto", "always", "never"), default="auto"),
            _f("track", "工作轨道", "choice", choices=("stable", "research", "experiment", "sandbox"), default="stable"),
            _f("topic", "探索主题", "text", maximum=100),
        ), requires_idle=True,
        description="建立活动任务、保存基线，并在需要时安全创建探索分支。",
    ),
    "state.update": ActionSpec(
        "state.update", "更新当前进度", "生命周期",
        (
            _f("status", "当前状态", natural=True, maximum=500),
            _f("goal", "当前目标", "multiline", natural=True),
            _f("judgment", "当前判断", "multiline", natural=True),
            _f("breakpoint", "工作断点", "multiline", natural=True),
            _f("next", "接下来步骤（每行一项）", "list", natural=True, maximum=500),
            _f("blocker", "阻塞", "multiline", natural=True),
            _f("main_goal_version", "主目标版本", "text", maximum=50),
        ), requires_active=True,
        description="记录当前目标、判断、断点、阻塞和最多三个下一步。",
    ),
    "decision.add": ActionSpec(
        "decision.add", "记录路线决策", "生命周期",
        (
            _f("decision", "决定", "multiline", required=True, natural=True),
            _f("alternatives", "备选方案", "multiline", required=True, natural=True),
            _f("basis", "依据", "multiline", required=True, natural=True),
            _f("reopen_condition", "重新评估条件", "multiline", required=True, natural=True),
        ), requires_active=True,
        description="路线变化时保存可审计的决定、备选方案和依据。",
    ),
    "attempt.update": ActionSpec(
        "attempt.update", "更新探索记录", "生命周期",
        (
            _f("hypothesis", "假设", "multiline", natural=True),
            _f("evidence", "证据（每行一项）", "list", natural=True, maximum=1000),
            _f("conclusion", "结论", "multiline", natural=True),
            _f("current_step", "当前步骤", "multiline", natural=True, maximum=500),
            _f("progress", "进展", "multiline", natural=True),
            _f("next_step", "下一步", "multiline", natural=True, maximum=500),
        ), requires_active=True, requires_exploration=True,
        description="保存探索假设、证据、进展和下一步。",
    ),
    "task.finish": ActionSpec(
        "task.finish", "完成工作周期", "生命周期",
        (
            _f("result", "结果", "choice", required=True,
               choices=("completed", "blocked", "failed", "indeterminate"), default="completed"),
            _f("route", "路线", "choice", required=True, choices=("unchanged", "changed"), default="unchanged"),
            _f("methods_action", "方法资料", "choice", required=True,
               choices=("updated", "reviewed-no-change"), default="reviewed-no-change"),
            _f("main_goal", "主目标", "choice", required=True, choices=("unchanged", "changed"), default="unchanged"),
            _f("note", "完成说明", "multiline", required=True, natural=True),
            _f("evidence", "完成证据（每行一项）", "list", natural=True, maximum=1000),
            _f("attempt_state", "探索结论状态", "choice",
               choices=("", "active", "validated", "negative", "inconclusive", "paused"), default=""),
            _f("status", "最终状态", natural=True, maximum=500),
            _f("goal", "最终目标", "multiline", natural=True),
            _f("judgment", "最终判断", "multiline", natural=True),
            _f("breakpoint", "最终断点", "multiline", natural=True),
            _f("next", "后续步骤（每行一项）", "list", natural=True, maximum=500),
            _f("blocker", "最终阻塞", "multiline", natural=True),
            _f("main_goal_version", "主目标版本", maximum=50),
            _f("commit_message", "自动提交说明", "text", natural=True, maximum=500),
            _f("writer_stopped", "确认 AI 和其他编辑器已停止写入", "bool"),
        ), confirmation_level="high", requires_active=True,
        description="在一次可恢复事务中写入最终状态并结束当前周期。",
    ),
    "task.recover": ActionSpec(
        "task.recover", "恢复中断周期", "恢复",
        (), confirmation_level="preview",
        description="从 sidecar、事件源和数据库投影恢复活动任务。",
    ),
    "task.recover_skip_commit": ActionSpec(
        "task.recover_skip_commit", "跳过持续失败的自动提交", "恢复",
        (_f("reason", "原因", "multiline", required=True, natural=True),),
        confirmation_level="high", requires_active=True,
        description="仅在完成事件已写入但自动提交持续失败时使用。",
    ),
    "task.abandon": ActionSpec(
        "task.abandon", "放弃当前周期", "恢复",
        (_f("reason", "放弃原因", "multiline", required=True, natural=True),),
        confirmation_level="high", requires_active=True,
        description="追加 abandoned 记录并解除活动状态；不删除、不重置文件、暂存区或分支。",
    ),
    "project.update": ActionSpec(
        "project.update", "更新项目资料", "项目与阶段",
        (
            _f("description", "项目描述", "multiline", natural=True, maximum=500),
            _f("big_goal", "长期目标", "multiline", natural=True, maximum=1000),
        ), requires_active=True, requires_stable=True,
    ),
    "stage.start": ActionSpec(
        "stage.start", "开始新阶段", "项目与阶段",
        (
            _f("stage_id", "阶段 ID", required=True, maximum=100),
            _f("title", "阶段标题", required=True, natural=True, maximum=200),
            _f("goal", "阶段目标", "multiline", required=True, natural=True),
            _f("acceptance", "验收条件（每行一项）", "list", required=True, natural=True, maximum=1000),
        ), confirmation_level="preview", requires_active=True, requires_stable=True,
    ),
    "stage.update": ActionSpec(
        "stage.update", "更新当前阶段", "项目与阶段",
        (
            _f("stage_id", "阶段 ID", required=True, maximum=100),
            _f("summary", "阶段进展", "multiline", natural=True),
            _f("current_step", "当前步骤", "multiline", natural=True, maximum=500),
            _f("next_step", "下一步", "multiline", natural=True, maximum=500),
            _f("blocker", "阶段阻塞", "multiline", natural=True),
            _f("evidence", "阶段证据（每行一项）", "list", natural=True, maximum=1000),
            _f("status", "阶段状态", "choice", choices=("", "active", "completed", "paused", "cancelled"), default=""),
        ), confirmation_level="preview", requires_active=True, requires_stable=True,
    ),
    "exploration.prepare_pr": ActionSpec(
        "exploration.prepare_pr", "准备本地 Squash PR", "探索",
        (), confirmation_level="preview", requires_idle=True, requires_exploration=True,
    ),
    "exploration.archive": ActionSpec(
        "exploration.archive", "归档探索分支", "探索",
        (), confirmation_level="high", requires_idle=True, requires_exploration=True,
    ),
    "exploration.import": ActionSpec(
        "exploration.import", "导入探索结论", "探索",
        (_f("archive_branch", "归档分支", required=True, maximum=300),),
        confirmation_level="preview", requires_idle=True, requires_stable=True,
    ),
    "catalog.add": ActionSpec(
        "catalog.add", "添加资料记录", "科研资料",
        (
            _f("kind", "资料类型", "choice", required=True,
               choices=("literature", "data", "theory", "simulation", "output", "other", "report")),
            _f("title", "标题", required=True, natural=True, maximum=500),
            _f("summary", "摘要", "multiline", natural=True),
            _f("path", "项目相对路径", maximum=500),
            _f("source", "来源", "multiline", natural=True),
            _f("tag", "标签（每行一项）", "list", natural=True, maximum=100),
        ), requires_active=True,
    ),
    "catalog.update": ActionSpec(
        "catalog.update", "更新资料记录", "科研资料",
        (
            _f("item_id", "资料 ID", required=True, maximum=100),
            _f("title", "标题", natural=True, maximum=500),
            _f("summary", "摘要", "multiline", natural=True),
            _f("status", "状态", "choice", choices=("", "active", "missing", "archived"), default=""),
            _f("path", "项目相对路径", maximum=500),
            _f("source", "来源", "multiline", natural=True),
            _f("tag", "标签（每行一项）", "list", natural=True, maximum=100),
        ), requires_active=True,
    ),
    "catalog.archive": ActionSpec(
        "catalog.archive", "归档资料记录", "科研资料",
        (_f("item_id", "资料 ID", required=True, maximum=100),),
        confirmation_level="preview", requires_active=True,
    ),
    "catalog.restore": ActionSpec(
        "catalog.restore", "恢复资料记录", "科研资料",
        (_f("item_id", "资料 ID", required=True, maximum=100),), requires_active=True,
    ),
    "catalog.link": ActionSpec(
        "catalog.link", "关联资料", "科研资料",
        (
            _f("source_id", "来源资料 ID", required=True, maximum=100),
            _f("relation_type", "关系", required=True, maximum=100),
            _f("target_id", "目标资料 ID", required=True, maximum=100),
            _f("note", "说明", "multiline", natural=True),
        ), requires_active=True,
    ),
    "catalog.unlink": ActionSpec(
        "catalog.unlink", "解除资料关联", "科研资料",
        (_f("relation_id", "关系 ID", required=True, maximum=100),),
        confirmation_level="preview", requires_active=True,
    ),
    "catalog.scan": ActionSpec(
        "catalog.scan", "扫描资料目录", "科研资料",
        (
            _f("root", "扫描子目录", maximum=500),
            _f("kind", "限定类型", "choice",
               choices=("", "literature", "data", "theory", "simulation", "output", "other", "report"), default=""),
            _f("dry_run", "仅预览", "bool", default=True),
        ), confirmation_level="preview", requires_active=True,
    ),
    "catalog.ingest": ActionSpec(
        "catalog.ingest", "导入外部资料", "科研资料",
        (
            _f("file", "文件路径", required=True, maximum=1000),
            _f("kind", "资料类型", "choice",
               choices=("", "literature", "data", "theory", "simulation", "output", "other", "report"), default=""),
            _f("title", "标题", natural=True, maximum=500),
            _f("summary", "摘要", "multiline", natural=True),
            _f("source", "来源", "multiline", natural=True),
            _f("tag", "标签（每行一项）", "list", natural=True, maximum=100),
            _f("dry_run", "仅预览", "bool", default=True),
        ), confirmation_level="preview",
    ),
    "catalog.bulk_update": ActionSpec(
        "catalog.bulk_update", "批量更新资料", "科研资料",
        (
            _f("id", "资料 ID（每行一项）", "list", maximum=100),
            _f("kind", "筛选类型", "choice",
               choices=("", "literature", "data", "theory", "simulation", "output", "other", "report"), default=""),
            _f("status", "筛选状态", "choice", choices=("", "active", "missing", "archived"), default=""),
            _f("query", "关键词", natural=True, maximum=500),
            _f("all", "更新全部匹配项", "bool"),
            _f("add_tag", "增加标签（每行一项）", "list", natural=True, maximum=100),
            _f("remove_tag", "移除标签（每行一项）", "list", natural=True, maximum=100),
            _f("summary", "统一摘要", "multiline", natural=True),
            _f("source", "统一来源", "multiline", natural=True),
            _f("dry_run", "仅预览", "bool", default=True),
        ), confirmation_level="preview", requires_active=True,
    ),
    "catalog.migrate": ActionSpec(
        "catalog.migrate", "迁移旧资料目录", "科研资料",
        (_f("dry_run", "仅预览", "bool", default=True),),
        confirmation_level="high", requires_active=True, requires_stable=True,
    ),
    "workbench.external.add": ActionSpec(
        "workbench.external.add", "登记外置工具", "工作台",
        (
            _f("tool_id", "工具 ID", required=True, maximum=80),
            _f("name", "名称", required=True, natural=True, maximum=120),
            _f("kind", "类型", "choice", required=True,
               choices=("notes", "literature", "computation", "skill", "repository", "other")),
            _f("purpose", "用途", "multiline", required=True, natural=True, maximum=500),
            _f("usage_hint", "使用提示", "multiline", natural=True, maximum=1000),
            _f("reference", "参考链接或标识", maximum=500),
        ), requires_active=True, requires_stable=True,
        description="登记项目会用到的外部软件、Skill 或仓库，仅作为工作台提醒。",
        dashboard_visible=False,
    ),
    "workbench.external.update": ActionSpec(
        "workbench.external.update", "更新外置工具", "工作台",
        (
            _f("tool_id", "工具 ID", required=True, maximum=80),
            _f("name", "名称", natural=True, maximum=120),
            _f("kind", "类型", "choice",
               choices=("", "notes", "literature", "computation", "skill", "repository", "other"), default=""),
            _f("purpose", "用途", "multiline", natural=True, maximum=500),
            _f("usage_hint", "使用提示", "multiline", natural=True, maximum=1000),
            _f("reference", "参考链接或标识", maximum=500),
        ), requires_active=True, requires_stable=True,
        description="更新外置工具提醒，不检测或运行对应程序。",
        dashboard_visible=False,
    ),
    "workbench.external.pause": ActionSpec(
        "workbench.external.pause", "暂停外置工具", "工作台",
        (_f("tool_id", "工具 ID", required=True, maximum=80), _f("note", "说明", "multiline", natural=True, maximum=500)),
        requires_active=True, requires_stable=True, dashboard_visible=False,
    ),
    "workbench.external.restore": ActionSpec(
        "workbench.external.restore", "恢复外置工具", "工作台",
        (_f("tool_id", "工具 ID", required=True, maximum=80), _f("note", "说明", "multiline", natural=True, maximum=500)),
        requires_active=True, requires_stable=True, dashboard_visible=False,
    ),
    "workbench.external.retire": ActionSpec(
        "workbench.external.retire", "停用外置工具", "工作台",
        (_f("tool_id", "工具 ID", required=True, maximum=80), _f("note", "说明", "multiline", natural=True, maximum=500)),
        confirmation_level="preview", requires_active=True, requires_stable=True,
        dashboard_visible=False,
    ),
    "health.check": ActionSpec(
        "health.check", "运行项目检查", "健康与更新", (), read_only=True,
    ),
    "db.verify": ActionSpec(
        "db.verify", "验证数据库投影", "健康与更新", (), read_only=True,
    ),
    "db.rebuild": ActionSpec(
        "db.rebuild", "从事件日志重建数据库", "高级维护", (),
        confirmation_level="high", requires_idle=True,
    ),
    "db.migrate": ActionSpec(
        "db.migrate", "迁移旧数据库", "高级维护",
        (_f("delete_legacy", "迁移后删除旧数据库", "bool"),),
        confirmation_level="high", requires_idle=True,
    ),
    "install": ActionSpec(
        "install", "安装到当前项目", "健康与更新",
        (_f("force", "强制接管已有 Hook", "bool"),), confirmation_level="high",
    ),
    "update.check": ActionSpec(
        "update.check", "检查软件更新", "健康与更新", (), read_only=True,
    ),
    "update.apply": ActionSpec(
        "update.apply", "一键安装更新", "健康与更新", (), confirmation_level="high",
        requires_idle=True, requires_stable=True, requires_frozen=True,
    ),
}


def action_specs() -> list[ActionSpec]:
    return list(ACTION_SPECS.values())


def action_spec(action_id: str) -> ActionSpec:
    try:
        return ACTION_SPECS[action_id]
    except KeyError as exc:
        raise ActionProtocolError(f"未知 Dashboard 动作：{action_id}") from exc


def _normalized_fields(spec: ActionSpec, fields: dict[str, Any]) -> dict[str, Any]:
    allowed = {item.name: item for item in spec.fields}
    unknown = sorted(set(fields) - set(allowed))
    if unknown:
        raise ActionProtocolError("包含未允许字段：" + ", ".join(unknown))
    normalized: dict[str, Any] = {}
    for name, field_spec in allowed.items():
        value = fields.get(name, field_spec.default)
        if field_spec.kind == "bool":
            if value is None:
                value = False
            if not isinstance(value, bool):
                raise ActionProtocolError(f"{field_spec.label}必须是 true 或 false")
        elif field_spec.kind == "list":
            if value is None or value == "":
                value = []
            if isinstance(value, str):
                value = [line.strip() for line in value.splitlines() if line.strip()]
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ActionProtocolError(f"{field_spec.label}必须是字符串列表")
            value = [item.strip() for item in value if item.strip()]
            if any(len(item) > field_spec.maximum for item in value):
                raise ActionProtocolError(f"{field_spec.label}单项最多 {field_spec.maximum} 个字符")
        else:
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ActionProtocolError(f"{field_spec.label}必须是文本")
            value = value.strip()
            if len(value) > field_spec.maximum:
                raise ActionProtocolError(f"{field_spec.label}最多 {field_spec.maximum} 个字符")
            if field_spec.choices and value not in field_spec.choices:
                raise ActionProtocolError(f"{field_spec.label}不是允许的选项")
        if field_spec.required and (value is False or value == "" or value == []):
            raise ActionProtocolError(f"{field_spec.label}不能为空")
        if value not in (None, "", [], False) or field_spec.kind == "bool":
            normalized[name] = value
    return normalized


def ai_form_template(action_id: str) -> str:
    spec = action_spec(action_id)
    fields: dict[str, Any] = {}
    for item in spec.fields:
        if not item.natural_text:
            continue
        fields[item.name] = [] if item.kind == "list" else ""
    return json.dumps(
        {"schema": AI_FORM_SCHEMA, "action": action_id, "fields": fields},
        ensure_ascii=False, indent=2,
    )


def parse_ai_form(text: str, expected_action: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ActionProtocolError(f"AI 内容不是合法 JSON：{exc.msg}") from exc
    if not isinstance(value, dict) or value.get("schema") != AI_FORM_SCHEMA:
        raise ActionProtocolError(f"AI JSON 的 schema 必须是 {AI_FORM_SCHEMA}")
    if value.get("action") != expected_action:
        raise ActionProtocolError(f"AI JSON 动作与当前表单 {expected_action} 不一致")
    fields = value.get("fields")
    if not isinstance(fields, dict):
        raise ActionProtocolError("AI JSON fields 必须是对象")
    spec = action_spec(expected_action)
    natural = {item.name: item for item in spec.fields if item.natural_text}
    unknown = sorted(set(fields) - set(natural))
    if unknown:
        raise ActionProtocolError("AI 不允许填写字段：" + ", ".join(unknown))
    partial = _normalized_fields(
        ActionSpec(spec.action_id, spec.label, spec.category, tuple(natural.values())), fields
    )
    return partial


def lifecycle_step(state: dict[str, Any]) -> dict[str, Any]:
    active = state.get("active_task")
    sidecar = state.get("sidecar") or {}
    if not active and not sidecar:
        if state.get("last_completed"):
            return {"key": "completed", "label": "已完成", "index": 6, "needs_recovery": False}
        return {"key": "idle", "label": "未开始", "index": 0, "needs_recovery": False}
    phase = sidecar.get("phase") or "active"
    if phase == "starting":
        return {"key": "starting", "label": "周期已建立", "index": 1, "needs_recovery": True}
    if phase == "finishing":
        return {"key": "finishing", "label": "正在收尾", "index": 5, "needs_recovery": True}
    changed = state.get("changed_paths") or []
    if active and active.get("state_updated"):
        if not state.get("health_errors") and state.get("worktree_quiet"):
            return {"key": "ready", "label": "可完成", "index": 4, "needs_recovery": False}
        return {"key": "progress", "label": "进展已记录", "index": 3, "needs_recovery": False}
    if changed:
        return {"key": "working", "label": "工作中", "index": 2, "needs_recovery": False}
    return {"key": "active", "label": "周期已建立", "index": 1, "needs_recovery": False}


def _token_material(state: dict[str, Any]) -> dict[str, Any]:
    active = state.get("active_task") or {}
    sidecar = state.get("sidecar") or {}
    git = state.get("git") or {}
    return {
        "project_version": state.get("project_version"),
        "application_version": state.get("application_version"),
        "branch": state.get("branch"),
        "head": git.get("head"),
        "relation": git.get("relation"),
        "worktree": state.get("worktree_signature"),
        "journal_hash": state.get("journal_hash"),
        "task_id": active.get("task_id"),
        "task_branch": active.get("branch"),
        "state_updated": active.get("state_updated"),
        "decisions_added": active.get("decisions_added"),
        "phase": sidecar.get("phase"),
        "last_completed": (state.get("last_completed") or {}).get("task_id"),
    }


def state_token(state: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(_token_material(state)).encode("utf-8")).hexdigest()[:24]


def _block(code: str, message: str, evidence: str = "", next_action: str | None = None) -> ActionBlocker:
    return ActionBlocker(code, message, evidence, next_action)


class WorkflowActionService:
    def __init__(
        self,
        project_root: Path,
        *,
        state_provider: Callable[[], dict[str, Any]],
        executor: Callable[[str, dict[str, Any], Callable[[ActionProgress], None]], Any],
    ):
        self.project_root = project_root.resolve()
        self.state_provider = state_provider
        self.executor = executor
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
            if state.get("health_errors"):
                blockers.append(_block(
                    "HEALTH_CHECK_FAILED", "项目检查尚未通过",
                    "；".join(str(item) for item in state.get("health_errors") or []), "health.check",
                ))
            if fields and fields.get("route") == "changed" and int(active.get("decisions_added") or 0) < 1:
                blockers.append(_block(
                    "DECISION_REQUIRED", "路线变化时必须先记录决策", "当前任务没有决策记录", "decision.add",
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
                context = mutation_lock(
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
            record = record_failure(
                self.project_root, exc, command=f"dashboard.{request.action_id}",
                application_version=__version__, schema_version=SCHEMA_VERSION,
                execution_mode=execution_mode(),
            )
            return ActionResult(
                "failed", format_failure(record),
                incident_id=record["incident_id"] if record.get("recorded", True) else None,
                code=record["code"],
            )
