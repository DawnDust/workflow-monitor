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
    for key in ("record_id", "event_id", "decision_id", "hash", "task_id", "branch"):
        if record.get(key) is not None:
            return key, record[key]
    return None


def dashboard_presets(snapshot: dict) -> dict[str, list[str]]:
    return {
        "recent": [item.get("event_id") for item in snapshot.get("history", [])[:10] if item.get("event_id")],
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
                 activate_label: str = "打开记录"):
        self.tk, self.ttk = tk, ttk
        self.frame = ttk.Frame(parent, padding=8)
        self.columns = columns
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
        if activate:
            self.tree.bind("<Double-1>", lambda _event: self.activate_selected())

        ttk.Label(self.frame, text="详情").pack(anchor="w", pady=(8, 3))
        self.detail = scrolledtext.ScrolledText(self.frame, height=10, wrap="word")
        self.detail.pack(fill="both", expand=False)
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


class TaskDetailPage:
    def __init__(self, parent, tk, ttk, scrolledtext, *, refresh: Callable[[], None],
                 open_related: Callable[[dict], None]):
        self.frame = ttk.Frame(parent, padding=8)
        self.tasks: dict[str, dict] = {}
        self.current_task_id: str | None = None

        controls = ttk.Frame(self.frame)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Label(controls, text="任务").pack(side="left")
        self.selected = tk.StringVar()
        self.selector = ttk.Combobox(controls, textvariable=self.selected, state="readonly", width=42)
        self.selector.pack(side="left", padx=(6, 8))
        self.selector.bind("<<ComboboxSelected>>", self.select_from_box)
        ttk.Button(controls, text="复制摘要", command=self.copy_summary).pack(side="left")
        ttk.Button(controls, text="刷新", command=refresh).pack(side="left", padx=(6, 0))

        ttk.Label(self.frame, text="任务摘要").pack(anchor="w", pady=(0, 3))
        self.summary = scrolledtext.ScrolledText(self.frame, height=12, wrap="word")
        self.summary.pack(fill="x")
        self.summary.configure(state="disabled")

        self.related_page = TablePage(
            self.frame, tk, ttk, scrolledtext,
            columns=[("kind_label", "类型", 80), ("occurred_at", "时间", 180),
                     ("title", "标题", 350), ("summary", "摘要", 300)],
            detail=lambda record: json.dumps(record, ensure_ascii=False, indent=2, default=str),
            refresh=refresh, activate=open_related, activate_label="打开记录",
        )
        self.related_page.frame.pack(fill="both", expand=True, padx=0, pady=(8, 0))
        self._set_summary("请选择任务历史中的记录，或从其他分页打开关联任务。")

    def set_tasks(self, tasks: dict[str, dict]) -> None:
        previous = self.current_task_id
        self.tasks = dict(tasks)
        ordered = sorted(
            self.tasks,
            key=lambda task_id: (self.tasks[task_id].get("started_at", ""), task_id),
            reverse=True,
        )
        self.selector.configure(values=ordered)
        if previous and previous in self.tasks:
            self.selected.set(previous)
            self.render()
        elif previous:
            self.current_task_id = None
            self.selected.set("")
            self.related_page.set_records([])
            self._set_summary(f"任务 {previous} 已不在当前数据中，可能已切换分支或记录被过滤。")

    def select_from_box(self, _event=None) -> None:
        self.open_task(self.selected.get())

    def open_task(self, task_id: str) -> bool:
        if task_id not in self.tasks:
            self.current_task_id = None
            self.related_page.set_records([])
            self._set_summary(f"找不到任务 {task_id}。")
            return False
        self.current_task_id = task_id
        self.selected.set(task_id)
        self.render()
        return True

    def render(self) -> None:
        task = self.tasks.get(self.current_task_id or "")
        if not task:
            return
        acceptance = task.get("acceptance") or "未记录"
        if isinstance(acceptance, list):
            acceptance = "\n".join(f"- {item}" for item in acceptance) or "未记录"
        evidence = "\n".join(f"- {item}" for item in task.get("evidence", [])) or "无"
        text = (
            f"任务：{task['task_id']}\n分支：{task.get('branch') or '未知'}\n"
            f"开始：{task.get('started_at') or '未知'}\n结束：{task.get('finished_at') or '进行中'}\n"
            f"结果：{task.get('result') or '未知'}\n路线：{task.get('route') or '未记录'}\n\n"
            f"目标\n{task.get('goal') or '未记录'}\n\n验收条件\n{acceptance}\n\n"
            f"结论\n{task.get('conclusion') or '未记录'}\n\n证据\n{evidence}"
        )
        self._set_summary(text)
        self.related_page.set_records(task.get("related", []))

    def _set_summary(self, text: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)
        self.summary.configure(state="disabled")

    def copy_summary(self) -> None:
        task = self.tasks.get(self.current_task_id or "")
        if not task:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(self.summary.get("1.0", "end-1c"))


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
        ttk.Button(controls, text="刷新", command=refresh).pack(side="left", padx=(6, 0))

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
        self.controller = DashboardController(provider)
        self.refresh_seconds = refresh_seconds
        self.snapshot: dict | None = None
        self.active_preset: str | None = None
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
        ttk.Button(overview_actions, text="刷新", command=self.refresh).pack(side="left", padx=(6, 0))
        self.overview = scrolledtext.ScrolledText(self.overview_frame, wrap="word")
        self.overview.pack(fill="both", expand=True)
        self.overview.configure(state="disabled")
        self.notebook.add(self.overview_frame, text="概览")

        self.search_page = TablePage(
            self.notebook, tk, ttk, scrolledtext,
            columns=[("kind_label", "类型", 80), ("occurred_at", "时间", 180), ("branch", "分支", 150),
                     ("title", "标题", 320), ("summary", "摘要", 250)],
            detail=self.search_result_detail, refresh=self.refresh,
            activate=self.open_search_result, show_query=False,
        )
        self.notebook.add(self.search_page.frame, text="搜索")

        self.task_page = TaskDetailPage(
            self.notebook, tk, ttk, scrolledtext, refresh=self.refresh,
            open_related=self.open_related_record,
        )
        self.notebook.add(self.task_page.frame, text="任务详情")

        self.timeline_page = TimelinePage(
            self.notebook, tk, ttk, scrolledtext, refresh=self.refresh, open_task=self.open_task,
        )
        self.notebook.add(self.timeline_page.frame, text="时间线")

        self.history_page = TablePage(self.notebook, tk, ttk, scrolledtext,
            columns=[("occurred_at", "时间", 180), ("task_id", "任务 ID", 190), ("result", "结果", 110), ("branch", "分支", 130), ("summary", "摘要", 360)],
            detail=self.history_detail, refresh=self.refresh, activate=self.open_record_task,
            activate_label="打开任务")
        self.decision_page = TablePage(self.notebook, tk, ttk, scrolledtext,
            columns=[("decision_id", "决策 ID", 180), ("occurred_at", "时间", 180), ("branch", "分支", 130), ("decision", "决策", 480)],
            detail=self.decision_detail, refresh=self.refresh, activate=self.open_record_task,
            activate_label="打开关联任务")
        self.exploration_page = TablePage(self.notebook, tk, ttk, scrolledtext,
            columns=[("branch", "分支", 210), ("occurred_at", "时间", 180), ("result", "结果", 110), ("goal", "目标", 330), ("disposition_ref", "PR / 归档", 180)],
            detail=self.exploration_detail, refresh=self.refresh, activate=self.open_record_task,
            activate_label="打开关联任务")
        self.event_page = TablePage(self.notebook, tk, ttk, scrolledtext,
            columns=[("occurred_at", "时间", 180), ("event_type", "事件类型", 190), ("branch", "分支", 140), ("task_id", "任务 ID", 210), ("event_id", "事件 ID", 300)],
            detail=self.event_detail, refresh=self.refresh, activate=self.open_record_task,
            activate_label="打开关联任务")
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
        self.task_page.set_tasks(snapshot.get("task_details", {}))
        self.history_page.set_records(snapshot["history"])
        self.decision_page.set_records(snapshot["decisions"])
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
        self.exploration_page.set_records(exploration_records)
        self.event_page.set_records(snapshot["events"])
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
        for page in (self.history_page, self.decision_page, self.exploration_page, self.event_page):
            page.query.set("")
        self.timeline_page.query.set("")
        self.apply_active_preset(switch=True)
        self.update_preset_buttons()

    def clear_preset(self) -> None:
        self.active_preset = None
        for page in (self.history_page, self.decision_page, self.exploration_page, self.event_page):
            page.set_predicate(None)
        self.timeline_page.set_branch_subset(None)
        self.update_preset_buttons()

    def apply_active_preset(self, *, switch: bool) -> None:
        if not self.snapshot or not self.active_preset:
            return
        self.history_page.set_predicate(None)
        self.exploration_page.set_predicate(None)
        self.timeline_page.set_branch_subset(None)
        if self.active_preset == "recent":
            recent_ids = set(dashboard_presets(self.snapshot)["recent"])
            self.history_page.set_predicate(lambda item, ids=recent_ids: item.get("event_id") in ids)
            target = self.history_page.frame
        elif self.active_preset == "negative":
            negative_ids = set(dashboard_presets(self.snapshot)["negative"])
            self.exploration_page.set_predicate(lambda item, ids=negative_ids: item.get("event_id") in ids)
            target = self.exploration_page.frame
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
            found = self.history_page.select_record("event_id", record_id)
            self.notebook.select(self.history_page.frame)
            return found
        elif target == "decisions":
            found = self.decision_page.select_record("event_id", record_id)
            self.notebook.select(self.decision_page.frame)
            return found
        elif target == "explorations":
            found = self.exploration_page.select_record("event_id", record_id)
            self.notebook.select(self.exploration_page.frame)
            return found
        elif target == "events":
            found = self.event_page.select_record("event_id", record_id)
            self.notebook.select(self.event_page.frame)
            return found
        elif target == "timeline" and record_id and self.timeline_page.reveal_commit(record_id):
            self.notebook.select(self.timeline_page.frame)
            return True
        self.status.configure(text=f"找不到关联记录：{target or '未知类型'} / {record_id or '未知 ID'}")
        return False

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
        else:
            self.status.configure(text=f"找不到关联任务：{task_id}")

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
