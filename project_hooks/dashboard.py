"""Read-only Tkinter dashboard for project maintenance data."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Callable

from .read_model import MaintenanceReadModel, ReadModelError


CLI_FALLBACK = (
    "可改用以下只读命令：\n"
    "  python -m project_hooks context\n"
    "  python -m project_hooks history\n"
    "  python -m project_hooks decisions\n"
    "  python -m project_hooks explorations\n"
    "  python -m project_hooks db status"
)


class DashboardError(RuntimeError):
    pass


def filter_records(records: list[dict], query: str) -> list[dict]:
    needle = query.strip().casefold()
    if not needle:
        return list(records)
    return [record for record in records if needle in json.dumps(record, ensure_ascii=False, default=str).casefold()]


def sort_records(records: list[dict], key: str, descending: bool = False) -> list[dict]:
    def value(record: dict) -> tuple[bool, str]:
        item = record.get(key)
        return item is None, str(item or "").casefold()
    return sorted(records, key=value, reverse=descending)


def short(value: object, limit: int = 90) -> str:
    text = "" if value is None else str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def timeline_layout(timeline: dict, branch: str = "全部分支", query: str = "") -> dict:
    """Return deterministic coordinates and visibility for the timeline canvas."""
    commits = list(timeline.get("commits", []))
    all_lanes = list(timeline.get("lanes", []))
    if branch != "全部分支":
        commits = [
            commit for commit in commits
            if commit.get("lane") == branch or branch in commit.get("event_branches", [])
        ]
        used = {branch}
        used.update(commit.get("lane") for commit in commits)
        lanes = [lane for lane in all_lanes if lane in used]
    else:
        lanes = all_lanes
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
    for index, commit in enumerate(commits):
        x = 70 + index * 132
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
        "associations": associations,
        "width": max(900, 140 + len(nodes) * 132),
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
            self.snapshot = snapshot
            return self.snapshot, None
        except Exception as exc:
            return self.snapshot, str(exc)


class TablePage:
    def __init__(self, parent, tk, ttk, scrolledtext, *, columns: list[tuple[str, str, int]], detail: Callable[[dict], str], refresh: Callable[[], None]):
        self.tk, self.ttk = tk, ttk
        self.frame = ttk.Frame(parent, padding=8)
        self.columns = columns
        self.detail_formatter = detail
        self.refresh_callback = refresh
        self.records: list[dict] = []
        self.visible: list[dict] = []
        keys = [item[0] for item in columns]
        self.sort_key = "occurred_at" if "occurred_at" in keys else keys[0]
        self.descending = True

        controls = ttk.Frame(self.frame)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Label(controls, text="筛选").pack(side="left")
        self.query = tk.StringVar()
        entry = ttk.Entry(controls, textvariable=self.query, width=36)
        entry.pack(side="left", padx=(6, 8))
        self.query.trace_add("write", lambda *_: self.render())
        ttk.Button(controls, text="复制选中", command=self.copy_selected).pack(side="left")
        ttk.Button(controls, text="刷新", command=refresh).pack(side="left", padx=(6, 0))

        table_frame = ttk.Frame(self.frame)
        table_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table_frame, columns=keys, show="headings", height=13)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for key, label, width in columns:
            self.tree.heading(key, text=label, command=lambda selected=key: self.sort(selected))
            self.tree.column(key, width=width, minwidth=70, stretch=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)

        ttk.Label(self.frame, text="详情").pack(anchor="w", pady=(8, 3))
        self.detail = scrolledtext.ScrolledText(self.frame, height=10, wrap="word")
        self.detail.pack(fill="both", expand=False)
        self.detail.configure(state="disabled")

    def set_records(self, records: list[dict]) -> None:
        self.records = list(records)
        self.render()

    def sort(self, key: str) -> None:
        if self.sort_key == key:
            self.descending = not self.descending
        else:
            self.sort_key, self.descending = key, False
        self.render()

    def render(self) -> None:
        selected = self.tree.selection()
        selected_id = selected[0] if selected else None
        self.visible = sort_records(filter_records(self.records, self.query.get()), self.sort_key, self.descending)
        self.tree.delete(*self.tree.get_children())
        for index, record in enumerate(self.visible):
            iid = f"row-{index}"
            self.tree.insert("", "end", iid=iid, values=[short(record.get(key)) for key, _, _ in self.columns])
        if selected_id and self.tree.exists(selected_id):
            self.tree.selection_set(selected_id)
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


class TimelinePage:
    ALL_BRANCHES = "全部分支"

    def __init__(self, parent, tk, ttk, scrolledtext, *, refresh: Callable[[], None]):
        self.tk, self.ttk = tk, ttk
        self.frame = ttk.Frame(parent, padding=8)
        self.refresh_callback = refresh
        self.timeline: dict = {"lanes": [], "commits": [], "edges": []}
        self.selected_hash: str | None = None
        self.node_by_hash: dict[str, dict] = {}
        self.first_render = True

        controls = ttk.Frame(self.frame)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Label(controls, text="分支").pack(side="left")
        self.branch = tk.StringVar(value=self.ALL_BRANCHES)
        self.branch_box = ttk.Combobox(controls, textvariable=self.branch, state="readonly", width=24)
        self.branch_box.pack(side="left", padx=(6, 12))
        self.branch_box.bind("<<ComboboxSelected>>", lambda _event: self.render())
        ttk.Label(controls, text="搜索（高亮）").pack(side="left")
        self.query = tk.StringVar()
        entry = ttk.Entry(controls, textvariable=self.query, width=30)
        entry.pack(side="left", padx=(6, 8))
        self.query.trace_add("write", lambda *_: self.render())
        ttk.Button(controls, text="复制选中", command=self.copy_selected).pack(side="left")
        ttk.Button(controls, text="刷新", command=refresh).pack(side="left", padx=(6, 0))

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
        lanes = list(timeline.get("lanes", []))
        values = [self.ALL_BRANCHES, *lanes]
        self.branch_box.configure(values=values)
        if self.branch.get() not in values:
            self.branch.set(self.ALL_BRANCHES)
        self.message.configure(text=(f"时间线刷新失败，继续显示上一次数据：{error}" if error else ""))
        self.render()

    def render(self) -> None:
        old_xview = self.canvas.xview()
        layout = timeline_layout(self.timeline, self.branch.get(), self.query.get())
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
        if self.first_render and layout["nodes"]:
            self.canvas.xview_moveto(1.0)
            self.first_render = False
        elif old_xview:
            self.canvas.xview_moveto(old_xview[0])

    def select(self, commit_hash: str) -> None:
        self.selected_hash = commit_hash
        self.render()

    def selected_record(self) -> dict | None:
        return self.node_by_hash.get(self.selected_hash or "")

    def show_detail(self) -> None:
        record = self.selected_record()
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

    def copy_selected(self) -> None:
        record = self.selected_record()
        if not record:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(json.dumps(record, ensure_ascii=False, indent=2, default=str))


class DashboardApp:
    def __init__(self, root, tk, ttk, scrolledtext, provider: DashboardDataProvider, refresh_seconds: float):
        self.root, self.tk, self.ttk = root, tk, ttk
        self.controller = DashboardController(provider)
        self.refresh_seconds = refresh_seconds
        self.snapshot: dict | None = None
        root.title("Project Maintenance")
        root.geometry("1120x760")
        root.minsize(800, 560)

        toolbar = ttk.Frame(root, padding=(10, 8))
        toolbar.pack(fill="x")
        self.branch_label = ttk.Label(toolbar, text="分支：加载中")
        self.branch_label.pack(side="left")
        self.health_label = ttk.Label(toolbar, text="数据库：加载中")
        self.health_label.pack(side="left", padx=(18, 0))
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(side="right")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.overview_frame = ttk.Frame(self.notebook, padding=8)
        overview_actions = ttk.Frame(self.overview_frame)
        overview_actions.pack(fill="x", pady=(0, 6))
        ttk.Button(overview_actions, text="复制概览", command=self.copy_overview).pack(side="left")
        ttk.Button(overview_actions, text="刷新", command=self.refresh).pack(side="left", padx=(6, 0))
        self.overview = scrolledtext.ScrolledText(self.overview_frame, wrap="word")
        self.overview.pack(fill="both", expand=True)
        self.overview.configure(state="disabled")
        self.notebook.add(self.overview_frame, text="概览")

        self.timeline_page = TimelinePage(
            self.notebook, tk, ttk, scrolledtext, refresh=self.refresh,
        )
        self.notebook.add(self.timeline_page.frame, text="时间线")

        self.history_page = TablePage(self.notebook, tk, ttk, scrolledtext,
            columns=[("occurred_at", "时间", 180), ("task_id", "任务 ID", 190), ("result", "结果", 110), ("branch", "分支", 130), ("summary", "摘要", 360)],
            detail=self.history_detail, refresh=self.refresh)
        self.decision_page = TablePage(self.notebook, tk, ttk, scrolledtext,
            columns=[("decision_id", "决策 ID", 180), ("occurred_at", "时间", 180), ("branch", "分支", 130), ("decision", "决策", 480)],
            detail=self.decision_detail, refresh=self.refresh)
        self.exploration_page = TablePage(self.notebook, tk, ttk, scrolledtext,
            columns=[("branch", "分支", 210), ("occurred_at", "时间", 180), ("result", "结果", 110), ("goal", "目标", 330), ("disposition_ref", "PR / 归档", 180)],
            detail=self.exploration_detail, refresh=self.refresh)
        self.event_page = TablePage(self.notebook, tk, ttk, scrolledtext,
            columns=[("occurred_at", "时间", 180), ("event_type", "事件类型", 190), ("branch", "分支", 140), ("task_id", "任务 ID", 210), ("event_id", "事件 ID", 300)],
            detail=self.event_detail, refresh=self.refresh)
        for page, label in ((self.history_page, "任务历史"), (self.decision_page, "决策"),
                            (self.exploration_page, "探索"), (self.event_page, "事件")):
            self.notebook.add(page.frame, text=label)

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
        self.health_label.configure(text=f"数据库：{health['status']}｜事件 {health['events']}")
        overview = self.overview_text(snapshot)
        self.overview.configure(state="normal")
        self.overview.delete("1.0", "end")
        self.overview.insert("1.0", overview)
        self.overview.configure(state="disabled")
        self.timeline_page.set_data(
            snapshot.get("timeline", {"lanes": [], "commits": [], "edges": []}),
            error=snapshot.get("timeline_error"),
        )
        self.history_page.set_records(snapshot["history"])
        self.decision_page.set_records(snapshot["decisions"])
        exploration_records = list(snapshot["explorations"])
        if snapshot.get("attempt"):
            attempt = dict(snapshot["attempt"])
            exploration_records.insert(0, {
                "branch": attempt["branch"], "occurred_at": attempt["updated_at"], "result": attempt["state"],
                "goal": attempt["goal"], "evidence": attempt["evidence"],
                "disposition_ref": attempt.get("pr") or attempt.get("archive_branch") or "active attempt",
                "_attempt": attempt,
            })
        self.exploration_page.set_records(exploration_records)
        self.event_page.set_records(snapshot["events"])

    @staticmethod
    def overview_text(snapshot: dict) -> str:
        context, health = snapshot["context"], snapshot["health"]
        state = context.get("state") or {}
        active = context.get("active_task")
        lines = [
            "数据库健康度", f"状态：{health['status']}", f"Schema：{health['schema_version']}",
            f"事件数量：{health['events']}", f"事件日志哈希：{health['journal_hash']}",
            f"本次刷新重建数据库：{'是' if health['rebuilt'] else '否'}", "",
            "活动任务", json.dumps(active, ensure_ascii=False, indent=2) if active else "无。", "",
            "当前目标", state.get("goal") or "未设置", "", "当前判决", state.get("judgment") or "未设置", "",
            "真实断点", state.get("breakpoint") or "未设置", "", "接下来三步",
        ]
        steps = state.get("next_steps", [])
        lines.extend(f"{index}. {item}" for index, item in enumerate(steps, 1))
        if not steps:
            lines.append("无。")
        lines += ["", "当前阻塞", state.get("blocker") or "无。", "", "最近交接"]
        for handoff in context.get("recent_handoffs", []):
            lines.append(f"- {handoff['occurred_at']}｜{handoff['task']}｜{handoff['result']}｜{handoff['main_goal_change']}")
        if not context.get("recent_handoffs"):
            lines.append("无。")
        return "\n".join(lines)

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
