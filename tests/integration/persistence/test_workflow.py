from __future__ import annotations

import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from project_hooks.ui.cli import commands as cli_module
from project_hooks.infrastructure.persistence.active_task import load_active_state
from project_hooks.infrastructure.persistence.database import repository
from project_hooks.ui.cli.catalog_commands import CatalogRuntime, migrate_layout
from project_hooks.infrastructure.persistence.read_model import (
    MaintenanceReadModel,
    action_overview_text,
    git_state_summary,
    is_auxiliary_task_id,
    is_publication_step,
    stage_freshness_warning,
)
from project_hooks.infrastructure.persistence.store import append_events, ensure_database, load_events, new_event, record_events, rebuild
from project_hooks.infrastructure.system.verification import (
    iso_now, verification_fingerprint, verify_receipts, write_test_receipt,
)


SOURCE_ROOT = Path(__file__).resolve().parents[3]


class ProjectHooksSqliteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.seed_temp = tempfile.TemporaryDirectory()
        cls.seed_root = Path(cls.seed_temp.name) / "seed"
        cls.seed_root.mkdir()
        for name in (".codex", ".githooks", "maintenance", "project_hooks"):
            shutil.copytree(SOURCE_ROOT / name, cls.seed_root / name, ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("source", "data", "theory", "analysis", "outputs", "others", "reports", "sparks"):
            directory = cls.seed_root / "resources" / name
            directory.mkdir(parents=True)
            (directory / ".gitkeep").write_text("\n", encoding="utf-8")
        for name in ("AGENTS.md", ".gitignore", ".gitattributes"):
            shutil.copy2(SOURCE_ROOT / name, cls.seed_root / name)
        (cls.seed_root / "maintenance/events.jsonl").write_text("", encoding="utf-8")
        commands = (
            ("init", "-b", "main"),
            ("config", "user.name", "Project Hooks Test"),
            ("config", "user.email", "hooks@example.invalid"),
            ("add", "."),
            ("commit", "-m", "baseline"),
        )
        for command in commands:
            subprocess.run(["git", *command], cwd=cls.seed_root, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.seed_temp.cleanup()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(self.seed_root), str(self.root)],
            check=True, capture_output=True,
        )
        self.git("remote", "remove", "origin")
        self.git("config", "user.name", "Project Hooks Test")
        self.git("config", "user.email", "hooks@example.invalid")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=check)

    def hooks(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", "project_hooks", *args], cwd=self.root,
                              text=True, encoding="utf-8", capture_output=True, check=check)

    def start(self, task_id: str, *extra: str, commit: str = "never", check: bool = True) -> subprocess.CompletedProcess[str]:
        extra_args = list(extra)
        if "--track" in extra_args:
            track = extra_args[extra_args.index("--track") + 1]
            if track != "stable" and "--without-stage-reason" not in extra_args:
                extra_args += ["--without-stage-reason", "legacy exploration fixture"]
        return self.hooks("start", task_id, "--kind", "analysis", "--scope", "test database workflow",
                          "--acceptance", "workflow behaves deterministically", "--git-commit", commit,
                          "--task-size", "large" if commit == "always" else "small",
                          *extra_args, check=check)

    def update_state(self, breakpoint: str = "test completed") -> None:
        self.hooks("state", "update", "--judgment", "test judgment", "--breakpoint", breakpoint,
                   "--next", "continue testing", "--blocker", "none")

    def end(self, task_id: str, *, state: str | None = None,
            stage_review: str | None = "reviewed-no-change", main_goal: str | None = None,
            check: bool = True) -> subprocess.CompletedProcess[str]:
        args = ["end", task_id, "--result", "completed",
                "--methods-action", "updated", "--note", "test completed",
                "--evidence", "unit test"]
        if main_goal:
            args += ["--main-goal", main_goal]
        if state:
            args += ["--attempt-state", state]
        if stage_review:
            args += ["--stage-review", stage_review]
        return self.hooks(*args, check=check)

    def commit_all(self, message: str) -> None:
        self.git("add", ".")
        self.git("commit", "-m", message)

    def test_install_rebuilds_database_and_context_is_available(self) -> None:
        database = self.root / ".project_hooks/maintenance.sqlite3"
        self.assertFalse(database.exists())
        (self.root / "resources/reports/.gitkeep").unlink()
        (self.root / "resources/reports").rmdir()
        missing = self.hooks(check=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn(".\\workflow-monitor.exe install", missing.stderr)
        self.assertIn(".\\workflow-monitor.exe check", missing.stderr)
        self.hooks("install")
        self.assertTrue(database.exists())
        self.assertTrue((self.root / "resources/reports/.gitkeep").is_file())
        overview = self.hooks().stdout
        self.assertIn("项目概览", overview)
        self.assertIn("项目描述：", overview)
        self.assertIn("当前研究篇章", overview)
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

    def test_v4_checkpoint_is_single_source_and_exposes_field_sources(self) -> None:
        task_id = "20260811_checkpoint_v4_001"
        self.start(task_id)
        self.hooks(
            "state", "update", "--current-step", "implement projection",
            "--judgment", "checkpoint wins", "--breakpoint", "read model",
            "--next", "run tests", "--blocker", "none",
        )
        context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertEqual(context["state"]["goal"], "test database workflow")
        self.assertEqual(context["state"]["current_step"], "implement projection")
        self.assertEqual(context["field_sources"]["goal"]["event_type"], "task.started")
        self.assertEqual(context["field_sources"]["current_step"]["event_type"], "task.checkpointed")

    def test_context_and_dashboard_share_finish_preflight(self) -> None:
        task_id = "20260811_preflight_consistency_001"
        self.start(task_id)
        self.update_state()
        (self.root / "project_hooks" / "preflight_probe.py").write_text("changed = True\n", encoding="utf-8")
        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: self.git("branch", "--show-current").stdout.strip(),
        )
        context = model.context()
        dashboard_context = model.dashboard_snapshot()["context"]
        self.assertEqual(context["checkpoint_status"], "stale")
        self.assertEqual(
            [item["code"] for item in context["finish_preflight"]["blockers"]],
            [item["code"] for item in dashboard_context["finish_preflight"]["blockers"]],
        )
        self.assertEqual(context["required_actions"], dashboard_context["required_actions"])
        event_types = [item["event_type"] for item in load_events(self.root / "maintenance/events.jsonl")]
        self.assertIn("task.checkpointed", event_types)
        self.assertNotIn("project_state.updated", event_types)

    def test_state_compatibility_aliases_route_without_goal_duplication(self) -> None:
        task_id = "20260811_state_alias_v4_001"
        self.start(task_id)
        accepted = json.loads(self.hooks(
            "state", "update", "--status", "working", "--goal", "test database workflow",
        ).stdout)
        self.assertEqual(accepted["current_step"], "working")
        self.assertTrue(accepted["warnings"])
        rejected = self.hooks("state", "update", "--goal", "different goal", check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("stage update / project update", rejected.stderr)
        version = self.hooks("state", "update", "--main-goal-version", "v2", check=False)
        self.assertNotEqual(version.returncode, 0)
        self.assertIn("project update --main-goal-version", version.stderr)

    def test_exploration_without_stage_is_blocked_before_branch_or_event(self) -> None:
        before = (self.root / "maintenance/events.jsonl").read_bytes()
        blocked = self.hooks(
            "start", "20260811_stage_draft_v4_001", "--kind", "analysis",
            "--scope", "test database workflow", "--acceptance", "workflow behaves deterministically",
            "--git-commit", "never", "--task-size", "small",
            "--track", "experiment", "--topic", "stage-draft", check=False,
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("PH-S120", blocked.stderr)
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "main")
        self.assertEqual((self.root / "maintenance/events.jsonl").read_bytes(), before)
        allowed = self.start(
            "20260811_stage_bypass_v4_001", "--track", "experiment", "--topic", "stage-bypass",
            "--without-stage-reason", "isolated spike",
        )
        self.assertEqual(json.loads(allowed.stdout)["branch"], "experiment/stage-bypass")
        attempt = next(
            item for item in load_events(self.root / "maintenance/events.jsonl")
            if item["event_type"] == "attempt.started"
        )
        self.assertEqual(attempt["payload"]["without_stage_reason"], "isolated spike")
        self.assertNotIn("goal", attempt["payload"])

    def test_same_timestamp_rebuild_preserves_declaration_before_attempt(self) -> None:
        database = self.root / ".project_hooks/same-time.sqlite3"
        journal = self.root / "maintenance/same-time-events.jsonl"
        occurred_at = "2026-08-11 00:00:00"
        append_events(journal, self.root / ".project_hooks", [
            {
                "event_id": "z-task-start", "schema_version": 4,
                "event_type": "task.started", "occurred_at": occurred_at,
                "branch": "experiment/same-time", "task_id": "same-time-task",
                "payload": {"scope": "projected task scope", "acceptance": ["projected"]},
            },
            {
                "event_id": "a-attempt-start", "schema_version": 4,
                "event_type": "attempt.started", "occurred_at": occurred_at,
                "branch": "experiment/same-time", "task_id": "same-time-task",
                "payload": {
                    "attempt_id": "same-time-task", "track": "experiment",
                    "topic": "same-time", "base_commit": "abc",
                    "source_task_id": "same-time-task",
                },
            },
        ])
        connection = rebuild(database, journal, preserve_active=False)
        attempt = connection.execute(
            "SELECT goal, acceptance_json FROM attempts WHERE attempt_id='same-time-task'"
        ).fetchone()
        connection.close()
        self.assertEqual(attempt[0], "projected task scope")
        self.assertEqual(json.loads(attempt[1]), ["projected"])

    def test_prepare_pr_summary_falls_back_to_source_scope_then_topic(self) -> None:
        attempt = {
            "attempt_id": "attempt-1", "branch": "experiment/summary",
            "goal": "", "topic": "summary-topic",
        }
        events = [
            {
                "event_type": "task.started", "task_id": "source-task",
                "payload": {"scope": "source task scope"},
            },
            {
                "event_type": "attempt.started", "task_id": "source-task",
                "branch": "experiment/summary",
                "payload": {"attempt_id": "attempt-1", "source_task_id": "source-task"},
            },
        ]
        self.assertEqual(cli_module.attempt_pr_summary(attempt, events), "source task scope")
        self.assertEqual(cli_module.attempt_pr_summary(attempt, []), "summary-topic")
        self.assertEqual(
            cli_module.attempt_pr_summary(dict(attempt, goal="explicit goal"), events),
            "explicit goal",
        )

    def test_receipts_become_stale_when_software_inputs_change(self) -> None:
        task_id = "20260811_receipt_stale_v4_001"
        self.start(task_id)
        runner = self.root / "scripts/run_tests.py"
        runner.parent.mkdir()
        runner.write_text("# fixture runner\n", encoding="utf-8")
        now = iso_now()
        write_test_receipt(
            self.root, suite="fast", result="passed", tests=10, failures=0,
            coverage=None, started_at=now, finished_at=now,
        )
        passed = verify_receipts(
            self.root, task_id, profile="auto", changed_paths=["project_hooks/core/events.py"],
        )
        self.assertEqual(passed["problems"][0]["suite"], "full")
        target = self.root / "project_hooks/core/events.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        stale = verify_receipts(
            self.root, task_id, profile="auto", changed_paths=["project_hooks/core/events.py"],
        )
        self.assertEqual({item["reason"] for item in stale["problems"]}, {"stale", "missing"})

    def test_stage_review_is_explicit_and_cannot_claim_false_update(self) -> None:
        setup_id = "20260811_stage_setup_v4_001"
        self.start(setup_id)
        self.update_state()
        self.hooks(
            "stage", "start", "schema-v4", "--title", "Schema v4", "--goal", "converge state",
            "--acceptance", "projection is single source",
        )
        self.end(setup_id)
        task_id = "20260811_stage_review_v4_001"
        self.start(task_id)
        self.update_state()
        missing = self.end(task_id, stage_review=None, check=False)
        self.assertIn("--stage-review", missing.stderr)
        false_update = self.hooks(
            "end", task_id, "--result", "completed",
            "--methods-action", "reviewed-no-change", "--main-goal", "unchanged",
            "--note", "reviewed", "--stage-review", "updated", check=False,
        )
        self.assertIn("找不到本任务产生的研究篇章事件", false_update.stderr)
        finished = self.hooks(
            "end", task_id, "--result", "completed",
            "--methods-action", "reviewed-no-change", "--main-goal", "unchanged",
            "--note", "reviewed", "--stage-review", "reviewed-no-change",
        )
        self.assertEqual(json.loads(finished.stdout)["stage_review"]["result"], "reviewed-no-change")

    def test_ensure_database_recreates_v3_numbered_legacy_schema_and_preserves_active_task(self) -> None:
        database = self.root / ".project_hooks/legacy.sqlite3"
        journal = self.root / "maintenance/legacy-events.jsonl"
        database.parent.mkdir(parents=True, exist_ok=True)
        legacy = sqlite3.connect(database)
        legacy.executescript(
            """
            CREATE TABLE attempts (
              attempt_id TEXT PRIMARY KEY, branch TEXT NOT NULL UNIQUE, track TEXT NOT NULL,
              topic TEXT NOT NULL, base_commit TEXT NOT NULL, goal TEXT NOT NULL,
              acceptance_json TEXT NOT NULL, hypothesis TEXT, conclusion TEXT,
              state TEXT NOT NULL, pr TEXT, archive_branch TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE active_tasks (
              task_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, branch TEXT NOT NULL,
              record_json TEXT NOT NULL, state_updated INTEGER NOT NULL DEFAULT 0,
              decisions_added INTEGER NOT NULL DEFAULT 0
            );
            PRAGMA user_version=3;
            """
        )
        legacy.execute(
            "INSERT INTO active_tasks VALUES (?, ?, ?, ?, ?, ?)",
            ("active-legacy", "2026-08-01", "main", "{}", 1, 0),
        )
        legacy.commit()
        legacy.close()
        append_events(
            journal,
            self.root / ".project_hooks",
            [{
                "event_id": "attempt-v3",
                "schema_version": 3,
                "event_type": "attempt.started",
                "occurred_at": "2026-08-01 00:00:00",
                "branch": "research/schema-rebuild",
                "task_id": "task-v3",
                "payload": {
                    "attempt_id": "task-v3",
                    "track": "research",
                    "topic": "schema-rebuild",
                    "base_commit": "abc123",
                    "goal": "verify schema rebuild",
                    "acceptance": ["new columns are projected"],
                    "stage_id": "current-stage",
                },
            }],
        )

        connection = ensure_database(database, journal)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(attempts)").fetchall()
        }
        attempt = connection.execute(
            "SELECT stage_id FROM attempts WHERE attempt_id='task-v3'"
        ).fetchone()
        active = connection.execute(
            "SELECT task_id FROM active_tasks WHERE task_id='active-legacy'"
        ).fetchone()
        connection.close()

        self.assertTrue({"stage_id", "current_step", "progress", "next_step"} <= columns)
        self.assertEqual(attempt[0], "current-stage")
        self.assertEqual(active[0], "active-legacy")

    def test_record_events_rolls_back_journal_when_projection_fails(self) -> None:
        database = self.root / ".project_hooks/rollback.sqlite3"
        journal = self.root / "maintenance/rollback-events.jsonl"
        state_dir = self.root / ".project_hooks"
        initial = {
            "event_id": "initial-task",
            "schema_version": 3,
            "event_type": "task.started",
            "occurred_at": "2026-08-01 00:00:00",
            "branch": "main",
            "task_id": "initial-task",
            "payload": {"scope": "initial"},
        }
        append_events(journal, state_dir, [initial])
        connection = rebuild(database, journal)
        connection.close()
        before = journal.read_bytes()
        addition = {
            "event_id": "new-state",
            "schema_version": 3,
            "event_type": "project_state.updated",
            "occurred_at": "2026-08-01 00:01:00",
            "branch": "main",
            "task_id": "initial-task",
            "payload": {"status": "testing", "next_steps": []},
        }
        real_rebuild = rebuild
        calls = 0

        def flaky_rebuild(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("injected projection failure")
            return real_rebuild(*args, **kwargs)

        with patch("project_hooks.infrastructure.persistence.store.rebuild", side_effect=flaky_rebuild):
            with self.assertRaises(sqlite3.OperationalError):
                record_events(database, journal, state_dir, [addition])

        self.assertEqual(journal.read_bytes(), before)
        connection = ensure_database(database, journal)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        connection.close()

    def test_failed_exploration_start_restores_branch_active_state_and_journal(self) -> None:
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/main", base)
        before = (self.root / "maintenance/events.jsonl").read_bytes()
        args = cli_module.build_parser().parse_args([
            "start", "20260801_atomic_start_001", "--kind", "analysis",
            "--scope", "verify atomic exploration start", "--acceptance", "no residue",
            "--track", "research", "--topic", "atomic-start", "--task-size", "small",
            "--git-commit", "never",
        ])
        real_rebuild = rebuild
        real_persist = cli_module.persist

        def failing_persist(events):
            calls = 0

            def flaky_rebuild(*call_args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise sqlite3.OperationalError("injected start projection failure")
                return real_rebuild(*call_args, **kwargs)

            with patch("project_hooks.infrastructure.persistence.store.rebuild", side_effect=flaky_rebuild):
                return real_persist(events)

        cli_module.set_project_root(self.root)
        try:
            with patch("project_hooks.ui.cli.commands.persist", side_effect=failing_persist):
                with self.assertRaises(cli_module.WorkflowError):
                    cli_module.start_task(args)
            self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "main")
            self.assertFalse(self.git("show-ref", "--verify", "refs/heads/research/atomic-start", check=False).returncode == 0)
            self.assertEqual((self.root / "maintenance/events.jsonl").read_bytes(), before)
            connection = ensure_database(
                self.root / ".project_hooks/maintenance.sqlite3",
                self.root / "maintenance/events.jsonl",
            )
            self.assertIsNone(connection.execute("SELECT 1 FROM active_tasks").fetchone())
            connection.close()
        finally:
            cli_module.set_project_root(SOURCE_ROOT)

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
        self.assertNotIn("publication_commits", partial)

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

    def test_auxiliary_publication_tasks_stay_hidden_without_commit_folding(self) -> None:
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
        self.assertIsNone(details[publish_id]["parent_task_id"])
        self.assertIsNone(details[record_id]["parent_task_id"])
        self.assertEqual(details[primary_id]["auxiliary_tasks"], [])
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
            ["task.checkpointed", "task.finished", "task.started"],
        )
        finish = next(item for item in load_events(self.root / "maintenance/events.jsonl")
                      if item["task_id"] == task_id and item["event_type"] == "task.finished")
        self.assertNotIn("started_at", finish["payload"])
        self.assertNotIn("attempt_state", finish["payload"])

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
        self.end(task_id)
        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        )
        snapshot = model.dashboard_snapshot()
        self.assertEqual(snapshot["context"]["state"]["breakpoint"], "read model breakpoint")
        self.assertEqual(snapshot["history"][0]["task_id"], task_id)
        self.assertNotIn("decisions", snapshot)
        self.assertGreaterEqual(len(snapshot["events"]), 3)
        self.assertEqual(snapshot["health"]["status"], "passed")
        self.assertNotIn("timeline", snapshot)
        self.assertIn("task", {item["kind"] for item in snapshot["search_index"]})
        self.assertNotIn("decision", {item["kind"] for item in snapshot["search_index"]})
        self.assertNotIn("commit", {item["kind"] for item in snapshot["search_index"]})

    def test_task_details_aggregate_explorations_and_events(self) -> None:
        task_id = "20260723_task_detail_001"
        self.start(task_id, "--track", "research", "--topic", "task-detail")
        self.update_state("task details ready")
        self.hooks("attempt", "update", "--hypothesis", "task links are sufficient", "--evidence", "records share task id",
                   "--conclusion", "aggregation is deterministic")
        self.end(task_id, state="validated")
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
        self.assertTrue({"exploration", "event"}.issubset(kinds))
        self.assertNotIn("decision", kinds)
        self.assertNotIn("commit", kinds)
        self.assertTrue(all(item["task_id"] == task_id for item in detail["related"]))

    def test_project_stage_and_attempt_progress_are_structured(self) -> None:
        stable_id = "20260801_stage_setup_001"
        self.start(stable_id)
        profile = json.loads(self.hooks(
            "project", "update", "--description", "A structured research project",
            "--big-goal", "Keep research auditable",
        ).stdout)
        self.assertEqual(profile["big_goal"], "Keep research auditable")
        stage = json.loads(self.hooks(
            "stage", "start", "validation", "--title", "Validation",
            "--goal", "Validate the workflow", "--acceptance", "All checks pass",
        ).stdout)
        self.assertEqual(stage["status"], "active")
        self.hooks(
            "stage", "update", "validation", "--summary", "Core path implemented",
            "--current-step", "Run branch experiment", "--next-step", "Review evidence",
        )
        duplicate = self.hooks(
            "stage", "start", "other", "--title", "Other", "--goal", "Other goal",
            "--acceptance", "Done", check=False,
        )
        self.assertIn("已有 active 研究篇章", duplicate.stderr)
        self.update_state("stage profile ready")
        self.end(stable_id)
        self.commit_all("record project profile and stage")
        self.git("update-ref", "refs/remotes/origin/main", self.git("rev-parse", "HEAD").stdout.strip())

        attempt_id = "20260801_stage_attempt_001"
        self.start(attempt_id, "--track", "research", "--topic", "stage-progress")
        updated = json.loads(self.hooks(
            "attempt", "update", "--current-step", "Calibrate model",
            "--progress", "Baseline completed", "--next-step", "Run corner cases",
        ).stdout)
        self.assertEqual(updated["stage_id"], "validation")
        self.assertEqual(updated["current_step"], "Calibrate model")
        self.assertEqual(updated["progress"], "Baseline completed")
        self.update_state("attempt progress recorded")
        self.end(attempt_id, state="active")
        self.commit_all("record active stage exploration")
        self.git("switch", "main")

        context = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).context()
        self.assertEqual(context["project_profile"]["description"], "A structured research project")
        self.assertEqual(context["current_stage"]["stage_id"], "validation")
        projected = next(item for item in context["active_attempts"] if item["attempt_id"] == attempt_id)
        self.assertEqual(projected["next_step"], "Run corner cases")

        blocker_id = "20260801_stage_block_001"
        self.start(blocker_id)
        blocked_closure = self.hooks(
            "stage", "update", "validation", "--status", "paused",
            "--pause-kind", "temporary-closure", "--pause-note", "Enough evidence for now",
            check=False,
        )
        self.assertIn("active 探索", blocked_closure.stderr)
        blocked = self.hooks(
            "stage", "update", "validation", "--status", "completed",
            "--summary", "Done", "--evidence", "tests", check=False,
        )
        self.assertIn("active 探索", blocked.stderr)
        self.update_state("stage remains active")
        self.end(blocker_id)

    @unittest.skip("retired Workbench surface is covered by CLI absence tests")
    def test_external_workbench_cli_is_event_backed_and_not_in_context(self) -> None:
        task_id = "20260802_external_tools_001"
        self.start(task_id, "--track", "stable")
        added = json.loads(self.hooks(
            "workbench", "external", "add", "obsidian",
            "--name", "Obsidian", "--kind", "notes",
            "--purpose", "管理笔记", "--usage-hint", "整理研究笔记",
            "--reference", "Obsidian",
        ).stdout)
        self.assertEqual(added["status"], "active")
        self.hooks(
            "workbench", "external", "update", "obsidian",
            "--purpose", "管理科研笔记",
        )
        self.hooks("workbench", "external", "pause", "obsidian", "--note", "暂不使用")
        paused = json.loads(self.hooks(
            "workbench", "external", "show", "obsidian", "--format", "json",
        ).stdout)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["purpose"], "管理科研笔记")
        self.hooks("workbench", "external", "restore", "obsidian")
        listed = json.loads(self.hooks(
            "workbench", "external", "list", "--format", "json",
        ).stdout)
        self.assertEqual(listed[0]["status"], "active")
        context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertNotIn("external_tools", context)
        self.assertNotIn("Obsidian", json.dumps(context, ensure_ascii=False))
        events = load_events(self.root / "maintenance/events.jsonl")
        self.assertTrue(any(item["event_type"] == "workbench.external_upserted" for item in events))
        connection = sqlite3.connect(self.root / ".project_hooks/maintenance.sqlite3")
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
        finally:
            connection.close()
        self.update_state("external tools recorded")
        self.end(task_id)

    @unittest.skip("retired Workbench surface is covered by CLI absence tests")
    def test_workbench_items_and_packages_are_indexed_but_not_in_context(self) -> None:
        task_id = "20260809_workbench_package_001"
        self.start(task_id, "--track", "stable")
        markdown = self.root / "workbench/local/limit-check.md"
        markdown.write_text("# 极限检查\n\n检查 $x \\to 0$。\n", encoding="utf-8")
        added = json.loads(self.hooks(
            "workbench", "item", "add", "limit-check",
            "--kind", "checklist", "--purpose", "validation", "--title", "极限检查",
            "--summary", "检查已知极限", "--path", "workbench/local/limit-check.md",
            "--tag", "physics",
        ).stdout)
        self.assertEqual(added["origin"], "local")
        self.assertEqual(added["kind"], "checklist")
        self.assertEqual(added["purposes"], ["validation"])
        listed = json.loads(self.hooks(
            "workbench", "item", "list", "--kind", "checklist", "--purpose", "validation", "--format", "json",
        ).stdout)
        self.assertTrue(any(item["item_id"] == "limit-check" for item in listed))
        context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertNotIn("workbench_items", context)
        self.assertNotIn("极限检查", json.dumps(context, ensure_ascii=False))

        exported = json.loads(self.hooks(
            "workbench", "package", "export", "physics-methods",
            "--name", "物理方法", "--version", "1.0.0", "--author", "Researcher",
            "--item", "limit-check",
        ).stdout)
        archive = self.root / exported["output"]
        self.assertTrue(archive.is_file())
        preview = self.hooks("workbench", "package", "inspect", str(archive)).stdout
        self.assertIn("物理方法", preview)
        self.assertIn("检查清单", preview)
        imported = json.loads(self.hooks("workbench", "package", "import", str(archive)).stdout)
        self.assertEqual(imported["package_id"], "physics-methods")
        imported_id = imported["items"][0]["item_id"]
        reviewed = json.loads(self.hooks(
            "workbench", "item", "update", imported_id,
            "--purpose", "theory_derivation", "--review-state", "reviewed", "--tag", "reviewed",
        ).stdout)
        self.assertEqual(reviewed["origin"], "imported")
        self.assertEqual(reviewed["purposes"], ["theory_derivation"])
        repeated = json.loads(self.hooks("workbench", "package", "import", str(archive)).stdout)
        self.assertEqual(repeated["status"], "unchanged")

        markdown.write_text("# 极限检查\n\n内容已更新。\n", encoding="utf-8")
        failed = self.hooks("check", check=False)
        self.assertIn("内容哈希不一致", failed.stderr)
        self.hooks("workbench", "item", "update", "limit-check", "--refresh-content")
        self.hooks("check")
        self.update_state("workbench package round trip completed")
        self.end(task_id)

    def test_research_chapter_revision_pause_switch_resume_and_seal(self) -> None:
        task_id = "20260813_chapter_nonlinear_001"
        self.start(task_id)
        self.hooks("stage", "start", "chapter-a", "--title", "Chapter A",
                   "--goal", "Iterate on a hypothesis", "--acceptance", "Evidence converges")
        missing_summary = self.hooks(
            "stage", "update", "chapter-a", "--revision", "New evidence changed the model",
            "--evidence", "experiment-a", check=False)
        self.assertIn("--summary", missing_summary.stderr)
        missing_evidence = self.hooks(
            "stage", "update", "chapter-a", "--revision", "New evidence changed the model",
            "--summary", "Model B is now preferred", check=False)
        self.assertIn("--evidence", missing_evidence.stderr)
        revised = json.loads(self.hooks(
            "stage", "update", "chapter-a", "--revision", "New evidence rejected model A",
            "--summary", "Model B is now preferred", "--evidence", "experiment-a").stdout)
        self.assertEqual(revised["revision"], "New evidence rejected model A")
        missing_pause = self.hooks("stage", "update", "chapter-a", "--status", "paused", check=False)
        self.assertIn("--pause-kind", missing_pause.stderr)
        self.hooks("stage", "update", "chapter-a", "--status", "paused",
                   "--pause-kind", "temporary-closure", "--pause-note", "Sufficient for now")
        self.hooks("stage", "start", "chapter-b", "--title", "Chapter B",
                   "--goal", "Investigate another question", "--acceptance", "Question reviewed")
        blocked_resume = self.hooks("stage", "update", "chapter-a", "--status", "active", check=False)
        self.assertIn("已有 active 研究篇章", blocked_resume.stderr)
        self.hooks("stage", "update", "chapter-b", "--status", "paused",
                   "--pause-kind", "interruption", "--pause-note", "Return to chapter A")
        self.hooks("stage", "update", "chapter-a", "--status", "active")
        shown = json.loads(self.hooks("stage", "show", "chapter-a", "--format", "json").stdout)
        self.assertEqual(shown["summary"], "Model B is now preferred")
        self.assertEqual(shown["revisions"][0]["evidence"], ["experiment-a"])
        self.assertEqual(shown["pause"]["kind"], "temporary-closure")
        self.hooks("stage", "update", "chapter-a", "--status", "completed",
                   "--summary", "Evidence converged", "--evidence", "final-review")
        sealed = self.hooks("stage", "update", "chapter-a", "--status", "active", check=False)
        self.assertIn("completed 研究篇章不能再更新", sealed.stderr)
        self.update_state("nonlinear chapter semantics verified")
        self.end(task_id, stage_review="updated")

    def test_legacy_pause_rebuilds_without_inventing_pause_semantics(self) -> None:
        task_id = "20260813_chapter_legacy_pause_001"
        self.start(task_id)
        self.hooks("stage", "start", "legacy-pause", "--title", "Legacy pause",
                   "--goal", "Keep old journals readable", "--acceptance", "Projection is compatible")
        event = new_event(
            "stage.state_changed", branch="main", task_id=task_id,
            payload={"stage_id": "legacy-pause", "status": "paused"},
            timezone="Asia/Shanghai", event_id="legacy-stage-pause",
        )
        event["schema_version"] = 4
        append_events(
            self.root / "maintenance/events.jsonl", self.root / ".project_hooks", [event],
        )
        rebuild(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
        ).close()
        context = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).context()
        self.assertIsNone(context["current_stage"])
        self.assertEqual(context["latest_stage"]["pause"]["kind"], "legacy")
        rendered = self.hooks("context", "--format", "markdown").stdout
        self.assertIn("历史暂停", rendered)
        self.assertIn("旧日志未记录", rendered)
        self.update_state("legacy pause compatibility verified")
        self.end(task_id, stage_review="updated")

    def test_stage_validation_and_legacy_projects_do_not_infer_profile(self) -> None:
        context = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).context()
        self.assertIsNone(context["project_profile"])
        self.assertIsNone(context["current_stage"])
        self.assertIn("project update", action_overview_text(context))

        task_id = "20260801_stage_rules_001"
        self.start(task_id)
        invalid = self.hooks(
            "stage", "start", "Bad_Name", "--title", "Bad", "--goal", "Bad",
            "--acceptance", "Bad", check=False,
        )
        self.assertIn("stage_id", invalid.stderr)
        too_long = self.hooks(
            "project", "update", "--description", "x" * 501, check=False,
        )
        self.assertIn("500", too_long.stderr)
        self.update_state("validation checked")
        self.end(task_id)






    def test_search_index_includes_exploration_records(self) -> None:
        task_id = "20260723_search_index_001"
        self.start(task_id, "--track", "research", "--topic", "search-index")
        self.update_state("search index complete")
        self.hooks("attempt", "update", "--hypothesis", "search is complete", "--evidence", "all kinds mapped",
                   "--conclusion", "shared index works")
        self.end(task_id, state="validated")
        snapshot = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "research/search-index",
        ).dashboard_snapshot()
        self.assertEqual({"task", "exploration"}, {item["kind"] for item in snapshot["search_index"]})
        exploration = next(item for item in snapshot["search_index"] if item["kind"] == "exploration")
        self.assertEqual(exploration["status"], "待合并")

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
        migrated_events = load_events(maintenance / "events.jsonl")
        self.assertTrue(any(item["event_type"] == "legacy.project_state_imported" for item in migrated_events))
        self.assertTrue(any(item["event_type"] == "project.profile_updated" for item in migrated_events))
        self.assertFalse(any(
            item["event_type"] == "project_state.updated" and item.get("schema_version") == 4
            for item in migrated_events
        ))
        self.assertEqual(context["field_sources"]["main_goal_version"]["event_type"], "project.profile_updated")
        self.assertFalse(any(item["event_type"] == "decision.recorded" for item in migrated_events))
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

    def test_checkpoint_freshness_and_inferred_finish_fields(self) -> None:
        task_id = "20260811_checkpoint_freshness_001"
        self.start(task_id)
        self.update_state("initial checkpoint")
        (self.root / "notes.txt").write_text("changed after checkpoint\n", encoding="utf-8")

        stale = self.hooks(
            "end", task_id, "--result", "completed", "--note", "done", check=False,
        )
        self.assertIn("CHECKPOINT_STALE", stale.stderr)

        self.update_state("fresh checkpoint")
        finished = json.loads(self.hooks(
            "end", task_id, "--result", "completed", "--note", "done",
        ).stdout)
        self.assertNotIn("route", finished)
        self.assertEqual(finished["main_goal"], "unchanged")
        events = [
            event for event in load_events(self.root / "maintenance/events.jsonl")
            if event.get("task_id") == task_id and event["event_type"] == "task.finished"
        ]
        self.assertNotIn("methods_action", events[0]["payload"])
        self.assertNotIn("main_goal", events[0]["payload"])
        history = json.loads(self.hooks("history", "--format", "json").stdout)
        self.assertEqual(history[0]["task_id"], task_id)

    @unittest.skip("decision and route semantics were removed in 2.0")
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

    def test_exploration_identity_is_branch_across_multiple_tasks_and_commits(self) -> None:
        branch = "experiment/branch-scoped"
        first_task = "20260722_branch_scoped_001"
        self.start(first_task, "--track", "experiment", "--topic", "branch-scoped")
        self.update_state("first cycle complete")
        self.hooks(
            "attempt", "update", "--hypothesis", "one branch is one exploration",
            "--evidence", "first cycle evidence", "--conclusion", "first cycle supports it",
        )
        self.end(first_task, state="validated")
        self.commit_all("first exploration cycle")

        first_attempt = json.loads(self.hooks("context", "--format", "json").stdout)["attempts"][0]
        second_task = "20260722_branch_scoped_002"
        self.start(second_task, "--track", "experiment", "--topic", "branch-scoped")
        active_context = json.loads(self.hooks("context", "--format", "json").stdout)
        current = next(item for item in active_context["attempts"] if item["branch"] == branch)
        self.assertEqual(current["state"], "active")
        self.assertEqual(current["attempt_id"], first_attempt["attempt_id"])
        self.assertEqual(current["exploration_id"], branch)

        self.update_state("second cycle complete")
        self.hooks("attempt", "update", "--evidence", "second cycle evidence")
        self.end(second_task, state="validated")
        self.commit_all("second exploration cycle")

        explorations = json.loads(self.hooks("explorations", "--format", "json").stdout)
        branch_records = [item for item in explorations if item["exploration_id"] == branch]
        self.assertEqual(len(branch_records), 1)
        self.assertEqual(branch_records[0]["task_id"], second_task)
        self.assertIn("first cycle evidence", branch_records[0]["evidence"])
        self.assertIn("second cycle evidence", branch_records[0]["evidence"])
        events = json.loads(self.hooks("history", "--format", "json").stdout)
        self.assertEqual({item["task_id"] for item in events[:2]}, {first_task, second_task})
        recorded = [
            event for event in load_events(self.root / "maintenance/events.jsonl")
            if event["event_type"] == "exploration.recorded" and event["branch"] == branch
        ]
        self.assertEqual(len(recorded), 2)

        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        merged_snapshot = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: branch,
        ).dashboard_snapshot()
        merged_record = next(
            item for item in merged_snapshot["search_index"]
            if item["kind"] == "exploration" and item["branch"] == branch
        )
        self.assertEqual(merged_record["status"], "已合并")

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
        archived_snapshot = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl",
            lambda: "main",
        ).dashboard_snapshot()
        archived_record = next(
            item for item in archived_snapshot["search_index"]
            if item["kind"] == "exploration" and item["summary"] == "negative"
        )
        self.assertEqual(archived_record["status"], "已遗弃")

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
        paper = self.root / "resources/source/paper.pdf"
        dataset = self.root / "resources/data/sample.csv"
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
        self.assertEqual(paper_item["path"], "resources/source/paper.pdf")
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
        self.assertEqual(len(dashboard["resource_directories"]), 8)
        data_directory = next(
            item for item in dashboard["resource_directories"] if item["name"] == "data"
        )
        self.assertEqual(data_directory["actual_files"], 1)
        self.assertEqual(data_directory["indexed_files"], 1)
        self.assertTrue(any(item["kind"] == "catalog" for item in dashboard["search_index"]))

        database = self.root / ".project_hooks/maintenance.sqlite3"
        database.unlink()
        rebuilt = json.loads(self.hooks("catalog", "list", "--format", "json").stdout)
        self.assertEqual(len(rebuilt), 3)

    def test_catalog_enforces_resource_layout_and_check_requires_index(self) -> None:
        task_id = "20260801_catalog_layout_001"
        self.start(task_id)
        other = self.root / "resources/others/uncategorized.bin"
        report = self.root / "resources/reports/status.md"
        other.write_bytes(b"other")
        report.write_text("# Status\n", encoding="utf-8")
        spark = self.root / "resources/sparks/free-idea.md"
        spark.write_text("# Unconstrained idea\n", encoding="utf-8")

        failed = self.hooks("check", check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("未索引文件", failed.stderr)
        self.assertNotIn("free-idea.md", failed.stderr)
        scanned = json.loads(self.hooks("catalog", "scan").stdout)
        self.assertEqual(scanned["scanned_files"], 2)
        items = json.loads(self.hooks("catalog", "list", "--format", "json").stdout)
        self.assertEqual({item["kind"] for item in items}, {"other", "report"})
        registered_spark = json.loads(self.hooks(
            "catalog", "add", "--kind", "spark", "--title", "Free idea",
            "--path", "resources/sparks/free-idea.md",
        ).stdout)
        self.assertEqual(registered_spark["kind"], "spark")
        self.assertEqual(self.hooks("check", check=False).returncode, 0)

        loose = self.root / "loose.pdf"
        loose.write_bytes(b"loose")
        rejected = self.hooks(
            "catalog", "add", "--kind", "other", "--title", "loose",
            "--path", "loose.pdf", check=False,
        )
        self.assertIn("资料文件必须位于 resources/source", rejected.stderr)
        mismatch = self.hooks(
            "catalog", "update",
            next(item["item_id"] for item in items if item["kind"] == "report"),
            "--kind", "other", check=False,
        )
        self.assertIn("与 --kind other 不一致", mismatch.stderr)

    def test_simulation_directory_bundle_is_atomic_and_refreshes_digest(self) -> None:
        task_id = "20260809_simulation_bundle_001"
        self.start(task_id)
        bundle = self.root / "resources/analysis/monte-carlo"
        bundle.mkdir()
        (bundle / "main.py").write_text("print('run')\n", encoding="utf-8")
        (bundle / "model.py").write_text("VALUE = 1\n", encoding="utf-8")
        cache = bundle / "__pycache__"
        cache.mkdir()
        (cache / "model.pyc").write_bytes(b"ignored")
        (bundle / "scratch.tmp").write_bytes(b"ignored")

        added = json.loads(self.hooks(
            "catalog", "add", "--kind", "simulation", "--title", "Monte Carlo 主程序",
            "--path", "resources/analysis/monte-carlo", "--entrypoint", "main.py",
        ).stdout)
        self.assertEqual(added["metadata"]["entry_type"], "bundle")
        self.assertEqual(added["metadata"]["file_count"], 2)
        self.assertEqual(added["metadata"]["entrypoint"], "main.py")
        first_digest = added["metadata"]["tree_sha256"]
        self.assertEqual(self.hooks("check", check=False).returncode, 0)

        model = MaintenanceReadModel(
            self.root / ".project_hooks/maintenance.sqlite3",
            self.root / "maintenance/events.jsonl", lambda: "main",
        )
        analysis = next(
            item for item in model.dashboard_snapshot()["resource_directories"]
            if item["kind"] == "simulation"
        )
        self.assertEqual(analysis["logical_items"], 1)
        self.assertEqual(analysis["indexed_items"], 1)
        self.assertEqual(analysis["bundle_count"], 1)
        self.assertEqual(analysis["contained_files"], 2)

        (bundle / "model.py").write_text("VALUE = 2\n", encoding="utf-8")
        changed = self.hooks("check", check=False)
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("模拟资料包内容已变化", changed.stderr)
        refreshed = json.loads(self.hooks("catalog", "scan").stdout)
        self.assertEqual(refreshed["scanned_files"], 2)
        self.assertEqual(refreshed["scanned_items"], 1)
        self.assertEqual(len(refreshed["changes"]), 1)
        current = json.loads(self.hooks(
            "catalog", "show", added["item_id"], "--format", "json",
        ).stdout)["item"]
        self.assertNotEqual(current["metadata"]["tree_sha256"], first_digest)
        self.assertEqual(self.hooks("check", check=False).returncode, 0)

        standalone = self.root / "resources/analysis/standalone.py"
        standalone.write_text("print('standalone')\n", encoding="utf-8")
        standalone_scan = json.loads(self.hooks("catalog", "scan").stdout)
        self.assertEqual(len(standalone_scan["changes"]), 1)
        simulations = json.loads(self.hooks(
            "catalog", "list", "--kind", "simulation", "--format", "json",
        ).stdout)
        self.assertEqual(len(simulations), 2)
        self.assertEqual(
            {item["metadata"].get("entry_type", "file") for item in simulations},
            {"bundle", "file"},
        )
        self.assertEqual(self.hooks("check", check=False).returncode, 0)

        duplicate = self.hooks(
            "catalog", "add", "--kind", "simulation", "--title", "重复文件",
            "--path", "resources/analysis/monte-carlo/model.py", check=False,
        )
        self.assertIn("位于已登记模拟资料包内", duplicate.stderr)
        nested = bundle / "nested"
        nested.mkdir()
        (nested / "run.py").write_text("pass\n", encoding="utf-8")
        nested_result = self.hooks(
            "catalog", "add", "--kind", "simulation", "--title", "嵌套资料包",
            "--path", "resources/analysis/monte-carlo/nested", check=False,
        )
        self.assertIn("位于已登记模拟资料包内", nested_result.stderr)
        invalid_entry = self.hooks(
            "catalog", "update", added["item_id"], "--entrypoint", "missing.py",
            check=False,
        )
        self.assertIn("入口文件不存在", invalid_entry.stderr)

    def test_catalog_rejects_non_simulation_directory_bundle(self) -> None:
        self.start("20260809_non_simulation_bundle_001")
        directory = self.root / "resources/theory/theory-project"
        directory.mkdir()
        (directory / "README.md").write_text("# Theory\n", encoding="utf-8")
        result = self.hooks(
            "catalog", "add", "--kind", "theory", "--title", "Theory project",
            "--path", "resources/theory/theory-project", check=False,
        )
        self.assertIn("只有 simulation 类型可以登记目录资料包", result.stderr)

    def test_catalog_migrate_layout_previews_preserves_ids_and_rejects_conflicts(self) -> None:
        task_id = "20260801_catalog_migrate_001"
        self.start(task_id)
        legacy = self.root / "theory/legacy.md"
        legacy.parent.mkdir()
        legacy.write_text("# Legacy\n", encoding="utf-8")
        item_id = "theory-legacy"
        event = {
            "event_id": "legacy-catalog-item",
            "schema_version": 3,
            "event_type": "catalog.item_upserted",
            "occurred_at": "2026-08-01 14:00:00（Asia/Shanghai）",
            "branch": "main",
            "task_id": task_id,
            "payload": {
                "item_id": item_id, "kind": "theory", "title": "Legacy",
                "summary": "keep", "path": "theory/legacy.md", "status": "active",
                "tags": ["core"], "source": "legacy", "metadata": {},
                "created_at": "2026-08-01 14:00:00（Asia/Shanghai）",
            },
        }
        journal = self.root / "maintenance/events.jsonl"
        append_events(journal, self.root / ".project_hooks", [event])
        connection = rebuild(self.root / ".project_hooks/maintenance.sqlite3", journal)
        connection.close()

        preview = json.loads(self.hooks("catalog", "migrate-layout", "--dry-run").stdout)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["move_count"], 1)
        self.assertTrue(legacy.is_file())
        migrated = json.loads(self.hooks("catalog", "migrate-layout").stdout)
        self.assertEqual(migrated["migrated"], 1)
        self.assertFalse(legacy.exists())
        target = self.root / "resources/theory/legacy.md"
        self.assertTrue(target.is_file())
        item = json.loads(self.hooks("catalog", "show", item_id, "--format", "json").stdout)["item"]
        self.assertEqual(item["path"], "resources/theory/legacy.md")
        self.assertEqual(item["tags"], ["core"])

        conflict_source = self.root / "source/conflict.pdf"
        conflict_source.parent.mkdir()
        conflict_source.write_bytes(b"old")
        conflict_target = self.root / "resources/source/conflict.pdf"
        conflict_target.write_bytes(b"new")
        conflict = json.loads(self.hooks("catalog", "migrate-layout", "--dry-run").stdout)
        self.assertEqual(conflict["conflict_count"], 1)
        rejected = self.hooks("catalog", "migrate-layout", check=False)
        self.assertIn("目标冲突", rejected.stderr)
        self.assertEqual(conflict_source.read_bytes(), b"old")
        self.assertEqual(conflict_target.read_bytes(), b"new")

    def test_catalog_migrate_layout_rolls_back_files_when_event_persist_fails(self) -> None:
        self.hooks("install")
        source = self.root / "data/rollback.csv"
        source.parent.mkdir()
        source.write_text("x\n1\n", encoding="utf-8")
        database_path = self.root / ".project_hooks/maintenance.sqlite3"

        def connection_factory():
            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            return repository(connection)

        runtime = CatalogRuntime(
            root=self.root,
            database=connection_factory,
            emit=lambda kind, **values: {"event_type": kind, **values},
            persist=lambda _events: (_ for _ in ()).throw(RuntimeError("injected persist failure")),
            write_context=lambda: ({"task_id": "migration", "git": {"track": "stable"}}, "main"),
            timestamp=lambda: "2026-08-01 14:00:00（Asia/Shanghai）",
            has_active_task=lambda: True,
            auto_start=lambda _name: {},
            auto_finish=lambda _success, _note, _evidence: {},
        )
        with self.assertRaisesRegex(RuntimeError, "injected persist failure"):
            migrate_layout(SimpleNamespace(dry_run=False), runtime)
        self.assertTrue(source.is_file())
        self.assertFalse((self.root / "resources/data/rollback.csv").exists())

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
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
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
        self.assertEqual(preview["target_path"], "resources/source/paper.pdf")
        self.assertFalse((self.root / "resources/source/paper.pdf").exists())
        self.assertFalse(json.loads(self.hooks("status").stdout)["active"])

        ingested = json.loads(self.hooks(
            "catalog", "ingest", str(outside), "--kind", "literature",
            "--summary", "first summary", "--tag", "core",
        ).stdout)
        self.assertTrue(ingested["copied"])
        self.assertEqual(ingested["item"]["kind"], "literature")
        self.assertTrue((self.root / "resources/source/paper.pdf").is_file())
        self.assertTrue(outside.is_file())
        self.assertFalse(json.loads(self.hooks("status").stdout)["active"])

        updated = json.loads(self.hooks(
            "catalog", "ingest", "resources/source/paper.pdf",
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
        self.assertEqual(renamed["target_path"], "resources/source/paper-2.pdf")

        failing = Path(self.temp.name) / "invalid.pdf"
        failing.write_bytes(b"invalid")
        failed = self.hooks(
            "catalog", "ingest", str(failing), "--kind", "literature",
            "--title", " ", check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse((self.root / "resources/source/invalid.pdf").exists())
        self.assertFalse(json.loads(self.hooks("status").stdout)["active"])

        wrong_location = self.root / "loose.pdf"
        wrong_location.write_bytes(b"loose")
        rejected = self.hooks(
            "catalog", "ingest", "loose.pdf", "--kind", "literature",
            "--dry-run", check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("必须位于七个标准 resources 目录之一", rejected.stderr)

    def test_catalog_ingest_reuses_active_task_and_bulk_update_is_safe(self) -> None:
        task_id = "20260726_catalogbulk_001"
        self.start(task_id)
        (self.root / "data").mkdir()
        first = self.root / "resources/data/first.csv"
        second = self.root / "resources/data/second.csv"
        first.write_text("x\n1\n", encoding="utf-8")
        second.write_text("x\n2\n", encoding="utf-8")
        one = json.loads(self.hooks("catalog", "ingest", "resources/data/first.csv", "--tag", "batch").stdout)
        two = json.loads(self.hooks("catalog", "ingest", "resources/data/second.csv", "--tag", "batch").stdout)
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

    def test_dashboard_cli_is_removed(self) -> None:
        result = self.hooks("dashboard", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice", result.stderr)

    def test_diagnostics_cli_skips_expected_rejection_and_exports_allowlisted_bundle(self) -> None:
        self.start("20260722_diag_001")
        rejected = self.start("20260722_diag_002", check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("PH-E100", rejected.stderr)
        self.assertNotIn("事件编号", rejected.stderr)
        status = json.loads(self.hooks("diagnostics", "status").stdout)
        self.assertEqual(status["records"], 0)
        self.assertFalse(status["automatic_upload"])
        output = self.root / "diagnostics.zip"
        exported = json.loads(self.hooks("diagnostics", "export", "--output", str(output)).stdout)
        self.assertEqual(exported["status"], "exported")
        self.assertTrue(output.is_file())
        import zipfile
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"manifest.json", "report.md", "environment.json", "checks.json", "diagnostics.jsonl"},
            )

    def test_root_help_separates_daily_and_advanced_commands(self) -> None:
        basic = self.hooks("--help").stdout
        self.assertIn("context", basic)
        self.assertIn("start", basic)
        self.assertIn("end", basic)
        self.assertNotIn("dashboard", basic)
        self.assertNotIn("prepare-pr", basic)
        self.assertNotIn("pre-commit", basic)
        advanced = self.hooks("--help-all").stdout
        self.assertIn("prepare-pr", advanced)
        self.assertIn("archive-attempt", advanced)
        self.assertIn("db", advanced)
        self.assertNotIn("pre-commit", advanced)


    def test_active_task_sidecar_restores_corrupt_database(self) -> None:
        task_id = "20260801_sidecar_001"
        self.start(task_id)
        sidecar = load_active_state(self.root / ".project_hooks")
        self.assertEqual(sidecar["record"]["task_id"], task_id)
        (self.root / ".project_hooks/maintenance.sqlite3").write_bytes(b"not sqlite")
        recovered = self.hooks("status")
        self.assertEqual(json.loads(recovered.stdout)["task_id"], task_id)
        connection = sqlite3.connect(self.root / ".project_hooks/maintenance.sqlite3")
        try:
            row = connection.execute("SELECT task_id FROM active_tasks").fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], task_id)

    def test_task_abandon_preserves_files_and_staging(self) -> None:
        task_id = "20260801_abandon_001"
        self.start(task_id)
        work = self.root / "preserved.txt"
        work.write_text("keep\n", encoding="utf-8")
        self.git("add", "preserved.txt")
        result = json.loads(self.hooks("task", "abandon", "--reason", "superseded").stdout)
        self.assertTrue(result["files_preserved"])
        self.assertEqual(work.read_text(encoding="utf-8"), "keep\n")
        self.assertIn("preserved.txt", self.git("diff", "--cached", "--name-only").stdout)
        events = load_events(self.root / "maintenance/events.jsonl")
        finished = [event for event in events if event["task_id"] == task_id and event["event_type"] == "task.finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["payload"]["result"], "abandoned")

    def test_simultaneous_start_has_one_winner_and_reports_holder(self) -> None:
        common = [
            sys.executable, "-m", "project_hooks", "start", None,
            "--kind", "analysis", "--scope", "concurrent start",
            "--acceptance", "one writer", "--git-commit", "never", "--task-size", "small",
        ]
        commands = []
        for task_id in ("20260801_race_a_001", "20260801_race_b_001"):
            command = list(common)
            command[4] = task_id
            commands.append(command)
        processes = [
            subprocess.Popen(command, cwd=self.root, text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for command in commands
        ]
        results = [process.communicate(timeout=20) + (process.returncode,) for process in processes]
        self.assertEqual(sorted(item[2] for item in results), [0, 1])
        failure = next(item[1] for item in results if item[2] == 1)
        self.assertIn("PID=", failure)

    def test_end_retry_does_not_duplicate_finished_event(self) -> None:
        task_id = "20260801_retry_end_001"
        self.start(task_id, commit="always")
        self.update_state()
        (self.root / "result.txt").write_text("result\n", encoding="utf-8")
        self.update_state("result ready")
        self.git("config", "user.name", "")
        self.git("config", "user.email", "")
        first = self.end(task_id, check=False)
        self.assertNotEqual(first.returncode, 0)
        first_events = load_events(self.root / "maintenance/events.jsonl")
        self.assertEqual(len([
            event for event in first_events
            if event["task_id"] == task_id and event["event_type"] == "task.finished"
        ]), 1)
        self.git("config", "user.name", "Project Hooks Test")
        self.git("config", "user.email", "hooks@example.invalid")
        self.end(task_id)
        final_events = load_events(self.root / "maintenance/events.jsonl")
        self.assertEqual(len([
            event for event in final_events
            if event["task_id"] == task_id and event["event_type"] == "task.finished"
        ]), 1)
        connection = sqlite3.connect(self.root / ".project_hooks/maintenance.sqlite3")
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM active_tasks").fetchone()[0], 0)
        finally:
            connection.close()

    def test_end_requires_and_records_explicit_stage_review(self) -> None:
        setup_task = "20260801_stage_setup_001"
        self.start(setup_task)
        self.hooks(
            "stage", "start", "validation", "--title", "Validation",
            "--goal", "Validate workflow", "--acceptance", "All tests pass",
        )
        self.update_state()
        setup_result = json.loads(self.end(setup_task).stdout)
        self.assertNotIn("warnings", setup_result)

        task_id = "20260801_stage_reminder_001"
        self.start(task_id)
        self.update_state()
        result = json.loads(self.end(task_id).stdout)
        self.assertEqual(result["stage_review"]["result"], "reviewed-no-change")
        self.assertEqual(result["stage_review"]["stage_id"], "validation")
