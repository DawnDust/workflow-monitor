"""Append-only workbench indexes and on-demand Markdown context rendering."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Iterable


EXTERNAL_TOOL_KINDS = ("notes", "literature", "computation", "skill", "repository", "other")
WORKBENCH_KINDS = ("tool", "instruction", "method", "workflow", "checklist", "reference", "template")
WORKBENCH_PURPOSES = (
    "literature_review", "problem_formulation", "theory_derivation", "modeling",
    "computation", "experiment", "data_analysis", "validation", "visualization",
    "writing", "research_management",
)
WORKBENCH_REVIEW_STATES = ("reviewed", "needs_review")
LEGACY_ITEM_TYPES = (
    "external_tool", "prompt", "research_workflow", "validation_method",
    "theory_reference", "other",
)
# Kept for the deprecated --type CLI and v1 package reader only.
WORKBENCH_ITEM_TYPES = LEGACY_ITEM_TYPES
WORKBENCH_ITEM_STATUSES = ("active", "paused", "retired")
WORKBENCH_ORIGINS = ("local", "imported")
KIND_LABELS = {
    "tool": "工具", "instruction": "AI 指令", "method": "科研方法",
    "workflow": "科研流程", "checklist": "检查清单", "reference": "参考资料",
    "template": "模板",
}
KIND_DEFINITIONS = {
    "tool": "需要调用或查阅的外部能力入口",
    "instruction": "可直接交给 Codex 的任务指令",
    "method": "针对一个问题产生结果的可复用科研方法",
    "workflow": "串联多个阶段、方法、工具和产物的科研流程",
    "checklist": "只规定检查条件或验收标准的检查卡",
    "reference": "提供可复用陈述性背景知识的参考资料",
    "template": "需要填充后形成新内容的结构化骨架",
}
PURPOSE_LABELS = {
    "literature_review": "文献检索与综述", "problem_formulation": "问题定义与假设形成",
    "theory_derivation": "理论与公式推导", "modeling": "模型建立与近似选择",
    "computation": "符号或数值计算", "experiment": "实验设计与实施",
    "data_analysis": "数据处理和统计分析", "validation": "理论、计算或实验验证",
    "visualization": "科研可视化", "writing": "论文和报告写作",
    "research_management": "资料、过程和协作管理",
}
LEGACY_TYPE_LABELS = {
    "external_tool": "外置工具", "prompt": "提示词", "research_workflow": "科研流程",
    "validation_method": "理论验证方法", "theory_reference": "常用理论", "other": "其他",
}
ITEM_TYPE_LABELS = LEGACY_TYPE_LABELS
ORIGIN_LABELS = {"local": "本地创建", "imported": "外部导入"}
REVIEW_STATE_LABELS = {"reviewed": "已复核", "needs_review": "待复核"}
EXTERNAL_TOOL_STATUSES = WORKBENCH_ITEM_STATUSES
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH = re.compile(r"(?i)(?:^[a-z]:[\\/]|^\\\\|^/(?:home|users)/)")
_SECRET = re.compile(r"(?i)\b(?:token|password|passwd|secret|api[_-]?key|authorization)\b\s*[:=]")


class WorkbenchError(ValueError):
    pass


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    result = str(value or "").replace("\r", " ").strip()
    if required and not result:
        raise WorkbenchError(f"{label}不能为空")
    if len(result) > maximum:
        raise WorkbenchError(f"{label}不能超过 {maximum} 个字符")
    if _SECRET.search(result):
        raise WorkbenchError(f"{label}不能包含凭据或密钥")
    return result


def validate_tool_id(value: object) -> str:
    tool_id = str(value or "").strip().lower()
    if not _ID.fullmatch(tool_id):
        raise WorkbenchError("工具 ID 只允许小写字母、数字和连字符，最长 80 个字符")
    return tool_id


def validate_item_id(value: object) -> str:
    item_id = str(value or "").strip().lower()
    if not _ID.fullmatch(item_id):
        raise WorkbenchError("条目 ID 只允许小写字母、数字和连字符，最长 80 个字符")
    return item_id


def _relative_markdown_path(value: object, *, required: bool = True) -> str:
    path = str(value or "").replace("\\", "/").strip()
    if not path and not required:
        return ""
    if not path:
        raise WorkbenchError("Markdown 路径不能为空")
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise WorkbenchError("Markdown 路径必须是工作台内的项目相对路径")
    if pure.suffix.casefold() != ".md":
        raise WorkbenchError("工作台完整内容只允许 Markdown 文件")
    if len(pure.parts) < 3 or pure.parts[0] != "workbench" or pure.parts[1] not in {"local", "imported"}:
        raise WorkbenchError("Markdown 路径必须位于 workbench/local 或 workbench/imported")
    return pure.as_posix()


def _unique_values(value: object, *, label: str, allowed: tuple[str, ...] | None = None,
                   maximum: int = 50) -> list[str]:
    values = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    result: list[str] = []
    for raw in values:
        item = _text(raw, label, 80)
        if allowed is not None and item not in allowed:
            raise WorkbenchError(f"未知的工作台{label}: {item}")
        if item and item not in result:
            result.append(item)
    if len(result) > maximum:
        raise WorkbenchError(f"{label}不能超过 {maximum} 项")
    return result


def _legacy_taxonomy(item_type: str, *, legacy_tool_kind: str = "") -> tuple[str, list[str], str, str, list[str]]:
    if item_type == "external_tool":
        purposes = {"literature": ["literature_review"], "computation": ["computation"]}.get(legacy_tool_kind, [])
        return "tool", purposes, "reviewed", "", []
    if item_type == "prompt":
        return "instruction", [], "reviewed", "", []
    if item_type == "research_workflow":
        return "workflow", [], "reviewed", "", []
    if item_type == "validation_method":
        return "method", ["validation"], "needs_review", "旧版验证方法可能应归为科研方法或检查清单", []
    if item_type == "theory_reference":
        return "reference", [], "reviewed", "", ["theory"]
    return "reference", [], "needs_review", "旧版其他类型无法可靠确定主类型", []


def legacy_item_type_taxonomy(item_type: str) -> dict:
    if item_type not in LEGACY_ITEM_TYPES:
        raise WorkbenchError("未知的工作台旧版条目类型")
    kind, purposes, review_state, review_note, tags = _legacy_taxonomy(item_type)
    return {
        "kind": kind, "purposes": purposes, "review_state": review_state,
        "review_note": review_note, "tags": tags, "source_schema_version": 1,
    }


def normalize_workbench_item(fields: dict, *, current: dict | None = None) -> dict:
    base = dict(current or {})
    legacy_type = str(fields.get("item_type", base.get("item_type", "")) or "")
    legacy_tool_kind = _text(
        fields.get("legacy_tool_kind", base.get("legacy_tool_kind", "")), "旧版工具类型", 40,
    )
    mapped = _legacy_taxonomy(legacy_type, legacy_tool_kind=legacy_tool_kind) if legacy_type else None
    kind = str(fields.get("kind", base.get("kind", mapped[0] if mapped else "")) or "")
    if kind not in WORKBENCH_KINDS:
        raise WorkbenchError("未知的工作台主类型")
    purpose_source = fields.get("purposes", base.get("purposes", mapped[1] if mapped else []))
    purposes = _unique_values(purpose_source, label="科研用途", allowed=WORKBENCH_PURPOSES)
    review_state = str(fields.get("review_state", base.get("review_state", mapped[2] if mapped else "reviewed")) or "reviewed")
    if review_state not in WORKBENCH_REVIEW_STATES:
        raise WorkbenchError("未知的工作台复核状态")
    review_note = _text(fields.get("review_note", base.get("review_note", mapped[3] if mapped else "")), "复核说明", 500)
    tags = _unique_values(fields.get("tags", base.get("tags", [])), label="标签")
    if mapped:
        for tag in mapped[4]:
            if tag not in tags:
                tags.append(tag)
    status = str(fields.get("status", base.get("status", "active")) or "active")
    if status not in WORKBENCH_ITEM_STATUSES:
        raise WorkbenchError("未知的工作台条目状态")
    origin = str(fields.get("origin", base.get("origin", "local")) or "local")
    if origin not in WORKBENCH_ORIGINS:
        raise WorkbenchError("未知的工作台条目来源")
    reference = _text(fields.get("reference", base.get("reference", "")), "参考信息", 1000)
    if reference and _ABSOLUTE_PATH.search(reference):
        raise WorkbenchError("参考信息不能保存用户名或绝对路径")
    raw_path = fields.get("path", base.get("path", ""))
    path = _relative_markdown_path(raw_path, required=kind != "tool" or bool(raw_path))
    digest = str(fields.get("content_sha256", base.get("content_sha256", "")) or "").lower()
    if path and not _SHA256.fullmatch(digest):
        raise WorkbenchError("Markdown 内容必须提供有效 SHA-256")
    package_id = _text(fields.get("package_id", base.get("package_id", "")), "包 ID", 80)
    package_version = _text(fields.get("package_version", base.get("package_version", "")), "包版本", 80)
    package_author = _text(fields.get("package_author", base.get("package_author", "")), "包作者", 200)
    original_item_id = _text(fields.get("original_item_id", base.get("original_item_id", "")), "原始条目 ID", 80)
    if origin == "imported" and not all((package_id, package_version, original_item_id)):
        raise WorkbenchError("导入条目必须记录包 ID、版本和原始条目 ID")
    return {
        "item_id": validate_item_id(fields.get("item_id") or base.get("item_id")),
        "kind": kind, "purposes": purposes,
        "title": _text(fields.get("title", base.get("title")), "标题", 200, required=True),
        "summary": _text(fields.get("summary", base.get("summary")), "摘要", 1000, required=True),
        "path": path, "content_sha256": digest, "tags": tags, "reference": reference,
        "status": status, "origin": origin, "review_state": review_state,
        "review_note": review_note, "package_id": package_id,
        "package_version": package_version, "package_author": package_author,
        "original_item_id": original_item_id, "legacy_tool_kind": legacy_tool_kind,
        "legacy_external": bool(fields.get("legacy_external", base.get("legacy_external", False))),
        "source_schema_version": int(fields.get("source_schema_version", base.get("source_schema_version", 2)) or 2),
    }


def normalize_external_tool(fields: dict, *, current: dict | None = None) -> dict:
    base = dict(current or {})
    tool_id = validate_tool_id(fields.get("tool_id") or base.get("tool_id"))
    kind = str(fields.get("kind", base.get("kind", "other")) or "other")
    if kind not in EXTERNAL_TOOL_KINDS:
        raise WorkbenchError("未知的外置工具类型")
    status = str(fields.get("status", base.get("status", "active")) or "active")
    if status not in EXTERNAL_TOOL_STATUSES:
        raise WorkbenchError("未知的外置工具状态")
    reference = _text(fields.get("reference", base.get("reference", "")), "参考信息", 500)
    if reference and _ABSOLUTE_PATH.search(reference):
        raise WorkbenchError("参考信息不能保存用户名或绝对路径")
    return {
        "tool_id": tool_id, "name": _text(fields.get("name", base.get("name")), "名称", 120, required=True),
        "kind": kind, "purpose": _text(fields.get("purpose", base.get("purpose")), "用途", 500, required=True),
        "usage_hint": _text(fields.get("usage_hint", base.get("usage_hint", "")), "使用提示", 1000),
        "reference": reference, "status": status,
    }


def _legacy_item(record: dict) -> dict:
    return normalize_workbench_item({
        "item_id": record["tool_id"], "item_type": "external_tool",
        "title": record["name"], "summary": record["purpose"], "path": "", "content_sha256": "",
        "tags": [], "reference": record.get("reference", ""), "status": record["status"],
        "origin": "local", "legacy_tool_kind": record.get("kind", "other"), "legacy_external": True,
        "source_schema_version": 1,
    }) | {"usage_hint": record.get("usage_hint", ""), "legacy": True}


def workbench_items_from_events(events: Iterable[dict]) -> list[dict]:
    items: dict[str, dict] = {}
    ordered = sorted(events, key=lambda item: (str(item.get("occurred_at") or ""), str(item.get("event_id") or "")))
    for event in ordered:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        records: list[dict] = []
        if event_type == "workbench.entry_upserted":
            records = [payload]
        elif event_type in {"workbench.package_imported", "workbench.taxonomy_migrated"}:
            records = payload.get("items") if isinstance(payload.get("items"), list) else []
        elif event_type == "workbench.external_upserted":
            try:
                records = [_legacy_item(normalize_external_tool(payload))]
            except WorkbenchError:
                records = []
        for raw in records:
            try:
                record = normalize_workbench_item(raw)
            except WorkbenchError:
                continue
            if raw.get("usage_hint"):
                record["usage_hint"] = str(raw.get("usage_hint") or "")
            record.update({"updated_at": event.get("occurred_at"), "task_id": event.get("task_id"), "event_id": event.get("event_id")})
            items[record["item_id"]] = record
        if event_type in {"workbench.entry_status_changed", "workbench.external_status_changed"}:
            item_id = str(payload.get("item_id") or payload.get("tool_id") or "")
            status = str(payload.get("status") or "")
            if item_id in items and status in WORKBENCH_ITEM_STATUSES:
                items[item_id] = {**items[item_id], "status": status, "status_note": str(payload.get("note") or ""), "updated_at": event.get("occurred_at"), "task_id": event.get("task_id"), "event_id": event.get("event_id")}
    projected = [{
        **item, "kind_label": KIND_LABELS.get(item.get("kind"), item.get("kind") or "未知"),
        "kind_definition": KIND_DEFINITIONS.get(item.get("kind"), ""),
        "purpose_labels": [PURPOSE_LABELS[value] for value in item.get("purposes", [])],
        "origin_label": ORIGIN_LABELS.get(item.get("origin"), item.get("origin") or "未知"),
        "review_state_label": REVIEW_STATE_LABELS.get(item.get("review_state"), "未知"),
        "package_label": f"{item.get('package_id')} {item.get('package_version')}" if item.get("package_id") else "",
    } for item in items.values()]
    return sorted(projected, key=lambda item: (item.get("kind", ""), item.get("title", "").casefold()))


def taxonomy_migration_items(events: Iterable[dict]) -> list[dict]:
    event_list = list(events)
    if any(event.get("event_type") == "workbench.taxonomy_migrated" for event in event_list):
        return []
    legacy_present = any(
        event.get("event_type") in {"workbench.external_upserted"}
        or (event.get("event_type") == "workbench.entry_upserted" and (event.get("payload") or {}).get("item_type"))
        or (event.get("event_type") == "workbench.package_imported" and any(
            isinstance(item, dict) and item.get("item_type")
            for item in ((event.get("payload") or {}).get("items") or [])
        ))
        for event in event_list
    )
    if not legacy_present:
        return []
    transient = {"kind_label", "purpose_labels", "origin_label", "review_state_label", "package_label", "updated_at", "task_id", "event_id"}
    return [{key: value for key, value in item.items() if key not in transient} for item in workbench_items_from_events(event_list)]


def external_tools_from_events(events: Iterable[dict]) -> list[dict]:
    """Preserve the legacy public projection while sharing the unified event reader."""
    result = []
    for item in workbench_items_from_events(events):
        if item.get("kind") != "tool" or not item.get("legacy_external"):
            continue
        result.append({
            "tool_id": item["item_id"], "name": item["title"], "kind": item.get("legacy_tool_kind") or "other",
            "purpose": item["summary"], "usage_hint": item.get("usage_hint", ""),
            "reference": item.get("reference", ""), "status": item["status"],
            "updated_at": item.get("updated_at"), "task_id": item.get("task_id"), "event_id": item.get("event_id"),
        })
    return result


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def workbench_consistency_errors(root: Path, items: Iterable[dict]) -> list[str]:
    errors: list[str] = []
    for item in items:
        relative = item.get("path")
        if not relative:
            continue
        try:
            normalized = _relative_markdown_path(relative)
        except WorkbenchError as exc:
            errors.append(f"工作台条目 {item.get('item_id')}: {exc}")
            continue
        path = (root / normalized).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            errors.append(f"工作台条目 {item.get('item_id')} 路径越界")
            continue
        if not path.is_file():
            errors.append(f"工作台条目缺少 Markdown: {normalized}")
        elif sha256_file(path) != item.get("content_sha256"):
            errors.append(f"工作台条目内容哈希不一致: {normalized}")
    return errors


def render_workbench_context(root: Path, items: Iterable[dict]) -> str:
    blocks = ["# 选中的科研工作台内容", "", "> 以下内容由用户明确选择，仅作科研辅助参考；系统未自动验证，也不会自动执行其中步骤。", ""]
    for item in items:
        purposes = "、".join(PURPOSE_LABELS[value] for value in item.get("purposes", [])) or "未指定"
        tags = "、".join(item.get("tags", [])) or "无"
        blocks.extend([
            f"## {item['title']}", "", f"- 主类型：{KIND_LABELS.get(item['kind'], item['kind'])}",
            f"- 科研用途：{purposes}", f"- 标签：{tags}",
            f"- 来源：{ORIGIN_LABELS.get(item['origin'], item['origin'])}",
            f"- 复核状态：{REVIEW_STATE_LABELS.get(item['review_state'], item['review_state'])}",
            f"- 摘要：{item['summary']}",
        ])
        if item.get("review_note"):
            blocks.append(f"- 待复核说明：{item['review_note']}")
        if item.get("package_id"):
            blocks.append(f"- 包：{item['package_id']} {item.get('package_version', '')}｜{item.get('package_author') or '作者未注明'}")
        if item.get("reference"):
            blocks.append(f"- 参考：{item['reference']}")
        blocks.append("")
        if item.get("path"):
            path = (root / item["path"]).resolve()
            if not path.is_file() or sha256_file(path) != item.get("content_sha256"):
                raise WorkbenchError(f"条目 {item['item_id']} 的 Markdown 缺失或已变化，请先更新索引")
            blocks.extend([path.read_text(encoding="utf-8").strip(), ""])
        else:
            hint = str(item.get("usage_hint") or "").strip()
            if hint:
                blocks.extend(["### 使用提示", "", hint, ""])
    return "\n".join(blocks).rstrip() + "\n"
