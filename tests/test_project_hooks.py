from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_hooks.dashboard import (
    CLI_FALLBACK,
    DashboardController,
    DashboardError,
    filter_records,
    launch_dashboard,
    short,
    sort_records,
)
from project_hooks.read_model import MaintenanceReadModel


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
        self.hooks("install")
        self.assertTrue(database.exists())
        output = self.hooks("context", "--format", "markdown").stdout
        self.assertIn("# 动态维护上下文", output)
        self.assertEqual(self.git("config", "--local", "--get", "core.hooksPath").stdout.strip(), ".githooks")

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


class DashboardPresentationTests(unittest.TestCase):
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

    def test_tkinter_unavailable_has_cli_fallback(self) -> None:
        with patch("project_hooks.dashboard.import_tk", side_effect=DashboardError("missing\n" + CLI_FALLBACK)):
            with self.assertRaisesRegex(DashboardError, "python -m project_hooks context"):
                launch_dashboard(object(), 0)


if __name__ == "__main__":
    unittest.main()
