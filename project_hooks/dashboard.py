"""Read-only Tkinter dashboard for project maintenance data."""

from __future__ import annotations

import json
import os
import platform
import queue
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__
from .catalog import CATALOG_KIND_LABELS, render_context_markdown
from .diagnostics import (
    cleanup_resolved_diagnostics,
    diagnostics_overview,
    execution_mode,
    record_failure,
    resolve_diagnostic,
)
from .dashboard_actions import export_bundle, report_bug as open_dashboard_bug
from .read_model import (
    MaintenanceReadModel,
    ReadModelError,
    action_overview_text,
    is_auxiliary_task_id,
)
from .resource_layout import RESOURCE_DIRECTORIES
from .updater import (
    check_latest_update,
    refresh_software_delivery as refresh_local_software_delivery,
    software_delivery_report,
    version_report,
)
from .store import SCHEMA_VERSION, load_events
from .launcher import ACTIVE_ENV, PORTABLE_ROOT_ENV, selected_executable
from .workflow_actions import (
    ActionProgress,
    ActionProtocolError,
    ActionRequest,
    WorkflowActionService,
    action_specs,
    ai_form_template,
    parse_ai_form,
)
from .workbench import EXTERNAL_TOOL_KINDS, external_tools_from_events


CLI_FALLBACK = (
    "可改用以下只读命令：\n"
    "  .\\workflow-monitor.exe context\n"
    "  .\\workflow-monitor.exe history\n"
    "  .\\workflow-monitor.exe decisions\n"
    "  .\\workflow-monitor.exe explorations\n"
    "  .\\workflow-monitor.exe catalog list\n"
    "  .\\workflow-monitor.exe db status"
)

class DashboardError(RuntimeError):
    pass


RESEARCH_PROMPT_TEMPLATES = {
    "literature_review": {
        "category": "文献研究",
        "label": "文献精读",
        "task": "深入阅读并评估给定文献或资料，提炼可直接服务当前研究的内容。",
        "sections": (
            "研究问题与背景",
            "核心观点与理论机制",
            "研究方法、数据与识别策略",
            "主要发现及证据强度",
            "贡献、局限与适用边界",
            "对当前项目可复用的概念、方法和线索",
        ),
    },
    "literature_comparison": {
        "category": "文献研究",
        "label": "文献比较",
        "task": "比较给定文献或资料，找出共识、分歧、证据差异和可推进的研究缺口。",
        "sections": (
            "共同研究问题与比较维度",
            "理论观点与关键假设对照",
            "方法、数据和样本差异",
            "结论一致处、冲突处及原因",
            "证据质量与局限",
            "可形成的新研究问题或综合框架",
        ),
    },
    "research_ideas": {
        "category": "研究设计",
        "label": "研究问题与思路",
        "task": "基于现有资料提出有依据、可验证且适合当前项目的研究问题与研究思路。",
        "sections": (
            "现有问题与知识缺口",
            "候选研究问题",
            "可能的理论机制与研究假设",
            "创新点和与既有工作的区别",
            "可行性、所需证据与主要风险",
            "优先级建议与最小验证步骤",
        ),
    },
    "method_design": {
        "category": "研究设计",
        "label": "研究方法设计",
        "task": "为当前研究问题设计可执行、可复核的研究方法，并说明关键取舍。",
        "sections": (
            "目标、研究问题与可检验假设",
            "研究设计与识别思路",
            "数据、变量、样本或模拟设置",
            "分析步骤与评价指标",
            "稳健性、有效性和替代解释检验",
            "失败条件、局限与实施顺序",
        ),
    },
    "process_review": {
        "category": "研究复盘",
        "label": "研究过程复盘",
        "task": "复盘当前研究过程，整理已完成工作、证据、决策、问题和下一步。",
        "sections": (
            "当前目标与已完成工作",
            "采用过的方法与关键过程",
            "获得的证据和仍未解决的问题",
            "已做决策及其依据",
            "失败、绕路与可复用经验",
            "下一步及优先级",
        ),
    },
    "conclusion_review": {
        "category": "研究复盘",
        "label": "结论与局限",
        "task": "从现有证据中提炼可靠结论，同时审查局限、边界条件和替代解释。",
        "sections": (
            "核心结论",
            "支持每项结论的证据",
            "结论强度与不确定性",
            "适用范围和边界条件",
            "局限、偏差与替代解释",
            "尚需验证的问题和后续研究",
        ),
    },
    "next_research_plan": {
        "category": "研究规划",
        "label": "下一步研究计划",
        "task": "根据当前研究状态生成按优先级排列、可以立即开始执行的下一步研究计划。",
        "sections": (
            "当前研究状态与已完成工作",
            "未解决问题、证据缺口和关键不确定性",
            "按优先级排列的下一步任务",
            "每项任务的目标、所需输入、方法、预期输出、完成标准、风险与依赖",
            "最小可执行第一步",
            "建议保存的项目记录",
        ),
    },
    "external_project_report": {
        "category": "对外沟通",
        "label": "对外项目总结",
        "task": "基于当前项目的结构化上下文和已索引证据，形成适合指定外部受众的项目总结。",
        "sections": (
            "报告受众、周期、语言、语气与保密边界",
            "项目背景、目标与当前阶段",
            "已完成工作、关键结果与可回查证据",
            "明确区分的事实、合理推断与待验证事项",
            "局限、风险和下一步",
            "报告文件路径、资料索引 ID 与未执行的发布动作",
        ),
        "extra_rules": (
            "写入前必须先确认受众、报告周期、语言、语气和保密边界；未确认时只提问，不生成文件。"
            "确认后将报告写为 `resources/reports/YYYYMMDD_<topic>_v01.md`，公式使用可预览的 "
            "Markdown/LaTeX 语法，并引用资料 ID 或项目相对路径。写入后通过 catalog 登记为 report，"
            "运行 check 与 db verify；不得自动提交、推送、发布或对外发送。"
        ),
    },
}

SOFTWARE_PROMPT_TEMPLATES = {
    "software_update_latest": {
        "category": "软件升级",
        "label": "更新到最新版本",
        "task": "在满足安全前提时执行本地 EXE update，将项目运行时和当前科研项目更新到最新稳定版本。",
        "sections": (
            "更新前分支、活动任务和 Git 同步检查",
            "原版本与目标版本",
            "下载和 SHA-256 校验结果",
            "配置、受管文件和数据库迁移结果",
            "保留的自定义内容与冲突候选",
            "更新后 check、db verify 和待提交文件",
        ),
        "extra_rules": (
            "仅在 main、无活动任务、工作树干净且与 origin/main 同步时执行 `workflow-monitor update`。"
            "升级完成后运行 `workflow-monitor check` 和 `workflow-monitor db verify`，不自动提交。"
        ),
    },
    "software_update_target": {
        "category": "软件升级",
        "label": "更新到指定版本",
        "task": "将项目内 EXE运行时和当前科研项目升级或回退到用户明确指定的兼容版本，并验证迁移结果。",
        "sections": (
            "当前版本和用户指定目标版本",
            "目标 Release 与兼容范围",
            "升级或回退前安全检查",
            "下载、摘要校验和迁移结果",
            "冲突、回滚或兼容性问题",
            "最终版本与项目健康状态",
        ),
        "extra_rules": "如果用户没有明确提供目标版本号，停止写操作并先询问；不得自行选择回退版本。",
    },
    "project_health_check": {
        "category": "软件诊断",
        "label": "验证项目健康",
        "task": "只读验证工作流配置、永久事件源、SQLite 投影、Git Hook 和版本兼容性。",
        "sections": (
            "项目根目录与配置状态",
            "workflow-monitor check 结果",
            "workflow-monitor db verify 结果",
            "事件数量、schema 和日志摘要",
            "Git Hook 与版本兼容状态",
            "问题分级和修复建议",
        ),
        "extra_rules": "依次执行 `workflow-monitor version`、`workflow-monitor check` 和 `workflow-monitor db verify`；不应用修复。",
    },
    "update_failure_diagnosis": {
        "category": "软件诊断",
        "label": "诊断升级失败",
        "task": "分析 Workflow Monitor 安装或升级失败的原因，优先使用只读证据并确认项目数据未受损。",
        "sections": (
            "失败命令、版本和完整错误",
            "网络、Release、摘要和权限检查",
            "项目分支、活动任务和工作树检查",
            "配置、事件日志和数据库完整性",
            "是否已自动回滚及当前可用版本",
            "最小风险修复步骤",
        ),
        "extra_rules": "诊断阶段不得先删除数据库或缓存；先确认事件日志完整和自动回滚状态，再提出修复。",
    },
    "release_commit": {
        "category": "版本发布",
        "label": "创建新版本提交",
        "task": "根据已完成改动和 SemVer 规则准备版本号、变更日志、构建与测试，并创建单一版本发布提交。",
        "sections": (
            "当前版本和待发布改动",
            "补丁、功能或主版本判断依据",
            "确认后的目标版本",
            "版本文件和 CHANGELOG 更新",
            "全量测试与 Release 资产构建结果",
            "创建的版本提交及尚未执行的发布动作",
        ),
        "extra_rules": (
            "如果用户没有明确确认目标版本号，先给出 SemVer 建议并停止在写操作前等待确认。"
            "确认后同步更新 `project_hooks/__init__.py` 和 `CHANGELOG.md`，"
            "运行全量测试并构建 Release 资产，提交消息使用 `release: v<version>`。"
            "只创建版本提交，不推送、不创建标签、不创建 Release。"
        ),
    },
    "github_release": {
        "category": "版本发布",
        "label": "发布 GitHub 新版本",
        "task": "在用户明确确认版本号和发布后，推送已验证的版本提交与同名标签，等待 GitHub Actions 创建 Release。",
        "sections": (
            "用户确认的版本号与发布范围",
            "main、origin/main 和工作树状态",
            "版本文件、CHANGELOG 与标签一致性",
            "发布前测试和资产检查",
            "提交推送、标签创建与标签推送结果",
            "GitHub Actions 和 Release 最终状态",
        ),
        "extra_rules": (
            "没有用户对具体版本号和本次发布的明确确认时，不得推送或创建标签。"
            "确认版本提交已经位于 main 且测试通过后，先推送 main，再创建并推送注释标签 `v<version>`，"
            "等待 Release workflow 结束并核对 EXE 和 manifest 两个正式资产。"
            "不得移动或复用既有版本标签，不得自动合并分支。"
        ),
    },
    "release_install_verify": {
        "category": "版本发布",
        "label": "验证正式版本安装",
        "task": "从正式 GitHub Release 下载指定版本 EXE，并在临时项目文件夹执行初始化和生命周期冒烟验证。",
        "sections": (
            "目标版本和 Release 资产",
            "EXE 和 manifest 摘要校验",
            "临时文件夹下载结果",
            "临时仓库 init、version 和 check",
            "start、state update、end 生命周期冒烟",
            "验证结论和临时文件清理状态",
        ),
        "extra_rules": (
            "只使用临时文件夹和临时 Git 仓库，从正式 Release 下载 EXE后验证 "
            "`init → version → check → start → state update → end`；不得拿真实科研项目做破坏性测试。"
        ),
    },
}

WORKBENCH_PROMPT_TEMPLATES = {
    **RESEARCH_PROMPT_TEMPLATES,
    **SOFTWARE_PROMPT_TEMPLATES,
}
WORKBENCH_PROMPT_CATEGORIES = (
    "全部",
    *dict.fromkeys(template["category"] for template in WORKBENCH_PROMPT_TEMPLATES.values()),
)

RESEARCH_PROMPT_COMMON_RULES = """请遵守以下规则：
0. 以下所有 `workflow-monitor` 命令均使用项目根目录的 `.\\workflow-monitor.exe` 执行。
1. 只使用当前 Codex 对话中已经选择的文件、已有消息和我提供的资料作为已有证据。
2. 如果材料不足，先明确指出还需要选择或提供哪些文件；停止补造事实、数据、引文或实验结果。
3. 明确区分“已有证据”“合理推断”和“待验证建议”。
4. 论述时尽量引用资料 ID、标题或项目相对路径，使结论可以回查。
5. 先完成分析，再单独列出建议保存的资料条目、资料关系、项目决策或探索记录。
6. 未经我在对话中明确确认，不得修改项目文件、数据库、事件或 Git 状态。
7. 如果我确认记录，再先运行 `workflow-monitor context --format markdown` 并按 core_read_order 阅读规范；复用已有活动任务且不替我结束，或按规范创建 stable 任务。只通过现有 project、stage、catalog、decision、attempt 和 state 命令记录；项目资料与阶段只在 main 的 stable 任务中更新，探索当前步骤、进展和下一步使用 attempt update；完成后更新项目概览，仅结束由你创建的任务。
"""

SOFTWARE_PROMPT_COMMON_RULES = """请遵守以下规则：
0. 以下所有 `workflow-monitor` 命令均使用项目根目录的 `.\\workflow-monitor.exe` 执行。
1. 先确认当前操作针对工作流软件开发仓库、普通科研项目还是临时测试仓库，不得混淆目标。
2. 除首次初始化外，先运行 `workflow-monitor context --format markdown` 并按 core_read_order 阅读规范。
3. 先做只读预检；任何写操作都必须遵守现有任务生命周期、分支限制、活动任务和 Git 同步要求。
4. 不得手工改写 `maintenance/events.jsonl` 既有行、直接编辑 SQLite、删除活动状态或绕过 `end`。
5. 只使用正式 GitHub Release；校验下载摘要，不从未发布的 main 分支替代稳定版本。
6. 除“创建新版本提交”和“发布 GitHub 新版本”外，不得自动提交、推送或创建标签；任何操作都不得自动合并。
7. 遇到缺少版本号、用户确认、凭据、干净工作树或同步 main 等前提时，停止危险动作并明确报告。
8. 完成后列出实际命令、版本变化、验证结果、文件变化、冲突和仍需用户执行的步骤。
"""


def build_research_prompt(template_id: str) -> str:
    template = WORKBENCH_PROMPT_TEMPLATES.get(template_id)
    if template is None:
        raise DashboardError(f"未知的科研工作台操作：{template_id}")
    sections = "\n".join(f"{index}. {label}" for index, label in enumerate(template["sections"], 1))
    rules = (
        RESEARCH_PROMPT_COMMON_RULES
        if template_id in RESEARCH_PROMPT_TEMPLATES
        else SOFTWARE_PROMPT_COMMON_RULES
    )
    extra_rules = template.get("extra_rules")
    extra = f"\n专项约束：{extra_rules}\n" if extra_rules else ""
    return (
        f"请立即执行“{template['label']}”，不要只提供行动方案。\n\n"
        f"任务目标：{template['task']}\n\n"
        f"{rules}{extra}\n"
        f"请按以下结构输出：\n{sections}\n"
    )


def copy_research_prompt(clipboard, prompt: str) -> str:
    clipboard.clipboard_clear()
    clipboard.clipboard_append(prompt)
    return "工作台提示词已复制，请粘贴到当前 Codex 对话框并发送。"


PRIMARY_TABS = ("工作流", "概览", "搜索", "资料", "工作台", "诊断", "解释")

RESULT_LABELS = {
    "completed": "完成",
    "blocked": "阻塞",
    "failed": "失败",
    "indeterminate": "待判定",
    "active": "进行中",
}


def result_label(value: object) -> str:
    text = str(value or "")
    return RESULT_LABELS.get(text, text or "未知")


def filter_records(records: list[dict], query: str) -> list[dict]:
    needle = query.strip().casefold()
    if not needle:
        return list(records)
    return [record for record in records if needle in json.dumps(record, ensure_ascii=False, default=str).casefold()]


def research_prompt_records(query: str = "", category: str = "全部") -> list[dict]:
    records = [
        {
            "template_id": template_id,
            "category": template["category"],
            "label": template["label"],
            "task": template["task"],
        }
        for template_id, template in WORKBENCH_PROMPT_TEMPLATES.items()
    ]
    if category and category != "全部":
        records = [record for record in records if record["category"] == category]
    return filter_records(records, query)


def global_search(records: list[dict], query: str) -> list[dict]:
    tokens = [item for item in query.casefold().split() if item]
    if not tokens:
        return []
    return [
        record for record in records
        if all(token in record.get("search_text", "").casefold() for token in tokens)
    ]


def linked_task_ids(record: dict | None) -> list[str]:
    if not record:
        return []
    values = list(record.get("task_ids") or [])
    if record.get("task_id"):
        values.insert(0, record["task_id"])
    return list(dict.fromkeys(value for value in values if value))


def record_location(record: dict) -> tuple[str | None, str | None]:
    return record.get("target"), record.get("record_id")


def record_identity(record: dict | None) -> tuple[str, object] | None:
    if not record:
        return None
    for key in ("record_id", "item_id", "relation_id", "event_id", "decision_id", "hash", "task_id", "branch"):
        if record.get(key) is not None:
            return key, record[key]
    return None


def normalize_records(decisions: list[dict], explorations: list[dict]) -> list[dict]:
    result = []
    for item in decisions:
        result.append({
            "record_type": "decision", "kind_label": "决策", "event_id": item.get("event_id"),
            "occurred_at": item.get("occurred_at"), "branch": item.get("branch"),
            "title": item.get("decision") or item.get("decision_id"), "result": "",
            "task_id": item.get("task_id"), "_source": item,
        })
    for item in explorations:
        result.append({
            "record_type": "exploration", "kind_label": "探索", "event_id": item.get("event_id"),
            "occurred_at": item.get("occurred_at"), "branch": item.get("branch"),
            "title": item.get("goal") or item.get("branch"), "result": item.get("result"),
            "task_id": item.get("task_id"), "_source": item,
        })
    return sort_records(result, "occurred_at", True)


def advanced_summary(snapshot: dict) -> str:
    health = snapshot.get("health", {})
    return (
        f"数据库：{health.get('status', '未知')}　Schema：{health.get('schema_version', '未知')}　"
        f"事件：{health.get('events', 0)}　本次重建：{'是' if health.get('rebuilt') else '否'}\n"
        f"事件日志哈希：{health.get('journal_hash') or '未知'}"
    )


def dashboard_presets(snapshot: dict) -> dict[str, list[str]]:
    if snapshot.get("task_details"):
        recent_tasks = sorted(
            (
                item for item in snapshot["task_details"].values()
                if not item.get("is_auxiliary")
            ),
            key=lambda item: (item.get("finished_at") or item.get("started_at") or "", item["task_id"]),
            reverse=True,
        )
    else:
        recent_tasks = [
            item for item in snapshot.get("history", [])
            if item.get("task_id") and not is_auxiliary_task_id(item["task_id"])
        ]
    return {
        "recent": [item["task_id"] for item in recent_tasks[:10]],
        "negative": [
            item.get("event_id") for item in snapshot.get("explorations", [])
            if item.get("result") == "negative" and item.get("event_id")
        ],
    }


def sort_records(records: list[dict], key: str, descending: bool = False) -> list[dict]:
    def value(record: dict) -> tuple[bool, str]:
        item = record.get(key)
        text = str(item or "").casefold()
        if key == "occurred_at":
            text = text[:19].replace(" ", "t")
        return item is None, text
    return sorted(records, key=value, reverse=descending)


def short(value: object, limit: int = 90) -> str:
    text = "" if value is None else str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def catalog_overview_text(items: list[dict]) -> str:
    counts = {kind: 0 for kind in CATALOG_KIND_LABELS}
    for item in items:
        if item.get("kind") in counts:
            counts[item["kind"]] += 1
    count_text = "　".join(
        f"{CATALOG_KIND_LABELS[kind]} {counts[kind]}" for kind in CATALOG_KIND_LABELS
    )
    missing = sum(item.get("status") == "missing" for item in items)
    archived = sum(item.get("status") == "archived" for item in items)
    recent = sorted(
        (item for item in items if item.get("status") != "archived"),
        key=lambda item: (item.get("created_at", ""), item.get("item_id", "")),
        reverse=True,
    )[:5]
    recent_text = "；".join(item.get("title") or item["item_id"] for item in recent) or "无"
    return (
        f"科研资料：共 {len(items)}　{count_text}　缺失 {missing}　归档 {archived}\n"
        f"最近新增：{recent_text}"
    )


def reveal_catalog_file(
    project_root: Path,
    item: dict,
    *,
    system: str | None = None,
    runner: Callable[[list[str]], object] | None = None,
) -> str:
    relative = item.get("path")
    if not relative:
        raise DashboardError("该资料没有项目文件路径。")
    root = project_root.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise DashboardError("资料路径超出项目目录，已拒绝打开。") from exc
    if not target.is_file():
        raise DashboardError(f"资料文件不存在：{relative}")
    system = system or platform.system()
    runner = runner or (lambda command: subprocess.Popen(command))
    if system == "Windows":
        command = ["explorer.exe", f"/select,{target}"]
    elif system == "Darwin":
        command = ["open", "-R", str(target)]
    else:
        command = ["xdg-open", str(target.parent)]
    try:
        runner(command)
    except OSError as exc:
        raise DashboardError(f"无法打开文件所在位置：{exc}") from exc
    return f"已打开文件所在位置：{relative}"


def open_resource_directory(
    project_root: Path,
    relative: str,
    *,
    system: str | None = None,
    runner: Callable[[list[str]], object] | None = None,
) -> str:
    root = project_root.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise DashboardError("资源目录超出项目范围，已拒绝打开。") from exc
    if not target.is_dir():
        raise DashboardError(f"资源目录不存在：{relative}")
    system = system or platform.system()
    runner = runner or (lambda command: subprocess.Popen(command))
    if system == "Windows":
        command = ["explorer.exe", str(target)]
    elif system == "Darwin":
        command = ["open", str(target)]
    else:
        command = ["xdg-open", str(target)]
    try:
        runner(command)
    except OSError as exc:
        raise DashboardError(f"无法打开资源目录：{exc}") from exc
    return f"已打开资源目录：{relative}"


class DashboardDataProvider:
    def __init__(
        self,
        model: MaintenanceReadModel,
        classifier: Callable[[str], dict],
        branch: str | None = None,
        action_service: WorkflowActionService | None = None,
    ):
        self.model = model
        self.classifier = classifier
        self.branch = branch
        self.action_service = action_service

    def load(self) -> dict:
        snapshot = self.model.dashboard_snapshot(self.branch)
        snapshot["classification"] = self.classifier(snapshot["branch"])
        snapshot["active_task_warning"] = active_task_warning(
            snapshot.get("context", {}).get("active_task")
        )
        events = load_events(self.model.journal_path)
        snapshot["external_tools"] = external_tools_from_events(events)
        snapshot["diagnostics"] = diagnostics_overview(
            self.project_root, application_version=__version__,
        )
        return snapshot

    @property
    def project_root(self) -> Path:
        return self.model.database_path.parent.parent

    def version_info(self) -> dict:
        return version_report(self.project_root)

    def check_for_updates(self) -> dict:
        return check_latest_update(self.project_root)

    def check_software_delivery(self) -> dict:
        return software_delivery_report(self.project_root)

    def refresh_software_delivery(self, release: dict) -> dict:
        return refresh_local_software_delivery(self.project_root, release)

    def lifecycle_snapshot(self) -> dict:
        if self.action_service is None:
            return {"lifecycle_step": {"key": "unavailable", "label": "仅查看", "index": 0}}
        return self.action_service.snapshot()


def version_status_text(report: dict) -> str:
    project = report.get("project_version") or "未初始化"
    build_id = (report.get("build_identity") or {}).get("build_id")
    build_text = f" ({build_id[:12]})" if build_id else ""
    return f"版本：EXE {report['application_version']}{build_text} / 项目 {project}"


def branch_status_text(branch: str, classification: dict) -> str:
    kind = classification.get("kind")
    if kind == "exploration":
        track = classification.get("track") or "exploration"
        label = f"探索 / {track}"
    elif kind == "stable":
        label = "稳定维护"
    elif kind == "archive":
        label = "归档探索"
    else:
        label = "不支持"
    return f"分支：{branch}（{label}）"


def delivery_status(state: dict) -> dict[str, str]:
    """Render the Git delivery state for the current work cycle."""
    dirty_count = len(state.get("dirty_paths") or [])
    git = state.get("git") or {}
    active = state.get("active_task") or {}
    head = str(git.get("head") or "")
    base_head = str(active.get("base_head") or "")
    head_changed = bool(active and head and base_head and head != base_head)

    worktree = "干净" if dirty_count == 0 else f"有 {dirty_count} 个未提交变更"
    if dirty_count and head_changed:
        commit = "部分已提交，仍有未提交变更"
    elif dirty_count:
        commit = "尚未提交当前修改"
    elif head_changed:
        commit = f"已提交到 {head[:8]}"
    else:
        commit = "没有待提交修改"

    relation = git.get("relation")
    if relation == "synced" and dirty_count:
        push = "未提交修改尚未进入推送范围"
    elif relation == "synced":
        push = f"已与 {git.get('upstream_ref') or 'origin/main'} 同步"
    elif relation == "ahead":
        push = f"待推送 {git.get('ahead') or 0} 个提交"
    elif relation == "behind":
        push = f"本地落后 {git.get('behind') or 0} 个提交"
    elif relation == "diverged":
        push = f"已分叉（领先 {git.get('ahead') or 0} / 落后 {git.get('behind') or 0}）"
    else:
        push = "远端状态不可用"

    return {"worktree": worktree, "commit": commit, "push": push}


def delivery_status_text(state: dict) -> str:
    status = delivery_status(state)
    return (
        f"工作区：{status['worktree']}　｜　提交：{status['commit']}　｜　"
        f"推送：{status['push']}"
    )


def software_delivery_status_text(report: dict | None) -> str:
    if not report:
        return "EXE 构建：读取中｜最近发布：读取中｜发布后软件修改：读取中"
    match = report.get("exe_repository_match")
    failed = report.get("status") == "software-delivery-check-failed"
    build = (
        "与当前仓库一致" if match is True
        else "与当前仓库不一致" if match is False
        else "未能核对" if failed
        else "项目未包含软件源码"
    )
    version = report.get("release_version")
    published_at = report.get("published_at")
    if version:
        try:
            value = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
            published = value.astimezone().strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            published = str(published_at or "时间未知")
        release = f"v{version}｜{published}"
    else:
        release = "未能核对"
    source_available = report.get("software_source_available")
    if source_available is None:
        changes = "未能核对" if failed else "不适用"
    elif not source_available:
        changes = "不适用"
    else:
        count = len(report.get("unreleased_software_changes") or [])
        changes = f"有 {count} 个软件文件尚未发布" if count else "无未发布软件修改"
    return f"EXE 构建：{build}　｜　最近发布：{release}　｜　发布后软件修改：{changes}"


def active_task_warning(active: dict | None, now: datetime | None = None) -> dict | None:
    if not active or not active.get("started_at"):
        return None
    clean = str(active["started_at"]).split("（", 1)[0].split("(", 1)[0].strip()
    try:
        started = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    age_hours = max(0.0, ((now or datetime.now()) - started).total_seconds() / 3600)
    if age_hours >= 24 * 7:
        return {"level": "red", "age_hours": round(age_hours, 1), "message": "活动任务已超过 7 天，请恢复或明确放弃"}
    if age_hours >= 24:
        return {"level": "yellow", "age_hours": round(age_hours, 1), "message": "活动任务已超过 24 小时，请确认是否继续"}
    return None


def update_status_text(result: dict | None = None, error: str | None = None) -> str:
    if error:
        return "更新：检查失败"
    if result and result.get("status") == "update-available":
        return f"更新：发现 {result['latest_version']}"
    if result and result.get("status") == "different-build":
        return "更新：同版本构建不一致"
    return "更新：已是最新版"


class DashboardController:
    """Keeps the last successful snapshot when a refresh fails."""

    def __init__(self, provider: DashboardDataProvider):
        self.provider = provider
        self.snapshot: dict | None = None

    def refresh(self) -> tuple[dict | None, str | None]:
        try:
            snapshot = self.provider.load()
            self.snapshot = snapshot
            return self.snapshot, None
        except Exception as exc:
            return self.snapshot, str(exc)


class TablePage:
    def __init__(self, parent, tk, ttk, scrolledtext, *, columns: list[tuple[str, str, int]],
                 detail: Callable[[dict], str], refresh: Callable[[], None],
                 activate: Callable[[dict], None] | None = None, show_query: bool = True,
                 activate_label: str = "打开记录", show_refresh: bool = False,
                 split_detail: bool = False):
        self.tk, self.ttk = tk, ttk
        self.frame = ttk.Frame(parent, padding=8)
        self.columns = columns
        self.split_detail = split_detail
        self.detail_formatter = detail
        self.refresh_callback = refresh
        self.activate_callback = activate
        self.predicate: Callable[[dict], bool] | None = None
        self.records: list[dict] = []
        self.visible: list[dict] = []
        keys = [item[0] for item in columns]
        self.sort_key = "occurred_at" if "occurred_at" in keys else keys[0]
        self.descending = True

        controls = ttk.Frame(self.frame)
        controls.pack(fill="x", pady=(0, 6))
        self.query = tk.StringVar()
        if show_query:
            ttk.Label(controls, text="筛选").pack(side="left")
            entry = ttk.Entry(controls, textvariable=self.query, width=36)
            entry.pack(side="left", padx=(6, 8))
        self.query.trace_add("write", lambda *_: self.render())
        ttk.Button(controls, text="复制选中", command=self.copy_selected).pack(side="left")
        if activate:
            ttk.Button(controls, text=activate_label, command=self.activate_selected).pack(side="left", padx=(6, 0))
        if show_refresh:
            ttk.Button(controls, text="刷新", command=refresh).pack(side="left", padx=(6, 0))

        if split_detail:
            self.panes = ttk.Panedwindow(self.frame, orient="horizontal")
            self.panes.pack(fill="both", expand=True)
            table_frame = ttk.Frame(self.panes, padding=(0, 0, 6, 0))
            detail_frame = ttk.Frame(self.panes, padding=(6, 0, 0, 0))
            self.panes.add(table_frame, weight=3)
            self.panes.add(detail_frame, weight=2)
        else:
            self.panes = None
            table_frame = ttk.Frame(self.frame)
            table_frame.pack(fill="both", expand=True)
            detail_frame = self.frame
        self.tree = ttk.Treeview(table_frame, columns=keys, show="headings", height=13)
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        for key, label, width in columns:
            self.tree.heading(key, text=label, command=lambda selected=key: self.sort(selected))
            self.tree.column(key, width=width, minwidth=70, stretch=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        if activate:
            self.tree.bind("<Double-1>", lambda _event: self.activate_selected())

        ttk.Label(
            detail_frame, text="详情",
        ).pack(anchor="w", pady=((0 if split_detail else 8), 3))
        self.detail = scrolledtext.ScrolledText(detail_frame, height=10, wrap="word")
        self.detail.pack(fill="both", expand=split_detail)
        self.detail.configure(state="disabled")

    def set_records(self, records: list[dict]) -> None:
        self.records = list(records)
        self.render()

    def set_predicate(self, predicate: Callable[[dict], bool] | None) -> None:
        self.predicate = predicate
        self.render()

    def sort(self, key: str) -> None:
        if self.sort_key == key:
            self.descending = not self.descending
        else:
            self.sort_key, self.descending = key, False
        self.render()

    def render(self) -> None:
        selected_key = record_identity(self.selected_record())
        source = self.records if self.predicate is None else [record for record in self.records if self.predicate(record)]
        self.visible = sort_records(filter_records(source, self.query.get()), self.sort_key, self.descending)
        self.tree.delete(*self.tree.get_children())
        for index, record in enumerate(self.visible):
            iid = f"row-{index}"
            self.tree.insert("", "end", iid=iid, values=[short(record.get(key)) for key, _, _ in self.columns])
        selected_index = next(
            (index for index, record in enumerate(self.visible) if record_identity(record) == selected_key),
            None,
        )
        if selected_index is not None:
            self.tree.selection_set(f"row-{selected_index}")
            self.show_detail()
        elif self.visible:
            self.tree.selection_set("row-0")
            self.show_detail()
        else:
            self._set_detail("没有匹配的数据。")

    def selected_record(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return self.visible[int(selection[0].split("-", 1)[1])]
        except (IndexError, ValueError):
            return None

    def show_detail(self, _event=None) -> None:
        record = self.selected_record()
        self._set_detail(self.detail_formatter(record) if record else "没有选中记录。")

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def copy_selected(self) -> None:
        record = self.selected_record()
        if not record:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(json.dumps(record, ensure_ascii=False, indent=2, default=str))

    def activate_selected(self) -> None:
        record = self.selected_record()
        if record and self.activate_callback:
            self.activate_callback(record)

    def select_record(self, key: str, value: object) -> bool:
        self.predicate = None
        self.query.set("")
        self.render()
        for index, record in enumerate(self.visible):
            if record.get(key) == value:
                iid = f"row-{index}"
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                self.show_detail()
                return True
        return False


class CatalogPage:
    def __init__(
        self, parent, tk, ttk, scrolledtext, *, refresh: Callable[[], None],
        project_root: Path, notify: Callable[[str], None],
        open_action: Callable[[str, dict | None], None] | None = None,
    ):
        self.frame = ttk.Frame(parent, padding=8)
        self.tk = tk
        self.ttk = ttk
        self.scrolledtext = scrolledtext
        self.project_root = project_root
        self.notify = notify
        self.open_action = open_action
        del refresh, open_action
        self.items: list[dict] = []
        self.relations: list[dict] = []
        self.directories: list[dict] = []
        self.directory_nodes: dict[str, str] = {}
        self.items_by_directory: dict[str, list[dict]] = {}
        self.item_nodes: dict[str, str] = {}
        self.records: dict[str, tuple[str, dict]] = {}

        controls = ttk.Frame(self.frame)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Label(controls, text="按标准资料目录展开索引；折叠目录不会创建文件列表。").pack(side="left")
        ttk.Button(controls, text="打开所在位置", command=self.open_location).pack(side="right")
        ttk.Button(controls, text="复制 AI 上下文", command=self.copy_context).pack(side="right", padx=(0, 6))

        panes = ttk.Panedwindow(self.frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, padding=(0, 0, 8, 0))
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(left, weight=2)
        panes.add(right, weight=5)

        self.tree = ttk.Treeview(left, columns=("status",), show="tree headings", height=22)
        self.tree.heading("#0", text="资料目录 / 索引")
        self.tree.heading("status", text="状态")
        self.tree.column("#0", width=330, minwidth=180, stretch=True)
        self.tree.column("status", width=90, minwidth=70, stretch=False)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewOpen>>", self.expand_selected_directory)
        self.tree.bind("<<TreeviewSelect>>", self.show_selected)

        self.title = ttk.Label(right, text="选择资料目录或索引", font=("TkDefaultFont", 12, "bold"))
        self.title.pack(fill="x", anchor="w")
        self.subtitle = ttk.Label(right, text="", justify="left", wraplength=760)
        self.subtitle.pack(fill="x", pady=(4, 8))
        self.detail = scrolledtext.ScrolledText(right, wrap="word")
        self.detail.pack(fill="both", expand=True)
        self.detail.configure(state="disabled")
        self._set_detail("展开左侧标准目录后选择资料索引。Dashboard 不修改科研资料。")

    def set_data(
        self, items: list[dict], relations: list[dict], directories: list[dict] | None = None,
    ) -> None:
        opened = {
            name for name, iid in self.directory_nodes.items()
            if self.tree.exists(iid) and bool(self.tree.item(iid, "open"))
        }
        selected_item = (self.selected_item() or {}).get("item_id")
        self.items = [dict(item) for item in items]
        self.relations = [dict(relation) for relation in relations]
        self.directories = [dict(item) for item in directories or []]
        by_id = {item["item_id"]: item for item in self.items}
        for item in self.items:
            item["_relations"] = []
        for relation in self.relations:
            source = by_id.get(relation["source_id"])
            target = by_id.get(relation["target_id"])
            relation["_source_title"] = source.get("title") if source else relation["source_id"]
            relation["_target_title"] = target.get("title") if target else relation["target_id"]
            if source:
                source["_relations"].append({**relation, "_direction": "out"})
            if target:
                target["_relations"].append({**relation, "_direction": "in"})
        kind_to_name = {item.kind: item.name for item in RESOURCE_DIRECTORIES}
        self.items_by_directory = {item.name: [] for item in RESOURCE_DIRECTORIES}
        for item in self.items:
            name = kind_to_name.get(item.get("kind"), "others")
            self.items_by_directory.setdefault(name, []).append(item)
        self.records.clear()
        self.directory_nodes.clear()
        self.item_nodes.clear()
        self.tree.delete(*self.tree.get_children())
        by_name = {item.get("name"): item for item in self.directories}
        for spec in RESOURCE_DIRECTORIES:
            directory = by_name.get(spec.name, {
                "name": spec.name, "label": spec.label, "path": spec.relative_path,
                "status": "unknown", "indexed_files": 0, "actual_files": 0,
            })
            indexed = len(self.items_by_directory.get(spec.name, []))
            actual = directory.get("actual_files", 0)
            marker = "" if directory.get("status") == "ok" else " ⚠"
            iid = f"catalog-directory-{spec.name}"
            self.tree.insert(
                "", "end", iid=iid,
                text=f"{directory.get('label') or spec.label}（索引 {indexed} / 文件 {actual}）{marker}",
                values=(directory.get("status") or "未知",), open=spec.name in opened,
            )
            self.directory_nodes[spec.name] = iid
            self.records[iid] = ("directory", directory)
            if indexed:
                self.tree.insert(iid, "end", iid=f"catalog-placeholder-{spec.name}", text="正在展开…")
            if spec.name in opened:
                self._populate_directory(spec.name)
        if selected_item:
            self.select_record(selected_item)

    def _populate_directory(self, name: str) -> None:
        parent = self.directory_nodes.get(name)
        if not parent or not self.tree.exists(parent):
            return
        placeholder = f"catalog-placeholder-{name}"
        if not self.tree.exists(placeholder):
            return
        self.tree.delete(placeholder)
        for item in sorted(
            self.items_by_directory.get(name, []),
            key=lambda value: (value.get("title") or "").casefold(),
        ):
            iid = f"catalog-item-{item['item_id']}"
            self.tree.insert(parent, "end", iid=iid, text=item.get("title") or item["item_id"], values=(item.get("status"),))
            self.item_nodes[item["item_id"]] = iid
            self.records[iid] = ("item", item)

    def expand_selected_directory(self, _event=None) -> None:
        selected = self.tree.focus()
        record = self.records.get(selected)
        if record and record[0] == "directory":
            self._populate_directory(record[1].get("name", ""))

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def show_selected(self, _event=None) -> None:
        selected = self.tree.selection()
        record = self.records.get(selected[0]) if selected else None
        if not record:
            return
        kind, value = record
        if kind == "directory":
            self.title.configure(text=value.get("label") or value.get("name") or "资料目录")
            self.subtitle.configure(text=value.get("path") or "")
            self._set_detail(
                f"目录状态：{value.get('status') or '未知'}\n"
                f"已登记索引：{value.get('indexed_files', 0)}\n"
                f"实际文件：{value.get('actual_files', 0)}\n\n"
                "点击左侧加号展开资料索引。"
            )
        else:
            self.title.configure(text=value.get("title") or value.get("item_id") or "资料")
            self.subtitle.configure(text=f"{value.get('kind_label') or value.get('kind')}｜{value.get('status')}")
            self._set_detail(self.detail_text(value))

    def select_record(self, item_id: str) -> bool:
        item = next((value for value in self.items if value.get("item_id") == item_id), None)
        if not item:
            return False
        name = next((spec.name for spec in RESOURCE_DIRECTORIES if spec.kind == item.get("kind")), "others")
        parent = self.directory_nodes.get(name)
        if parent:
            self.tree.item(parent, open=True)
        self._populate_directory(name)
        iid = self.item_nodes.get(item_id)
        if not iid:
            return False
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self.show_selected()
        return True

    def selected_item(self) -> dict | None:
        selected = self.tree.selection()
        record = self.records.get(selected[0]) if selected else None
        return record[1] if record and record[0] == "item" else None

    def open_directory(self, name: str) -> None:
        directory = next((item for item in self.directories if item.get("name") == name), None)
        if directory is None:
            self.notify(f"找不到资源目录：{name}")
            return
        try:
            self.notify(open_resource_directory(self.project_root, directory["path"]))
        except DashboardError as exc:
            self.notify(str(exc))

    def open_location(self) -> None:
        item = self.selected_item()
        if not item:
            selected = self.tree.selection()
            record = self.records.get(selected[0]) if selected else None
            if record and record[0] == "directory":
                self.open_directory(record[1].get("name", ""))
                return
            self.notify("没有选中科研资料或目录。")
            return
        try:
            self.notify(reveal_catalog_file(self.project_root, item))
        except DashboardError as exc:
            self.notify(str(exc))

    def edit_selected(self) -> None:
        item = self.selected_item()
        if not item or self.open_action is None:
            self.notify("没有选中科研资料。")
            return
        self.open_action("catalog.update", {
            "item_id": item.get("item_id", ""),
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "status": item.get("status", ""),
            "path": item.get("path", ""),
            "source": item.get("source", ""),
            "tag": item.get("tags", []),
        })

    def copy_context(self) -> None:
        item = self.selected_item()
        if not item:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(render_context_markdown([item], item.get("_relations", [])))

    @staticmethod
    def detail_text(item: dict) -> str:
        relation_lines = []
        for relation in item.get("_relations", []):
            if relation["_direction"] == "out":
                relation_lines.append(
                    f"- {relation['relation_type']} → {relation['_target_title']} "
                    f"({relation['target_id']})"
                )
            else:
                relation_lines.append(
                    f"- {relation['_source_title']} ({relation['source_id']}) "
                    f"→ {relation['relation_type']}"
                )
            if relation.get("note"):
                relation_lines.append(f"  {relation['note']}")
        missing = "\n警告：项目文件当前不存在。\n" if item.get("status") == "missing" else ""
        return (
            f"ID：{item.get('item_id')}\n类型：{item.get('kind_label')}\n"
            f"状态：{item.get('status')}\n路径：{item.get('path') or '无项目文件'}\n"
            f"来源：{item.get('source') or '未记录'}\n"
            f"更新时间：{item.get('updated_at')}\n{missing}\n"
            f"摘要\n{item.get('summary') or '暂无摘要。'}\n\n"
            f"关联\n"
            + ("\n".join(relation_lines) if relation_lines else "无")
        )


class ResearchWorkbenchPage:
    def __init__(
        self,
        parent,
        tk,
        ttk,
        scrolledtext,
        *,
        notify: Callable[[str], None],
    ):
        self.frame = ttk.Frame(parent, padding=8)
        self.notify = notify
        self.external_tools: list[dict] = []
        self.records: dict[str, tuple[str, object]] = {}
        self.current_template_id: str | None = None

        panes = ttk.Panedwindow(self.frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        list_frame = ttk.Frame(panes, padding=(0, 0, 6, 0))
        detail_frame = ttk.Frame(panes, padding=(6, 0, 0, 0))
        panes.add(list_frame, weight=2)
        panes.add(detail_frame, weight=3)

        self.tree = ttk.Treeview(
            list_frame, columns=("item", "status"), show="tree headings", height=18,
        )
        self.tree.heading("#0", text="工作台")
        self.tree.heading("item", text="项目")
        self.tree.heading("status", text="状态")
        self.tree.column("#0", width=120, minwidth=85, stretch=False)
        self.tree.column("item", width=230, minwidth=140, stretch=True)
        self.tree.column("status", width=85, minwidth=70, stretch=False)
        vertical = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vertical.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.select_from_tree)

        detail_controls = ttk.Frame(detail_frame)
        detail_controls.pack(fill="x", pady=(0, 4))
        self.detail_label = ttk.Label(detail_controls, text="完整提示词")
        self.detail_label.pack(side="left")
        self.copy_button = ttk.Button(
            detail_controls, text="复制提示词", command=self.copy_current,
        )
        self.copy_button.pack(side="right")
        self.preview = scrolledtext.ScrolledText(detail_frame, wrap="word")
        self.preview.pack(fill="both", expand=True)
        self.render_tree()

    def render_tree(self) -> None:
        selected = self.tree.selection()
        previous = selected[0] if selected else None
        self.records.clear()
        self.tree.delete(*self.tree.get_children())
        builtin = self.tree.insert("", "end", iid="workbench-builtin", text=f"内置（{len(WORKBENCH_PROMPT_TEMPLATES)}）", open=True)
        categories: dict[str, str] = {}
        for template_id, template in WORKBENCH_PROMPT_TEMPLATES.items():
            category = template["category"]
            if category not in categories:
                categories[category] = self.tree.insert(
                    builtin, "end", iid=f"builtin-category-{len(categories)}", text=category, open=False,
                )
            iid = f"builtin-{template_id}"
            self.tree.insert(categories[category], "end", iid=iid, values=(template["label"], "内置"))
            self.records[iid] = ("builtin", template_id)
        external = self.tree.insert("", "end", iid="workbench-external", text=f"外置（{len(self.external_tools)}）", open=True)
        kind_labels = {
            "notes": "笔记", "literature": "文献", "computation": "计算",
            "skill": "Skill", "repository": "仓库", "other": "其他",
        }
        external_groups: dict[str, str] = {}
        for kind in EXTERNAL_TOOL_KINDS:
            matching = [item for item in self.external_tools if item.get("kind") == kind]
            if not matching:
                continue
            group = self.tree.insert(
                external, "end", iid=f"external-kind-{kind}",
                text=f"{kind_labels[kind]}（{len(matching)}）", open=False,
            )
            external_groups[kind] = group
            for item in matching:
                iid = f"external-{item['tool_id']}"
                self.tree.insert(group, "end", iid=iid, values=(item["name"], item["status"]))
                self.records[iid] = ("external", item)
        target = previous if previous in self.records else "builtin-literature_review"
        if target in self.records:
            self.tree.selection_set(target)
            self.tree.focus(target)
            parent = self.tree.parent(target)
            if parent:
                self.tree.item(parent, open=True)
            self.tree.see(target)
            self.select_from_tree()

    def set_external_tools(self, tools: list[dict]) -> None:
        revision = json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str)
        if getattr(self, "external_revision", None) == revision:
            return
        self.external_revision = revision
        self.external_tools = list(tools)
        self.render_tree()

    def select_from_tree(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            self.current_template_id = None
            self._set_preview("请选择左侧提示词。")
            return
        record = self.records.get(selection[0])
        if record is None:
            self.current_template_id = None
            self._set_preview("请选择左侧内置提示词或外置工具。")
            return
        kind, value = record
        if kind == "builtin":
            self.current_template_id = str(value)
            self.detail_label.configure(text="完整提示词")
            self.copy_button.configure(state="normal")
            self.render_detail()
        else:
            self.current_template_id = None
            item = dict(value)
            self.detail_label.configure(text="外置工具说明")
            self.copy_button.configure(state="disabled")
            status_note = f"\n状态说明：{item.get('status_note')}" if item.get("status_note") else ""
            self._set_preview(
                f"名称：{item.get('name')}\n工具 ID：{item.get('tool_id')}\n"
                f"类型：{item.get('kind')}\n状态：{item.get('status')}\n"
                f"更新时间：{item.get('updated_at') or '未知'}{status_note}\n\n"
                f"用途\n{item.get('purpose') or '未记录'}\n\n"
                f"使用提示\n{item.get('usage_hint') or '未记录'}\n\n"
                f"参考链接或标识\n{item.get('reference') or '未记录'}\n\n"
                "此处仅作项目提醒；Dashboard 不检测安装、不启动程序、不执行脚本，也不联网验证。"
            )

    def render_detail(self) -> None:
        if self.current_template_id is None:
            self._set_preview("请选择左侧提示词。")
            return
        try:
            prompt = build_research_prompt(self.current_template_id)
        except DashboardError as exc:
            self.current_template_id = None
            self._set_preview(str(exc))
            self.notify(str(exc))
            return
        self._set_preview(prompt)

    def _set_preview(self, text: str) -> None:
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)

    def copy_current(self) -> None:
        if self.current_template_id is None:
            self.notify("当前没有选中的科研提示词。")
            return
        prompt = self.preview.get("1.0", "end-1c")
        if not prompt.strip():
            self.notify("当前没有可复制的科研提示词。")
            return
        self.notify(copy_research_prompt(self.frame, prompt))


class TaskPage:
    def __init__(self, parent, tk, ttk, scrolledtext, *, open_related: Callable[[dict], None]):
        self.frame = ttk.Frame(parent, padding=8)
        self.tasks: dict[str, dict] = {}
        self.visible: list[dict] = []
        self.current_task_id: str | None = None
        self.related_records: list[dict] = []

        controls = ttk.Frame(self.frame)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Label(controls, text="筛选任务").pack(side="left")
        self.query = tk.StringVar()
        ttk.Entry(controls, textvariable=self.query, width=34).pack(side="left", padx=(6, 8))
        self.query.trace_add("write", lambda *_: self.render_list())
        ttk.Button(controls, text="复制摘要", command=self.copy_summary).pack(side="left")

        panes = ttk.Panedwindow(self.frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        list_frame = ttk.Frame(panes, padding=(0, 0, 6, 0))
        detail_frame = ttk.Frame(panes, padding=(6, 0, 0, 0))
        panes.add(list_frame, weight=2)
        panes.add(detail_frame, weight=3)

        self.tree = ttk.Treeview(
            list_frame, columns=("occurred_at", "goal", "result", "status"),
            show="headings", height=18,
        )
        for key, label, width in (("occurred_at", "时间", 135), ("goal", "任务", 300),
                                  ("result", "结果", 75), ("status", "状态", 85)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=65, stretch=True)
        vertical = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.select_from_tree)

        ttk.Label(detail_frame, text="任务摘要").pack(anchor="w")
        self.summary = scrolledtext.ScrolledText(detail_frame, height=15, wrap="word")
        self.summary.pack(fill="both", expand=True, pady=(3, 8))
        self.summary.configure(state="disabled")

        related_controls = ttk.Frame(detail_frame)
        related_controls.pack(fill="x", pady=(0, 3))
        ttk.Label(related_controls, text="关联记录").pack(side="left")
        ttk.Button(related_controls, text="打开选中", command=lambda: self.activate_related(open_related)).pack(side="right")
        self.related = ttk.Treeview(
            detail_frame, columns=("kind_label", "occurred_at", "title"), show="headings", height=8,
        )
        for key, label, width in (("kind_label", "类型", 70), ("occurred_at", "时间", 165), ("title", "标题", 330)):
            self.related.heading(key, text=label)
            self.related.column(key, width=width, minwidth=65, stretch=True)
        related_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.related.yview)
        self.related.configure(yscrollcommand=related_scroll.set)
        self.related.pack(side="left", fill="both", expand=True)
        related_scroll.pack(side="right", fill="y")
        self.related.bind("<Double-1>", lambda _event: self.activate_related(open_related))
        self._set_summary("选择左侧任务即可查看完整摘要和关联记录。")

    def set_tasks(self, tasks: dict[str, dict]) -> None:
        previous = self.current_task_id
        self.tasks = dict(tasks)
        if previous and previous not in self.tasks:
            self.current_task_id = None
            self._set_summary(f"任务 {previous} 已不在当前数据中，可能已切换分支或记录被过滤。")
            self.set_related([])
        self.render_list()
        if self.current_task_id:
            self.render_detail()
        elif self.visible:
            self.open_task(self.visible[0]["task_id"])

    def set_predicate(self, task_ids: set[str] | None) -> None:
        self.task_subset = task_ids
        if task_ids is not None and self.current_task_id not in task_ids:
            self.current_task_id = None
        self.render_list()
        if self.current_task_id:
            self.render_detail()
        elif self.visible:
            self.current_task_id = self.visible[0]["task_id"]
            self.render_list()
            self.render_detail()

    def render_list(self) -> None:
        subset = getattr(self, "task_subset", None)
        records = [
            item for item in self.tasks.values()
            if not item.get("is_auxiliary")
            and (subset is None or item["task_id"] in subset)
        ]
        records = filter_records(records, self.query.get())
        self.visible = sorted(records, key=lambda item: (item.get("started_at", ""), item["task_id"]), reverse=True)
        self.tree.delete(*self.tree.get_children())
        selected_iid = None
        for index, task in enumerate(self.visible):
            iid = f"task-{index}"
            occurred_at = task.get("finished_at") or task.get("started_at")
            self.tree.insert("", "end", iid=iid, values=(
                short(occurred_at, 16), short(task.get("goal"), 65),
                result_label(task.get("result")), task.get("status") or "未知",
            ))
            if task["task_id"] == self.current_task_id:
                selected_iid = iid
        if selected_iid:
            self.tree.selection_set(selected_iid)
            self.tree.focus(selected_iid)
            self.tree.see(selected_iid)

    def select_from_tree(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        try:
            task = self.visible[int(selection[0].split("-", 1)[1])]
        except (IndexError, ValueError):
            return
        self.current_task_id = task["task_id"]
        self.render_detail()

    def open_task(self, task_id: str) -> bool:
        if task_id not in self.tasks:
            self.current_task_id = None
            self.set_related([])
            self._set_summary(f"找不到任务 {task_id}。")
            return False
        self.task_subset = None
        self.query.set("")
        self.current_task_id = task_id
        self.render_list()
        self.render_detail()
        return True

    def render_detail(self) -> None:
        task = self.tasks.get(self.current_task_id or "")
        if not task:
            return
        acceptance = task.get("acceptance") or "未记录"
        if isinstance(acceptance, list):
            acceptance = "\n".join(f"- {item}" for item in acceptance) or "未记录"
        evidence = "\n".join(f"- {item}" for item in task.get("evidence", [])) or "无"
        self._set_summary(
            f"任务：{task['task_id']}\n分支：{task.get('branch') or '未知'}\n"
            f"开始：{task.get('started_at') or '未知'}\n结束：{task.get('finished_at') or '进行中'}\n"
            f"结果：{result_label(task.get('result'))}　状态：{task.get('status') or '未知'}　"
            f"路线：{task.get('route') or '未记录'}\n\n"
            f"目标\n{task.get('goal') or '未记录'}\n\n验收条件\n{acceptance}\n\n"
            f"结论\n{task.get('conclusion') or '未记录'}\n\n证据\n{evidence}"
        )
        self.set_related(task.get("related", []))

    def set_related(self, records: list[dict]) -> None:
        selected_key = record_identity(self.selected_related())
        self.related_records = list(records)
        self.related.delete(*self.related.get_children())
        selected_iid = None
        for index, record in enumerate(self.related_records):
            iid = f"related-{index}"
            self.related.insert("", "end", iid=iid, values=(
                record.get("kind_label"), short(record.get("occurred_at"), 19), short(record.get("title"), 65),
            ))
            if record_identity(record) == selected_key:
                selected_iid = iid
        if selected_iid:
            self.related.selection_set(selected_iid)
        elif self.related_records:
            self.related.selection_set("related-0")

    def selected_related(self) -> dict | None:
        selection = self.related.selection()
        if not selection:
            return None
        try:
            return self.related_records[int(selection[0].split("-", 1)[1])]
        except (IndexError, ValueError):
            return None

    def activate_related(self, callback: Callable[[dict], None]) -> None:
        record = self.selected_related()
        if record:
            callback(record)

    def _set_summary(self, text: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)
        self.summary.configure(state="disabled")

    def copy_summary(self) -> None:
        if not self.current_task_id:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(self.summary.get("1.0", "end-1c"))


class AdvancedWindow:
    def __init__(self, root, tk, ttk, scrolledtext, *, open_task: Callable[[dict], None],
                 on_close: Callable[[], None]):
        self.window = tk.Toplevel(root)
        self.window.title("高级查看")
        self.window.geometry("1040x680")
        self.window.minsize(760, 480)
        self.on_close = on_close
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.health = ttk.Label(self.window, text="正在加载技术信息…", padding=(10, 8), justify="left")
        self.health.pack(fill="x")
        self.software = ttk.Label(
            self.window,
            text=software_delivery_status_text(None),
            padding=(10, 0, 10, 8),
            justify="left",
            wraplength=1000,
        )
        self.software.pack(fill="x")
        self.events = TablePage(
            self.window, tk, ttk, scrolledtext,
            columns=[("occurred_at", "时间", 180), ("event_type", "事件类型", 190),
                     ("branch", "分支", 140), ("task_id", "任务 ID", 220), ("event_id", "事件 ID", 300)],
            detail=lambda record: json.dumps(record, ensure_ascii=False, indent=2, default=str),
            refresh=lambda: None, activate=open_task, activate_label="打开关联任务",
        )
        self.events.frame.pack(fill="both", expand=True)

    def set_snapshot(self, snapshot: dict) -> None:
        self.health.configure(text=advanced_summary(snapshot))
        self.events.set_records(snapshot.get("events", []))

    def set_software_delivery(self, report: dict | None) -> None:
        self.software.configure(text=software_delivery_status_text(report))

    def select_event(self, event_id: str) -> bool:
        return self.events.select_record("event_id", event_id)

    def focus(self) -> None:
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self) -> None:
        if self.window.winfo_exists():
            self.window.destroy()
        self.on_close()


class StagePage:
    def __init__(
        self, parent, tk, ttk, scrolledtext, *, open_task: Callable[[str], None],
        open_action: Callable[[str, dict | None], None] | None = None,
    ):
        self.tk, self.ttk = tk, ttk
        self.frame = ttk.Frame(parent, padding=8)
        self.open_task_callback = open_task
        self.stages: dict[str, dict] = {}
        self.attempts: dict[str, dict] = {}

        current_frame = ttk.LabelFrame(self.frame, text="当前阶段", padding=6)
        current_frame.pack(fill="x", pady=(0, 8))
        if open_action:
            controls = ttk.Frame(current_frame)
            controls.pack(fill="x", pady=(0, 4))
            ttk.Button(controls, text="更新当前阶段", command=lambda: open_action("stage.update", None)).pack(side="left")
            ttk.Button(controls, text="开始新阶段", command=lambda: open_action("stage.start", None)).pack(side="left", padx=(6, 0))
        self.current = scrolledtext.ScrolledText(current_frame, height=9, wrap="word")
        self.current.pack(fill="x")
        self.current.configure(state="disabled")

        attempts_frame = ttk.LabelFrame(self.frame, text="当前探索", padding=6)
        attempts_frame.pack(fill="both", expand=True, pady=(0, 8))
        attempt_columns = ("branch", "track", "goal", "current_step", "next_step", "state", "updated_at")
        self.attempt_tree = ttk.Treeview(attempts_frame, columns=attempt_columns, show="headings", height=6)
        for key, label, width in (
            ("branch", "分支", 160), ("track", "类型", 80), ("goal", "目标", 220),
            ("current_step", "当前步骤", 180), ("next_step", "下一步", 180),
            ("state", "状态", 80), ("updated_at", "更新时间", 160),
        ):
            self.attempt_tree.heading(key, text=label)
            self.attempt_tree.column(key, width=width, minwidth=60)
        attempt_scroll = ttk.Scrollbar(attempts_frame, orient="vertical", command=self.attempt_tree.yview)
        self.attempt_tree.configure(yscrollcommand=attempt_scroll.set)
        self.attempt_tree.pack(side="left", fill="both", expand=True)
        attempt_scroll.pack(side="right", fill="y")
        self.attempt_tree.bind("<Double-1>", self.open_selected_attempt)

        history_frame = ttk.LabelFrame(self.frame, text="阶段历史", padding=6)
        history_frame.pack(fill="both", expand=True)
        history_body = ttk.Frame(history_frame)
        history_body.pack(fill="both", expand=True)
        stage_columns = ("sequence", "title", "status", "started_at", "finished_at")
        self.stage_tree = ttk.Treeview(history_body, columns=stage_columns, show="headings", height=7)
        for key, label, width in (
            ("sequence", "序号", 55), ("title", "阶段", 240), ("status", "状态", 85),
            ("started_at", "开始", 165), ("finished_at", "结束", 165),
        ):
            self.stage_tree.heading(key, text=label)
            self.stage_tree.column(key, width=width, minwidth=50)
        self.stage_tree.pack(side="left", fill="both", expand=True)
        self.stage_tree.bind("<<TreeviewSelect>>", self.show_selected_stage)
        self.stage_detail = scrolledtext.ScrolledText(history_body, width=52, wrap="word")
        self.stage_detail.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.stage_detail.configure(state="disabled")

    @staticmethod
    def stage_text(
        stage: dict | None,
        attempts: list[dict] | None = None,
        freshness_warning: dict | None = None,
    ) -> str:
        if not stage:
            return "当前没有 active 阶段。\n请在 main 的 stable 活动任务中运行 stage start。"
        acceptance = "\n".join(f"- {item}" for item in stage.get("acceptance", [])) or "- 未设置"
        evidence = "\n".join(f"- {item}" for item in stage.get("evidence", [])) or "- 无"
        related = [item for item in (attempts or []) if item.get("stage_id") == stage.get("stage_id")]
        branches = "\n".join(
            f"- {item['branch']}｜{item['state']}｜{item.get('current_step') or '未记录当前步骤'}"
            for item in related
        ) or "- 无"
        warning_text = (
            f"\n\n提醒\n- {freshness_warning['message']}\n"
            f"- 阶段最后更新：{freshness_warning.get('stage_updated_at') or '未知'}"
            if freshness_warning else ""
        )
        return (
            f"{stage.get('sequence')}. {stage.get('title')}（{stage.get('status')}）\n"
            f"目标：{stage.get('goal')}\n进展：{stage.get('summary') or '未设置'}\n"
            f"当前步骤：{stage.get('current_step') or '未设置'}\n"
            f"下一步：{stage.get('next_step') or '未设置'}\n"
            f"阻塞：{stage.get('blocker') or '无。'}\n\n验收条件\n{acceptance}\n\n"
            f"证据\n{evidence}\n\n关联探索\n{branches}{warning_text}"
        )

    def set_data(self, context: dict) -> None:
        stages = context.get("stages") or []
        attempts = context.get("attempts") or context.get("active_attempts") or []
        self.stages = {item["stage_id"]: item for item in stages}
        self.attempts = {item["attempt_id"]: item for item in attempts}
        self.current.configure(state="normal")
        self.current.delete("1.0", "end")
        self.current.insert(
            "1.0",
            self.stage_text(
                context.get("current_stage"), attempts,
                context.get("stage_freshness_warning"),
            ),
        )
        self.current.configure(state="disabled")

        for item in self.attempt_tree.get_children():
            self.attempt_tree.delete(item)
        for attempt in context.get("active_attempts") or []:
            self.attempt_tree.insert("", "end", iid=attempt["attempt_id"], values=(
                attempt["branch"], attempt["track"], short(attempt.get("goal"), 50),
                short(attempt.get("current_step") or "未设置", 40),
                short(attempt.get("next_step") or "未设置", 40), attempt["state"], attempt["updated_at"],
            ))

        selected = self.stage_tree.selection()
        selected_id = selected[0] if selected else None
        for item in self.stage_tree.get_children():
            self.stage_tree.delete(item)
        for stage in stages:
            self.stage_tree.insert("", "end", iid=stage["stage_id"], values=(
                stage["sequence"], stage["title"], stage["status"],
                stage["started_at"], stage.get("finished_at") or "—",
            ))
        if selected_id in self.stages:
            self.stage_tree.selection_set(selected_id)
        elif stages:
            self.stage_tree.selection_set(stages[0]["stage_id"])
        self.show_selected_stage()

    def show_selected_stage(self, _event=None) -> None:
        selected = self.stage_tree.selection()
        stage = self.stages.get(selected[0]) if selected else None
        self.stage_detail.configure(state="normal")
        self.stage_detail.delete("1.0", "end")
        self.stage_detail.insert("1.0", self.stage_text(stage, list(self.attempts.values())))
        self.stage_detail.configure(state="disabled")

    def open_selected_attempt(self, _event=None) -> None:
        selected = self.attempt_tree.selection()
        if selected:
            self.open_task_callback(selected[0])


class ActionCenterPage:
    """Generic form UI backed by the shared workflow action registry."""

    def __init__(
        self,
        parent,
        tk,
        ttk,
        scrolledtext,
        *,
        service: WorkflowActionService | None,
        notify: Callable[[str], None],
        refresh: Callable[[], None],
        progress: Callable[[ActionProgress], None],
    ):
        self.tk, self.ttk = tk, ttk
        self.service = service
        self.notify = notify
        self.refresh_callback = refresh
        self.progress_callback = progress
        self.frame = ttk.Frame(parent, padding=8)
        self.specs = action_specs()
        self.current_action_id: str | None = None
        self.widgets: dict[str, tuple[object, object]] = {}
        self.current_token: str | None = None
        self.running = False
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        panes = ttk.Panedwindow(self.frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, padding=(0, 0, 6, 0))
        right = ttk.Frame(panes, padding=(6, 0, 0, 0))
        panes.add(left, weight=2)
        panes.add(right, weight=4)

        self.tree = ttk.Treeview(left, columns=("category", "label", "status"), show="headings", height=24)
        for key, label, width in (
            ("category", "分类", 100), ("label", "动作", 190), ("status", "状态", 90),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=65)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.select_action)

        self.title = ttk.Label(right, text="选择左侧动作", font=("TkDefaultFont", 11, "bold"))
        self.title.pack(anchor="w")
        self.description = ttk.Label(right, text="", wraplength=620, justify="left")
        self.description.pack(fill="x", pady=(3, 5))
        self.reason = scrolledtext.ScrolledText(right, height=5, wrap="word")
        self.reason.pack(fill="x", pady=(0, 6))
        self.reason.configure(state="disabled")

        form_outer = ttk.Frame(right)
        form_outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(form_outer, highlightthickness=0)
        form_scroll = ttk.Scrollbar(form_outer, orient="vertical", command=self.canvas.yview)
        self.form = ttk.Frame(self.canvas)
        self.form_window = self.canvas.create_window((0, 0), window=self.form, anchor="nw")
        self.canvas.configure(yscrollcommand=form_scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        form_scroll.pack(side="right", fill="y")
        self.form.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.form_window, width=event.width))

        controls = ttk.Frame(right)
        controls.pack(fill="x", pady=(8, 0))
        self.copy_button = ttk.Button(controls, text="复制 AI 填写模板", command=self.copy_ai_template)
        self.copy_button.pack(side="left")
        self.paste_button = ttk.Button(controls, text="从剪贴板填入 AI JSON", command=self.paste_ai_json)
        self.paste_button.pack(side="left", padx=(6, 0))
        ttk.Button(controls, text="重新预检", command=self.refresh_availability).pack(side="left", padx=(6, 0))
        self.run_button = ttk.Button(controls, text="执行动作", command=self.execute_current)
        self.run_button.pack(side="right")

        if service is None:
            self._set_reason("当前 Dashboard 处于仅查看模式，动作服务不可用。")
            self.run_button.configure(state="disabled")
        else:
            self.refresh_actions()

    def _set_reason(self, text: str) -> None:
        self.reason.configure(state="normal")
        self.reason.delete("1.0", "end")
        self.reason.insert("1.0", text)
        self.reason.configure(state="disabled")

    def refresh_actions(self) -> None:
        if self.service is None:
            return
        selected = self.current_action_id
        self.tree.delete(*self.tree.get_children())
        for spec in self.specs:
            availability = self.service.availability(spec.action_id)
            status = "可执行" if availability.enabled else f"阻塞 {len(availability.blockers)}"
            self.tree.insert("", "end", iid=spec.action_id, values=(spec.category, spec.label, status))
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
        elif self.specs:
            self.tree.selection_set(self.specs[0].action_id)
            self.tree.focus(self.specs[0].action_id)
            self.select_action()

    def select_action(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        self.current_action_id = selected[0]
        spec = next(item for item in self.specs if item.action_id == self.current_action_id)
        self.title.configure(text=spec.label)
        self.description.configure(text=spec.description or f"动作 ID：{spec.action_id}")
        self._build_form(spec)
        self.refresh_availability()

    def _build_form(self, spec) -> None:
        for child in self.form.winfo_children():
            child.destroy()
        self.widgets = {}
        for row, field_spec in enumerate(spec.fields):
            label = field_spec.label + (" *" if field_spec.required else "")
            self.ttk.Label(self.form, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=3)
            if field_spec.kind == "bool":
                variable = self.tk.BooleanVar(value=bool(field_spec.default))
                widget = self.ttk.Checkbutton(self.form, variable=variable)
                widget.grid(row=row, column=1, sticky="w", pady=3)
            elif field_spec.kind == "choice":
                variable = self.tk.StringVar(value=str(field_spec.default or ""))
                widget = self.ttk.Combobox(
                    self.form, textvariable=variable, values=field_spec.choices,
                    state="readonly", width=48,
                )
                widget.grid(row=row, column=1, sticky="ew", pady=3)
            elif field_spec.kind in {"multiline", "list"}:
                variable = None
                widget = self.tk.Text(self.form, height=3, wrap="word")
                if field_spec.default:
                    widget.insert("1.0", str(field_spec.default))
                widget.grid(row=row, column=1, sticky="ew", pady=3)
            else:
                variable = self.tk.StringVar(value=str(field_spec.default or ""))
                widget = self.ttk.Entry(self.form, textvariable=variable, width=60)
                widget.grid(row=row, column=1, sticky="ew", pady=3)
            self.widgets[field_spec.name] = (field_spec, variable or widget)
        self.form.columnconfigure(1, weight=1)
        self._prefill_context(spec.action_id)

    def _prefill_context(self, action_id: str) -> None:
        if self.service is None:
            return
        try:
            state = self.service.snapshot()
        except Exception:
            return
        values: dict[str, object] = {}
        active = state.get("active_task") or {}
        if action_id == "task.start":
            values["track"] = (state.get("classification") or {}).get("track") or "stable"
        if action_id == "stage.update":
            try:
                full = self.service.project_root
                del full
            except Exception:
                pass
        if action_id == "task.finish":
            values["note"] = active.get("scope") or ""
        for name, value in values.items():
            self._set_field(name, value)

    def open_action(self, action_id: str, preset: dict | None = None) -> None:
        if not self.tree.exists(action_id):
            self.notify(f"找不到动作：{action_id}")
            return
        self.tree.selection_set(action_id)
        self.tree.focus(action_id)
        self.tree.see(action_id)
        self.select_action()
        for name, value in (preset or {}).items():
            self._set_field(name, value)
        self.refresh_availability()

    def _set_field(self, name: str, value: object) -> None:
        pair = self.widgets.get(name)
        if pair is None:
            return
        field_spec, target = pair
        if field_spec.kind in {"multiline", "list"}:
            target.delete("1.0", "end")
            if isinstance(value, list):
                target.insert("1.0", "\n".join(str(item) for item in value))
            else:
                target.insert("1.0", str(value or ""))
        else:
            target.set(value)

    def fields(self) -> dict[str, object]:
        values: dict[str, object] = {}
        for name, (field_spec, source) in self.widgets.items():
            if field_spec.kind in {"multiline", "list"}:
                values[name] = source.get("1.0", "end-1c")
            else:
                values[name] = source.get()
        return values

    def refresh_availability(self) -> None:
        if self.service is None or not self.current_action_id:
            return
        try:
            try:
                fields = self.service.validate_fields(self.current_action_id, self.fields())
            except ActionProtocolError:
                fields = None
            availability = self.service.availability(self.current_action_id, fields)
            self.current_token = availability.state_token
            lines = []
            if availability.blockers:
                lines.append("当前不可执行：")
                lines.extend(
                    f"- [{item.code}] {item.message}" + (f"\n  证据：{item.evidence}" if item.evidence else "")
                    for item in availability.blockers
                )
            else:
                lines.append("安全预检：通过。填写必填字段后可以执行。")
            if availability.warnings:
                lines.append("\n提醒：")
                lines.extend(f"- [{item.code}] {item.message}" for item in availability.warnings)
            self._set_reason("\n".join(lines))
            self.run_button.configure(state="normal" if availability.enabled and not self.running else "disabled")
        except Exception as exc:
            self._set_reason(f"无法完成预检：{exc}")
            self.run_button.configure(state="disabled")

    def copy_ai_template(self) -> None:
        if not self.current_action_id:
            return
        text = ai_form_template(self.current_action_id)
        self.frame.clipboard_clear()
        self.frame.clipboard_append(text)
        self.notify("AI 填写模板已复制；AI 只能填写允许的自然语言字段。")

    def paste_ai_json(self) -> None:
        if not self.current_action_id:
            return
        try:
            text = self.frame.clipboard_get()
            values = parse_ai_form(text, self.current_action_id)
            for name, value in values.items():
                self._set_field(name, value)
            self.refresh_availability()
            self.notify("AI JSON 已填入表单，尚未执行；请逐项检查。")
        except Exception as exc:
            self.notify(f"无法导入 AI JSON：{exc}")

    def execute_current(self) -> None:
        if self.service is None or not self.current_action_id or self.running:
            return
        try:
            values = self.service.validate_fields(self.current_action_id, self.fields())
            availability = self.service.availability(self.current_action_id, values, force=True)
            if not availability.enabled:
                self.refresh_availability()
                self.notify("动作当前不可执行，请查看原因面板。")
                return
            if self.current_token and self.current_token != availability.state_token:
                self.current_token = availability.state_token
                self.refresh_availability()
                self.notify("项目状态已经变化；草稿已保留，请检查后再次执行。")
                return
            confirmed = availability.confirmation_level != "high"
            if availability.confirmation_level == "high":
                from tkinter import messagebox
                spec = next(item for item in self.specs if item.action_id == self.current_action_id)
                confirmed = messagebox.askyesno(
                    "确认高风险动作",
                    f"确认执行“{spec.label}”吗？\n\n{spec.description}\n\n该动作会再次获得写锁并重新预检。",
                    parent=self.frame,
                )
            if not confirmed:
                self.notify("已取消动作。")
                return
            request = ActionRequest(self.current_action_id, values, availability.state_token, confirmed)
        except Exception as exc:
            self.notify(f"表单校验失败：{exc}")
            return

        self.running = True
        self.run_button.configure(state="disabled")

        def worker() -> None:
            assert self.service is not None
            result = self.service.execute(request, progress=self._thread_progress)
            self.worker_queue.put(("result", result))

        threading.Thread(target=worker, daemon=True).start()
        self.frame.after(50, self._poll_worker_queue)

    def _thread_progress(self, item: ActionProgress) -> None:
        self.worker_queue.put(("progress", item))

    def _poll_worker_queue(self) -> None:
        result = None
        while True:
            try:
                kind, value = self.worker_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                self.progress_callback(value)
            elif kind == "result":
                result = value
        if result is None:
            if self.running and self.frame.winfo_exists():
                self.frame.after(50, self._poll_worker_queue)
            return
        self.running = False
        if result.status == "success":
            self.notify(result.summary)
            self.refresh_callback()
            self.refresh_actions()
        elif result.status == "blocked":
            self.notify(f"无法执行 [{result.code}]：{result.summary}")
            self.refresh_availability()
        else:
            self.notify(result.summary)
            self.refresh_availability()


class WorkflowPage:
    """Read-only, expandable navigator for the complete user-facing workflow."""

    STATUS_LABELS = {
        "available": "可执行",
        "needs_input": "等待 AI 文本",
        "blocked": "暂不可用",
        "running": "执行中",
    }

    def __init__(self, parent, tk, ttk, scrolledtext):
        self.tk, self.ttk = tk, ttk
        self.frame = ttk.Frame(parent, padding=8)
        self.records: dict[str, tuple[str, dict | object]] = {}
        self.snapshot: dict | None = None
        self.matrix = None
        self.revision: tuple | None = None

        panes = ttk.Panedwindow(self.frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, padding=(0, 0, 8, 0))
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(left, weight=2)
        panes.add(right, weight=5)

        self.tree = ttk.Treeview(
            left, columns=("item", "status"), show="tree headings", height=24,
        )
        self.tree.heading("#0", text="工作流")
        self.tree.heading("item", text="项目")
        self.tree.heading("status", text="状态")
        self.tree.column("#0", width=105, minwidth=80, stretch=False)
        self.tree.column("item", width=255, minwidth=150, stretch=True)
        self.tree.column("status", width=92, minwidth=75, stretch=False)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.select_item)

        self.title = ttk.Label(right, text="正在读取当前工作…", font=("TkDefaultFont", 12, "bold"))
        self.title.pack(fill="x", anchor="w")
        self.subtitle = ttk.Label(right, text="", justify="left", wraplength=760)
        self.subtitle.pack(fill="x", pady=(4, 8))
        self.detail = scrolledtext.ScrolledText(right, wrap="word")
        self.detail.pack(fill="both", expand=True)
        self.detail.configure(state="disabled")
        self._set_detail("Dashboard 仅观察工作流；任务操作由 AI 按项目内置规则执行。")

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    @staticmethod
    def _task_text(task: dict) -> str:
        acceptance = task.get("acceptance") or []
        if isinstance(acceptance, str):
            acceptance = [acceptance]
        evidence = task.get("evidence") or []
        return (
            f"任务 ID：{task.get('task_id') or '未知'}\n"
            f"分支：{task.get('branch') or '未知'}\n"
            f"开始：{task.get('started_at') or '未知'}\n"
            f"结束：{task.get('finished_at') or '进行中'}\n"
            f"状态：{task.get('status') or ('已完成' if task.get('finished_at') else '活动中')}\n"
            f"结果：{result_label(task.get('result'))}\n\n"
            f"目标\n{task.get('goal') or '未记录'}\n\n"
            "验收条件\n" + ("\n".join(f"- {item}" for item in acceptance) or "- 未记录") +
            "\n\n结论\n" + (task.get("conclusion") or "未记录") +
            "\n\n证据\n" + ("\n".join(f"- {item}" for item in evidence) or "- 无")
        )

    @staticmethod
    def _stage_text(stage: dict, attempts: list[dict]) -> str:
        acceptance = "\n".join(f"- {item}" for item in stage.get("acceptance", [])) or "- 未设置"
        evidence = "\n".join(f"- {item}" for item in stage.get("evidence", [])) or "- 无"
        related = [item for item in attempts if item.get("stage_id") == stage.get("stage_id")]
        related_text = "\n".join(
            f"- {item.get('branch')}｜{item.get('state')}｜{item.get('current_step') or '未记录当前步骤'}"
            for item in related
        ) or "- 无"
        return (
            f"阶段 ID：{stage.get('stage_id')}\n状态：{stage.get('status')}\n"
            f"开始：{stage.get('started_at') or '未知'}\n更新：{stage.get('updated_at') or '未知'}\n\n"
            f"目标\n{stage.get('goal') or '未设置'}\n\n"
            f"进展\n{stage.get('summary') or '未设置'}\n\n"
            f"当前步骤\n{stage.get('current_step') or '未设置'}\n\n"
            f"下一步\n{stage.get('next_step') or '未设置'}\n\n"
            f"阻塞\n{stage.get('blocker') or '无'}\n\n"
            f"验收条件\n{acceptance}\n\n证据\n{evidence}\n\n关联探索\n{related_text}"
        )

    def _action_text(self, action) -> str:
        spec = next(item for item in action_specs() if item.action_id == action.action_id)
        availability = action.availability
        lines = [
            f"动作 ID：{action.action_id}",
            f"状态：{self.STATUS_LABELS.get(action.status, action.status)}",
            f"类别：{action.category}",
            "",
            spec.description or "无额外说明。",
        ]
        if action.missing_fields:
            lines.extend(["", "需要 AI 根据用户意图补充："])
            lines.extend(f"- {item}" for item in action.missing_fields)
        if availability.blockers:
            lines.extend(["", "当前阻塞："])
            for blocker in availability.blockers:
                lines.append(f"- [{blocker.code}] {blocker.message}")
                if blocker.evidence:
                    lines.append(f"  证据：{blocker.evidence}")
                if blocker.next_action:
                    lines.append(f"  安全下一步：{blocker.next_action}")
        if availability.warnings:
            lines.extend(["", "提醒："])
            lines.extend(f"- [{item.code}] {item.message}" for item in availability.warnings)
        if not availability.blockers:
            lines.extend(["", "该动作由 AI 通过结构化工作流服务执行；Dashboard 不写入业务数据。"])
        return "\n".join(lines)

    @staticmethod
    def _decision_text(decision: dict) -> str:
        return (
            f"决策 ID：{decision.get('decision_id') or '未知'}\n"
            f"时间：{decision.get('occurred_at') or '未知'}\n"
            f"分支：{decision.get('branch') or '未知'}\n\n"
            f"决策\n{decision.get('decision') or '未记录'}\n\n"
            f"替代方案\n{decision.get('alternatives') or '未记录'}\n\n"
            f"依据\n{decision.get('basis') or '未记录'}\n\n"
            f"重开条件\n{decision.get('reopen_condition') or '未记录'}"
        )

    def set_data(self, snapshot: dict, matrix) -> None:
        context = snapshot.get("context") or {}
        latest_event = (snapshot.get("events") or [{}])[-1].get("event_id") if snapshot.get("events") else None
        revision = (
            matrix.state_token,
            latest_event,
            len(snapshot.get("task_details") or {}),
            tuple((item.get("stage_id"), item.get("updated_at")) for item in context.get("stages") or []),
            tuple(item.get("event_id") for item in snapshot.get("decisions") or []),
            tuple(item.get("event_id") for item in snapshot.get("explorations") or []),
        )
        if revision == self.revision:
            return
        self.revision = revision
        self.snapshot, self.matrix = snapshot, matrix
        selected = self.tree.selection()
        selected_id = selected[0] if selected else None
        self.records.clear()
        self.tree.delete(*self.tree.get_children())

        active = context.get("active_task") or {}
        task_details = snapshot.get("task_details") or {}
        current_task = task_details.get(active.get("task_id")) or active

        task_count = sum(
            1 for item in task_details.values() if not item.get("is_auxiliary")
        )
        if current_task and active.get("task_id") not in task_details:
            task_count += 1
        task_group = self.tree.insert(
            "", "end", iid="group-tasks", text=f"任务（{task_count}）", open=True,
        )
        if current_task:
            iid = "current-task"
            self.tree.insert(task_group, "end", iid=iid, text="当前", values=(
                current_task.get("goal") or current_task.get("task_id"), "活动中",
            ))
            self.records[iid] = ("task", current_task)
        active_id = active.get("task_id")
        historical_tasks = [
            (task_id, task) for task_id, task in sorted(
                task_details.items(), key=lambda item: item[1].get("started_at") or "", reverse=True,
            )
            if task_id != active_id and not task.get("is_auxiliary")
        ]
        task_history_group = self.tree.insert(
            task_group, "end", iid="group-task-history",
            text=f"历史任务（{len(historical_tasks)}）", open=False,
        )
        for task_id, task in historical_tasks:
            iid = f"history-task-{task_id}"
            self.tree.insert(task_history_group, "end", iid=iid, values=(
                task.get("goal") or task_id, result_label(task.get("result")),
            ))
            self.records[iid] = ("task", task)

        action_counts = {
            status: sum(1 for action in matrix.actions if action.status == status)
            for status in self.STATUS_LABELS
        }
        action_group = self.tree.insert(
            "", "end", iid="group-actions",
            text=f"动作（可执行 {action_counts['available']} / 阻塞 {action_counts['blocked']}）",
            open=True,
        )
        action_parents = {}
        for status in ("available", "needs_input", "running", "blocked"):
            action_parents[status] = self.tree.insert(
                action_group, "end", iid=f"group-actions-{status}",
                text=f"{self.STATUS_LABELS[status]}（{action_counts[status]}）", open=False,
            )
        for action in matrix.actions:
            iid = f"action-{action.action_id}"
            self.tree.insert(
                action_parents[action.status], "end", iid=iid,
                values=(action.label, self.STATUS_LABELS.get(action.status, action.status)),
            )
            self.records[iid] = ("action", action)

        stage = context.get("current_stage")
        stages = context.get("stages") or []
        stage_count = len(stages) + (1 if stage and not any(
            item.get("stage_id") == stage.get("stage_id") for item in stages
        ) else 0)
        stage_group = self.tree.insert(
            "", "end", iid="group-stages", text=f"阶段（{stage_count}）", open=False,
        )
        if stage:
            iid = "current-stage"
            self.tree.insert(stage_group, "end", iid=iid, text="当前", values=(stage.get("title"), stage.get("status")))
            self.records[iid] = ("stage", stage)
        historical_stages = [
            item for item in stages
            if not stage or item.get("stage_id") != stage.get("stage_id")
        ]
        stage_history_group = self.tree.insert(
            stage_group, "end", iid="group-stage-history",
            text=f"历史阶段（{len(historical_stages)}）", open=False,
        )
        for historical_stage in historical_stages:
            iid = f"history-stage-{historical_stage.get('stage_id')}"
            self.tree.insert(stage_history_group, "end", iid=iid, values=(
                historical_stage.get("title"), historical_stage.get("status"),
            ))
            self.records[iid] = ("stage", historical_stage)

        attempt = snapshot.get("attempt")
        explorations = snapshot.get("explorations") or []
        attempt_group = self.tree.insert(
            "", "end", iid="group-attempts", text=f"探索（{len(explorations) + (1 if attempt else 0)}）", open=False,
        )
        if attempt:
            iid = "current-attempt"
            self.tree.insert(attempt_group, "end", iid=iid, text="当前", values=(attempt.get("goal"), attempt.get("state")))
            self.records[iid] = ("attempt", attempt)
        exploration_history_group = self.tree.insert(
            attempt_group, "end", iid="group-exploration-history",
            text=f"历史探索（{len(explorations)}）", open=False,
        )
        for index, exploration in enumerate(explorations):
            event_id = exploration.get("event_id") or f"index-{index}"
            iid = f"exploration-{event_id}"
            self.tree.insert(exploration_history_group, "end", iid=iid, values=(
                exploration.get("goal") or exploration.get("branch") or event_id,
                exploration.get("result") or exploration.get("state") or "未知",
            ))
            self.records[iid] = ("exploration", exploration)

        decisions = snapshot.get("decisions") or []
        decision_group = self.tree.insert(
            "", "end", iid="group-decisions", text=f"决策（{len(decisions)}）", open=False,
        )
        for index, decision in enumerate(decisions):
            record_id = decision.get("event_id") or decision.get("decision_id") or f"index-{index}"
            iid = f"decision-{record_id}"
            self.tree.insert(decision_group, "end", iid=iid, values=(
                decision.get("decision") or decision.get("decision_id") or record_id,
                decision.get("occurred_at") or "",
            ))
            self.records[iid] = ("decision", decision)

        target = selected_id if selected_id in self.records else ("current-task" if "current-task" in self.records else "current-stage")
        if target in self.records:
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.tree.see(target)
            self.select_item()

    def select_item(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected or selected[0] not in self.records:
            return
        kind, value = self.records[selected[0]]
        if kind == "task":
            self.title.configure(text=value.get("goal") or value.get("task_id") or "任务")
            self.subtitle.configure(text=f"任务｜{value.get('task_id') or '未知'}")
            text = self._task_text(value)
        elif kind == "stage":
            self.title.configure(text=value.get("title") or "阶段")
            self.subtitle.configure(text=f"阶段｜{value.get('stage_id') or '未知'}")
            text = self._stage_text(value, (self.snapshot or {}).get("context", {}).get("attempts") or [])
        elif kind == "attempt":
            self.title.configure(text=value.get("goal") or "当前探索")
            self.subtitle.configure(text=f"探索｜{value.get('branch') or '未知'}｜{value.get('state') or '未知'}")
            text = DashboardApp.exploration_detail({"_attempt": value})
        elif kind == "exploration":
            self.title.configure(text=value.get("goal") or value.get("branch") or "探索")
            self.subtitle.configure(text=f"探索｜{value.get('branch') or '未知'}｜{value.get('result') or '未知'}")
            text = DashboardApp.exploration_detail(value)
        elif kind == "decision":
            self.title.configure(text=value.get("decision") or value.get("decision_id") or "决策")
            self.subtitle.configure(text=f"决策｜{value.get('decision_id') or '未知'}")
            text = self._decision_text(value)
        else:
            self.title.configure(text=value.label)
            self.subtitle.configure(text=f"工作流动作｜{value.action_id}")
            text = self._action_text(value)
        self._set_detail(text)

    def open_task(self, task_id: str) -> bool:
        candidates = ["current-task", f"history-task-{task_id}"]
        for iid in candidates:
            record = self.records.get(iid)
            if record and record[0] == "task" and record[1].get("task_id") == task_id:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                self.select_item()
                return True
        return False

    def open_action(self, action_id: str) -> bool:
        iid = f"action-{action_id}"
        if iid not in self.records:
            return False
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self.select_item()
        return True

    def open_record(self, kind: str, record_id: str) -> bool:
        expected = "decision" if kind == "decisions" else "exploration"
        for iid, (record_kind, value) in self.records.items():
            if record_kind != expected:
                continue
            identities = {value.get("event_id"), value.get("decision_id"), value.get("branch")}
            if record_id not in identities:
                continue
            parent = self.tree.parent(iid)
            if parent:
                self.tree.item(parent, open=True)
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
            self.select_item()
            return True
        return False


class DiagnosticsPage:
    STATUS_LABELS = {
        "current": "当前问题",
        "old_version": "旧版本",
        "old_version_protected": "待复查·受保护",
        "resolved": "已确认解决",
    }

    def __init__(
        self, parent, ttk, scrolledtext, *, refresh: Callable[[], None],
        export: Callable[[], None], report_bug: Callable[[], None],
        resolve_and_cleanup: Callable[[str], None],
    ):
        self.frame = ttk.Frame(parent, padding=8)
        self.refresh = refresh
        self.resolve_and_cleanup = resolve_and_cleanup
        self.records: dict[str, tuple[str, dict]] = {}
        controls = ttk.Frame(self.frame)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Button(controls, text="重新读取", command=refresh).pack(side="left")
        ttk.Button(controls, text="导出诊断包", command=export).pack(side="left", padx=(6, 0))
        ttk.Button(controls, text="报告 Bug", command=report_bug).pack(side="left", padx=(6, 0))
        self.resolve_button = ttk.Button(
            controls, text="确认已解决并删除", command=self.confirm_resolve, state="disabled",
        )
        self.resolve_button.pack(side="left", padx=(6, 0))
        ttk.Label(
            controls, text="诊断仅保存在本地；导出不会自动上传。",
        ).pack(side="left", padx=(14, 0))

        panes = ttk.Panedwindow(self.frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, padding=(0, 0, 8, 0))
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(left, weight=3)
        panes.add(right, weight=5)
        self.tree = ttk.Treeview(
            left, columns=("code", "count", "export", "status"), show="tree headings", height=24,
        )
        self.tree.heading("#0", text="诊断问题")
        for key, label, width in (
            ("code", "代码", 80), ("count", "次数", 48),
            ("export", "导出", 72), ("status", "状态", 120),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=45, stretch=key == "status")
        self.tree.column("#0", width=245, minwidth=150, stretch=True)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.show_selected)
        self.title = ttk.Label(right, text="诊断概览", font=("TkDefaultFont", 12, "bold"))
        self.title.pack(fill="x", anchor="w")
        self.subtitle = ttk.Label(right, text="选择左侧问题查看导出覆盖和安全处理方式。", wraplength=760, justify="left")
        self.subtitle.pack(fill="x", pady=(4, 8))
        self.detail = scrolledtext.ScrolledText(right, wrap="word")
        self.detail.pack(fill="both", expand=True)
        self.detail.configure(state="disabled")
        self.revision: str | None = None

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def set_data(self, overview: dict) -> None:
        revision = json.dumps(overview, ensure_ascii=False, sort_keys=True, default=str)
        if revision == self.revision:
            return
        self.revision = revision
        selected = self.tree.selection()
        selected_id = selected[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        self.records.clear()
        issues = overview.get("issues") or []
        issues_group = self.tree.insert("", "end", iid="diagnostics-issues", text=f"问题（{len(issues)}）", open=True)
        for issue in issues:
            iid = f"diagnostic-{issue['fingerprint']}"
            self.tree.insert(issues_group, "end", iid=iid, text=short(issue.get("summary"), 42), values=(
                issue.get("code"), issue.get("count"), "已导出" if issue.get("exported") else "未导出",
                self.STATUS_LABELS.get(issue.get("status"), issue.get("status")),
            ))
            self.records[iid] = ("issue", issue)
        cleanups = overview.get("cleanups") or []
        cleanup_group = self.tree.insert("", "end", iid="diagnostics-cleanups", text=f"最近清理（{len(cleanups)}）", open=False)
        for index, cleanup in enumerate(cleanups):
            iid = f"diagnostic-cleanup-{index}"
            self.tree.insert(cleanup_group, "end", iid=iid, text=cleanup.get("cleaned_at") or "未知时间", values=(
                "cleanup", cleanup.get("records", 0), "—", "自动" if cleanup.get("type") == "records_auto_cleaned" else "手动",
            ))
            self.records[iid] = ("cleanup", cleanup)
        target = selected_id if selected_id in self.records else next(iter(self.records), None)
        if target:
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.tree.see(target)
            self.show_selected()
        else:
            self.title.configure(text="当前没有诊断问题")
            self.subtitle.configure(text="后续失败、冲突或异常会在这里出现。")
            self._set_detail("诊断记录为空。\n\n导出诊断包仍会包含版本环境和只读健康检查结果。")
            self.resolve_button.configure(state="disabled")

    def show_selected(self, _event=None) -> None:
        selected = self.tree.selection()
        record = self.records.get(selected[0]) if selected else None
        if record is None:
            return
        kind, value = record
        if kind == "cleanup":
            self.title.configure(text="诊断清理回执")
            self.subtitle.configure(text=value.get("cleaned_at") or "未知时间")
            self._set_detail(
                f"方式：{'升级后自动清理' if value.get('type') == 'records_auto_cleaned' else '用户确认清理'}\n"
                f"清理记录：{value.get('records', 0)}\n"
                f"来源版本：{value.get('from_version') or '不适用'}\n"
                f"目标版本：{value.get('to_version') or '不适用'}\n"
                f"故障指纹：{', '.join(value.get('fingerprints') or []) or '无'}"
            )
            self.resolve_button.configure(state="disabled")
            return
        self.title.configure(text=f"[{value.get('code')}] {value.get('summary')}")
        self.subtitle.configure(text=f"指纹 {value.get('fingerprint')}｜{self.STATUS_LABELS.get(value.get('status'), value.get('status'))}")
        export_text = (
            f"已导出：{value.get('last_export_id')}｜{value.get('last_exported_at')}"
            if value.get("exported") else "尚未进入任何诊断包"
        )
        self._set_detail(
            f"分类：{value.get('category')}\n重复次数：{value.get('count')}\n"
            f"最近发生：{value.get('latest_at') or '未知'}\n涉及版本：{', '.join(value.get('versions') or [])}\n"
            f"受保护：{'是' if value.get('protected') else '否'}\n{export_text}\n\n"
            f"事件编号\n" + "\n".join(f"- {item}" for item in value.get("incident_ids") or []) +
            f"\n\n建议\n{value.get('suggestion') or '请检查问题。'}\n\n"
            "确认已解决并删除会先记录本地解决回执，再原子清理对应指纹；以后再次出现仍会作为新问题记录。"
        )
        self.resolve_button.configure(state="normal")

    def confirm_resolve(self) -> None:
        selected = self.tree.selection()
        record = self.records.get(selected[0]) if selected else None
        if record is None or record[0] != "issue":
            return
        issue = record[1]
        from tkinter import messagebox
        if not messagebox.askyesno(
            "确认解决并删除",
            f"将删除指纹 {issue['fingerprint']} 对应的 {issue['count']} 条本地诊断记录。\n\n"
            "不会修改项目文件、事件日志或数据库；若故障再次发生会重新记录。是否继续？",
            parent=self.frame.winfo_toplevel(),
        ):
            return
        self.resolve_and_cleanup(issue["fingerprint"])


class ExplanationPage:
    """Static bilingual glossary for states shown by the read-only Dashboard."""

    SECTIONS = (
        ("工作流 Workflow", (
            ("idle", "未开始", "当前没有活动任务。", "可以根据用户的新意图建立任务周期。"),
            ("starting", "正在建立周期", "start 事务尚未全部完成。", "等待完成；若进程异常退出，使用 task recover。"),
            ("started", "周期已建立", "任务已经建立，但尚未产生文件变化或进度记录。", "开始工作并及时记录进展。"),
            ("working", "工作中", "任务期间检测到工作树变化。", "继续工作，阶段性使用 state update。"),
            ("progress-recorded", "进展已记录", "当前任务已经保存结构化进度。", "继续执行或补充决策、探索证据。"),
            ("ready", "可完成", "主要完成前置条件已经满足。", "确认编辑停止并运行健康检查后结束任务。"),
            ("finishing", "正在收尾", "完成事件、投影或自动提交正在处理。", "不要重复写入；异常退出后使用 task recover。"),
            ("completed", "已完成", "最近任务已经产生完成回执，当前无活动任务。", "查看下一步，或开始新的任务周期。"),
            ("recovery-required", "需要恢复", "sidecar、事件或投影表明事务中途停止。", "先执行 task recover，不要手工删除状态文件。"),
        )),
        ("任务 Task", (
            ("active", "活动中", "当前项目唯一允许写入生命周期的任务。", "继续工作、更新进度，完成后运行 end。"),
            ("completed", "完成", "目标按本次任务定义完成并留下证据。", "检查后续步骤以及是否需要提交、推送或发布。"),
            ("blocked", "阻塞", "任务无法继续，但原因已经明确。", "记录阻塞条件，外部条件变化后恢复。"),
            ("failed", "失败", "本次执行没有达到目标并明确失败。", "保留证据，建立修复任务或重新评估路线。"),
            ("indeterminate", "待判定", "证据不足，暂时不能判断成功或失败。", "补充验证，不要把它当成已完成。"),
            ("abandoned", "已放弃", "用户明确停止该任务，但文件和分支仍被保留。", "需要时从保留现场建立新任务；不会自动回滚。"),
        )),
        ("动作 Action", (
            ("available", "可执行", "结构状态满足，AI 可以立即调用该动作。", "仍应按动作风险和用户意图执行。"),
            ("needs_input", "等待 AI 文本", "安全前置条件满足，但缺少目标、原因或证据等自然语言。", "AI 从用户描述提取字段；Dashboard 不负责填写。"),
            ("running", "执行中", "当前写锁对应这个生命周期动作。", "等待动作完成，不要并发执行其他写动作。"),
            ("blocked", "暂不可用", "分支、活动任务、恢复、健康、版本或锁条件不满足。", "查看原因代码、证据和安全下一步。"),
        )),
        ("阶段 Stage", (
            ("active", "进行中", "这是当前长期大阶段。", "持续更新摘要、当前步骤、下一步和证据。"),
            ("paused", "已暂停", "方向仍有效，但当前暂不推进。", "恢复前先核对目标与阻塞是否仍然成立。"),
            ("completed", "已完成", "原阶段验收条件已满足且证据完整。", "建立下一阶段，或进入发布与维护。"),
            ("cancelled", "已取消", "原目标已经作废，不再继续。", "保留历史原因；不要伪装成 completed。"),
        )),
        ("探索 Exploration", (
            ("active", "探索中", "假设或不确定路线仍在验证。", "持续记录当前步骤、进展、证据和下一步。"),
            ("validated", "已验证", "假设、证据和结论完整，结果支持该路线。", "可以准备 Squash PR，但合并仍需用户确认。"),
            ("negative", "负面结果", "证据表明路线不可行或不值得继续。", "保存失败价值并归档，不删除科研现场。"),
            ("inconclusive", "无定论", "现有证据不足以支持或否定假设。", "补充实验，或记录限制后暂停。"),
            ("paused", "已暂停", "探索暂时停止但没有得出最终结论。", "保留分支和证据，条件成熟后恢复。"),
        )),
        ("决策 Decision", (
            ("decision", "选择", "最终采用的路线或判断。", "路线变化时必须记录。"),
            ("alternatives", "替代方案", "曾考虑但没有采用的方案。", "用于理解为什么没有走其他路线。"),
            ("basis", "依据", "支持选择的证据、约束和权衡。", "应尽量具体并可复核。"),
            ("reopen_condition", "重开条件", "什么新证据出现时应重新讨论该决策。", "条件满足时建立新决策，不改写旧记录。"),
            ("immutable", "不可改写", "决策是追加式审计记录，没有可变状态。", "需要纠正时追加新决策并引用旧决策。"),
        )),
        ("资料 Resource", (
            ("active", "有效", "资料索引正常参与项目使用。", "保持路径、类型和实际文件一致。"),
            ("missing", "文件缺失", "索引存在，但登记的项目文件当前找不到。", "恢复文件或运行扫描确认，不要静默删除索引。"),
            ("archived", "已归档", "资料被软归档，不作为当前主要材料。", "需要时可以恢复，文件和历史仍保留。"),
            ("ok", "目录正常", "目录存在，文件全部已索引且类型匹配。", "无需处理。"),
            ("attention", "目录需关注", "存在未索引文件、类型错位或缺失状态不一致。", "让 AI 运行 catalog scan 或检查具体文件。"),
            ("missing directory", "目录缺失", "七个标准资源目录之一不存在。", "运行 install 恢复目录结构，不要自行改变标准布局。"),
        )),
        ("诊断 Diagnostics", (
            ("current", "当前问题", "该故障在当前应用版本中出现。", "检查建议；需要报告时先导出诊断包。"),
            ("old_version", "旧版本问题", "该故障只在旧应用版本中出现。", "更新成功后普通校验和冲突会自动清理。"),
            ("old_version_protected", "旧版本待复查", "旧版本的内部异常或数据完整性问题仍被保护。", "确认新版本不可复现且证据已保留后再删除。"),
            ("resolved", "已确认解决", "用户已经明确确认该故障指纹不再需要保留。", "执行原子清理；以后复发会生成新记录。"),
            ("exported", "已导出", "至少一个事件已经进入带 export_id 的诊断包。", "报告 Bug 时检查并手工附加对应 ZIP。"),
        )),
        ("外置工具 External tool", (
            ("active", "正在使用", "该工具是当前项目的有效外部提醒。", "需要时由用户或 AI 主动选择使用；Dashboard 不自动运行。"),
            ("paused", "暂时停用", "工具记录仍有效，但当前阶段不建议使用。", "重新需要时由 AI 追加 restore 记录。"),
            ("retired", "已经停用", "工具不再作为当前工作方式，但历史用途仍保留。", "不要删除历史；重新采用时显式 restore。"),
        )),
        ("Git 与发布 Git / Release", (
            ("clean", "工作区干净", "没有未提交的已跟踪或未跟踪修改。", "可以继续检查提交和推送状态。"),
            ("dirty", "工作区有修改", "存在尚未提交的文件变化。", "先验证并提交；不要把远端同步误认为工作已发布。"),
            ("synced", "已同步", "当前 HEAD 与已知 origin/main 相同。", "只代表提交已推送，不代表工作区修改或 Release 已发布。"),
            ("ahead", "本地领先", "本地有尚未推送的提交。", "确认后推送，并重新核对远端。"),
            ("behind", "本地落后", "远端有本地尚未包含的提交。", "停止发布，先安全同步。"),
            ("diverged", "已经分叉", "本地与远端各自包含不同提交。", "人工审查并选择合并或变基策略。"),
            ("unavailable", "远端不可用", "没有 origin/main、离线或 Git 检查失败。", "只能陈述未知，不能假定已经推送。"),
            ("build match", "EXE 与仓库一致", "EXE 内嵌源码指纹与当前软件源码仓库指纹相同。", "表示该 EXE 能代表当前仓库；普通科研项目没有软件源码时不适用。"),
            ("latest release", "最近正式发布", "显示 GitHub 最新正式 Release 的版本与发布时间。", "这是辅助信息，不是科研工作周期的完成条件。"),
            ("unreleased software", "发布后软件修改", "最新 Release 标签之后仍有软件源码、测试、构建脚本或维护文档变化。", "按需提交、推送并在合适时发布新软件版本；科研 resources 不计入。"),
        )),
    )

    def __init__(self, parent, ttk, scrolledtext):
        self.frame = ttk.Frame(parent, padding=8)
        self.records: dict[str, tuple[str, str, str, str, str]] = {}
        panes = ttk.Panedwindow(self.frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, padding=(0, 0, 8, 0))
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(left, weight=2)
        panes.add(right, weight=5)
        self.tree = ttk.Treeview(left, columns=("meaning",), show="tree headings", height=24)
        self.tree.heading("#0", text="类别 / English status")
        self.tree.heading("meaning", text="中文")
        self.tree.column("#0", width=260, minwidth=150, stretch=True)
        self.tree.column("meaning", width=110, minwidth=80, stretch=False)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.show_selected)
        for section_index, (section, items) in enumerate(self.SECTIONS):
            group = self.tree.insert(
                "", "end", iid=f"explain-group-{section_index}", text=section,
                open=section_index == 0,
            )
            for item_index, item in enumerate(items):
                iid = f"explain-{section_index}-{item_index}"
                self.tree.insert(group, "end", iid=iid, text=item[0], values=(item[1],))
                self.records[iid] = (section, *item)
        self.title = ttk.Label(right, text="状态解释", font=("TkDefaultFont", 12, "bold"))
        self.title.pack(fill="x", anchor="w")
        self.subtitle = ttk.Label(right, text="选择左侧英文状态查看它在项目中的实际含义。", wraplength=760, justify="left")
        self.subtitle.pack(fill="x", pady=(4, 8))
        self.detail = scrolledtext.ScrolledText(right, wrap="word")
        self.detail.pack(fill="both", expand=True)
        self.detail.configure(state="disabled")

    def show_selected(self, _event=None) -> None:
        selected = self.tree.selection()
        record = self.records.get(selected[0]) if selected else None
        if not record:
            return
        section, code, meaning, implication, next_step = record
        self.title.configure(text=f"{code}｜{meaning}")
        self.subtitle.configure(text=section)
        text = (
            f"英文状态\n{code}\n\n中文含义\n{meaning}\n\n"
            f"意味着什么\n{implication}\n\n安全下一步\n{next_step}"
        )
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")


class DashboardApp:
    def __init__(self, root, tk, ttk, scrolledtext, provider: DashboardDataProvider, refresh_seconds: float):
        from project_hooks.app_icon import apply_window_icon

        self.root, self.tk, self.ttk = root, tk, ttk
        self.scrolledtext = scrolledtext
        self.controller = DashboardController(provider)
        self.provider = provider
        self.refresh_seconds = refresh_seconds
        self.snapshot: dict | None = None
        self.advanced_window: AdvancedWindow | None = None
        self.last_refresh_error: str | None = None
        self.lifecycle_polling = False
        self.lifecycle_token: str | None = None
        self.latest_update_result: dict | None = None
        self.software_delivery_result: dict | None = None
        self.software_delivery_running = False
        self.software_delivery_pending = False
        self.ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.refresh_running = False
        self.refresh_pending = False
        self.refresh_force_full = False
        self.last_full_state_token: str | None = None
        self.action_matrix = None
        self.section_revisions: dict[str, str] = {}
        apply_window_icon(root, tk)
        root.title("Workflow Monitor")
        root.geometry("1220x820")
        root.minsize(900, 620)

        toolbar = ttk.Frame(root, padding=(10, 8))
        toolbar.pack(fill="x")
        self.branch_label = ttk.Label(toolbar, text="分支：加载中")
        self.branch_label.pack(side="left")
        self.health_label = ttk.Label(toolbar, text="数据库：加载中")
        self.health_label.pack(side="left", padx=(18, 0))
        self.version_label = ttk.Label(toolbar, text="版本：加载中…")
        self.version_label.pack(side="left", padx=(18, 0))
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(side="right")
        ttk.Button(toolbar, text="高级查看", command=self.open_advanced).pack(side="right", padx=(0, 6))
        self.update_button = ttk.Button(toolbar, text="检查更新", command=self.check_for_updates)
        self.update_button.pack(side="right", padx=(0, 6))
        self.update_label = ttk.Label(toolbar, text="")
        self.update_label.pack(side="right", padx=(0, 8))

        lifecycle = ttk.LabelFrame(root, text="当前工作周期（近实时）", padding=(10, 6))
        lifecycle.pack(fill="x", padx=10, pady=(0, 7))
        self.lifecycle_steps = ttk.Label(lifecycle, text="未开始 → 周期已建立 → 工作中 → 进展已记录 → 可完成 → 正在收尾 → 已完成")
        self.lifecycle_steps.pack(anchor="w")
        self.lifecycle_detail = ttk.Label(lifecycle, text="正在读取活动任务…", wraplength=1160, justify="left")
        self.lifecycle_detail.pack(fill="x", pady=(3, 0))
        self.lifecycle_action = ttk.Label(lifecycle, text="最近动作：无")
        self.lifecycle_action.pack(fill="x", pady=(2, 0))
        self.lifecycle_delivery = ttk.Label(
            lifecycle, text="工作区：读取中　｜　提交：读取中　｜　推送：读取中",
            wraplength=1160, justify="left",
        )
        self.lifecycle_delivery.pack(fill="x", pady=(2, 0))

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.workflow_page = WorkflowPage(self.notebook, tk, ttk, scrolledtext)
        self.notebook.add(self.workflow_page.frame, text="工作流")

        self.overview_frame = ttk.Frame(self.notebook, padding=8)
        overview_actions = ttk.Frame(self.overview_frame)
        overview_actions.pack(fill="x", pady=(0, 6))
        ttk.Button(overview_actions, text="复制概览", command=self.copy_overview).pack(side="left")
        self.overview = scrolledtext.ScrolledText(self.overview_frame, wrap="word")
        self.overview.pack(fill="both", expand=True)
        self.overview.configure(state="disabled")
        self.notebook.add(self.overview_frame, text="概览")

        self.search_frame = ttk.Frame(self.notebook, padding=8)
        search_controls = ttk.Frame(self.search_frame)
        search_controls.pack(fill="x", pady=(0, 6))
        ttk.Label(search_controls, text="全局搜索").pack(side="left")
        self.global_query = tk.StringVar()
        global_entry = ttk.Entry(search_controls, textvariable=self.global_query, width=42)
        global_entry.pack(side="left", padx=(6, 6))
        global_entry.bind("<Return>", lambda _event: self.perform_global_search())
        ttk.Button(search_controls, text="搜索", command=self.perform_global_search).pack(side="left")
        ttk.Button(search_controls, text="清除", command=self.clear_global_search).pack(side="left", padx=(6, 0))
        ttk.Label(
            search_controls, text="搜索任务、阶段、决策、探索、资料和原始事件的只读索引。",
        ).pack(side="left", padx=(14, 0))
        self.search_page = TablePage(
            self.search_frame, tk, ttk, scrolledtext,
            columns=[("kind_label", "类型", 80), ("occurred_at", "时间", 180), ("branch", "分支", 150),
                     ("title", "标题", 320), ("summary", "摘要", 250)],
            detail=self.search_result_detail, refresh=self.refresh,
            activate=self.open_search_result, show_query=False, split_detail=True,
        )
        self.search_page.frame.pack(fill="both", expand=True, padx=0, pady=0)
        self.notebook.add(self.search_frame, text="搜索")

        self.catalog_page = CatalogPage(
            self.notebook, tk, ttk, scrolledtext, refresh=self.refresh,
            project_root=provider.model.database_path.parent.parent,
            notify=self.notify,
            open_action=None,
        )
        self.notebook.add(self.catalog_page.frame, text="资料")

        self.workbench_page = ResearchWorkbenchPage(
            self.notebook,
            tk,
            ttk,
            scrolledtext,
            notify=self.notify,
        )
        self.notebook.add(self.workbench_page.frame, text="工作台")

        self.diagnostics_page = DiagnosticsPage(
            self.notebook, ttk, scrolledtext, refresh=self.refresh,
            export=self.export_diagnostic_bundle, report_bug=self.report_bug,
            resolve_and_cleanup=self.resolve_diagnostic_issue,
        )
        self.notebook.add(self.diagnostics_page.frame, text="诊断")

        self.explanation_page = ExplanationPage(self.notebook, ttk, scrolledtext)
        self.notebook.add(self.explanation_page.frame, text="解释")

        self.status = ttk.Label(root, text="窗口已就绪，正在后台读取项目…", padding=(10, 5))
        self.status.pack(fill="x")
        root.after(50, self.drain_ui_queue)
        root.after(0, lambda: self.request_refresh(force_full=True))
        root.after(1000, self.auto_refresh)

    def auto_refresh(self) -> None:
        try:
            if self.root.state() != "iconic":
                self.request_refresh(force_full=False)
        finally:
            if self.root.winfo_exists():
                self.root.after(1000, self.auto_refresh)

    def notify(self, message: str) -> None:
        if hasattr(self, "status"):
            self.status.configure(text=message)

    def post_ui(self, callback: Callable[[], None]) -> None:
        self.ui_queue.put(callback)

    def drain_ui_queue(self) -> None:
        while True:
            try:
                callback = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception as exc:
                self.notify(f"界面回调失败：{exc}")
        if self.root.winfo_exists():
            self.root.after(50, self.drain_ui_queue)

    def request_refresh(self, *, force_full: bool = False) -> None:
        """Run one non-overlapping background refresh and coalesce later requests."""
        if self.refresh_running:
            self.refresh_pending = True
            self.refresh_force_full = self.refresh_force_full or force_full
            return
        self.refresh_running = True
        requested_full = force_full or self.snapshot is None

        def worker() -> None:
            matrix = None
            snapshot = None
            version = None
            error = None
            try:
                service = self.provider.action_service
                if service is not None:
                    matrix = service.availability_matrix(
                        force=requested_full, max_age=1.25,
                    )
                    requested = requested_full or matrix.state_token != self.last_full_state_token
                else:
                    requested = requested_full
                if requested:
                    snapshot = self.provider.load()
                if self.snapshot is None:
                    version = self.provider.version_info()
            except Exception as exc:
                error = str(exc)

            self.post_ui(lambda: self._finish_refresh(matrix, snapshot, version, error))

        threading.Thread(target=worker, daemon=True, name="dashboard-refresh").start()

    def _finish_refresh(self, matrix, snapshot: dict | None, version: dict | None, error: str | None) -> None:
        self.refresh_running = False
        if matrix is not None:
            self.action_matrix = matrix
            self.apply_lifecycle(matrix.state)
        if snapshot is not None:
            self.snapshot = snapshot
            self.last_refresh_error = None
            self.last_full_state_token = matrix.state_token if matrix is not None else self.last_full_state_token
            self.apply_snapshot(snapshot)
            rebuilt = "；本次已重建" if snapshot["health"]["rebuilt"] else ""
            self.status.configure(text=f"刷新成功：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{rebuilt}")
        elif error:
            self.status.configure(text=f"刷新失败，继续显示上一次数据：{error}")
            if error != self.last_refresh_error:
                record_failure(
                    self.project_root, DashboardError(error), command="dashboard.refresh",
                    application_version=__version__, schema_version=SCHEMA_VERSION,
                    execution_mode=execution_mode(),
                )
                self.last_refresh_error = error
        if version is not None:
            self.version_label.configure(text=version_status_text(version))
        if self.snapshot is not None and self.action_matrix is not None:
            self.workflow_page.set_data(self.snapshot, self.action_matrix)
        if self.refresh_pending:
            pending_full = self.refresh_force_full
            self.refresh_pending = False
            self.refresh_force_full = False
            self.root.after(0, lambda: self.request_refresh(force_full=pending_full))

    def open_action(self, action_id: str, preset: dict | None = None) -> None:
        del preset
        if self.workflow_page.open_action(action_id):
            self.notebook.select(self.workflow_page.frame)
        else:
            self.notify(f"找不到工作流动作：{action_id}")

    def action_progress(self, item: ActionProgress) -> None:
        self.lifecycle_action.configure(
            text=f"当前动作：{item.action_id}｜{item.phase}｜{item.percent}%｜{item.message}"
        )

    def poll_lifecycle(self) -> None:
        self.request_refresh(force_full=False)

    def apply_lifecycle(self, state: dict) -> None:
        step = state.get("lifecycle_step") or {"index": 0, "label": "未知"}
        labels = ["未开始", "周期已建立", "工作中", "进展已记录", "可完成", "正在收尾", "已完成"]
        rendered = [f"【{label}】" if index == step.get("index") else label for index, label in enumerate(labels)]
        self.lifecycle_steps.configure(text=" → ".join(rendered))
        active = state.get("active_task") or {}
        writer = state.get("writer_lock") or {}
        writer_text = (
            f"写锁 PID {writer.get('pid')} / {writer.get('command')}"
            if writer.get("status") == "active" else "写锁空闲"
        )
        if active:
            detail = (
                f"{active.get('task_id')}｜{active.get('kind') or 'other'}｜{active.get('branch')}｜"
                f"开始 {active.get('started_at')}｜变化 {len(state.get('changed_paths') or [])} 个文件｜"
                f"进度 {'已记录' if active.get('state_updated') else '未记录'}｜"
                f"决策 {active.get('decisions_added') or 0}｜{writer_text}｜"
                f"工作树稳定 {state.get('worktree_quiet_seconds', 0)} 秒"
            )
        else:
            last = state.get("last_completed") or {}
            if step.get("key") == "completed" and last:
                detail = (
                    f"最近完成 {last.get('task_id')}｜{last.get('result') or 'completed'}｜"
                    f"{last.get('occurred_at')}｜分支 {state.get('branch') or '未知'}｜{writer_text}"
                )
            else:
                detail = f"当前没有活动工作周期｜分支 {state.get('branch') or '未知'}｜{writer_text}"
        if (state.get("sidecar") or {}).get("error"):
            detail += f"｜需要恢复：{state['sidecar']['error']}"
        self.lifecycle_detail.configure(text=detail)
        self.lifecycle_delivery.configure(
            text=delivery_status_text(state),
        )
        token = state.get("state_token")
        if self.lifecycle_token and token != self.lifecycle_token:
            self.lifecycle_action.configure(text="项目状态已变化；只读工作流视图正在同步。")
            if self.software_delivery_result is not None:
                self.root.after(0, lambda: self.request_software_delivery(initial=False))
        self.lifecycle_token = token

    def check_for_updates(self) -> None:
        self.update_button.configure(state="disabled")
        self.update_label.configure(text="更新：检查中…")

        def worker() -> None:
            detail = ""
            result = None
            try:
                result = self.provider.check_for_updates()
                message = update_status_text(result)
            except Exception as exc:
                message = update_status_text(error=str(exc))
                detail = f"{message}：{exc}"

            def finish() -> None:
                self.update_label.configure(text=message)
                self.update_button.configure(state="normal")
                self.latest_update_result = result
                if self.action_matrix is not None:
                    self.apply_lifecycle(self.action_matrix.state)
                if self.snapshot is not None:
                    self.section_revisions.pop("overview", None)
                    self.apply_snapshot(self.snapshot)
                self.update_button.configure(text="检查更新", command=self.check_for_updates)
                self.status.configure(text=detail or message)

            self.post_ui(finish)

        threading.Thread(target=worker, daemon=True).start()

    def request_software_delivery(self, *, initial: bool) -> None:
        """Refresh auxiliary software state without blocking the lifecycle view."""
        if self.software_delivery_running:
            self.software_delivery_pending = True
            return
        self.software_delivery_running = True

        def worker() -> None:
            try:
                if initial or self.software_delivery_result is None:
                    result = self.provider.check_software_delivery()
                else:
                    result = self.provider.refresh_software_delivery(self.software_delivery_result)
            except Exception:
                result = dict(self.software_delivery_result or {})
                result["status"] = "software-delivery-check-failed"

            def finish() -> None:
                self.software_delivery_running = False
                self.software_delivery_result = result
                if self.advanced_window is not None:
                    self.advanced_window.set_software_delivery(result)
                if self.software_delivery_pending:
                    self.software_delivery_pending = False
                    self.root.after(0, lambda: self.request_software_delivery(initial=False))

            self.post_ui(finish)

        threading.Thread(
            target=worker,
            daemon=True,
            name="dashboard-software-delivery",
        ).start()

    def apply_update(self) -> None:
        self.notify("Dashboard 为只读观察台；请在 AI 对话中确认并执行软件更新。")
        return
        # Kept below for source compatibility with pre-release v1.5 tests; the
        # read-only Dashboard has no control that can reach this legacy path.
        service = self.provider.action_service
        if service is None:
            self.notify("当前 Dashboard 没有动作服务，无法执行更新。")
            return
        availability = service.availability("update.apply", force=True)
        if not availability.enabled:
            detail = "；".join(f"[{item.code}] {item.message}" for item in availability.blockers)
            self.notify("无法一键更新：" + detail)
            self.open_action("update.apply")
            return
        from tkinter import messagebox
        target = (self.latest_update_result or {}).get("latest_version") or "最新稳定版"
        if not messagebox.askyesno(
            "确认软件更新",
            f"将下载、校验并安装 {target}。\n\n要求 main、无活动任务、工作树干净且同步。"
            "更新器会保留事件历史并在失败时回滚。是否继续？",
            parent=self.root,
        ):
            return
        self.update_button.configure(state="disabled")
        request = ActionRequest("update.apply", {}, availability.state_token, True)

        def worker() -> None:
            result = service.execute(
                request,
                progress=lambda item: self.post_ui(lambda item=item: self.action_progress(item)),
            )

            def finish() -> None:
                self.update_button.configure(state="normal")
                if result.status != "success":
                    self.notify(result.summary)
                    return
                data = result.data if isinstance(result.data, dict) else {}
                changed = "\n".join(f"- {item}" for item in data.get("changed", [])) or "- 无"
                conflicts = "\n".join(f"- {item}" for item in data.get("conflicts", [])) or "- 无"
                restart = messagebox.askyesno(
                    "更新完成",
                    f"更新已完成。\n\n变更：\n{changed}\n\n模板冲突：\n{conflicts}\n\n"
                    "是否现在启动新版 Dashboard 并关闭当前窗口？",
                    parent=self.root,
                )
                if restart:
                    self.restart_updated_dashboard()
                else:
                    self.notify("更新已完成；当前仍是旧窗口，稍后请重新打开 Dashboard。")
            self.post_ui(finish)

        threading.Thread(target=worker, daemon=True).start()

    def restart_updated_dashboard(self) -> None:
        executable = selected_executable(self.project_root)
        if executable is None:
            self.notify("找不到已安装的新版 EXE，请手工重新打开项目根目录的 workflow-monitor.exe。")
            return
        env = os.environ.copy()
        env[ACTIVE_ENV] = "1"
        env[PORTABLE_ROOT_ENV] = str(self.project_root)
        try:
            subprocess.Popen(
                [str(executable), "--project", str(self.project_root), "dashboard"],
                env=env, close_fds=True,
            )
        except OSError as exc:
            self.notify(f"新版已安装，但自动重启失败：{exc}")
            return
        self.root.destroy()

    @property
    def project_root(self) -> Path:
        return self.provider.model.database_path.parent.parent

    def export_diagnostic_bundle(self) -> None:
        try:
            from tkinter import filedialog
            export_folder = self.project_root / "diagnostics-export"
            export_folder.mkdir(parents=True, exist_ok=True)
            selected = filedialog.asksaveasfilename(
                parent=self.root,
                title="导出脱敏诊断包",
                defaultextension=".zip",
                initialdir=str(export_folder),
                initialfile=f"workflow-monitor-diagnostics-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip",
                filetypes=(("ZIP 文件", "*.zip"),),
            )
            if not selected:
                self.notify("已取消导出诊断包")
                return
            # Imported lazily to avoid a module cycle during CLI startup.
            from .cli import diagnostic_check_snapshot
            result = export_bundle(self.project_root, Path(selected), diagnostic_check_snapshot)
            self.notify(
                f"诊断包已保存：{result['output']}｜导出批次 {result.get('export_id')}（不会自动上传）"
            )
            self.refresh()
        except Exception as exc:
            record = record_failure(
                self.project_root, exc, command="dashboard.diagnostics.export",
                application_version=__version__, schema_version=SCHEMA_VERSION,
                execution_mode=execution_mode(),
            )
            self.notify(f"导出失败 [{record['code']}]，事件编号：{record['incident_id']}")

    def report_bug(self) -> None:
        try:
            from .diagnostics import diagnostics_status
            latest = diagnostics_status(self.project_root).get("latest_incident_id")
            opened = open_dashboard_bug(incident_id=latest)
            self.notify("已打开 GitHub Bug 报告，请检查并手工附加诊断 ZIP" if opened else "无法打开浏览器，请手工访问项目 Issues")
        except Exception as exc:
            self.notify(f"无法打开 Bug 报告：{exc}")

    def resolve_diagnostic_issue(self, fingerprint: str) -> None:
        try:
            resolved = resolve_diagnostic(
                self.project_root, fingerprint, reason="用户在 Dashboard 确认问题已解决",
                application_version=__version__,
            )
            cleaned = cleanup_resolved_diagnostics(self.project_root)
            if cleaned.get("status") != "cleaned":
                raise DashboardError("诊断解决回执已保存，但清理失败")
            self.notify(
                f"已解决并删除指纹 {resolved['fingerprint']} 的 {cleaned.get('records', 0)} 条本地诊断记录"
            )
            self.refresh()
        except Exception as exc:
            self.notify(f"无法清理诊断：{exc}")

    def refresh(self) -> None:
        self.request_refresh(force_full=True)

    def _section_changed(self, name: str, value: object) -> bool:
        revision = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if self.section_revisions.get(name) == revision:
            return False
        self.section_revisions[name] = revision
        return True

    def apply_snapshot(self, snapshot: dict) -> None:
        health = snapshot["health"]
        self.branch_label.configure(text=branch_status_text(
            snapshot["branch"], snapshot["classification"],
        ))
        warning = snapshot.get("active_task_warning")
        health_text = f"数据库：{health['status']}"
        if warning:
            health_text += f" | {warning['message']}"
        self.health_label.configure(text=health_text)
        overview_source = {
            "profile": (snapshot.get("context") or {}).get("project_profile"),
            "state": (snapshot.get("context") or {}).get("overview_state"),
            "stage": (snapshot.get("context") or {}).get("current_stage"),
            "attempts": (snapshot.get("context") or {}).get("active_attempts"),
            "handoffs": (snapshot.get("context") or {}).get("recent_handoffs"),
            "health": health,
            "catalog": snapshot.get("catalog_items", []),
            "delivery": (self.action_matrix.state if self.action_matrix is not None else None),
        }
        if self._section_changed("overview", overview_source):
            overview = self.overview_text(
                snapshot,
                self.action_matrix.state if self.action_matrix is not None else None,
            )
            self.overview.configure(state="normal")
            self.overview.delete("1.0", "end")
            self.overview.insert("1.0", overview)
            self.overview.configure(state="disabled")
        catalog_source = (
            snapshot.get("catalog_items", []), snapshot.get("catalog_relations", []),
            snapshot.get("resource_directories", []),
        )
        if self._section_changed("catalog", catalog_source):
            self.catalog_page.set_data(*catalog_source)
        if self._section_changed("workbench", snapshot.get("external_tools", [])):
            self.workbench_page.set_external_tools(snapshot.get("external_tools", []))
        if self._section_changed("diagnostics", snapshot.get("diagnostics", {})):
            self.diagnostics_page.set_data(snapshot.get("diagnostics", {}))
        if self.advanced_window is not None and self._section_changed("advanced", snapshot.get("events", [])):
            self.advanced_window.set_snapshot(snapshot)
            self.advanced_window.set_software_delivery(self.software_delivery_result)
        if self.global_query.get().strip():
            self.update_search_results(switch=False)

    def perform_global_search(self) -> None:
        self.update_search_results(switch=True)

    def clear_global_search(self) -> None:
        self.global_query.set("")
        self.search_page.set_records([])
        self.notebook.tab(self.search_frame, text="搜索")

    def update_search_results(self, *, switch: bool) -> None:
        records = global_search((self.snapshot or {}).get("search_index", []), self.global_query.get())
        self.search_page.set_records(records)
        self.notebook.tab(self.search_frame, text=f"搜索（{len(records)}）" if self.global_query.get().strip() else "搜索")
        if switch:
            self.notebook.select(self.search_frame)

    def open_search_result(self, record: dict) -> None:
        target, record_id = record_location(record)
        self.open_target_record(target, record_id)

    def open_target_record(self, target: str | None, record_id: str | None) -> bool:
        if target == "history":
            task_id = next((item.get("task_id") for item in (self.snapshot or {}).get("history", []) if item.get("event_id") == record_id), None)
            if task_id:
                self.open_task(task_id)
                return True
            found = False
        elif target in {"decisions", "explorations"} and record_id:
            found = self.workflow_page.open_record(target, record_id)
            self.notebook.select(self.workflow_page.frame)
            return found
        elif target == "catalog" and record_id:
            found = self.catalog_page.select_record(record_id)
            self.notebook.select(self.catalog_page.frame)
            return found
        elif target == "events":
            return self.open_advanced(record_id)
        self.status.configure(text=f"找不到关联记录：{target or '未知类型'} / {record_id or '未知 ID'}")
        return False

    def open_advanced(self, event_id: str | None = None) -> bool:
        if self.advanced_window is None:
            self.advanced_window = AdvancedWindow(
                self.root, self.tk, self.ttk, self.scrolledtext,
                open_task=self.open_record_task, on_close=self.close_advanced,
            )
        if self.snapshot:
            self.advanced_window.set_snapshot(self.snapshot)
        self.advanced_window.set_software_delivery(self.software_delivery_result)
        if self.software_delivery_result is None:
            self.request_software_delivery(initial=True)
        self.advanced_window.focus()
        if event_id and not self.advanced_window.select_event(event_id):
            self.status.configure(text=f"高级查看中找不到事件：{event_id}")
            return False
        return True

    def close_advanced(self) -> None:
        self.advanced_window = None

    def open_related_record(self, record: dict) -> None:
        self.open_target_record(*record_location(record))

    def open_record_task(self, record: dict) -> None:
        task_ids = linked_task_ids(record)
        if not task_ids:
            self.status.configure(text="该记录没有关联任务。")
            return
        self.open_task(task_ids[0])

    def open_task(self, task_id: str) -> None:
        if self.workflow_page.open_task(task_id):
            self.notebook.select(self.workflow_page.frame)
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        else:
            self.status.configure(text=f"找不到关联任务：{task_id}")

    @staticmethod
    def overview_text(
        snapshot: dict, delivery_state: dict | None = None,
    ) -> str:
        context = snapshot.get("context") or {}
        profile = context.get("project_profile") or {}
        health = snapshot.get("health") or {}
        state = context.get("overview_state") or context.get("state") or {}
        active = context.get("active_task") or {}
        stage = context.get("current_stage") or {}
        attempts = context.get("active_attempts") or []
        separator = "─" * 32
        steps = state.get("next_steps") or []
        step_text = "\n".join(
            f"{index}. {item}" for index, item in enumerate(steps, 1)
        ) or "无。"
        attempt_text = "；".join(
            f"{item.get('branch')}（{item.get('current_step') or '未记录当前步骤'}）"
            for item in attempts
        ) or "无 active 探索"
        handoffs = context.get("recent_handoffs") or []
        if handoffs:
            latest = handoffs[0]
            latest_text = (
                f"{latest.get('occurred_at') or '未知时间'}｜"
                f"{latest.get('task') or latest.get('task_id') or '未知任务'}｜"
                f"{latest.get('result') or '未知结果'}"
            )
        else:
            latest_text = "无"
        if delivery_state is None:
            fallback_git = context.get("git_state") or {}
            delivery_state = {
                "application_version": __version__, "dirty_paths": [], "changed_paths": [],
                "git": fallback_git, "active_task": active, "build_identity": {},
            }
        return "\n".join([
            "项目概览",
            f"项目描述：{profile.get('description') or '未设置'}",
            f"大目标：{profile.get('big_goal') or '未设置'}",
            f"当前分支：{snapshot.get('branch') or '未知'}",
            f"项目健康：{health.get('status') or '未知'}",
            separator,
            "",
            "当前状态",
            f"状态：{state.get('status') or '未设置'}",
            f"活动任务：{active.get('task_id') or '无'}",
            f"当前阶段：{stage.get('title') or '无 active 阶段'}",
            f"活动探索：{attempt_text}",
            f"当前判决：{state.get('judgment') or '未设置'}",
            f"工作断点：{state.get('breakpoint') or '未设置'}",
            f"当前阻塞：{state.get('blocker') or '无'}",
            "下一步：",
            step_text,
            separator,
            "",
            "代码交付",
            delivery_status_text(delivery_state),
            f"最近完成：{latest_text}",
            separator,
            "",
            "资料概览",
            catalog_overview_text(snapshot.get("catalog_items", [])),
            separator,
        ]) + "\n"

    def copy_overview(self) -> None:
        if not self.snapshot:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.overview_text(
            self.snapshot,
            self.action_matrix.state if self.action_matrix is not None else None,
        ))

    @staticmethod
    def history_detail(record: dict) -> str:
        payload = record.get("payload_json")
        try:
            payload = json.loads(payload) if isinstance(payload, str) else payload
        except json.JSONDecodeError:
            pass
        return (f"任务：{record.get('task_id')}\n时间：{record.get('occurred_at')}\n分支：{record.get('branch')}\n"
                f"结果：{record.get('result')}\n\n摘要\n{record.get('summary', '')}\n\n证据\n{record.get('evidence', '')}\n\n"
                f"完整记录\n{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}")

    @staticmethod
    def decision_detail(record: dict) -> str:
        return (f"决策 ID：{record.get('decision_id')}\n时间：{record.get('occurred_at')}\n分支：{record.get('branch')}\n\n"
                f"决策\n{record.get('decision', '')}\n\n替代方案\n{record.get('alternatives', '')}\n\n"
                f"依据\n{record.get('basis', '')}\n\n重开条件\n{record.get('reopen_condition', '')}")

    @classmethod
    def record_detail(cls, record: dict) -> str:
        source = record.get("_source", record)
        if record.get("record_type") == "decision":
            return cls.decision_detail(source)
        return cls.exploration_detail(source)

    @staticmethod
    def exploration_detail(record: dict) -> str:
        attempt = record.get("_attempt")
        if attempt:
            return (f"分支：{attempt['branch']}\n状态：{attempt['state']}\n类型：{attempt['track']}\n基线：{attempt['base_commit']}\n\n"
                    f"所属阶段：{attempt.get('stage_id') or '未归属阶段'}\n\n目标\n{attempt['goal']}\n\n"
                    f"当前步骤\n{attempt.get('current_step') or '未填写'}\n\n"
                    f"进展\n{attempt.get('progress') or '未填写'}\n\n"
                    f"下一步\n{attempt.get('next_step') or '未填写'}\n\n"
                    f"假设\n{attempt.get('hypothesis') or '未填写'}\n\n证据\n"
                    + "\n".join(f"- {item}" for item in attempt.get("evidence", []))
                    + f"\n\n结论\n{attempt.get('conclusion') or '未填写'}")
        return (f"分支：{record.get('branch')}\n时间：{record.get('occurred_at')}\n结果：{record.get('result')}\n\n"
                f"目标\n{record.get('goal', '')}\n\n证据\n{record.get('evidence', '')}\n\n去向\n{record.get('disposition_ref', '')}")

    @staticmethod
    def search_result_detail(record: dict) -> str:
        return (
            f"类型：{record.get('kind_label')}\n时间：{record.get('occurred_at')}\n分支：{record.get('branch')}\n\n"
            f"标题\n{record.get('title', '')}\n\n摘要\n{record.get('summary', '')}\n\n"
            f"双击记录或点击“打开记录”可定位到原分页。"
        )

    @staticmethod
    def event_detail(record: dict) -> str:
        return json.dumps(record, ensure_ascii=False, indent=2, default=str)


def import_tk():
    try:
        import tkinter as tk
        from tkinter import scrolledtext, ttk
        return tk, ttk, scrolledtext
    except (ImportError, RuntimeError) as exc:
        raise DashboardError(f"无法加载 Tkinter: {exc}\n{CLI_FALLBACK}") from exc


def launch_dashboard(provider: DashboardDataProvider, refresh_seconds: float, *, root_factory=None) -> None:
    tk, ttk, scrolledtext = import_tk()
    try:
        root = root_factory() if root_factory else tk.Tk()
    except Exception as exc:
        raise DashboardError(f"无法创建图形窗口: {exc}\n{CLI_FALLBACK}") from exc
    DashboardApp(root, tk, ttk, scrolledtext, provider, refresh_seconds)
    root.mainloop()
