from __future__ import annotations

import inspect
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from project_hooks.dashboard import (
    CLI_FALLBACK,
    CODEX_CATALOG_SCAN_PROMPT,
    CatalogPage,
    DashboardApp,
    DashboardController,
    DashboardError,
    PRIMARY_TABS,
    RESEARCH_PROMPT_TEMPLATES,
    SOFTWARE_PROMPT_TEMPLATES,
    WORKBENCH_PROMPT_TEMPLATES,
    ResearchWorkbenchPage,
    RecordsPage,
    TablePage,
    AdvancedWindow,
    advanced_summary,
    build_research_prompt,
    catalog_overview_text,
    copy_catalog_scan_prompt,
    copy_research_prompt,
    dashboard_presets,
    filter_records,
    global_search,
    launch_dashboard,
    linked_task_ids,
    normalize_records,
    record_identity,
    record_location,
    reveal_catalog_file,
    research_prompt_records,
    result_label,
    short,
    sort_records,
    timeline_layout,
)
from project_hooks.read_model import (
    MaintenanceReadModel,
    ReadModelError,
    action_overview_text,
    git_state_summary,
    is_auxiliary_task_id,
    is_publication_step,
)
from project_hooks.store import append_events


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class ProjectHooksSqliteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        for name in (".codex", ".githooks", "maintenance", "project_hooks"):
            shutil.copytree(SOURCE_ROOT / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("AGENTS.md", ".gitignore", ".gitattributes"):
            shutil.copy2(SOURCE_ROOT / name, self.root / name)
        (self.root / "maintenance/events.jsonl").write_text("", encoding="utf-8")
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Project Hooks Test")
        self.git("config", "user.email", "hooks@example.invalid")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=check)

    def hooks(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", "project_hooks", *args], cwd=self.root,
                              text=True, capture_output=True, check=check)

    def start(self, task_id: str, *extra: str, commit: str = "never", check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.hooks("start", task_id, "--kind", "analysis", "--scope", "test database workflow",
                          "--acceptance", "workflow behaves deterministically", "--git-commit", commit,
                          "--task-size", "large" if commit == "always" else "small",
                          *extra, check=check)

    def update_state(self, breakpoint: str = "test completed") -> None:
        self.hooks("state", "update", "--judgment", "test judgment", "--breakpoint", breakpoint,
                   "--next", "continue testing", "--blocker", "none")

    def end(self, task_id: str, *, state: str | None = None, route: str = "unchanged", check: bool = True) -> subprocess.CompletedProcess[str]:
        args = ["end", task_id, "--result", "completed", "--route", route,
                "--methods-action", "updated", "--main-goal", "unchanged", "--note", "test completed",
                "--evidence", "unit test"]
        if state:
            args += ["--attempt-state", state]
        return self.hooks(*args, check=check)

    def commit_all(self, message: str) -> None:
        self.git("add", ".")
        self.git("commit", "-m", message)

    def test_install_rebuilds_database_and_context_is_available(self) -> None:
        database = self.root / ".project_hooks/maintenance.sqlite3"
        self.assertFalse(database.exists())
        missing = self.hooks(check=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("python -m project_hooks install", missing.stderr)
        self.assertIn("python -m project_hooks check", missing.stderr)
        self.hooks("install")
        self.assertTrue(database.exists())
        overview = self.hooks().stdout
        self.assertIn("项目行动概览", overview)
        self.assertIn("状态：", overview)
        self.assertIn("活动任务：", overview)
        self.assertIn("当前阻塞：", overview)
        self.assertIn("下一步：", overview)
        self.assertIn("Git 同步：", overview)
        output = self.hooks("context", "--format", "markdown").stdout
        self.assertIn("# 动态维护上下文", output)
        self.assertIn("## 工作断点", output)
        self.assertIn("## 真实断点", output)
        context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertEqual(context["git_state"]["relation"], "unavailable")
        self.assertEqual(self.git("config", "--local", "--get", "core.hooksPath").stdout.strip(), ".githooks")

    def test_git_state_tracks_synced_ahead_behind_and_diverged(self) -> None:
        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        )
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/main", base)
        self.assertEqual(model.git_state()["relation"], "synced")

        local = self.root / "local.txt"
        local.write_text("local\n", encoding="utf-8")
        self.git("add", "local.txt")
        self.git("commit", "-m", "local change")
        self.assertEqual(model.git_state()["relation"], "ahead")

        local_tip = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/main", local_tip)
        self.git("reset", "--hard", base)
        self.assertEqual(model.git_state()["relation"], "behind")

        divergent = self.root / "divergent.txt"
        divergent.write_text("divergent\n", encoding="utf-8")
        self.git("add", "divergent.txt")
        self.git("commit", "-m", "divergent change")
        state = model.git_state()
        self.assertEqual(state["relation"], "diverged")
        self.assertIn("关系：分叉", git_state_summary(state))

    def test_publication_status_is_derived_from_linked_commits(self) -> None:
        base = self.git("rev-parse", "HEAD").stdout.strip()
        task_id = "20260723_publication_001"
        self.start(task_id)
        self.update_state("publication started")
        self.commit_all("record task start")
        first_commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.end(task_id)
        self.commit_all("record task finish")
        final_commit = self.git("rev-parse", "HEAD").stdout.strip()

        self.git("update-ref", "refs/remotes/origin/main", base)
        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        )
        self.assertEqual(model.dashboard_snapshot()["task_details"][task_id]["publication_status"], "待发布")

        self.git("update-ref", "refs/remotes/origin/main", first_commit)
        partial = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).dashboard_snapshot()["task_details"][task_id]
        self.assertEqual(partial["publication_status"], "部分发布")
        self.assertEqual(partial["publication_commits"], [first_commit])

        self.git("update-ref", "refs/remotes/origin/main", final_commit)
        published = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).dashboard_snapshot()["task_details"][task_id]
        self.assertEqual(published["publication_status"], "已发布")

        recorded_id = "20260723_recorded_only_001"
        self.start(recorded_id)
        self.update_state("record only")
        self.end(recorded_id)
        recorded = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).dashboard_snapshot()["task_details"][recorded_id]
        self.assertEqual(recorded["publication_status"], "仅记录")

        self.git("update-ref", "-d", "refs/remotes/origin/main")
        unknown = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).dashboard_snapshot()["task_details"][task_id]
        self.assertEqual(unknown["publication_status"], "未知")

    def test_synced_publication_derives_completed_overview_without_mutating_state(self) -> None:
        task_id = "20260723_synced_overview_001"
        self.start(task_id)
        self.hooks(
            "state", "update",
            "--status", "已验证，准备发布",
            "--judgment", "改动可提交",
            "--breakpoint", "测试通过，待提交推送",
            "--next", "提交已验证改动",
            "--next", "推送 main 并核对远端哈希",
        )
        self.end(task_id)
        self.commit_all("publish completed task")
        commit_hash = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/main", commit_hash)

        context = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).context()
        self.assertEqual(context["state"]["status"], "已验证，准备发布")
        self.assertEqual(context["state"]["next_steps"], ["提交已验证改动", "推送 main 并核对远端哈希"])
        self.assertEqual(context["overview_state"]["status"], "已完成并发布")
        self.assertEqual(context["overview_state"]["next_steps"], [])
        self.assertIn(commit_hash[:8], context["overview_state"]["breakpoint"])
        self.assertEqual(context["completed_next_steps"], context["state"]["next_steps"])
        self.assertTrue(context["publication_completed"])

    def test_overview_keeps_business_steps_and_unpublished_task_state(self) -> None:
        base_context = {
            "branch": "main",
            "state": {
                "task_id": "task-1",
                "status": "准备发布",
                "judgment": "ready",
                "breakpoint": "tested",
                "next_steps": ["检查并发布全部已验证改动", "分析下一批数据"],
            },
            "git_state": {
                "relation": "synced",
                "upstream_ref": "origin/main",
                "upstream_head": "a" * 40,
            },
            "active_task": None,
        }
        details = {
            "task-1": {
                "task_id": "task-1",
                "goal": "implement feature",
                "conclusion": "feature complete",
                "publication_status": "已发布",
                "parent_task_id": None,
            },
        }
        mixed = MaintenanceReadModel._apply_overview_state(dict(base_context), details)
        self.assertEqual(mixed["visible_next_steps"], ["分析下一批数据"])
        self.assertEqual(mixed["overview_state"]["status"], "已完成并发布")
        self.assertTrue(mixed["publication_completed"])

        unpublished_context = dict(base_context)
        unpublished_context["state"] = dict(base_context["state"], next_steps=["推送 main"])
        unpublished_details = {"task-1": dict(details["task-1"], publication_status="仅记录")}
        unpublished = MaintenanceReadModel._apply_overview_state(unpublished_context, unpublished_details)
        self.assertEqual(unpublished["visible_next_steps"], ["推送 main"])
        self.assertFalse(unpublished["publication_completed"])

    def test_auxiliary_publication_tasks_fold_into_primary_details(self) -> None:
        primary_id = "20260723_primary_feature_001"
        publish_id = "20260723_publish_primary_feature_001"
        record_id = "20260723_record_primary_feature_publication_001"

        self.start(primary_id)
        self.update_state("feature complete")
        self.end(primary_id)
        self.start(publish_id)
        self.update_state("ready to publish")
        self.end(publish_id)
        self.commit_all("feature and publication preparation")

        self.start(record_id)
        self.update_state("publication recorded")
        self.end(record_id)
        self.commit_all("record publication")

        snapshot = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).dashboard_snapshot()
        details = snapshot["task_details"]
        self.assertEqual(details[publish_id]["parent_task_id"], primary_id)
        self.assertEqual(details[record_id]["parent_task_id"], primary_id)
        self.assertEqual(
            [item["task_id"] for item in details[primary_id]["auxiliary_tasks"]],
            [publish_id, record_id],
        )
        self.assertNotIn(publish_id, dashboard_presets(snapshot)["recent"])
        self.assertNotIn(record_id, dashboard_presets(snapshot)["recent"])
        self.assertNotIn(publish_id, [item.get("task_id") for item in snapshot["context"]["recent_handoffs"]])
        self.assertTrue(any(publish_id in item["task_ids"] for item in snapshot["search_index"]))

    def test_end_can_update_final_state_in_one_step_with_auto_commit(self) -> None:
        self.hooks("install")
        task_id = "20260723_one_step_end_001"
        self.start(task_id, commit="always")
        (self.root / "one-step.txt").write_text("one step\n", encoding="utf-8")
        result = json.loads(self.hooks(
            "end", task_id,
            "--result", "completed",
            "--route", "unchanged",
            "--methods-action", "updated",
            "--main-goal", "unchanged",
            "--note", "one-step completion",
            "--evidence", "unit test",
            "--status", "已完成",
            "--judgment", "一步收尾可用",
            "--breakpoint", "自动提交通过",
            "--next", "继续维护",
        ).stdout)
        self.assertEqual(result["git"]["status"], "committed")
        context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertEqual(context["state"]["status"], "已完成")
        events = [
            item for item in MaintenanceReadModel(
                self.root / ".project_hooks/maintenance.sqlite3",
                self.root / "maintenance/events.jsonl",
                lambda: "main",
            ).records("events", 20)
            if item["task_id"] == task_id
        ]
        self.assertEqual(
            sorted(item["event_type"] for item in events),
            ["project_state.updated", "task.finished", "task.started"],
        )

    def test_finish_uses_one_event_and_recent_handoffs_deduplicate_legacy(self) -> None:
        task_id = "20260723_single_finish_001"
        self.start(task_id)
        self.update_state("single finish")
        self.end(task_id)
        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        )
        events = [item for item in model.records("events", 20) if item["task_id"] == task_id]
        self.assertEqual([item["event_type"] for item in events].count("task.finished"), 1)
        self.assertNotIn("handoff.recorded", [item["event_type"] for item in events])
        self.assertEqual(model.context()["recent_handoffs"][0]["task_id"], task_id)

        finish = next(item for item in events if item["event_type"] == "task.finished")
        append_events(
            self.root / "maintenance/events.jsonl",
            self.root / ".project_hooks",
            [{
                "event_id": "legacy-handoff-for-finished-task",
                "schema_version": 1,
                "event_type": "handoff.recorded",
                "occurred_at": finish["occurred_at"],
                "branch": "main",
                "task_id": task_id,
                "payload": {
                    "task": "legacy duplicate",
                    "result": "legacy duplicate",
                    "main_goal_change": "unchanged",
                },
            }],
        )
        (self.root / ".project_hooks/maintenance.sqlite3").unlink()
        deduplicated = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).context()["recent_handoffs"]
        self.assertEqual(sum(item.get("task_id") == task_id for item in deduplicated), 1)
        self.assertEqual(deduplicated[0]["result"], "test completed")

    def test_shared_read_model_maps_dashboard_data(self) -> None:
        task_id = "20260722_readmodel_001"
        self.start(task_id)
        self.update_state("read model breakpoint")
        self.hooks("decision", "add", "--decision", "read model", "--alternatives", "duplicate queries",
                   "--basis", "one projection", "--reopen-condition", "new backend")
        self.end(task_id, route="changed")
        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        )
        snapshot = model.dashboard_snapshot()
        self.assertEqual(snapshot["context"]["state"]["breakpoint"], "read model breakpoint")
        self.assertEqual(snapshot["history"][0]["task_id"], task_id)
        self.assertEqual(snapshot["decisions"][0]["decision"], "read model")
        self.assertGreaterEqual(len(snapshot["events"]), 4)
        self.assertEqual(snapshot["health"]["status"], "passed")
        self.assertEqual(snapshot["timeline"]["status"], "passed")
        self.assertGreaterEqual(len(snapshot["timeline"]["commits"]), 1)
        self.assertTrue({"task", "decision", "commit"}.issubset({item["kind"] for item in snapshot["search_index"]}))
        self.assertEqual(snapshot["timeline"]["branches"][0]["name"], "main")

    def test_task_details_aggregate_decisions_explorations_events_and_commits(self) -> None:
        task_id = "20260723_task_detail_001"
        self.start(task_id, "--track", "research", "--topic", "task-detail")
        self.update_state("task details ready")
        self.hooks("decision", "add", "--decision", "use a task detail center", "--alternatives", "separate pages",
                   "--basis", "one navigation hub", "--reopen-condition", "new record type")
        self.hooks("attempt", "update", "--hypothesis", "task links are sufficient", "--evidence", "records share task id",
                   "--conclusion", "aggregation is deterministic")
        self.end(task_id, state="validated", route="changed")
        self.commit_all("record task detail experiment")

        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "research/task-detail",
        )
        detail = model.dashboard_snapshot()["task_details"][task_id]
        self.assertEqual(detail["goal"], "test database workflow")
        self.assertEqual(detail["result"], "completed")
        self.assertEqual(detail["conclusion"], "aggregation is deterministic")
        self.assertIn("records share task id", detail["evidence"])
        kinds = {item["kind"] for item in detail["related"]}
        self.assertTrue({"decision", "exploration", "event", "commit"}.issubset(kinds))
        self.assertTrue(all(item["task_id"] == task_id for item in detail["related"]))

    def test_one_commit_can_link_to_multiple_task_details(self) -> None:
        task_ids = ["20260723_multi_001", "20260723_multi_002"]
        for task_id in task_ids:
            self.start(task_id)
            self.update_state(f"complete {task_id}")
            self.end(task_id)
        self.commit_all("complete two maintenance tasks")
        commit_hash = self.git("rev-parse", "HEAD").stdout.strip()

        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        )
        details = model.dashboard_snapshot()["task_details"]
        for task_id in task_ids:
            commits = [item for item in details[task_id]["related"] if item["kind"] == "commit"]
            self.assertEqual([item["record_id"] for item in commits], [commit_hash])

    def test_timeline_maps_branches_tasks_unlinked_commits_and_squash(self) -> None:
        note = self.root / "plain.txt"
        note.write_text("ordinary commit\n", encoding="utf-8")
        self.git("add", "plain.txt")
        self.git("commit", "-m", "ordinary unlinked commit")
        ordinary_hash = self.git("rev-parse", "HEAD").stdout.strip()

        task_id = "20260722_timeline_001"
        self.start(task_id, "--track", "research", "--topic", "timeline")
        experiment = self.root / "experiment.txt"
        experiment.write_text("timeline experiment\n", encoding="utf-8")
        self.update_state("timeline branch complete")
        self.end(task_id, state="active")
        self.commit_all("record timeline experiment")
        research_hash = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/research/timeline", research_hash)

        self.git("switch", "main")
        self.git("merge", "--squash", "research/timeline")
        self.git("commit", "-m", "squash timeline experiment")
        squash_hash = self.git("rev-parse", "HEAD").stdout.strip()

        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        )
        timeline = model.timeline()
        commits = {item["hash"]: item for item in timeline["commits"]}
        self.assertIn("main", timeline["lanes"])
        self.assertIn("research/timeline", timeline["lanes"])
        self.assertEqual(commits[ordinary_hash]["task_ids"], [])
        self.assertIn(task_id, commits[research_hash]["task_ids"])
        self.assertIn(task_id, commits[squash_hash]["task_ids"])
        self.assertEqual(commits[research_hash]["lane"], "research/timeline")
        self.assertEqual(commits[squash_hash]["lane"], "main")

        self.git("branch", "-D", "research/timeline")
        remote_only = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).timeline()
        self.assertIn(research_hash, {item["hash"] for item in remote_only["commits"]})
        self.git("update-ref", "-d", "refs/remotes/origin/research/timeline")
        after_delete = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).timeline()
        self.assertIn("research/timeline", after_delete["lanes"])
        visible = {item["hash"]: item for item in after_delete["commits"]}
        self.assertNotIn(research_hash, visible)
        self.assertIn("research/timeline", visible[squash_hash]["event_branches"])

    def test_timeline_git_failure_does_not_block_sqlite_snapshot(self) -> None:
        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        )
        with patch.object(model, "_build_timeline", side_effect=ReadModelError("git unavailable")):
            snapshot = model.dashboard_snapshot()
        self.assertEqual(snapshot["health"]["status"], "passed")
        self.assertEqual(snapshot["timeline"]["status"], "unavailable")
        self.assertIn("git unavailable", snapshot["timeline_error"])
        self.assertEqual(snapshot["task_details"], {})

    def test_timeline_cache_invalidates_after_git_commit(self) -> None:
        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        )
        before = model.timeline()
        extra = self.root / "cache.txt"
        extra.write_text("new tip\n", encoding="utf-8")
        self.git("add", "cache.txt")
        self.git("commit", "-m", "advance timeline tip")
        after = model.timeline()
        self.assertEqual(len(after["commits"]), len(before["commits"]) + 1)
        self.assertNotEqual(after, before)

    def test_timeline_branch_metadata_marks_unmerged_and_excludes_archive(self) -> None:
        self.git("switch", "-c", "research/open-model")
        open_file = self.root / "open.txt"
        open_file.write_text("open branch\n", encoding="utf-8")
        self.git("add", "open.txt")
        self.git("commit", "-m", "open research")
        open_tip = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/research/open-model", open_tip)
        self.git("branch", "archive/research/old-model", open_tip)

        self.git("switch", "main")
        self.git("switch", "-c", "experiment/merged-model")
        merged_file = self.root / "merged.txt"
        merged_file.write_text("merged branch\n", encoding="utf-8")
        self.git("add", "merged.txt")
        self.git("commit", "-m", "validated experiment")
        self.git("switch", "main")
        self.git("merge", "--no-ff", "experiment/merged-model", "-m", "merge validated experiment")

        timeline = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).timeline()
        branches = {item["name"]: item for item in timeline["branches"]}
        self.assertTrue(branches["research/open-model"]["unmerged"])
        self.assertIn("origin/research/open-model", branches["research/open-model"]["refs"])
        self.assertTrue(branches["experiment/merged-model"]["merged"])
        self.assertFalse(branches["experiment/merged-model"]["unmerged"])
        self.assertFalse(branches["archive/research/old-model"]["unmerged"])

    def test_search_index_includes_exploration_records(self) -> None:
        task_id = "20260723_search_index_001"
        self.start(task_id, "--track", "research", "--topic", "search-index")
        self.update_state("search index complete")
        self.hooks("decision", "add", "--decision", "index all records", "--alternatives", "separate search",
                   "--basis", "one query", "--reopen-condition", "new record type")
        self.hooks("attempt", "update", "--hypothesis", "search is complete", "--evidence", "all kinds mapped",
                   "--conclusion", "shared index works")
        self.end(task_id, state="validated", route="changed")
        snapshot = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "research/search-index",
        ).dashboard_snapshot()
        self.assertEqual({"task", "decision", "exploration", "commit"}, {item["kind"] for item in snapshot["search_index"]})

    def test_legacy_migration_is_complete_idempotent_and_deletes_sources(self) -> None:
        maintenance = self.root / "maintenance"
        (maintenance / "change_archive.md").write_text(
            "| 探索简单总结 | 任务/批次 | 任务或产物时间（Asia/Shanghai） | 时间证据 | 状态摘要 |\n"
            "|:---|:---|:---|:---|:---|\n| old task | 20260722_old_001 | 2026-07-22 10:00:00 | proof | success |\n",
            encoding="utf-8")
        (maintenance / "decision_log.md").write_text(
            "| ID | 时间（Asia/Shanghai） | 决策 | 替代方案 | 依据 | 重开条件 |\n|:---|:---|:---|:---|:---|:---|\n"
            "| D-20260722-001 | 2026-07-22 10:00:00 | use db | text | query | new backend |\n", encoding="utf-8")
        (maintenance / "current_task.md").write_text(
            "# 当前任务与交接\n\n**最后更新**：2026-07-22 10:00:00（Asia/Shanghai）\n**当前状态**：完成\n**主目标版本**：v2\n\n"
            "## 当前主目标\n\nGoal\n\n## 当前判决\n\nDecision\n\n## 真实断点\n\nBreakpoint\n\n## 接下来三步\n\n1. Next\n\n"
            "## 当前阻塞\n\nNone\n\n## 最近交接\n\n| 任务时间（Asia/Shanghai） | 本次任务 | 结果与真实断点 | 主目标变化 |\n"
            "|:---|:---|:---|:---|\n| 2026-07-22 10:00:00 | migrated | ok | unchanged |\n", encoding="utf-8")
        (maintenance / "exploration_log.md").write_text(
            "| 分支 | 目标 | 结果 | 证据 | PR 或归档分支 |\n|:---|:---|:---|:---|:---|\n"
            "| archive/research/x | goal | negative | proof | archive/research/x |\n", encoding="utf-8")
        history = self.root / ".project_hooks/history"
        history.mkdir(parents=True)
        (history / "20260722_receipt_001.json").write_text(json.dumps({
            "start": {"task_id": "20260722_receipt_001", "started_at": "2026-07-22 09:00:00", "declaration": {"scope": "receipt"}, "git": {"branch": "main"}},
            "end": {"finished_at": "2026-07-22 09:30:00", "result": "completed", "route": "unchanged", "note": "done", "git": {"commit": "abc"}},
        }), encoding="utf-8")

        first = json.loads(self.hooks("db", "migrate").stdout)
        rerun = self.hooks("db", "migrate", check=False)
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        second = json.loads(rerun.stdout)
        self.assertEqual(first["counts"], second["counts"])
        deletion = self.hooks("db", "migrate", "--delete-legacy", check=False)
        self.assertEqual(deletion.returncode, 0, deletion.stderr)
        deleted = json.loads(deletion.stdout)
        self.assertTrue(deleted["legacy_deleted"])
        for name in ("change_archive.md", "decision_log.md", "current_task.md", "exploration_log.md"):
            self.assertFalse((maintenance / name).exists())
        self.assertFalse(history.exists())
        context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertEqual(context["state"]["goal"], "Goal")
        self.assertEqual(len(json.loads(self.hooks("decisions", "--format", "json").stdout)), 1)
        self.assertGreaterEqual(len(json.loads(self.hooks("history", "--format", "json").stdout)), 2)

    def test_end_requires_state_update(self) -> None:
        task_id = "20260722_state_001"
        self.start(task_id)
        rebuild = self.hooks("db", "rebuild", check=False)
        self.assertNotEqual(rebuild.returncode, 0)
        self.assertIn("活动任务期间不能", rebuild.stderr)
        rejected = self.end(task_id, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("state update", rejected.stderr)
        self.update_state()
        self.end(task_id)
        history = json.loads(self.hooks("history", "--format", "json").stdout)
        self.assertEqual(history[0]["task_id"], task_id)

    def test_route_change_requires_decision_in_same_task(self) -> None:
        task_id = "20260722_decision_001"
        self.start(task_id)
        self.update_state()
        rejected = self.end(task_id, route="changed", check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.hooks("decision", "add", "--decision", "new route", "--alternatives", "old route",
                   "--basis", "evidence", "--reopen-condition", "new evidence")
        self.end(task_id, route="changed")
        decisions = json.loads(self.hooks("decisions", "--format", "json").stdout)
        self.assertEqual(decisions[0]["decision"], "new route")

    def test_exploration_validates_and_prepares_pr(self) -> None:
        task_id = "20260722_validated_001"
        self.start(task_id, "--track", "experiment", "--topic", "solver-check")
        self.update_state()
        self.hooks("attempt", "update", "--hypothesis", "solver is correct", "--evidence", "tests passed",
                   "--conclusion", "criteria satisfied")
        self.end(task_id, state="validated")
        self.commit_all("validated attempt")
        result = json.loads(self.hooks("prepare-pr").stdout)
        self.assertTrue(result["ready"])
        self.assertEqual(result["merge_method"], "squash_pr")
        self.assertFalse(result["network_actions_performed"])

    def test_validated_attempt_requires_all_evidence_fields(self) -> None:
        task_id = "20260722_missing_001"
        self.start(task_id, "--track", "research", "--topic", "missing-proof")
        self.update_state()
        rejected = self.end(task_id, state="validated", check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("hypothesis", rejected.stderr)

    def test_branch_switch_is_detected(self) -> None:
        self.start("20260722_switch_001", "--track", "sandbox", "--topic", "switch-check")
        self.git("switch", "main")
        for command in (("status",), ("pre-commit",)):
            result = self.hooks(*command, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("任务启动于分支", result.stderr)

    def test_unsafe_exploration_starts_fail_without_active_task(self) -> None:
        (self.root / "dirty.txt").write_text("dirty", encoding="utf-8")
        result = self.start("20260722_dirty_001", "--track", "research", "--topic", "dirty", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(self.hooks("status").stdout)["active"])
        (self.root / "dirty.txt").unlink()
        bad = self.start("20260722_bad_001", "--track", "research", "--topic", "Bad_Name", check=False)
        self.assertNotEqual(bad.returncode, 0)

    def test_negative_attempt_archive_and_main_import(self) -> None:
        task_id = "20260722_negative_001"
        self.start(task_id, "--track", "research", "--topic", "failed-model")
        self.update_state()
        self.hooks("attempt", "update", "--hypothesis", "model works", "--evidence", "counterexample",
                   "--conclusion", "model rejected")
        self.end(task_id, state="negative")
        self.commit_all("negative attempt")
        archived = json.loads(self.hooks("archive-attempt").stdout)
        self.assertEqual(archived["branch"], "archive/research/failed-model")
        self.commit_all("archive attempt")
        self.git("switch", "main")
        stable = "20260722_import_001"
        self.start(stable)
        self.update_state()
        imported = self.hooks("exploration", "import", "archive/research/failed-model", check=False)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.end(stable)
        explorations = json.loads(self.hooks("explorations", "--format", "json").stdout)
        self.assertEqual(explorations[0]["result"], "negative")

    def test_database_missing_or_corrupt_rebuilds_from_journal(self) -> None:
        task_id = "20260722_rebuild_001"
        self.start(task_id)
        self.update_state("stored breakpoint")
        self.end(task_id)
        database = self.root / ".project_hooks/maintenance.sqlite3"
        database.unlink()
        context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertEqual(context["state"]["breakpoint"], "stored breakpoint")
        rebuilt = json.loads(self.hooks("db", "rebuild").stdout)
        self.assertEqual(rebuilt["status"], "rebuilt")
        database.write_bytes(b"not sqlite")
        model = MaintenanceReadModel(database, self.root / "maintenance/events.jsonl", lambda: "main")
        dashboard = model.dashboard_snapshot()
        self.assertTrue(dashboard["health"]["rebuilt"])
        self.assertEqual(dashboard["context"]["state"]["breakpoint"], "stored breakpoint")
        checked = self.hooks("db", "verify", check=False)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        verified = json.loads(checked.stdout)
        self.assertEqual(verified["status"], "passed")

    def test_catalog_cli_scan_relations_context_and_rebuild(self) -> None:
        task_id = "20260726_catalog_001"
        self.start(task_id)
        for directory in ("source", "data", "theory", "analysis", "outputs"):
            (self.root / directory).mkdir()
        paper = self.root / "source/paper.pdf"
        dataset = self.root / "data/sample.csv"
        paper.write_bytes(b"%PDF-test")
        dataset.write_text("x,y\n1,2\n", encoding="utf-8")

        preview = json.loads(self.hooks("catalog", "scan", "--dry-run").stdout)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["scanned_files"], 2)
        self.assertEqual(json.loads(self.hooks("catalog", "list", "--format", "json").stdout), [])

        scanned = json.loads(self.hooks("catalog", "scan").stdout)
        self.assertEqual(len(scanned["changes"]), 2)
        second = json.loads(self.hooks("catalog", "scan").stdout)
        self.assertEqual(second["changes"], [])
        items = json.loads(self.hooks("catalog", "list", "--format", "json").stdout)
        paper_item = next(item for item in items if item["kind"] == "literature")
        data_item = next(item for item in items if item["kind"] == "data")
        self.assertEqual(paper_item["path"], "source/paper.pdf")
        self.assertEqual(paper_item["metadata"]["extension"], ".pdf")

        theory = json.loads(self.hooks(
            "catalog", "add", "--kind", "theory", "--title", "核心理论",
            "--summary", "公式 $E=mc^2$", "--tag", "core,physics",
            "--source", "https://example.invalid/theory", "--meta", "stage=draft",
        ).stdout)
        theory_id = theory["item_id"]
        updated = json.loads(self.hooks(
            "catalog", "update", theory_id, "--status", "active",
            "--summary", "更新后的公式 $E=mc^2$", "--tag", "core",
        ).stdout)
        self.assertEqual(updated["tags"], ["core"])

        linked = self.hooks(
            "catalog", "link", paper_item["item_id"], "supports", theory_id,
            "--note", "提供理论依据", check=False,
        )
        self.assertEqual(linked.returncode, 0, linked.stderr)
        relation = json.loads(linked.stdout)
        context = self.hooks(
            "catalog", "context", "--id", theory_id, "--format", "markdown"
        ).stdout
        self.assertIn("# 科研资料上下文", context)
        self.assertIn("核心理论", context)
        self.assertIn("supports", context)
        related = json.loads(self.hooks(
            "catalog", "list", "--related-to", theory_id, "--format", "json"
        ).stdout)
        self.assertEqual([item["item_id"] for item in related], [paper_item["item_id"]])

        archived = json.loads(self.hooks("catalog", "archive", data_item["item_id"]).stdout)
        self.assertEqual(archived["status"], "archived")
        restored = json.loads(self.hooks("catalog", "restore", data_item["item_id"]).stdout)
        self.assertEqual(restored["status"], "active")
        self.assertTrue(json.loads(self.hooks(
            "catalog", "unlink", relation["relation_id"]
        ).stdout)["removed"])

        paper.unlink()
        missing = json.loads(self.hooks("catalog", "scan").stdout)
        self.assertIn("missing", [change["action"] for change in missing["changes"]])
        missing_item = json.loads(self.hooks(
            "catalog", "show", paper_item["item_id"], "--format", "json"
        ).stdout)["item"]
        self.assertEqual(missing_item["status"], "missing")

        outside = Path(self.temp.name) / "outside.pdf"
        outside.write_bytes(b"outside")
        rejected = self.hooks(
            "catalog", "add", "--kind", "literature", "--title", "outside",
            "--path", str(outside), check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("项目目录内", rejected.stderr)

        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        )
        dashboard = model.dashboard_snapshot()
        self.assertEqual(len(dashboard["catalog_items"]), 3)
        self.assertTrue(any(item["kind"] == "catalog" for item in dashboard["search_index"]))

        database = self.root / ".project_hooks/maintenance.sqlite3"
        database.unlink()
        rebuilt = json.loads(self.hooks("catalog", "list", "--format", "json").stdout)
        self.assertEqual(len(rebuilt), 3)

    def test_catalog_writes_require_active_task_and_v1_events_remain_supported(self) -> None:
        rejected = self.hooks(
            "catalog", "add", "--kind", "theory", "--title", "no task", check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("没有活动任务", rejected.stderr)

        journal = self.root / "maintenance/events.jsonl"
        legacy = {
            "event_id": "legacy-v1",
            "schema_version": 1,
            "event_type": "project_state.updated",
            "occurred_at": "2026-01-01 00:00:00（Asia/Shanghai）",
            "branch": "main",
            "task_id": None,
            "payload": {
                "status": "完成", "main_goal_version": "v1", "goal": "legacy",
                "judgment": "legacy", "breakpoint": "legacy", "next_steps": [],
                "blocker": "",
            },
        }
        journal.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
        rebuilt = self.hooks("db", "rebuild", check=False)
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        database = self.root / ".project_hooks/maintenance.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        finally:
            connection.close()
        context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertEqual(context["state"]["goal"], "legacy")
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
        finally:
            connection.close()

    def test_catalog_ingest_auto_lifecycle_copy_dry_run_idempotency_and_failure_cleanup(self) -> None:
        outside = Path(self.temp.name) / "paper.pdf"
        outside.write_bytes(b"%PDF-ingest")
        preview = json.loads(self.hooks(
            "catalog", "ingest", str(outside), "--kind", "literature",
            "--tag", "core", "--dry-run",
        ).stdout)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["target_path"], "source/paper.pdf")
        self.assertFalse((self.root / "source/paper.pdf").exists())
        self.assertFalse(json.loads(self.hooks("status").stdout)["active"])

        ingested = json.loads(self.hooks(
            "catalog", "ingest", str(outside), "--kind", "literature",
            "--summary", "first summary", "--tag", "core",
        ).stdout)
        self.assertTrue(ingested["copied"])
        self.assertEqual(ingested["item"]["kind"], "literature")
        self.assertTrue((self.root / "source/paper.pdf").is_file())
        self.assertTrue(outside.is_file())
        self.assertFalse(json.loads(self.hooks("status").stdout)["active"])

        updated = json.loads(self.hooks(
            "catalog", "ingest", "source/paper.pdf",
            "--summary", "updated summary", "--tag", "reviewed",
        ).stdout)
        self.assertEqual(updated["action"], "update")
        self.assertFalse(updated["copied"])
        self.assertEqual(updated["item"]["summary"], "updated summary")
        self.assertEqual(updated["item"]["tags"], ["core", "reviewed"])

        other = Path(self.temp.name) / "other" / "paper.pdf"
        other.parent.mkdir()
        other.write_bytes(b"collision")
        collision = self.hooks(
            "catalog", "ingest", str(other), "--kind", "literature", check=False,
        )
        self.assertNotEqual(collision.returncode, 0)
        self.assertIn("拒绝覆盖", collision.stderr)
        renamed = json.loads(self.hooks(
            "catalog", "ingest", str(other), "--kind", "literature",
            "--name", "paper-2.pdf",
        ).stdout)
        self.assertEqual(renamed["target_path"], "source/paper-2.pdf")

        failing = Path(self.temp.name) / "invalid.pdf"
        failing.write_bytes(b"invalid")
        failed = self.hooks(
            "catalog", "ingest", str(failing), "--kind", "literature",
            "--title", " ", check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse((self.root / "source/invalid.pdf").exists())
        self.assertFalse(json.loads(self.hooks("status").stdout)["active"])

        wrong_location = self.root / "loose.pdf"
        wrong_location.write_bytes(b"loose")
        rejected = self.hooks(
            "catalog", "ingest", "loose.pdf", "--kind", "literature",
            "--dry-run", check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("必须先移动", rejected.stderr)

    def test_catalog_ingest_reuses_active_task_and_bulk_update_is_safe(self) -> None:
        task_id = "20260726_catalogbulk_001"
        self.start(task_id)
        (self.root / "data").mkdir()
        first = self.root / "data/first.csv"
        second = self.root / "data/second.csv"
        first.write_text("x\n1\n", encoding="utf-8")
        second.write_text("x\n2\n", encoding="utf-8")
        one = json.loads(self.hooks("catalog", "ingest", "data/first.csv", "--tag", "batch").stdout)
        two = json.loads(self.hooks("catalog", "ingest", "data/second.csv", "--tag", "batch").stdout)
        self.assertIsNone(one["task"])
        self.assertIsNone(two["task"])
        self.assertTrue(json.loads(self.hooks("status").stdout)["active"])

        unsafe = self.hooks(
            "catalog", "bulk-update", "--add-tag", "reviewed", check=False,
        )
        self.assertNotEqual(unsafe.returncode, 0)
        self.assertIn("--all", unsafe.stderr)
        conflict = self.hooks(
            "catalog", "bulk-update", "--tag", "batch",
            "--add-tag", "same", "--remove-tag", "same", check=False,
        )
        self.assertNotEqual(conflict.returncode, 0)
        preview = json.loads(self.hooks(
            "catalog", "bulk-update", "--tag", "batch",
            "--add-tag", "reviewed", "--summary", "shared", "--dry-run",
        ).stdout)
        self.assertEqual(preview["matched"], 2)
        self.assertEqual(preview["changed"], 2)
        unchanged = json.loads(self.hooks(
            "catalog", "list", "--tag", "reviewed", "--format", "json"
        ).stdout)
        self.assertEqual(unchanged, [])

        applied = json.loads(self.hooks(
            "catalog", "bulk-update", "--kind", "data", "--tag", "batch",
            "--add-tag", "reviewed", "--summary", "shared",
            "--source", "project import",
        ).stdout)
        self.assertEqual(applied["changed"], 2)
        records = json.loads(self.hooks(
            "catalog", "list", "--tag", "reviewed", "--format", "json"
        ).stdout)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(item["summary"] == "shared" for item in records))
        self.assertTrue(all(item["source"] == "project import" for item in records))

        cleared = json.loads(self.hooks(
            "catalog", "bulk-update", "--id", one["item"]["item_id"],
            "--remove-tag", "reviewed", "--clear-summary", "--clear-source",
        ).stdout)
        self.assertEqual(cleared["changed"], 1)
        self.update_state()
        self.end(task_id)

    def test_invalid_and_conflicting_events_are_rejected(self) -> None:
        journal = self.root / "maintenance/events.jsonl"
        journal.write_text("not-json\n", encoding="utf-8")
        invalid = self.hooks("db", "rebuild", check=False)
        self.assertNotEqual(invalid.returncode, 0)
        event = {"event_id": "same", "schema_version": 1, "event_type": "task.started",
                 "occurred_at": "2026-07-22 10:00:00", "branch": "main", "task_id": "x", "payload": {"a": 1}}
        other = dict(event)
        other["payload"] = {"a": 2}
        journal.write_text(json.dumps(event) + "\n" + json.dumps(other) + "\n", encoding="utf-8")
        conflict = self.hooks("db", "rebuild", check=False)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("事件 ID 内容冲突", conflict.stderr)

    def test_branch_scoped_state_survives_union_style_event_combination(self) -> None:
        main_task = "20260722_mainstate_001"
        self.start(main_task)
        self.update_state("main breakpoint")
        self.end(main_task)
        self.commit_all("main state")
        self.start("20260722_branchstate_001", "--track", "research", "--topic", "branch-state")
        self.update_state("branch breakpoint")
        self.end("20260722_branchstate_001", state="active")
        self.commit_all("branch state")
        branch_context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertEqual(branch_context["state"]["breakpoint"], "branch breakpoint")
        self.git("switch", "main")
        second_main = "20260722_mainstate_002"
        self.start(second_main)
        self.update_state("newer main breakpoint")
        self.end(second_main)
        self.commit_all("advance main state")
        merged = self.git("merge", "--no-commit", "--no-ff", "research/branch-state", check=False)
        self.assertEqual(merged.returncode, 0, merged.stderr)
        main_context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertEqual(main_context["state"]["breakpoint"], "newer main breakpoint")
        attempt = json.loads(self.hooks("attempt", "show").stdout)
        self.assertIsNone(attempt)

    def test_auto_commit_preserves_unrelated_staging_and_leaves_task_paths_clean(self) -> None:
        note = self.root / "user_note.txt"
        note.write_text("baseline\n", encoding="utf-8")
        self.git("add", "user_note.txt")
        self.git("commit", "-m", "add note")
        note.write_text("user staged work\n", encoding="utf-8")
        self.git("add", "user_note.txt")
        task_id = "20260722_autocommit_001"
        self.start(task_id, commit="always")
        self.update_state()
        result = json.loads(self.end(task_id).stdout)
        self.assertEqual(result["git"]["status"], "committed")
        self.assertEqual(self.git("status", "--porcelain=v1").stdout.splitlines(), ["M  user_note.txt"])

    def test_dashboard_cli_help_and_refresh_validation(self) -> None:
        help_result = self.hooks("dashboard", "--help")
        self.assertIn("--refresh-seconds", help_result.stdout)
        self.assertIn("--branch", help_result.stdout)
        invalid = self.hooks("dashboard", "--refresh-seconds", "-1", check=False)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("必须大于或等于 0", invalid.stderr)

    def test_root_help_separates_daily_and_advanced_commands(self) -> None:
        basic = self.hooks("--help").stdout
        self.assertIn("context", basic)
        self.assertIn("start", basic)
        self.assertIn("end", basic)
        self.assertIn("dashboard", basic)
        self.assertNotIn("prepare-pr", basic)
        self.assertNotIn("pre-commit", basic)
        advanced = self.hooks("--help-all").stdout
        self.assertIn("prepare-pr", advanced)
        self.assertIn("archive-attempt", advanced)
        self.assertIn("db", advanced)
        self.assertNotIn("pre-commit", advanced)


class DashboardPresentationTests(unittest.TestCase):
    def test_action_overview_is_shared_and_action_first(self) -> None:
        context = {
            "branch": "main",
            "overview_state": {
                "status": "进行中",
                "main_goal_version": "v9",
                "goal": "  精简\n维护体验  ",
                "judgment": "不应出现在默认概览",
                "breakpoint": "不应出现在默认概览",
                "blocker": "",
                "next_steps": ["完成代码", "运行测试"],
            },
            "active_task": {"task_id": "task-1", "branch": "main"},
            "git_state": {
                "branch": "main",
                "head": "1234567890",
                "upstream_ref": "origin/main",
                "upstream_head": "abcdef1234",
                "relation": "synced",
            },
            "recent_handoffs": [{
                "occurred_at": "2026-07-23 10:00:00",
                "task": "上一个任务",
                "result": "完成",
            }],
        }
        text = action_overview_text(context)
        self.assertLess(text.index("状态："), text.index("当前目标："))
        self.assertIn("活动任务：task-1（main）", text)
        self.assertIn("当前阻塞：无。", text)
        self.assertIn("1. 完成代码", text)
        self.assertIn("Git 同步：main 12345678 与 origin/main abcdef12：同步", text)
        self.assertIn("当前目标：精简 维护体验", text)
        self.assertIn("最近完成：2026-07-23 10:00:00｜上一个任务｜完成", text)
        self.assertNotIn("判断", text)
        self.assertNotIn("断点", text)
        dashboard_text = DashboardApp.overview_text({"context": context, "catalog_items": []})
        self.assertTrue(dashboard_text.startswith(text.rstrip()))
        self.assertIn("科研资料：共 0", dashboard_text)

    def test_publication_classifiers_are_conservative(self) -> None:
        self.assertTrue(is_publication_step("提交已验证改动"))
        self.assertTrue(is_publication_step("push main and verify remote"))
        self.assertTrue(is_publication_step("检查并发布全部已验证改动"))
        self.assertTrue(is_auxiliary_task_id("20260723_record_dashboard_publication_001"))
        self.assertFalse(is_publication_step("提交研究申请"))
        self.assertFalse(is_publication_step("发布研究申请"))
        self.assertFalse(is_auxiliary_task_id("20260723_record_simplification_001"))

    def test_result_labels_are_presentational_only(self) -> None:
        self.assertEqual(result_label("completed"), "完成")
        self.assertEqual(result_label("indeterminate"), "待判定")
        self.assertEqual(result_label("custom"), "custom")

    def test_filter_sort_and_long_values(self) -> None:
        records = [
            {"task": "beta", "summary": "短文本"},
            {"task": "alpha", "summary": "数据库迁移" + "x" * 200},
            {"task": "gamma", "summary": None},
        ]
        self.assertEqual([item["task"] for item in filter_records(records, "数据库")], ["alpha"])
        self.assertEqual([item["task"] for item in sort_records(records, "task")], ["alpha", "beta", "gamma"])
        self.assertEqual(filter_records([], "anything"), [])
        self.assertLessEqual(len(short(records[1]["summary"], 40)), 40)

    def test_global_search_requires_all_tokens_and_handles_empty_query(self) -> None:
        records = [
            {"record_id": "1", "search_text": "Alpha 数据库 migration"},
            {"record_id": "2", "search_text": "alpha dashboard"},
            {"record_id": "3", "search_text": "数据库 decision"},
        ]
        self.assertEqual([item["record_id"] for item in global_search(records, "数据库 ALPHA")], ["1"])
        self.assertEqual(global_search(records, ""), [])
        self.assertEqual(global_search(records, "missing"), [])
        mixed_times = [
            {"occurred_at": "2026-07-23 10:30:00（Asia/Shanghai）", "id": "task"},
            {"occurred_at": "2026-07-23T09:45:00+08:00", "id": "commit"},
        ]
        self.assertEqual([item["id"] for item in sort_records(mixed_times, "occurred_at", True)], ["task", "commit"])

    def test_record_navigation_handles_unlinked_and_multiple_tasks(self) -> None:
        self.assertEqual(linked_task_ids({"task_id": None}), [])
        self.assertEqual(linked_task_ids({"task_id": "task-1", "task_ids": ["task-1", "task-2"]}), ["task-1", "task-2"])
        self.assertEqual(record_location({"target": "timeline", "record_id": "abc"}), ("timeline", "abc"))
        self.assertEqual(record_identity({"event_id": "event-1", "task_id": "task-1"}), ("event_id", "event-1"))
        self.assertIsNone(record_identity({}))

    def test_dashboard_presets_use_ten_negative_only_and_unmerged_explorations(self) -> None:
        snapshot = {
            "history": [{"task_id": f"task-{index}"} for index in range(15)],
            "explorations": [
                {"event_id": "negative", "result": "negative"},
                {"event_id": "paused", "result": "paused"},
                {"event_id": "inconclusive", "result": "inconclusive"},
            ],
            "timeline": {"branches": [
                {"name": "research/open", "unmerged": True},
                {"name": "archive/research/old", "unmerged": False},
                {"name": "experiment/done", "unmerged": False},
            ]},
        }
        presets = dashboard_presets(snapshot)
        self.assertEqual(presets["recent"], [f"task-{index}" for index in range(10)])
        self.assertEqual(presets["negative"], ["negative"])
        self.assertEqual(presets["unmerged"], ["research/open"])

    def test_seven_primary_tabs_and_normalized_records(self) -> None:
        self.assertEqual(PRIMARY_TABS, ("概览", "搜索", "资料", "工作台", "任务", "时间线", "记录"))
        records = normalize_records(
            [{"event_id": "d1", "occurred_at": "2026-07-23 10:00:00", "branch": "main",
              "decision": "keep five pages", "decision_id": "D-1", "task_id": "task-1"}],
            [{"event_id": "e1", "occurred_at": "2026-07-23 11:00:00", "branch": "research/ui",
              "goal": "test layout", "result": "validated", "task_id": "task-2"}],
        )
        self.assertEqual([item["record_type"] for item in records], ["exploration", "decision"])
        self.assertEqual(records[0]["title"], "test layout")
        self.assertEqual(records[1]["task_id"], "task-1")

    def test_search_catalog_and_records_use_horizontal_detail_without_changing_advanced_view(self) -> None:
        table_source = inspect.getsource(TablePage)
        self.assertIn("split_detail: bool = False", table_source)
        self.assertIn("Panedwindow", table_source)
        self.assertIn('orient="horizontal"', table_source)
        self.assertIn("split_detail=True", inspect.getsource(CatalogPage))
        self.assertIn("split_detail=True", inspect.getsource(RecordsPage))
        self.assertIn("split_detail=True", inspect.getsource(DashboardApp))
        self.assertNotIn("split_detail=True", inspect.getsource(AdvancedWindow))

    def test_research_workbench_templates_include_structure_and_safety_rules(self) -> None:
        self.assertEqual(
            [template["label"] for template in RESEARCH_PROMPT_TEMPLATES.values()],
            [
                "文献精读", "文献比较", "研究问题与思路", "研究方法设计",
                "研究过程复盘", "结论与局限", "下一步研究计划",
            ],
        )
        self.assertEqual(
            [template["category"] for template in RESEARCH_PROMPT_TEMPLATES.values()],
            ["文献研究", "文献研究", "研究设计", "研究设计", "研究复盘", "研究复盘", "研究规划"],
        )
        for template_id, template in RESEARCH_PROMPT_TEMPLATES.items():
            prompt = build_research_prompt(template_id)
            self.assertIn(template["label"], prompt)
            self.assertIn("当前 Codex 对话中已经选择的文件", prompt)
            self.assertIn("还需要选择或提供哪些文件", prompt)
            self.assertIn("已有证据", prompt)
            self.assertIn("合理推断", prompt)
            self.assertIn("待验证建议", prompt)
            self.assertIn("未经我在对话中明确确认", prompt)
            self.assertIn("更新项目概览", prompt)
            self.assertIn("建议保存的资料条目", prompt)
            self.assertNotIn("上下文范围：", prompt)
            for section in template["sections"]:
                self.assertIn(section, prompt)
        with self.assertRaisesRegex(DashboardError, "未知的科研工作台操作"):
            build_research_prompt("unknown")

    def test_research_workbench_has_no_catalog_context_controls_or_callbacks(self) -> None:
        source = inspect.getsource(ResearchWorkbenchPage)
        parameters = inspect.signature(ResearchWorkbenchPage.__init__).parameters
        self.assertNotIn("上下文范围", source)
        self.assertNotIn("RESEARCH_CONTEXT", source)
        self.assertNotIn("selected_item", parameters)
        self.assertNotIn("visible_items", parameters)
        self.assertNotIn("relations", parameters)
        self.assertNotIn("overview", parameters)

    def test_research_prompt_list_filters_category_label_and_task_in_stable_order(self) -> None:
        self.assertEqual(
            [record["template_id"] for record in research_prompt_records()],
            list(WORKBENCH_PROMPT_TEMPLATES),
        )
        self.assertEqual(
            [record["label"] for record in research_prompt_records("文献研究")],
            ["文献精读", "文献比较"],
        )
        self.assertEqual(
            [record["label"] for record in research_prompt_records("方法设计")],
            ["研究方法设计"],
        )
        self.assertEqual(
            [record["label"] for record in research_prompt_records("可以立即开始执行")],
            ["下一步研究计划"],
        )
        self.assertEqual(research_prompt_records("不存在的筛选词"), [])

    def test_software_workbench_templates_cover_install_update_release_and_diagnostics(self) -> None:
        self.assertEqual(
            [template["label"] for template in SOFTWARE_PROMPT_TEMPLATES.values()],
            [
                "安装工作流软件", "初始化科研项目", "接管旧版项目",
                "查看软件版本", "检查可用更新", "更新到最新版本", "更新到指定版本",
                "验证项目健康", "诊断升级失败",
                "创建新版本提交", "发布 GitHub 新版本", "验证正式版本安装",
            ],
        )
        self.assertEqual(
            [record["label"] for record in research_prompt_records("版本发布")],
            ["创建新版本提交", "发布 GitHub 新版本", "验证正式版本安装"],
        )
        for template_id, template in SOFTWARE_PROMPT_TEMPLATES.items():
            prompt = build_research_prompt(template_id)
            self.assertIn(template["label"], prompt)
            self.assertIn("先做只读预检", prompt)
            self.assertIn("正式 GitHub Release", prompt)
            self.assertIn("不得手工改写 `maintenance/events.jsonl`", prompt)
            self.assertIn("实际命令", prompt)
            for section in template["sections"]:
                self.assertIn(section, prompt)
        self.assertIn("没有明确提供目标版本号", build_research_prompt("software_update_target"))
        self.assertIn("只创建版本提交，不推送", build_research_prompt("release_commit"))
        release = build_research_prompt("github_release")
        self.assertIn("具体版本号和本次发布的明确确认", release)
        self.assertIn("不得移动或复用既有版本标签", release)

    def test_research_workbench_uses_split_list_and_selection_does_not_copy(self) -> None:
        source = inspect.getsource(ResearchWorkbenchPage)
        self.assertIn("Panedwindow", source)
        self.assertIn("Treeview", source)
        self.assertIn('"category", "label"', source)
        self.assertNotIn("LabelFrame", source)

        class Tree:
            def __init__(self):
                self.selected = ("prompt-0",)

            def selection(self):
                return self.selected

        class Preview:
            def __init__(self):
                self.value = ""

            def delete(self, *_args):
                self.value = ""

            def insert(self, _index, value):
                self.value = value

            def get(self, *_args):
                return self.value

        class Frame:
            def __init__(self):
                self.copies = 0
                self.value = ""

            def clipboard_clear(self):
                self.copies += 1
                self.value = ""

            def clipboard_append(self, value):
                self.value += value

        page = ResearchWorkbenchPage.__new__(ResearchWorkbenchPage)
        page.visible = research_prompt_records()
        page.current_template_id = None
        page.tree = Tree()
        page.preview = Preview()
        page.frame = Frame()
        page.notify = lambda _message: None
        page.select_from_tree()
        self.assertEqual(page.current_template_id, "literature_review")
        self.assertIn("文献精读", page.preview.value)
        self.assertEqual(page.frame.copies, 0)

        original = deepcopy(RESEARCH_PROMPT_TEMPLATES)
        page.preview.value = "临时编辑"
        page.tree.selected = ("prompt-1",)
        page.select_from_tree()
        self.assertEqual(page.current_template_id, "literature_comparison")
        self.assertEqual(page.preview.value, build_research_prompt("literature_comparison"))
        self.assertEqual(RESEARCH_PROMPT_TEMPLATES, original)
        page.copy_current()
        self.assertEqual(page.frame.copies, 1)
        self.assertEqual(page.frame.value, page.preview.value)

    def test_research_workbench_defaults_to_first_prompt_and_handles_empty_filter(self) -> None:
        class Query:
            value = ""

            def get(self):
                return self.value

        class Tree:
            def __init__(self):
                self.rows = []
                self.selected = None

            def get_children(self):
                return tuple(row[0] for row in self.rows)

            def delete(self, *_items):
                self.rows = []

            def insert(self, _parent, _where, *, iid, values):
                self.rows.append((iid, values))

            def selection_set(self, iid):
                self.selected = iid

            def focus(self, _iid):
                pass

            def see(self, _iid):
                pass

        class Preview:
            def __init__(self):
                self.value = ""

            def delete(self, *_args):
                self.value = ""

            def insert(self, _index, value):
                self.value = value

        page = ResearchWorkbenchPage.__new__(ResearchWorkbenchPage)
        page.query = Query()
        page.tree = Tree()
        page.preview = Preview()
        page.current_template_id = None
        page.notify = lambda _message: None
        page.render_list()
        self.assertEqual(page.current_template_id, "literature_review")
        self.assertEqual(page.tree.selected, "prompt-0")
        self.assertEqual(page.preview.value, build_research_prompt("literature_review"))

        page.query.value = "不存在的筛选词"
        page.render_list()
        self.assertIsNone(page.current_template_id)
        self.assertEqual(page.preview.value, "没有匹配的提示词。")

    def test_next_research_plan_is_actionable_and_does_not_auto_execute(self) -> None:
        prompt = build_research_prompt("next_research_plan")
        for text in (
            "当前研究状态", "未解决问题", "证据缺口", "优先级", "所需输入", "方法",
            "预期输出", "完成标准", "风险与依赖", "最小可执行第一步", "建议保存的项目记录",
        ):
            self.assertIn(text, prompt)
        self.assertIn("未经我在对话中明确确认，不得修改项目", prompt)

    def test_copy_research_prompt_uses_edited_text_without_mutating_templates(self) -> None:
        class Clipboard:
            def __init__(self):
                self.value = ""

            def clipboard_clear(self):
                self.value = ""

            def clipboard_append(self, value):
                self.value += value

        original = deepcopy(RESEARCH_PROMPT_TEMPLATES)
        clipboard = Clipboard()
        message = copy_research_prompt(clipboard, "临时修改后的提示词")
        self.assertEqual(clipboard.value, "临时修改后的提示词")
        self.assertIn("已复制", message)
        self.assertEqual(RESEARCH_PROMPT_TEMPLATES, original)

    def test_catalog_detail_shows_metadata_relations_and_missing_warning(self) -> None:
        detail = CatalogPage.detail_text({
            "item_id": "lit-1", "kind_label": "文献", "status": "missing",
            "title": "Paper", "path": "source/paper.pdf", "source": "doi:10/example",
            "tags": ["core"], "summary": "summary", "metadata": {"year": "2026"},
            "updated_at": "2026-07-26 10:00:00", "_relations": [{
                "relation_type": "supports", "target_id": "theory-1",
                "_target_title": "Theory", "_direction": "out", "note": "evidence",
            }],
        })
        self.assertIn("文件当前不存在", detail)
        self.assertIn("supports → Theory", detail)
        self.assertIn('"year": "2026"', detail)

    def test_catalog_overview_and_cross_platform_file_reveal(self) -> None:
        items = [
            {"item_id": "lit-1", "kind": "literature", "title": "Paper",
             "status": "active", "created_at": "2026-07-26 10:00:00"},
            {"item_id": "data-1", "kind": "data", "title": "Dataset",
             "status": "missing", "created_at": "2026-07-26 11:00:00"},
            {"item_id": "out-1", "kind": "output", "title": "Old output",
             "status": "archived", "created_at": "2026-07-26 12:00:00"},
        ]
        overview = catalog_overview_text(items)
        self.assertIn("共 3", overview)
        self.assertIn("文献 1", overview)
        self.assertIn("数据 1", overview)
        self.assertIn("缺失 1", overview)
        self.assertIn("归档 1", overview)
        self.assertIn("Dataset；Paper", overview)
        self.assertNotIn("Old output", overview.splitlines()[1])

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "source/paper.pdf"
            target.parent.mkdir()
            target.write_bytes(b"pdf")
            commands = []
            item = {"path": "source/paper.pdf"}
            reveal_catalog_file(root, item, system="Windows", runner=commands.append)
            reveal_catalog_file(root, item, system="Darwin", runner=commands.append)
            reveal_catalog_file(root, item, system="Linux", runner=commands.append)
            self.assertEqual(commands[0][0], "explorer.exe")
            self.assertEqual(commands[1][:2], ["open", "-R"])
            self.assertEqual(commands[2], ["xdg-open", str(target.parent)])
            with self.assertRaisesRegex(DashboardError, "没有项目文件路径"):
                reveal_catalog_file(root, {}, runner=commands.append)
            target.unlink()
            with self.assertRaisesRegex(DashboardError, "文件不存在"):
                reveal_catalog_file(root, item, runner=commands.append)

    def test_catalog_scan_prompt_copies_without_codex_detection(self) -> None:
        class Clipboard:
            def __init__(self):
                self.value = ""

            def clipboard_clear(self):
                self.value = ""

            def clipboard_append(self, value):
                self.value += value

        clipboard = Clipboard()
        message = copy_catalog_scan_prompt(clipboard)
        self.assertEqual(clipboard.value, CODEX_CATALOG_SCAN_PROMPT)
        self.assertIn("catalog scan --dry-run", clipboard.value)
        self.assertIn("不要提交或推送", clipboard.value)
        self.assertNotIn("CODEX_THREAD_ID", clipboard.value)
        self.assertIn("粘贴到当前 Codex 对话框", message)

    def test_advanced_summary_contains_technical_status(self) -> None:
        text = advanced_summary({
            "health": {"status": "passed", "schema_version": 1, "events": 42,
                       "rebuilt": False, "journal_hash": "abc123"},
            "timeline_error": None,
        })
        self.assertIn("数据库：passed", text)
        self.assertIn("Schema：1", text)
        self.assertIn("事件：42", text)
        self.assertIn("abc123", text)

    def test_refresh_failure_preserves_last_snapshot(self) -> None:
        class Provider:
            def __init__(self):
                self.calls = 0

            def load(self):
                self.calls += 1
                if self.calls == 1:
                    return {"health": {"status": "passed"}}
                raise RuntimeError("database unavailable")

        controller = DashboardController(Provider())
        first, error = controller.refresh()
        self.assertIsNone(error)
        second, error = controller.refresh()
        self.assertEqual(second, first)
        self.assertIn("database unavailable", error)

    def test_timeline_layout_filters_highlights_and_links_branches(self) -> None:
        timeline = {
            "lanes": ["main", "research/model"],
            "commits": [
                {"hash": "a" * 40, "short_hash": "aaaaaaaa", "parents": [], "lane": "main",
                 "subject": "baseline", "task_ids": [], "event_branches": [], "events": []},
                {"hash": "b" * 40, "short_hash": "bbbbbbbb", "parents": ["a" * 40], "lane": "research/model",
                 "subject": "model task", "task_ids": ["task-1"], "event_branches": ["research/model"], "events": []},
                {"hash": "c" * 40, "short_hash": "cccccccc", "parents": ["a" * 40], "lane": "main",
                 "subject": "squash model", "task_ids": ["task-1"], "event_branches": ["research/model"], "events": []},
            ],
            "edges": [
                {"parent": "a" * 40, "child": "b" * 40},
                {"parent": "a" * 40, "child": "c" * 40},
            ],
        }
        layout = timeline_layout(timeline, query="model task")
        self.assertEqual(len(layout["nodes"]), 3)
        self.assertEqual([item["match"] for item in layout["nodes"]], [False, True, False])
        self.assertEqual(len(layout["associations"]), 1)
        focused = timeline_layout(timeline, branch="research/model")
        self.assertEqual({item["hash"] for item in focused["nodes"]}, {"b" * 40, "c" * 40})
        self.assertEqual(focused["lanes"], ["main", "research/model"])

    def test_timeline_layout_folds_after_fifty_and_can_expand(self) -> None:
        commits = []
        edges = []
        for index in range(60):
            commit_hash = f"{index:040x}"
            parent = f"{index - 1:040x}" if index else None
            commits.append({
                "hash": commit_hash, "short_hash": commit_hash[:8], "parents": [parent] if parent else [],
                "lane": "main", "subject": f"commit {index}", "task_ids": [], "event_branches": [], "events": [],
            })
            if parent:
                edges.append({"parent": parent, "child": commit_hash})
        timeline = {"lanes": ["main"], "commits": commits, "edges": edges}
        folded = timeline_layout(timeline)
        self.assertEqual(len(folded["nodes"]), 50)
        self.assertEqual(folded["folded_count"], 10)
        self.assertEqual(len(folded["truncated"]), 1)
        expanded = timeline_layout(timeline, expanded=True)
        self.assertEqual(len(expanded["nodes"]), 60)
        self.assertEqual(expanded["folded_count"], 0)
        subset = timeline_layout(timeline, branch_subset={"research/missing"})
        self.assertEqual(subset["nodes"], [])

    def test_timeline_refresh_error_preserves_previous_graph(self) -> None:
        class Provider:
            def __init__(self):
                self.calls = 0

            def load(self):
                self.calls += 1
                if self.calls == 1:
                    return {"timeline": {"status": "passed", "commits": [{"hash": "abc"}]}, "timeline_error": None,
                            "search_index": [{"kind": "commit", "record_id": "abc", "occurred_at": "2"}]}
                return {"timeline": {"status": "unavailable", "commits": []}, "timeline_error": "git unavailable",
                        "search_index": [{"kind": "task", "record_id": "task", "occurred_at": "3"}]}

        controller = DashboardController(Provider())
        first, error = controller.refresh()
        self.assertIsNone(error)
        second, error = controller.refresh()
        self.assertIsNone(error)
        self.assertEqual(second["timeline"]["commits"], first["timeline"]["commits"])
        self.assertTrue(second["timeline"]["stale"])
        self.assertEqual({item["kind"] for item in second["search_index"]}, {"task", "commit"})

    def test_tkinter_unavailable_has_cli_fallback(self) -> None:
        with patch("project_hooks.dashboard.import_tk", side_effect=DashboardError("missing\n" + CLI_FALLBACK)):
            with self.assertRaisesRegex(DashboardError, "project-hooks context"):
                launch_dashboard(object(), 0)


if __name__ == "__main__":
    unittest.main()
