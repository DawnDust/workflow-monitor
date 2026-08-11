"""Pure workflow action contracts, field rules, and state tokens."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
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
            _f("verification_profile", "验证档位", "choice", choices=("auto", "release"), default="auto"),
            _f("without_stage_reason", "无需阶段理由", "multiline", natural=True),
        ), requires_idle=True,
        description="建立活动任务、保存基线，并在需要时安全创建探索分支。",
    ),
    "state.update": ActionSpec(
        "state.update", "更新当前进度", "生命周期",
        (
            _f("current_step", "当前步骤", natural=True, maximum=500),
            _f("status", "当前步骤（弃用别名）", natural=True, maximum=500),
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
            _f("route", "路线", "choice", choices=("", "unchanged", "changed"), default=""),
            _f("methods_action", "方法资料（兼容参数）", "choice",
               choices=("", "updated", "reviewed-no-change"), default=""),
            _f("main_goal", "主目标（兼容参数）", "choice",
               choices=("", "unchanged", "changed"), default=""),
            _f("note", "完成说明", "multiline", required=True, natural=True),
            _f("evidence", "完成证据（每行一项）", "list", natural=True, maximum=1000),
            _f("attempt_state", "探索结论状态", "choice",
               choices=("", "active", "validated", "negative", "inconclusive", "paused"), default=""),
            _f("stage_review", "阶段审阅", "choice",
               choices=("", "updated", "reviewed-no-change"), default=""),
            _f("current_step", "最终当前步骤", natural=True, maximum=500),
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
            _f("main_goal_version", "主目标版本", maximum=100),
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
            _f("entrypoint", "模拟资料包入口文件", maximum=500),
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
            _f("entrypoint", "模拟资料包入口文件", maximum=500),
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
    material = json.dumps(
        _token_material(state), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
