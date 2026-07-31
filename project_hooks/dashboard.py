"""Read-only Tkinter dashboard for project maintenance data."""

from __future__ import annotations

import json
import platform
import subprocess
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Callable

from .catalog import CATALOG_KIND_LABELS, render_context_markdown
from .read_model import (
    MaintenanceReadModel,
    ReadModelError,
    action_overview_text,
    is_auxiliary_task_id,
)
from .updater import check_latest_update, version_report


CLI_FALLBACK = (
    "可改用以下只读命令：\n"
    "  .\\project-hooks.exe context\n"
    "  .\\project-hooks.exe history\n"
    "  .\\project-hooks.exe decisions\n"
    "  .\\project-hooks.exe explorations\n"
    "  .\\project-hooks.exe catalog list\n"
    "  .\\project-hooks.exe db status"
)

CODEX_CATALOG_SCAN_PROMPT = """请立即在当前项目执行科研资料扫描和索引登记，不要只提供方案。

执行范围和约束：
0. 以下所有 `project-hooks` 命令均使用项目根目录的 `.\\project-hooks.exe` 执行。
1. 先运行 `project-hooks context --format markdown`，并按 core_read_order 阅读项目规范。
2. 只扫描项目内的五个标准目录：source/、data/、theory/、analysis/、outputs/。
3. 先运行 `project-hooks catalog scan --dry-run`；存在变化时再运行实际的 `catalog scan`。
4. 扫描应新增尚未登记的文件、更新已登记文件的大小和修改时间，并将消失文件标记为 missing。
5. 如果已有活动维护任务，复用它并且绝不替用户结束；如果没有活动任务，仅在确有变化时创建一个
   YYYYMMDD_catalog_scan_NNN stable 小任务，完成扫描、状态更新、校验和 end。
6. 完成后运行 `project-hooks db verify`。不要修改代码，不要直接编辑 SQLite 或既有事件，
   不要读取 Zotero 的随机存储目录，不要提交或推送，不要操作项目外文件。
7. 如果当前处于 Plan Mode 或规则禁止执行，请明确说明原因，不要绕过限制。

完成后请简短报告新增、更新、缺失和未变化的资料数量。
"""


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
            "仅在 main、无活动任务、工作树干净且与 origin/main 同步时执行 `project-hooks update`。"
            "升级完成后运行 `project-hooks check` 和 `project-hooks db verify`，不自动提交。"
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
            "project-hooks check 结果",
            "project-hooks db verify 结果",
            "事件数量、schema 和日志摘要",
            "Git Hook 与版本兼容状态",
            "问题分级和修复建议",
        ),
        "extra_rules": "依次执行 `project-hooks version`、`project-hooks check` 和 `project-hooks db verify`；不应用修复。",
    },
    "update_failure_diagnosis": {
        "category": "软件诊断",
        "label": "诊断升级失败",
        "task": "分析 project-hooks 安装或升级失败的原因，优先使用只读证据并确认项目数据未受损。",
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
0. 以下所有 `project-hooks` 命令均使用项目根目录的 `.\\project-hooks.exe` 执行。
1. 只使用当前 Codex 对话中已经选择的文件、已有消息和我提供的资料作为已有证据。
2. 如果材料不足，先明确指出还需要选择或提供哪些文件；停止补造事实、数据、引文或实验结果。
3. 明确区分“已有证据”“合理推断”和“待验证建议”。
4. 论述时尽量引用资料 ID、标题或项目相对路径，使结论可以回查。
5. 先完成分析，再单独列出建议保存的资料条目、资料关系、项目决策或探索记录。
6. 未经我在对话中明确确认，不得修改项目文件、数据库、事件或 Git 状态。
7. 如果我确认记录，再先运行 `project-hooks context --format markdown` 并按 core_read_order 阅读规范；复用已有活动任务且不替我结束，或按规范创建 stable 任务。只通过现有 catalog、decision、attempt 和 state 命令记录，完成后更新项目概览；仅结束由你创建的任务。
"""

SOFTWARE_PROMPT_COMMON_RULES = """请遵守以下规则：
0. 以下所有 `project-hooks` 命令均使用项目根目录的 `.\\project-hooks.exe` 执行。
1. 先确认当前操作针对工作流软件开发仓库、普通科研项目还是临时测试仓库，不得混淆目标。
2. 除首次初始化外，先运行 `project-hooks context --format markdown` 并按 core_read_order 阅读规范。
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


def copy_catalog_scan_prompt(clipboard) -> str:
    clipboard.clipboard_clear()
    clipboard.clipboard_append(CODEX_CATALOG_SCAN_PROMPT)
    return "扫描提示词已复制，请粘贴到当前 Codex 对话框并发送。"


PRIMARY_TABS = ("概览", "搜索", "资料", "工作台", "任务", "时间线", "记录")

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
    timeline_error = snapshot.get("timeline_error") or "无"
    return (
        f"数据库：{health.get('status', '未知')}　Schema：{health.get('schema_version', '未知')}　"
        f"事件：{health.get('events', 0)}　本次重建：{'是' if health.get('rebuilt') else '否'}\n"
        f"事件日志哈希：{health.get('journal_hash') or '未知'}\n时间线警告：{timeline_error}"
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
        "unmerged": [
            item["name"] for item in snapshot.get("timeline", {}).get("branches", [])
            if item.get("unmerged")
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


def timeline_layout(
    timeline: dict,
    branch: str = "全部分支",
    query: str = "",
    *,
    expanded: bool = False,
    limit: int = 50,
    branch_subset: set[str] | None = None,
) -> dict:
    """Return deterministic coordinates and visibility for the timeline canvas."""
    commits = list(timeline.get("commits", []))
    all_lanes = list(timeline.get("lanes", []))
    if branch_subset is not None:
        commits = [
            commit for commit in commits
            if commit.get("lane") in branch_subset or branch_subset.intersection(commit.get("event_branches", []))
        ]
        used = set(branch_subset)
        used.update(commit.get("lane") for commit in commits)
        lanes = [lane for lane in all_lanes if lane in used]
    elif branch != "全部分支":
        commits = [
            commit for commit in commits
            if commit.get("lane") == branch or branch in commit.get("event_branches", [])
        ]
        used = {branch}
        used.update(commit.get("lane") for commit in commits)
        lanes = [lane for lane in all_lanes if lane in used]
    else:
        lanes = all_lanes

    total_count = len(commits)
    folded_count = 0 if expanded or total_count <= limit else total_count - limit
    hidden_hashes = {commit["hash"] for commit in commits[:folded_count]}
    if folded_count:
        commits = commits[folded_count:]
    for commit in commits:
        if commit.get("lane") not in lanes:
            lanes.append(commit.get("lane") or "其他")
        for event_branch in commit.get("event_branches", []):
            if branch == "全部分支" and event_branch not in lanes:
                lanes.append(event_branch)

    lane_y = {lane: 48 + index * 72 for index, lane in enumerate(lanes)}
    needle = query.strip().casefold()
    nodes: list[dict] = []
    coordinates: dict[str, tuple[int, int]] = {}
    start_x = 170 if folded_count else 70
    for index, commit in enumerate(commits):
        x = start_x + index * 132
        y = lane_y[commit.get("lane") or "其他"]
        searchable = json.dumps(commit, ensure_ascii=False, default=str).casefold()
        node = {**commit, "x": x, "y": y, "match": not needle or needle in searchable}
        nodes.append(node)
        coordinates[commit["hash"]] = (x, y)

    visible = set(coordinates)
    edges = [
        {**edge, "start": coordinates[edge["parent"]], "end": coordinates[edge["child"]]}
        for edge in timeline.get("edges", [])
        if edge["parent"] in visible and edge["child"] in visible
    ]
    truncated = [
        {**edge, "end": coordinates[edge["child"]]}
        for edge in timeline.get("edges", [])
        if edge["parent"] in hidden_hashes and edge["child"] in visible
    ]
    associations = []
    for node in nodes:
        for event_branch in node.get("event_branches", []):
            if event_branch != node.get("lane") and event_branch in lane_y:
                associations.append({
                    "commit": node["hash"], "branch": event_branch,
                    "start": (node["x"], node["y"]), "end": (node["x"], lane_y[event_branch]),
                })
    return {
        "lanes": lanes,
        "lane_y": lane_y,
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
        "associations": associations,
        "total_count": total_count,
        "folded_count": folded_count,
        "width": max(900, start_x + 70 + len(nodes) * 132),
        "height": max(160, 92 + len(lanes) * 72),
    }


class DashboardDataProvider:
    def __init__(self, model: MaintenanceReadModel, classifier: Callable[[str], dict], branch: str | None = None):
        self.model = model
        self.classifier = classifier
        self.branch = branch

    def load(self) -> dict:
        snapshot = self.model.dashboard_snapshot(self.branch)
        snapshot["classification"] = self.classifier(snapshot["branch"])
        return snapshot

    @property
    def project_root(self) -> Path:
        return self.model.database_path.parent.parent

    def version_info(self) -> dict:
        return version_report(self.project_root)

    def check_for_updates(self) -> dict:
        return check_latest_update(self.project_root)


def version_status_text(report: dict) -> str:
    project = report.get("project_version") or "未初始化"
    return f"版本：EXE {report['application_version']} / 项目 {project}"


def update_status_text(result: dict | None = None, error: str | None = None) -> str:
    if error:
        return "更新：检查失败"
    if result and result.get("status") == "update-available":
        return f"更新：发现 {result['latest_version']}"
    return "更新：已是最新版"


class DashboardController:
    """Keeps the last successful snapshot when a refresh fails."""

    def __init__(self, provider: DashboardDataProvider):
        self.provider = provider
        self.snapshot: dict | None = None

    def refresh(self) -> tuple[dict | None, str | None]:
        try:
            snapshot = self.provider.load()
            if snapshot.get("timeline_error") and self.snapshot and self.snapshot.get("timeline", {}).get("status") == "passed":
                snapshot["timeline"] = deepcopy(self.snapshot["timeline"])
                snapshot["timeline"]["stale"] = True
                old_commits = [item for item in self.snapshot.get("search_index", []) if item.get("kind") == "commit"]
                current = [item for item in snapshot.get("search_index", []) if item.get("kind") != "commit"]
                snapshot["search_index"] = sorted(
                    [*current, *deepcopy(old_commits)],
                    key=lambda item: (item.get("occurred_at", ""), item.get("record_id", "")),
                    reverse=True,
                )
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
    ):
        self.frame = ttk.Frame(parent, padding=8)
        self.tk = tk
        self.ttk = ttk
        self.scrolledtext = scrolledtext
        self.project_root = project_root
        self.notify = notify
        self.items: list[dict] = []
        self.relations: list[dict] = []
        self.prompt_window = None

        filters = ttk.Frame(self.frame)
        filters.pack(fill="x", pady=(0, 6))
        ttk.Label(filters, text="类型").pack(side="left")
        self.kind = tk.StringVar(value="全部")
        kind_values = ("全部", *CATALOG_KIND_LABELS.values())
        kind_box = ttk.Combobox(filters, textvariable=self.kind, values=kind_values, state="readonly", width=9)
        kind_box.pack(side="left", padx=(5, 10))
        ttk.Label(filters, text="状态").pack(side="left")
        self.status = tk.StringVar(value="全部")
        status_box = ttk.Combobox(
            filters, textvariable=self.status,
            values=("全部", "active", "missing", "archived"), state="readonly", width=10,
        )
        status_box.pack(side="left", padx=(5, 10))
        ttk.Label(filters, text="标签").pack(side="left")
        self.tags = tk.StringVar()
        ttk.Entry(filters, textvariable=self.tags, width=22).pack(side="left", padx=(5, 10))
        ttk.Button(filters, text="打开所在位置", command=self.open_location).pack(side="left")
        ttk.Button(filters, text="复制路径", command=self.copy_path).pack(side="left")
        ttk.Button(filters, text="复制 AI 上下文", command=self.copy_context).pack(side="left", padx=(6, 0))
        self.prompt_button = ttk.Button(
            filters, text="复制 Codex 扫描提示词", command=self.show_codex_scan_prompt,
        )
        self.prompt_button.pack(side="left", padx=(6, 0))

        self.table = TablePage(
            self.frame, tk, ttk, scrolledtext,
            columns=[
                ("kind_label", "类型", 75), ("status", "状态", 85),
                ("title", "标题", 300), ("path", "路径", 300), ("updated_at", "更新时间", 175),
            ],
            detail=self.detail_text, refresh=refresh, show_refresh=True, split_detail=True,
        )
        self.table.frame.pack(fill="both", expand=True)
        self.kind.trace_add("write", lambda *_: self.apply_filters())
        self.status.trace_add("write", lambda *_: self.apply_filters())
        self.tags.trace_add("write", lambda *_: self.apply_filters())

    def set_data(self, items: list[dict], relations: list[dict]) -> None:
        self.items = [dict(item) for item in items]
        self.relations = [dict(relation) for relation in relations]
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
        self.table.set_records(self.items)
        self.apply_filters()

    def apply_filters(self) -> None:
        label = self.kind.get()
        selected_kind = next(
            (kind for kind, kind_label in CATALOG_KIND_LABELS.items() if kind_label == label),
            None,
        )
        selected_status = None if self.status.get() == "全部" else self.status.get()
        required_tags = {
            tag.strip() for raw in self.tags.get().split(",") for tag in [raw] if tag.strip()
        }

        def predicate(item: dict) -> bool:
            return (
                (selected_kind is None or item.get("kind") == selected_kind)
                and (selected_status is None or item.get("status") == selected_status)
                and required_tags.issubset(set(item.get("tags", [])))
            )

        self.table.set_predicate(predicate)

    def select_record(self, item_id: str) -> bool:
        self.kind.set("全部")
        self.status.set("全部")
        self.tags.set("")
        return self.table.select_record("item_id", item_id)

    def selected_item(self) -> dict | None:
        return self.table.selected_record()

    def copy_path(self) -> None:
        item = self.selected_item()
        if not item or not item.get("path"):
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(item["path"])

    def open_location(self) -> None:
        item = self.selected_item()
        if not item:
            self.notify("没有选中科研资料。")
            return
        try:
            self.notify(reveal_catalog_file(self.project_root, item))
        except DashboardError as exc:
            self.notify(str(exc))

    def copy_context(self) -> None:
        item = self.selected_item()
        if not item:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(render_context_markdown([item], item.get("_relations", [])))

    def show_codex_scan_prompt(self) -> None:
        message = copy_catalog_scan_prompt(self.frame)
        self.notify(message)
        if self.prompt_window is not None and self.prompt_window.winfo_exists():
            self.prompt_window.deiconify()
            self.prompt_window.lift()
            return
        window = self.tk.Toplevel(self.frame.winfo_toplevel())
        window.title("Codex 扫描提示词")
        window.geometry("720x500")
        window.transient(self.frame.winfo_toplevel())
        self.ttk.Label(
            window,
            text="提示词已复制。请切换到 Codex 对话框，粘贴并发送。",
            padding=(10, 10, 10, 4),
        ).pack(fill="x")
        preview = self.scrolledtext.ScrolledText(window, wrap="word")
        preview.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        preview.insert("1.0", CODEX_CATALOG_SCAN_PROMPT)
        preview.configure(state="disabled")
        actions = self.ttk.Frame(window, padding=(10, 0, 10, 10))
        actions.pack(fill="x")
        self.ttk.Button(actions, text="关闭", command=window.destroy).pack(side="right")
        self.ttk.Button(
            actions,
            text="再次复制",
            command=lambda: self.notify(copy_catalog_scan_prompt(self.frame)),
        ).pack(side="right", padx=(0, 6))
        self.prompt_window = window

    @staticmethod
    def detail_text(item: dict) -> str:
        tags = ", ".join(item.get("tags", [])) or "无"
        metadata = json.dumps(item.get("metadata", {}), ensure_ascii=False, indent=2)
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
            f"来源：{item.get('source') or '未记录'}\n标签：{tags}\n"
            f"更新时间：{item.get('updated_at')}\n{missing}\n"
            f"摘要\n{item.get('summary') or '暂无摘要。'}\n\n"
            f"扩展信息\n{metadata}\n\n关联\n"
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
        self.visible: list[dict] = []
        self.current_template_id: str | None = None

        controls = ttk.Frame(self.frame)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Label(controls, text="类型").pack(side="left")
        self.category = tk.StringVar(value="全部")
        ttk.Combobox(
            controls,
            textvariable=self.category,
            values=WORKBENCH_PROMPT_CATEGORIES,
            state="readonly",
            width=10,
        ).pack(side="left", padx=(6, 12))
        ttk.Label(controls, text="筛选提示词").pack(side="left")
        self.query = tk.StringVar()
        ttk.Entry(controls, textvariable=self.query, width=34).pack(side="left", padx=(6, 8))
        ttk.Label(
            controls,
            text="科研分析先选择文件；软件操作先确认目标仓库。",
        ).pack(side="left")
        self.category.trace_add("write", lambda *_: self.render_list())
        self.query.trace_add("write", lambda *_: self.render_list())

        panes = ttk.Panedwindow(self.frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        list_frame = ttk.Frame(panes, padding=(0, 0, 6, 0))
        detail_frame = ttk.Frame(panes, padding=(6, 0, 0, 0))
        panes.add(list_frame, weight=2)
        panes.add(detail_frame, weight=3)

        self.tree = ttk.Treeview(
            list_frame, columns=("category", "label"), show="headings", height=18,
        )
        for key, label, width in (
            ("category", "类型", 105),
            ("label", "提示词", 230),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=80, stretch=True)
        vertical = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vertical.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.select_from_tree)

        detail_controls = ttk.Frame(detail_frame)
        detail_controls.pack(fill="x", pady=(0, 4))
        ttk.Label(detail_controls, text="完整提示词").pack(side="left")
        ttk.Button(
            detail_controls, text="复制提示词", command=self.copy_current,
        ).pack(side="right")
        self.preview = scrolledtext.ScrolledText(detail_frame, wrap="word")
        self.preview.pack(fill="both", expand=True)
        self.render_list()

    def render_list(self) -> None:
        previous = self.current_template_id
        self.visible = research_prompt_records(self.query.get(), self.category.get())
        self.tree.delete(*self.tree.get_children())
        selected_iid = None
        for index, record in enumerate(self.visible):
            iid = f"prompt-{index}"
            self.tree.insert(
                "", "end", iid=iid,
                values=(record["category"], record["label"]),
            )
            if record["template_id"] == previous:
                selected_iid = iid
        if selected_iid is None and self.visible:
            selected_iid = "prompt-0"
            self.current_template_id = self.visible[0]["template_id"]
        if selected_iid is not None:
            self.tree.selection_set(selected_iid)
            self.tree.focus(selected_iid)
            self.tree.see(selected_iid)
            self.render_detail()
        else:
            self.current_template_id = None
            self._set_preview("没有匹配的提示词。")

    def select_from_tree(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            self.current_template_id = None
            self._set_preview("请选择左侧提示词。")
            return
        try:
            record = self.visible[int(selection[0].split("-", 1)[1])]
        except (IndexError, ValueError):
            self.current_template_id = None
            self._set_preview("请选择左侧提示词。")
            return
        self.current_template_id = record["template_id"]
        self.render_detail()

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
        linked_commits = "\n".join(f"- {item}" for item in task.get("linked_commits", [])) or "无"
        publication_commits = "\n".join(f"- {item}" for item in task.get("publication_commits", [])) or "无"
        auxiliary_lines = []
        for item in task.get("auxiliary_tasks", []):
            auxiliary_lines.append(
                f"- {item['finished_at'] or item['started_at']}｜{item['goal']}｜"
                f"{result_label(item['result'])}｜{item['task_id']}"
            )
            if item.get("conclusion"):
                auxiliary_lines.append(f"  {item['conclusion']}")
        auxiliary_tasks = "\n".join(auxiliary_lines) or "无"
        self._set_summary(
            f"任务：{task['task_id']}\n分支：{task.get('branch') or '未知'}\n"
            f"开始：{task.get('started_at') or '未知'}\n结束：{task.get('finished_at') or '进行中'}\n"
            f"结果：{result_label(task.get('result'))}　状态：{task.get('status') or '未知'}　"
            f"路线：{task.get('route') or '未记录'}\n\n"
            f"目标\n{task.get('goal') or '未记录'}\n\n验收条件\n{acceptance}\n\n"
            f"结论\n{task.get('conclusion') or '未记录'}\n\n证据\n{evidence}\n\n"
            f"关联提交\n{linked_commits}\n\n已发布提交\n{publication_commits}\n\n"
            f"发布记录\n{auxiliary_tasks}"
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


class RecordsPage:
    FILTERS = {"全部记录": None, "仅决策": "decision", "仅探索": "exploration"}

    def __init__(self, parent, tk, ttk, scrolledtext, *, open_task: Callable[[dict], None],
                 detail: Callable[[dict], str]):
        self.frame = ttk.Frame(parent, padding=8)
        self.records: list[dict] = []
        controls = ttk.Frame(self.frame)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Label(controls, text="类型").pack(side="left")
        self.kind = tk.StringVar(value="全部记录")
        kind_box = ttk.Combobox(controls, textvariable=self.kind, values=list(self.FILTERS), state="readonly", width=12)
        kind_box.pack(side="left", padx=(6, 12))
        kind_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_filter())
        self.table = TablePage(
            self.frame, tk, ttk, scrolledtext,
            columns=[("kind_label", "类型", 80), ("occurred_at", "时间", 180), ("branch", "分支", 150),
                     ("title", "标题", 390), ("result", "结果", 110)],
            detail=detail, refresh=lambda: None, activate=open_task, activate_label="打开关联任务",
            split_detail=True,
        )
        self.table.frame.pack(fill="both", expand=True, padx=0, pady=0)

    def set_records(self, records: list[dict]) -> None:
        self.records = list(records)
        self.apply_filter()

    def apply_filter(self) -> None:
        selected = self.FILTERS.get(self.kind.get())
        self.table.predicate = None
        self.table.set_records([
            record for record in self.records if selected is None or record.get("record_type") == selected
        ])

    def select_record(self, event_id: str) -> bool:
        record = next((item for item in self.records if item.get("event_id") == event_id), None)
        if not record:
            return False
        self.kind.set("全部记录")
        self.apply_filter()
        return self.table.select_record("event_id", event_id)

    def set_negative_only(self, event_ids: set[str] | None) -> None:
        self.kind.set("全部记录" if event_ids is None else "仅探索")
        self.apply_filter()
        self.table.set_predicate(None if event_ids is None else lambda item, ids=event_ids: item.get("event_id") in ids)


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


class TimelinePage:
    ALL_BRANCHES = "全部分支"
    FOLD_LIMIT = 50

    def __init__(self, parent, tk, ttk, scrolledtext, *, refresh: Callable[[], None],
                 open_task: Callable[[str], None]):
        self.tk, self.ttk = tk, ttk
        self.frame = ttk.Frame(parent, padding=8)
        self.refresh_callback = refresh
        self.timeline: dict = {"lanes": [], "commits": [], "edges": []}
        self.selected_hash: str | None = None
        self.node_by_hash: dict[str, dict] = {}
        self.first_render = True
        self.expanded = False
        self.branch_subset: set[str] | None = None
        self.error_message: str | None = None
        self.open_task_callback = open_task

        controls = ttk.Frame(self.frame)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Label(controls, text="分支").pack(side="left")
        self.branch = tk.StringVar(value=self.ALL_BRANCHES)
        self.branch_box = ttk.Combobox(controls, textvariable=self.branch, state="readonly", width=24)
        self.branch_box.pack(side="left", padx=(6, 12))
        self.branch_box.bind("<<ComboboxSelected>>", self.branch_changed)
        ttk.Label(controls, text="本页高亮").pack(side="left")
        self.query = tk.StringVar()
        entry = ttk.Entry(controls, textvariable=self.query, width=30)
        entry.pack(side="left", padx=(6, 8))
        self.query.trace_add("write", lambda *_: self.render())
        ttk.Button(controls, text="复制选中", command=self.copy_selected).pack(side="left")
        self.fold_button = ttk.Button(controls, text="无需折叠", command=self.toggle_expanded, state="disabled")
        self.fold_button.pack(side="left", padx=(6, 0))

        task_controls = ttk.Frame(self.frame)
        task_controls.pack(fill="x", pady=(0, 6))
        ttk.Label(task_controls, text="关联任务").pack(side="left")
        self.task_id = tk.StringVar()
        self.task_box = ttk.Combobox(task_controls, textvariable=self.task_id, state="readonly", width=38)
        self.task_box.pack(side="left", padx=(6, 8))
        ttk.Button(task_controls, text="打开关联任务", command=self.open_selected_task).pack(side="left")

        ttk.Label(
            self.frame,
            text="● 关联任务的提交　○ 普通提交　实线 Git 父子关系　虚线 跨分支事件关联",
        ).pack(anchor="w", pady=(0, 5))

        graph_frame = ttk.Frame(self.frame)
        graph_frame.pack(fill="both", expand=True)
        self.labels = tk.Canvas(graph_frame, width=180, background="#f8fafc", highlightthickness=0)
        self.canvas = tk.Canvas(graph_frame, background="#ffffff", highlightthickness=1, highlightbackground="#d1d5db")
        vertical = ttk.Scrollbar(graph_frame, orient="vertical", command=self.scroll_y)
        horizontal = ttk.Scrollbar(graph_frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.labels.configure(yscrollcommand=vertical.set)
        self.labels.grid(row=0, column=0, sticky="ns")
        self.canvas.grid(row=0, column=1, sticky="nsew")
        vertical.grid(row=0, column=2, sticky="ns")
        horizontal.grid(row=1, column=1, sticky="ew")
        graph_frame.columnconfigure(1, weight=1)
        graph_frame.rowconfigure(0, weight=1)

        ttk.Label(self.frame, text="提交详情").pack(anchor="w", pady=(8, 3))
        self.detail = scrolledtext.ScrolledText(self.frame, height=9, wrap="word")
        self.detail.pack(fill="x")
        self.detail.configure(state="disabled")
        self.message = ttk.Label(self.frame, text="", foreground="#92400e")
        self.message.pack(fill="x", pady=(4, 0))

    def scroll_y(self, *arguments) -> None:
        self.labels.yview(*arguments)
        self.canvas.yview(*arguments)

    def set_data(self, timeline: dict, *, error: str | None = None) -> None:
        self.timeline = timeline
        self.error_message = error
        lanes = list(timeline.get("lanes", []))
        values = [self.ALL_BRANCHES, *lanes]
        self.branch_box.configure(values=values)
        if self.branch.get() not in values:
            self.branch.set(self.ALL_BRANCHES)
        self.render()

    def branch_changed(self, _event=None) -> None:
        self.branch_subset = None
        self.render()

    def set_branch_subset(self, branches: set[str] | None) -> None:
        self.branch_subset = None if branches is None else set(branches)
        if branches is not None:
            self.branch.set(self.ALL_BRANCHES)
        self.render()

    def toggle_expanded(self) -> None:
        self.expanded = not self.expanded
        self.render()

    def render(self) -> None:
        old_xview = self.canvas.xview()
        layout = timeline_layout(
            self.timeline, self.branch.get(), self.query.get(), expanded=self.expanded,
            limit=self.FOLD_LIMIT, branch_subset=self.branch_subset,
        )
        self.canvas.delete("all")
        self.labels.delete("all")
        self.node_by_hash = {node["hash"]: node for node in layout["nodes"]}
        self.canvas.configure(scrollregion=(0, 0, layout["width"], layout["height"]))
        self.labels.configure(scrollregion=(0, 0, 180, layout["height"]))

        for lane in layout["lanes"]:
            y = layout["lane_y"][lane]
            self.canvas.create_line(0, y, layout["width"], y, fill="#e5e7eb", width=1)
            font = ("TkDefaultFont", 9, "bold") if lane == self.timeline.get("default_branch") else ("TkDefaultFont", 9)
            self.labels.create_text(10, y, anchor="w", text=short(lane, 25), fill="#111827", font=font)

        if layout["folded_count"]:
            self.canvas.create_rectangle(
                8, 8, 125, layout["height"] - 8,
                fill="#f1f5f9", outline="#94a3b8", dash=(4, 3),
            )
            self.canvas.create_text(
                66, layout["height"] / 2,
                text=f"早期 {layout['folded_count']} 次提交\n已折叠\n{len(layout['truncated'])} 条关系截断",
                width=105, justify="center", fill="#475569",
            )
            for edge in layout["truncated"]:
                x2, y2 = edge["end"]
                self.canvas.create_line(125, y2, x2, y2, fill="#94a3b8", dash=(5, 4), width=2)

        for edge in layout["edges"]:
            x1, y1 = edge["start"]
            x2, y2 = edge["end"]
            midpoint = (x1 + x2) / 2
            self.canvas.create_line(x1, y1, midpoint, y1, midpoint, y2, x2, y2, fill="#64748b", width=2)
        for association in layout["associations"]:
            x1, y1 = association["start"]
            x2, y2 = association["end"]
            self.canvas.create_line(x1, y1, x2, y2, fill="#2563eb", dash=(4, 3), width=1)
            self.canvas.create_rectangle(x2 - 4, y2 - 4, x2 + 4, y2 + 4, outline="#2563eb", fill="#ffffff")

        if self.selected_hash not in self.node_by_hash:
            self.selected_hash = layout["nodes"][-1]["hash"] if layout["nodes"] else None
        for node in layout["nodes"]:
            x, y = node["x"], node["y"]
            linked = bool(node.get("task_ids"))
            matched = node["match"]
            fill = "#2563eb" if linked and matched else ("#ffffff" if matched else "#e5e7eb")
            outline = "#b45309" if node["hash"] == self.selected_hash else ("#1e3a8a" if matched else "#9ca3af")
            width = 3 if node["hash"] == self.selected_hash else 2
            tag = f"commit-{node['hash']}"
            if len(node.get("parents", [])) > 1:
                item = self.canvas.create_rectangle(x - 8, y - 8, x + 8, y + 8, fill=fill, outline=outline, width=width, tags=(tag,))
            else:
                item = self.canvas.create_oval(x - 8, y - 8, x + 8, y + 8, fill=fill, outline=outline, width=width, tags=(tag,))
            self.canvas.tag_bind(item, "<Button-1>", lambda _event, commit_hash=node["hash"]: self.select(commit_hash))
            label = f"{node['short_hash']}\n{short(node['subject'], 18)}"
            text_item = self.canvas.create_text(x, y - 28, text=label, width=118, justify="center", fill="#111827" if matched else "#9ca3af", tags=(tag,))
            self.canvas.tag_bind(text_item, "<Button-1>", lambda _event, commit_hash=node["hash"]: self.select(commit_hash))
            if node.get("task_ids"):
                self.canvas.create_text(x, y + 19, text=f"{len(node['task_ids'])} 个任务", fill="#1e3a8a", font=("TkDefaultFont", 8))

        self.show_detail()
        if layout["total_count"] > self.FOLD_LIMIT:
            self.fold_button.configure(
                state="normal",
                text=(f"恢复折叠（保留 {self.FOLD_LIMIT} 条）" if self.expanded else f"展开全部（折叠 {layout['folded_count']} 条）"),
            )
        else:
            self.fold_button.configure(state="disabled", text="无需折叠")
        messages = []
        if self.error_message:
            messages.append(f"时间线刷新失败，继续显示上一次数据：{self.error_message}")
        if self.branch_subset is not None:
            messages.append(
                f"未合并分支：{len(self.branch_subset)} 条" if self.branch_subset
                else "没有未合并的探索分支。"
            )
        if layout["folded_count"]:
            messages.append(f"已折叠早期 {layout['folded_count']} 次提交。")
        self.message.configure(text="｜".join(messages))
        if self.first_render and layout["nodes"]:
            self.canvas.xview_moveto(1.0)
            self.first_render = False
        elif old_xview:
            self.canvas.xview_moveto(old_xview[0])

    def select(self, commit_hash: str) -> None:
        self.selected_hash = commit_hash
        self.render()

    def reveal_commit(self, commit_hash: str) -> bool:
        if not any(item.get("hash") == commit_hash for item in self.timeline.get("commits", [])):
            return False
        self.expanded = True
        self.branch_subset = None
        self.branch.set(self.ALL_BRANCHES)
        self.query.set("")
        self.selected_hash = commit_hash
        self.render()
        record = self.node_by_hash.get(commit_hash)
        if record:
            width = max(1, int(self.canvas.cget("scrollregion").split()[2]))
            viewport = max(1, self.canvas.winfo_width())
            self.canvas.xview_moveto(max(0.0, min(1.0, (record["x"] - viewport / 2) / width)))
        return True

    def selected_record(self) -> dict | None:
        return self.node_by_hash.get(self.selected_hash or "")

    def show_detail(self) -> None:
        record = self.selected_record()
        task_ids = linked_task_ids(record)
        self.task_box.configure(values=task_ids)
        if self.task_id.get() not in task_ids:
            self.task_id.set(task_ids[0] if task_ids else "")
        if record is None:
            text = "没有可显示的提交。"
        else:
            tasks = "\n".join(f"- {item}" for item in record.get("task_ids", [])) or "无"
            event_lines = []
            for item in record.get("events", []):
                payload = item.get("payload", {})
                summary = next((payload.get(key) for key in ("summary", "scope", "goal", "decision", "state") if payload.get(key)), "")
                task = f"｜{item['task_id']}" if item.get("task_id") else ""
                suffix = f"｜{short(summary, 80)}" if summary else ""
                event_lines.append(f"- {item['event_type']}｜{item.get('branch')}{task}{suffix}")
            events = "\n".join(event_lines) or "无"
            text = (
                f"提交：{record['hash']}\n时间：{record['occurred_at']}\n作者：{record['author']}\n"
                f"泳道：{record['lane']}\n引用：{', '.join(record.get('refs', [])) or '无'}\n"
                f"父提交：{', '.join(record.get('parents', [])) or '无'}\n\n标题\n{record['subject']}\n\n"
                f"关联任务\n{tasks}\n\n关联事件\n{events}"
            )
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def open_selected_task(self) -> None:
        if self.task_id.get():
            self.open_task_callback(self.task_id.get())

    def copy_selected(self) -> None:
        record = self.selected_record()
        if not record:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(json.dumps(record, ensure_ascii=False, indent=2, default=str))


class DashboardApp:
    def __init__(self, root, tk, ttk, scrolledtext, provider: DashboardDataProvider, refresh_seconds: float):
        self.root, self.tk, self.ttk = root, tk, ttk
        self.scrolledtext = scrolledtext
        self.controller = DashboardController(provider)
        self.provider = provider
        self.refresh_seconds = refresh_seconds
        self.snapshot: dict | None = None
        self.active_preset: str | None = None
        self.advanced_window: AdvancedWindow | None = None
        root.title("Project Maintenance")
        root.geometry("1120x760")
        root.minsize(800, 560)

        toolbar = ttk.Frame(root, padding=(10, 8))
        toolbar.pack(fill="x")
        self.branch_label = ttk.Label(toolbar, text="分支：加载中")
        self.branch_label.pack(side="left")
        self.health_label = ttk.Label(toolbar, text="数据库：加载中")
        self.health_label.pack(side="left", padx=(18, 0))
        self.version_label = ttk.Label(toolbar, text=version_status_text(provider.version_info()))
        self.version_label.pack(side="left", padx=(18, 0))
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(side="right")
        ttk.Button(toolbar, text="高级查看", command=self.open_advanced).pack(side="right", padx=(0, 6))
        self.update_button = ttk.Button(toolbar, text="检查更新", command=self.check_for_updates)
        self.update_button.pack(side="right", padx=(0, 6))
        self.update_label = ttk.Label(toolbar, text="")
        self.update_label.pack(side="right", padx=(0, 8))

        search_toolbar = ttk.Frame(root, padding=(10, 0, 10, 8))
        search_toolbar.pack(fill="x")
        ttk.Label(search_toolbar, text="全局搜索").pack(side="left")
        self.global_query = tk.StringVar()
        global_entry = ttk.Entry(search_toolbar, textvariable=self.global_query, width=27)
        global_entry.pack(side="left", padx=(6, 5))
        global_entry.bind("<Return>", lambda _event: self.perform_global_search())
        ttk.Button(search_toolbar, text="搜索", command=self.perform_global_search).pack(side="left")
        ttk.Label(search_toolbar, text="快捷筛选").pack(side="left", padx=(18, 5))
        self.preset_buttons = {}
        for name, label in (("recent", "最近任务"), ("negative", "失败探索"), ("unmerged", "未合并分支")):
            button = ttk.Button(search_toolbar, text=label, command=lambda selected=name: self.toggle_preset(selected))
            button.pack(side="left", padx=(0, 5))
            self.preset_buttons[name] = button
        ttk.Button(search_toolbar, text="清除", command=self.clear_preset).pack(side="left")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.overview_frame = ttk.Frame(self.notebook, padding=8)
        overview_actions = ttk.Frame(self.overview_frame)
        overview_actions.pack(fill="x", pady=(0, 6))
        ttk.Button(overview_actions, text="复制概览", command=self.copy_overview).pack(side="left")
        self.overview = scrolledtext.ScrolledText(self.overview_frame, wrap="word")
        self.overview.pack(fill="both", expand=True)
        self.overview.configure(state="disabled")
        self.notebook.add(self.overview_frame, text="概览")

        self.search_page = TablePage(
            self.notebook, tk, ttk, scrolledtext,
            columns=[("kind_label", "类型", 80), ("occurred_at", "时间", 180), ("branch", "分支", 150),
                     ("title", "标题", 320), ("summary", "摘要", 250)],
            detail=self.search_result_detail, refresh=self.refresh,
            activate=self.open_search_result, show_query=False, split_detail=True,
        )
        self.notebook.add(self.search_page.frame, text="搜索")

        self.catalog_page = CatalogPage(
            self.notebook, tk, ttk, scrolledtext, refresh=self.refresh,
            project_root=provider.model.database_path.parent.parent,
            notify=self.notify,
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

        self.task_page = TaskPage(
            self.notebook, tk, ttk, scrolledtext, open_related=self.open_related_record,
        )
        self.notebook.add(self.task_page.frame, text="任务")

        self.timeline_page = TimelinePage(
            self.notebook, tk, ttk, scrolledtext, refresh=self.refresh, open_task=self.open_task,
        )
        self.notebook.add(self.timeline_page.frame, text="时间线")

        self.records_page = RecordsPage(
            self.notebook, tk, ttk, scrolledtext, open_task=self.open_record_task,
            detail=self.record_detail,
        )
        self.notebook.add(self.records_page.frame, text="记录")

        self.status = ttk.Label(root, text="准备刷新", padding=(10, 5))
        self.status.pack(fill="x")
        self.refresh()
        if refresh_seconds > 0:
            root.after(max(250, int(refresh_seconds * 1000)), self.auto_refresh)

    def auto_refresh(self) -> None:
        try:
            if self.root.state() != "iconic":
                self.refresh()
        finally:
            if self.refresh_seconds > 0 and self.root.winfo_exists():
                self.root.after(max(250, int(self.refresh_seconds * 1000)), self.auto_refresh)

    def notify(self, message: str) -> None:
        if hasattr(self, "status"):
            self.status.configure(text=message)

    def check_for_updates(self) -> None:
        self.update_button.configure(state="disabled")
        self.update_label.configure(text="更新：检查中…")

        def worker() -> None:
            detail = ""
            try:
                result = self.provider.check_for_updates()
                message = update_status_text(result)
            except Exception as exc:
                message = update_status_text(error=str(exc))
                detail = f"{message}：{exc}"

            def finish() -> None:
                self.update_label.configure(text=message)
                self.update_button.configure(state="normal")
                self.status.configure(text=detail or message)

            try:
                self.root.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def refresh(self) -> None:
        snapshot, error = self.controller.refresh()
        if error is None and snapshot is not None:
            self.snapshot = snapshot
            self.apply_snapshot(snapshot)
            rebuilt = "；本次已重建" if snapshot["health"]["rebuilt"] else ""
            timeline_warning = f"；时间线警告：{snapshot['timeline_error']}" if snapshot.get("timeline_error") else ""
            self.status.configure(text=f"刷新成功：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{rebuilt}{timeline_warning}")
        else:
            self.status.configure(text=f"刷新失败，继续显示上一次数据：{error}")

    def apply_snapshot(self, snapshot: dict) -> None:
        branch_type = snapshot["classification"]["kind"]
        health = snapshot["health"]
        self.branch_label.configure(text=f"分支：{snapshot['branch']}（{branch_type}）")
        self.health_label.configure(text=f"数据库：{health['status']}")
        self.version_label.configure(text=version_status_text(self.provider.version_info()))
        overview = self.overview_text(snapshot)
        self.overview.configure(state="normal")
        self.overview.delete("1.0", "end")
        self.overview.insert("1.0", overview)
        self.overview.configure(state="disabled")
        self.timeline_page.set_data(
            snapshot.get("timeline", {"lanes": [], "commits": [], "edges": []}),
            error=snapshot.get("timeline_error"),
        )
        self.catalog_page.set_data(
            snapshot.get("catalog_items", []),
            snapshot.get("catalog_relations", []),
        )
        self.task_page.set_tasks(snapshot.get("task_details", {}))
        exploration_records = list(snapshot["explorations"])
        if snapshot.get("attempt"):
            attempt = dict(snapshot["attempt"])
            exploration_records.insert(0, {
                "branch": attempt["branch"], "occurred_at": attempt["updated_at"], "result": attempt["state"],
                "goal": attempt["goal"], "evidence": attempt["evidence"],
                "disposition_ref": attempt.get("pr") or attempt.get("archive_branch") or "active attempt",
                "task_id": attempt["attempt_id"],
                "_attempt": attempt,
            })
        self.records_page.set_records(normalize_records(snapshot["decisions"], exploration_records))
        if self.advanced_window is not None:
            self.advanced_window.set_snapshot(snapshot)
        self.update_preset_buttons()
        if self.active_preset:
            self.apply_active_preset(switch=False)
        if self.global_query.get().strip():
            self.update_search_results(switch=False)

    def perform_global_search(self) -> None:
        self.clear_preset()
        self.update_search_results(switch=True)

    def update_search_results(self, *, switch: bool) -> None:
        records = global_search((self.snapshot or {}).get("search_index", []), self.global_query.get())
        self.search_page.set_records(records)
        self.notebook.tab(self.search_page.frame, text=f"搜索（{len(records)}）" if self.global_query.get().strip() else "搜索")
        if switch:
            self.notebook.select(self.search_page.frame)

    def toggle_preset(self, name: str) -> None:
        if self.active_preset == name:
            self.clear_preset()
            return
        self.active_preset = name
        self.global_query.set("")
        self.search_page.set_records([])
        self.notebook.tab(self.search_page.frame, text="搜索")
        self.task_page.query.set("")
        self.records_page.table.query.set("")
        self.timeline_page.query.set("")
        self.apply_active_preset(switch=True)
        self.update_preset_buttons()

    def clear_preset(self) -> None:
        self.active_preset = None
        self.task_page.set_predicate(None)
        self.records_page.set_negative_only(None)
        self.timeline_page.set_branch_subset(None)
        self.update_preset_buttons()

    def apply_active_preset(self, *, switch: bool) -> None:
        if not self.snapshot or not self.active_preset:
            return
        self.task_page.set_predicate(None)
        self.records_page.set_negative_only(None)
        self.timeline_page.set_branch_subset(None)
        if self.active_preset == "recent":
            recent_ids = set(dashboard_presets(self.snapshot)["recent"])
            self.task_page.set_predicate(recent_ids)
            target = self.task_page.frame
        elif self.active_preset == "negative":
            negative_ids = set(dashboard_presets(self.snapshot)["negative"])
            self.records_page.set_negative_only(negative_ids)
            target = self.records_page.frame
        else:
            names = set(dashboard_presets(self.snapshot)["unmerged"])
            self.timeline_page.set_branch_subset(names)
            target = self.timeline_page.frame
        if switch:
            self.notebook.select(target)

    def update_preset_buttons(self) -> None:
        if not hasattr(self, "preset_buttons"):
            return
        snapshot = self.snapshot or {}
        values = dashboard_presets(snapshot)
        counts = {name: len(items) for name, items in values.items()}
        labels = {"recent": "最近任务", "negative": "失败探索", "unmerged": "未合并分支"}
        for name, button in self.preset_buttons.items():
            prefix = "✓ " if self.active_preset == name else ""
            button.configure(text=f"{prefix}{labels[name]}（{counts[name]}）")

    def open_search_result(self, record: dict) -> None:
        target, record_id = record_location(record)
        self.clear_preset()
        self.open_target_record(target, record_id)

    def open_target_record(self, target: str | None, record_id: str | None) -> bool:
        if target == "history":
            task_id = next((item.get("task_id") for item in (self.snapshot or {}).get("history", []) if item.get("event_id") == record_id), None)
            if task_id:
                self.open_task(task_id)
                return True
            found = False
        elif target in {"decisions", "explorations"} and record_id:
            found = self.records_page.select_record(record_id)
            self.notebook.select(self.records_page.frame)
            return found
        elif target == "catalog" and record_id:
            found = self.catalog_page.select_record(record_id)
            self.notebook.select(self.catalog_page.frame)
            return found
        elif target == "events":
            return self.open_advanced(record_id)
        elif target == "timeline" and record_id and self.timeline_page.reveal_commit(record_id):
            self.notebook.select(self.timeline_page.frame)
            return True
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
        self.advanced_window.focus()
        if event_id and not self.advanced_window.select_event(event_id):
            self.status.configure(text=f"高级查看中找不到事件：{event_id}")
            return False
        return True

    def close_advanced(self) -> None:
        self.advanced_window = None

    def open_related_record(self, record: dict) -> None:
        self.clear_preset()
        self.open_target_record(*record_location(record))

    def open_record_task(self, record: dict) -> None:
        task_ids = linked_task_ids(record)
        if not task_ids:
            self.status.configure(text="该记录没有关联任务。")
            return
        self.open_task(task_ids[0])

    def open_task(self, task_id: str) -> None:
        self.clear_preset()
        if self.task_page.open_task(task_id):
            self.notebook.select(self.task_page.frame)
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        else:
            self.status.configure(text=f"找不到关联任务：{task_id}")

    @staticmethod
    def overview_text(snapshot: dict) -> str:
        base = action_overview_text(snapshot["context"]).rstrip()
        return base + "\n\n" + catalog_overview_text(snapshot.get("catalog_items", []))

    def copy_overview(self) -> None:
        if not self.snapshot:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.overview_text(self.snapshot))

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
                    f"目标\n{attempt['goal']}\n\n假设\n{attempt.get('hypothesis') or '未填写'}\n\n证据\n"
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
