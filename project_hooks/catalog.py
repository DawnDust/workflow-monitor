"""Validation and presentation helpers for the project research catalog."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path


CATALOG_KINDS = ("literature", "data", "theory", "simulation", "output")
CATALOG_KIND_LABELS = {
    "literature": "文献",
    "data": "数据",
    "theory": "理论",
    "simulation": "模拟",
    "output": "输出",
}
CATALOG_KIND_PREFIXES = {
    "literature": "lit",
    "data": "data",
    "theory": "theory",
    "simulation": "sim",
    "output": "out",
}
CATALOG_DIRECTORIES = {
    "source": "literature",
    "data": "data",
    "theory": "theory",
    "analysis": "simulation",
    "outputs": "output",
}
CATALOG_STATUSES = ("active", "missing", "archived")
RELATION_TYPES = (
    "supports",
    "uses",
    "produces",
    "validates",
    "contradicts",
    "derived-from",
)
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
RELATION_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)


class CatalogError(RuntimeError):
    pass


def normalize_project_path(root: Path, value: str, *, require_file: bool = False) -> str:
    candidate = Path(value)
    absolute = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    project_root = root.resolve()
    try:
        relative = absolute.relative_to(project_root)
    except ValueError as exc:
        raise CatalogError("资料路径必须位于项目目录内") from exc
    if relative == Path("."):
        raise CatalogError("资料路径不能是项目根目录")
    if require_file and not absolute.is_file():
        raise CatalogError(f"资料文件不存在: {relative.as_posix()}")
    return relative.as_posix()


def validate_identifier(value: str, *, label: str = "条目 ID") -> str:
    text = value.strip()
    if not text or not IDENTIFIER_RE.fullmatch(text):
        raise CatalogError(f"{label} 只能包含字母、数字、点、下划线和连字符")
    return text


def validate_relation_type(value: str) -> str:
    text = value.strip().casefold()
    if not RELATION_TYPE_RE.fullmatch(text):
        raise CatalogError("关系类型只能包含小写字母、数字、点、下划线和连字符")
    return text


def parse_tags(values: list[str] | None) -> list[str]:
    tags: list[str] = []
    for raw in values or []:
        for value in raw.split(","):
            tag = value.strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def parse_metadata(values: list[str] | None) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise CatalogError(f"扩展字段必须使用 key=value: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise CatalogError("扩展字段的 key 不能为空")
        if key in metadata:
            raise CatalogError(f"扩展字段重复: {key}")
        metadata[key] = value.strip()
    return metadata


def generated_item_id(kind: str, path: str | None = None) -> str:
    prefix = CATALOG_KIND_PREFIXES[kind]
    if path:
        digest = hashlib.sha256(path.casefold().encode("utf-8")).hexdigest()[:12]
        return f"{prefix}-{digest}"
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def generated_relation_id(source_id: str, relation_type: str, target_id: str) -> str:
    raw = f"{source_id}\0{relation_type}\0{target_id}"
    return f"rel-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def decode_item(row: dict) -> dict:
    item = dict(row)
    for source, target, fallback in (
        ("tags_json", "tags", []),
        ("metadata_json", "metadata", {}),
    ):
        value = item.pop(source, fallback)
        item[target] = json.loads(value) if isinstance(value, str) else value
    item["kind_label"] = CATALOG_KIND_LABELS.get(item.get("kind"), item.get("kind", ""))
    return item


def file_metadata(path: Path) -> dict[str, str | int]:
    stat = path.stat()
    return {
        "file_size": stat.st_size,
        "file_mtime_ns": stat.st_mtime_ns,
        "extension": path.suffix.casefold(),
    }


def context_payload(items: list[dict], relations: list[dict]) -> dict:
    selected = {item["item_id"] for item in items}
    related = [
        relation for relation in relations
        if relation["source_id"] in selected or relation["target_id"] in selected
    ]
    return {"items": items, "relations": related}


def render_context_markdown(items: list[dict], relations: list[dict]) -> str:
    lines = ["# 科研资料上下文", ""]
    if not items:
        return "# 科研资料上下文\n\n没有匹配的资料。\n"
    relation_map: dict[str, list[str]] = {item["item_id"]: [] for item in items}
    titles = {item["item_id"]: item.get("title") or item["item_id"] for item in items}
    for relation in relations:
        source_id, target_id = relation["source_id"], relation["target_id"]
        if source_id in relation_map:
            relation_map[source_id].append(
                f"- {relation['relation_type']} → {titles.get(target_id, target_id)} (`{target_id}`)"
            )
        if target_id in relation_map:
            relation_map[target_id].append(
                f"- {titles.get(source_id, source_id)} (`{source_id}`) → {relation['relation_type']}"
            )
    for item in items:
        lines.extend([
            f"## {item.get('title') or item['item_id']}",
            "",
            f"- ID：`{item['item_id']}`",
            f"- 类型：{item.get('kind_label') or item.get('kind')}",
            f"- 状态：{item.get('status')}",
        ])
        if item.get("path"):
            lines.append(f"- 路径：`{item['path']}`")
        if item.get("source"):
            lines.append(f"- 来源：{item['source']}")
        if item.get("tags"):
            lines.append(f"- 标签：{', '.join(item['tags'])}")
        lines.extend(["", item.get("summary") or "暂无摘要。"])
        if item.get("metadata"):
            lines.extend(["", "### 扩展信息", ""])
            lines.extend(f"- {key}：{value}" for key, value in item["metadata"].items())
        if relation_map[item["item_id"]]:
            lines.extend(["", "### 关联", "", *relation_map[item["item_id"]]])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
