"""Small, loss-aware CLI views of the existing workflow facts."""

from __future__ import annotations

import json
from typing import Any


def pick(value: dict, names: tuple[str, ...]) -> dict:
    return {name: value[name] for name in names if name in value}


def verification_view(context: dict) -> dict:
    active = context.get("active_task") or {}
    verification = context.get("verification") or {}
    preflight = context.get("finish_preflight") or {}
    result = {
        "task_id": active.get("task_id"),
        "status": verification.get("status", "not-applicable"),
    }
    if verification:
        result.update(pick(verification, ("profile", "required_suites", "problems")))
        result["receipts"] = [
            pick(item, ("suite", "result", "tests", "failures"))
            for item in verification.get("receipts", [])
        ]
    if active:
        result["checkpoint_status"] = context.get("checkpoint_status", active.get("checkpoint_status"))
    result.update(pick(preflight, ("status", "blockers", "warnings")))
    if verification:
        result["verification_status"] = verification.get("status")
    result["next_actions"] = context.get("required_actions") or preflight.get("required_actions") or []
    return result


def brief_context(context: dict) -> dict:
    active = context.get("active_task")
    result = {
        "contract": context.get("contract") or {},
        "branch": context.get("branch"),
        "git": pick(context.get("git_state") or {}, ("relation", "upstream_ref", "ahead", "behind", "error")),
        "task": None,
    }
    if active:
        task = pick(active, ("task_id", "scope", "acceptance", "track", "lifecycle_phase"))
        task["changed_count"] = len(active.get("changed_paths") or [])
        state = context.get("overview_state") or context.get("state") or {}
        sources = context.get("field_sources") or {}
        for name in ("current_step", "judgment", "breakpoint", "blocker", "next_steps"):
            source = sources.get(name) or {}
            if name in state and (not sources or source.get("task_id") == active.get("task_id")):
                task[name] = state[name]
        result["task"] = task
    else:
        handoffs = context.get("recent_handoffs") or []
        if handoffs:
            result["last_completed"] = pick(handoffs[0], ("task_id", "occurred_at", "result"))
    stage = context.get("current_stage")
    if stage:
        result["stage"] = pick(stage, ("stage_id", "title", "status", "goal", "blocker", "pause"))
    result["gate"] = verification_view(context)
    warnings = []
    if context.get("stage_version_conflicts"):
        warnings.append({"message": "篇章存在分叉判断", "stage_ids": context["stage_version_conflicts"], "next_action": "stage list --all-branches"})
    if context.get("stage_freshness_warning"):
        warnings.append(context["stage_freshness_warning"])
    warnings.extend(context.get("research_attention") or [])
    if warnings:
        result["warnings"] = warnings
    review_summary = context.get("review_summary") or {}
    if review_summary.get("pending_count"):
        result["reviews"] = review_summary
    return result


def markdown_view(value: dict, *, view: str) -> str:
    """Render every projected fact; no safety field is shortened for display."""
    labels = {
        "contract": "规范", "application_version": "版本", "schema_version": "Schema",
        "core_read_order": "规范入口", "branch": "分支", "git": "Git",
        "task": "活动任务", "task_id": "任务", "scope": "目标", "acceptance": "验收",
        "track": "轨道", "lifecycle_phase": "生命周期", "changed_count": "改动数",
        "current_step": "当前进展", "judgment": "判断", "breakpoint": "断点",
        "blocker": "阻塞", "next_steps": "下一步", "last_completed": "最近完成",
        "stage": "研究篇章", "title": "标题", "goal": "篇章目标", "pause": "暂停",
        "gate": "门禁", "status": "状态", "profile": "验证档位",
        "required_suites": "要求套件", "receipts": "回执", "problems": "回执问题",
        "checkpoint_status": "进展有效性", "verification_status": "测试状态",
        "blockers": "结束阻塞", "warnings": "警告", "next_actions": "必须执行",
    }
    lines = ["# " + ("结束门禁" if view == "verification" else "工作上下文")]
    for key, item in value.items():
        if key == "task" and item is None:
            lines.append("- 活动任务：无")
        elif item is not None and item != [] and item != {}:
            label = labels.get(key, key)
            if isinstance(item, dict):
                parts = []
                for field, content in item.items():
                    if content is not None and content != [] and content != {}:
                        rendered = json.dumps(content, ensure_ascii=False, separators=(",", ":")) if isinstance(content, (dict, list)) else str(content)
                        parts.append(f"{labels.get(field, field)}={rendered}")
                lines.append(f"- {label}：" + "；".join(parts))
            else:
                rendered = json.dumps(item, ensure_ascii=False, separators=(",", ":")) if isinstance(item, list) else str(item)
                lines.append(f"- {label}：{rendered}")
    return "\n".join(lines) + "\n"


def compact_result(output: dict[str, Any], *, task_id: str | None = None) -> dict:
    result = pick(output, (
        "task_id", "attempt_id", "result", "branch", "track", "state", "stage_review", "git",
        "warnings", "deprecation_warnings", "next_actions", "next_step", "changed_count", "logs", "review_summary",
    ))
    result.setdefault("task_id", task_id)
    result.setdefault("result", "success")
    if "current_step" in output:
        result["current_step"] = output["current_step"]
    if "changed_paths" in output:
        result["changed_count"] = len(output["changed_paths"])
    if "verification" in output:
        result["verification"] = [pick(item, ("suite", "result", "tests", "failures")) for item in output["verification"]]
    return result
