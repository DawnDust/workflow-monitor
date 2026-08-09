"""Pure research-catalog value and presentation-neutral projection rules."""

from __future__ import annotations

import json


CATALOG_KIND_LABELS = {
    "literature": "文献",
    "data": "数据",
    "theory": "理论",
    "simulation": "模拟",
    "output": "输出",
    "other": "其他",
    "report": "报告",
}


def decode_item(row: dict) -> dict:
    item = dict(row)
    for source, target, fallback in (
        ("tags_json", "tags", []),
        ("metadata_json", "metadata", {}),
    ):
        value = item.pop(source, fallback)
        item[target] = json.loads(value) if isinstance(value, str) else value
    item["kind_label"] = CATALOG_KIND_LABELS.get(item.get("kind"), item.get("kind", ""))
    if item.get("kind") == "simulation" and item["metadata"].get("entry_type") == "bundle":
        item["kind_label"] = "模拟资料包"
    return item


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
            f"## {item.get('title') or item['item_id']}", "",
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
