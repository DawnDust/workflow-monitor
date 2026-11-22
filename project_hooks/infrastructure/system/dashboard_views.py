"""Read only the data used by the selected Dashboard page."""

from pathlib import Path

from ..git import client as git
from ..git.worktrees import repository_context
from .workflow_views import project_snapshot


DASHBOARD_VIEWS = frozenset({"overview", "workflow", "workspaces", "search", "resources", "research-stages", "settings"})


def file_revision(path):
    try:
        info = path.stat()
        return info.st_mtime_ns, info.st_ctime_ns, info.st_size
    except FileNotFoundError:
        return None


class DashboardViews:
    def __init__(self, root, model, search, diagnostics):
        self.root = root
        self.model = model
        self.search = search
        self.diagnostics = diagnostics
        self.overview_revision = None
        self.overview = None

    def revision(self):
        paths = [self.model.journal_path, self.root / ".codex/project-maintenance-workflow.json"]
        marker = self.root / ".git"
        if marker.exists():
            git_context = repository_context(self.root)
            registry = git_context.shared_dir / "registry.sqlite3"
            paths.extend((git_context.git_dir / "HEAD", registry, registry.with_name(registry.name + "-wal")))
        return tuple(file_revision(path) for path in paths)

    def overview_branch(self):
        try:
            head = repository_context(self.root).git_dir / "HEAD"
            branch = head.read_text(encoding="utf-8").strip()
            if branch.startswith("ref: refs/heads/"):
                return branch.removeprefix("ref: refs/heads/")
        except (OSError, RuntimeError, ValueError):
            pass
        return self.model.branch_provider()

    def load(self, view, *, force=False):
        if view == "overview":
            revision = self.revision()
            if force or self.overview is None or revision != self.overview_revision:
                self.overview = self.model.dashboard_overview(self.overview_branch())
                self.overview_revision = self.revision()
            return dict(self.overview)
        if view == "workflow":
            return {"branch": self.model.branch_provider()}
        if view == "workspaces":
            return {"project_monitor": self.workspaces()}
        if view == "search":
            return self.search.load(force=force)
        result = self.model.dashboard_snapshot(view=view)
        if view == "settings":
            result["diagnostics"] = self.diagnostics()
        return result

    def workspaces(self):
        monitor = project_snapshot(self.root, known_primary=True)
        if not monitor["enabled"]:
            context = self.load("overview")["context"]
            active = context["active_task"]
            main = monitor["workflows"][0]
            main["branch"] = context["branch"]
            main["current_task"] = {**active, "status": active["lifecycle_phase"],
                                    "updated_at": active["started_at"]} if active else None
        physical = {Path(tree["path"]): tree for tree in monitor["worktrees"]}
        summaries = []
        for item in monitor["workflows"]:
            active = item.get("current_task")
            last = active or item.get("last_task") or {}
            path = Path(item["path"]) if item.get("path") else None
            branch = item.get("archive_branch") or item["branch"]
            archived = item["status"] == "archived"
            issues = [] if archived else list(last.get("issues", []))
            dirty = None if archived else last.get("dirty")
            if not archived and path and not last.get("workspace"):
                tree = physical.get(path)
                if monitor["enabled"] and tree is None:
                    issues.append("路径失效" if not path.is_dir() else "Git 已移除或工作树已移动")
                else:
                    changes = git.run(path, ["status", "--porcelain", "-z", "--untracked-files=normal"], check=False)
                    if changes.returncode:
                        issues.append("无法读取工作区状态")
                    else:
                        dirty = bool(changes.stdout)
            summaries.append({
                "workflow_id": item["workflow_id"], "topic": item.get("topic"), "track": item["track"],
                "branch": branch, "path": item.get("path"),
                "status": item["status"], "current_status": active["status"] if active else None,
                "dirty": dirty, "issues": issues, "updated_at": last.get("updated_at"),
            })
        return {**{key: monitor.get(key) for key in ("enabled", "unified", "pending_events", "conflicts",
                  "migration_conflicts", "sync_failures")}, "workflows": summaries}
