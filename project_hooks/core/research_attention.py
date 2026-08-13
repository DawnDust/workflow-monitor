"""Deterministic research attention signals derived from existing facts."""

from __future__ import annotations

from typing import Iterable


EMPTY_BLOCKERS = frozenset({"", "-", "none", "null", "n/a", "na", "无", "无。", "暂无", "没有"})


def _present(value: object) -> bool:
    return str(value or "").strip().lower() not in EMPTY_BLOCKERS


def _signal(code: str, summary_zh: str, summary_en: str, source_type: str,
            source_ids: Iterable[object], target: str) -> dict:
    return {
        "code": code,
        "summary": {"zh-CN": summary_zh, "en": summary_en},
        "source_type": source_type,
        "source_ids": [str(value) for value in source_ids if value],
        "target": target,
    }


def research_attention(context: dict, catalog_items: list[dict],
                       catalog_relations: list[dict]) -> list[dict]:
    """Return at most three grouped, ordered, read-only attention signals."""
    signals: list[dict] = []
    missing = [item for item in catalog_items if item.get("status") == "missing"]
    if missing:
        signals.append(_signal(
            "REGISTERED_RESOURCE_MISSING",
            f"{len(missing)} 项已登记资料的文件缺失，请核对资料索引。",
            f"{len(missing)} registered resource file(s) are missing; review the resource index.",
            "catalog_item", [item.get("item_id") for item in missing], "resources",
        ))
    contradictions = [item for item in catalog_relations if item.get("relation_type") == "contradicts"]
    if contradictions:
        signals.append(_signal(
            "EXPLICIT_EVIDENCE_CONTRADICTION",
            f"存在 {len(contradictions)} 条已登记的矛盾证据关系，请复核相关判断。",
            f"{len(contradictions)} explicit contradictory evidence relation(s) need review.",
            "catalog_relation", [item.get("relation_id") for item in contradictions], "evidence-matrix",
        ))
    stage = context.get("current_stage") or {}
    if stage and _present(stage.get("blocker")):
        blocker = str(stage.get("blocker")).strip()
        signals.append(_signal(
            "RESEARCH_CHAPTER_BLOCKED", f"当前研究篇章存在明确阻塞：{blocker}",
            f"The current Research Chapter has an explicit blocker: {blocker}",
            "research_chapter", [stage.get("stage_id")], "research-stages",
        ))
    incomplete = [
        item for item in context.get("active_attempts") or []
        if not str(item.get("current_step") or "").strip()
        or not str(item.get("next_step") or "").strip()
    ]
    if incomplete:
        signals.append(_signal(
            "ACTIVE_EXPLORATION_CONTINUITY_MISSING",
            f"{len(incomplete)} 项活动探索缺少当前步骤或下一步，研究断点可能不完整。",
            f"{len(incomplete)} active exploration(s) lack a current or next step; continuity may be incomplete.",
            "attempt", [item.get("attempt_id") or item.get("branch") for item in incomplete], "research-stages",
        ))
    return signals[:3]
