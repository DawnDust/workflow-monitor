from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from project_hooks import __version__
from project_hooks.core.events import new_event
from project_hooks.infrastructure.git.client import run as git
from project_hooks.infrastructure.git.worktrees import invoke_workflow, repository_context
from project_hooks.infrastructure.persistence.project_registry import registry_for, ProjectRegistry, RegistryError
from project_hooks.infrastructure.persistence.store import append_events, load_events, record_events, sync_registry_events
from project_hooks.infrastructure.persistence.transaction import mutation_lock
from project_hooks.infrastructure.system.project_manager import initialize_project
from project_hooks.infrastructure.system.multi_worktree import enable, merge_events, snapshot, process_identity
from project_hooks.infrastructure.system.worktree_migration import plan_migration, migrate_project
from project_hooks.infrastructure.system.workflow_views import checkout_model, workflow_snapshot
from project_hooks.infrastructure.system.project_search import ProjectSearch
from project_hooks.infrastructure.system.verification import verification_fingerprint
from project_hooks.infrastructure.persistence.search_projection import search_records


class MultiWorktreeTests(unittest.TestCase):
    def test_search_skips_integration_for_unvalidated_explorations(self):
        model = checkout_model(self.root, "main")
        events = [new_event("exploration.recorded", branch="experiment/" + result, task_id=None,
            payload={"branch": "experiment/" + result, "goal": result, "result": result,
                     "evidence": "saved", "disposition_ref": ""}, timezone="UTC")
            for result in ["active", "negative", "inconclusive", "paused"]]
        with patch.object(model, "_exploration_integration_status", side_effect=AssertionError("unneeded Git proof")):
            records = search_records(model, events)
        self.assertEqual(len(records), 4)
        self.assertTrue(all(item["merge_status_code"] == "not_applicable" for item in records))

    def test_integration_reader_reuses_objects_but_checks_each_event(self):
        from project_hooks.infrastructure.git import client
        head = git(self.root, ["rev-parse", "HEAD"]).stdout.strip()
        reader = client.IntegrationEvidenceReader(self.root, {"refs/heads/main": head, "refs/heads/source": head})
        event_id = load_events(self.root / "maintenance/events.jsonl")[0]["event_id"]
        with patch.object(client, "run", wraps=client.run) as calls:
            result = reader.evidence("refs/heads/main", "refs/heads/source",
                required_path="maintenance/events.jsonl", required_text=event_id)
            self.assertEqual(result["state"], "merged")
            count = calls.call_count
            self.assertEqual(reader.evidence("refs/heads/main", "refs/heads/source",
                required_path="maintenance/events.jsonl", required_text=event_id), result)
            missing = reader.evidence("refs/heads/main", "refs/heads/source",
                required_path="maintenance/events.jsonl", required_text="missing-event")
            self.assertEqual(missing["reason"], "source_record_missing")
            self.assertEqual(calls.call_count, count)

    def test_search_warning_cache_retries_without_rebuilding_each_refresh(self):
        search = ProjectSearch(self.root, checkout_model(self.root, "main"))
        events = load_events(self.root / "maintenance/events.jsonl")
        with patch.object(search, "events", side_effect=[OSError("temporarily unavailable"), events]) as reads:
            with patch("project_hooks.infrastructure.system.project_search.time.monotonic", return_value=100):
                first = search.load()
            self.assertTrue(first["search_warnings"])
            with patch("project_hooks.infrastructure.system.project_search.time.monotonic", return_value=105):
                self.assertIs(search.load(), first)
            self.assertEqual(reads.call_count, 1)
            with patch("project_hooks.infrastructure.system.project_search.time.monotonic", return_value=111):
                recovered = search.load()
            self.assertFalse(recovered["search_warnings"])
            self.assertEqual(reads.call_count, 2)

    def test_search_cache_invalidates_on_journal_and_ref_changes(self):
        search = ProjectSearch(self.root, checkout_model(self.root, "main"))
        first = search.load()
        self.assertIs(search.load(), first)
        append_events(self.root / "maintenance/events.jsonl", self.root / ".project_hooks", [
            new_event("catalog.item_upserted", branch="main", task_id=None,
                payload={"item_id": "cache-resource", "kind": "theory", "title": "New resource",
                         "path": "resources/theory/cache.md"}, timezone="UTC")])
        changed = search.load()
        self.assertTrue(any(row["record_id"] == "cache-resource" for row in changed["search_index"]))
        reader = search.integration_reader
        git(self.root, ["branch", "new-ref"])
        self.assertIsNot(search.load(), changed)
        self.assertIsNot(search.integration_reader, reader)
        self.assertIn("refs/heads/new-ref", search.integration_reader.refs)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow parallel ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Project with spaces"
        self.root.mkdir()
        git(self.root, ["init", "-b", "main"])
        git(self.root, ["config", "user.name", "Test"])
        git(self.root, ["config", "user.email", "test@example.invalid"])
        initialize_project(self.root, __version__, unified=False)
        (self.root / "shared.txt").write_text("first\nsecond\n", encoding="utf-8")
        git(self.root, ["-c", "core.hooksPath=", "add", "."])
        git(self.root, ["-c", "core.hooksPath=", "commit", "-m", "baseline"])

    def command(self, root, *arguments, success=True):
        result = invoke_workflow(root, list(arguments))
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        return result

    def activate(self):
        result = enable(self.root, apply=True)
        self.assertFalse(result["conflicts"])
        return registry_for(self.root)

    def start(self, topic="alpha"):
        return self.command(self.root, "start", "--kind", "docs", "--scope", topic, "--acceptance", "inspect output", "--track", "experiment", "--topic", topic, "--stage", "none", "--without-stage-reason", "software test", "--git-commit", "never")

    def test_three_real_worktrees_parallel_and_project_progress(self):
        self.activate()
        with ThreadPoolExecutor(max_workers=3) as pool:
            tasks = list(pool.map(self.start, ("alpha", "beta", "gamma")))
        self.assertEqual(len({t["task_id"] for t in tasks}), 3)
        for task in tasks:
            self.assertTrue(Path(task["worktree"]).name.startswith(task["task_id"] + "-"))
            self.assertIn("/" + task["task_id"] + "-", task["branch"])
            self.command(Path(task["worktree"]), "report", "--current-step", "progress " + task["task_id"])
        data = self.command(self.root, "status", "--all")
        self.assertEqual(len(data["tasks"]), 3)
        self.assertTrue(all(t["current_step"].startswith("progress") for t in data["tasks"]))
        self.assertEqual(data["pending_events"], 0)
        identities = [e["event_id"] for t in tasks for e in load_events(Path(t["worktree"]) / "maintenance/events.jsonl") if e.get("task_id") == t["task_id"]]
        self.assertEqual(len(identities), len(set(identities)))

    def test_same_tree_refuses_second_task_pause_resume_and_finish(self):
        registry = self.activate()
        task = self.start()
        path = Path(task["worktree"])
        rejected = self.command(path, "start", "--kind", "docs", "--scope", "second", "--acceptance", "done", success=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.command(path, "task", "pause", "--reason", "waiting", "--next", "continue tomorrow")
        self.assertEqual(registry.task(task["task_id"])["status"], "paused")
        rejected = self.command(path, "report", "--current-step", "must resume", success=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.command(path, "task", "resume")
        self.command(path, "end", "--result", "completed", "--note", "done", "--attempt-state", "negative", "--evidence", "test fixture", "--conclusion", "no research result")
        self.assertEqual(registry.task(task["task_id"])["status"], "completed")

    def test_dirty_main_and_active_stable_do_not_block_creation(self):
        registry = self.activate()
        stable = self.command(self.root, "start", "--kind", "docs", "--scope", "main work", "--acceptance", "done", "--git-commit", "never")
        (self.root / "shared.txt").write_text("uncommitted main\n")
        task = self.start()
        self.assertEqual(registry.task(stable["task_id"])["status"], "active")
        self.assertEqual((Path(task["worktree"]) / "shared.txt").read_text(), "first\nsecond\n")

    def test_registry_reservations_unique_across_processes_and_over_999(self):
        registry = self.activate()
        with registry.transaction() as connection:
            connection.execute("INSERT INTO counters VALUES ('20261117',999)")
        task = registry.reserve(self.root, "20261117", track="experiment", topic="counter")
        self.assertEqual(task["task_id"], "20261117-1000")
        self.assertNotEqual(registry.reserve(self.root, "20261118", track="experiment", topic="next")["task_id"], task["task_id"])

    def test_log_preserves_original_bytes_and_id_content_conflicts(self):
        log = self.root / "maintenance/events.jsonl"
        initial = new_event("example", branch="main", task_id=None, payload={"text": "研究"}, timezone="UTC")
        raw = (json.dumps(initial, ensure_ascii=False, indent=None) + "\r\n\r\n").encode("utf-8")
        log.write_bytes(raw)
        addition = new_event("example", branch="main", task_id=None, payload={}, timezone="UTC")
        append_events(log, self.root / ".project_hooks", [initial, addition, addition])
        self.assertTrue(log.read_bytes().startswith(raw))
        self.assertEqual(len(load_events(log)), 2)
        corrupt = {**addition, "payload": {"different": True}}
        before = log.read_bytes()
        with self.assertRaises(RuntimeError):
            append_events(log, self.root / ".project_hooks", [corrupt])
        self.assertEqual(log.read_bytes(), before)

    def test_outbox_survives_failure_and_retries_without_duplicates(self):
        registry = self.activate()
        task = self.start()
        path = Path(task["worktree"])
        event = new_event("example", branch=task["branch"], task_id=task["task_id"], payload={}, timezone="UTC")
        registry.record_events(path, [event])
        with patch("project_hooks.infrastructure.persistence.store.append_events", side_effect=OSError("disk unavailable")):
            failures = sync_registry_events(registry)
        self.assertTrue(failures)
        self.assertTrue(registry.pending())
        self.assertFalse(sync_registry_events(registry))
        self.assertFalse(sync_registry_events(registry))
        self.assertEqual(sum(e["event_id"] == event["event_id"] for e in load_events(path / "maintenance/events.jsonl")), 1)

    def test_other_tree_writer_does_not_block_report(self):
        self.activate()
        first, second = self.start("alpha"), self.start("beta")
        with mutation_lock(Path(first["worktree"]) / ".project_hooks", command="long-test"):
            self.command(Path(second["worktree"]), "report", "--current-step", "independent")
        self.assertEqual(next(t for t in snapshot(self.root)["tasks"] if t["task_id"] == second["task_id"])["current_step"], "independent")

    def test_file_overlap_and_research_decision_invalidated_on_change(self):
        registry = self.activate()
        first, second = self.start("alpha"), self.start("beta")
        for task, value in ((first, "left"), (second, "right")):
            path = Path(task["worktree"])
            (path / "shared.txt").write_text(value)
            event = new_event("stage.updated", branch=task["branch"], task_id=task["task_id"], payload={"stage_id": "same-stage", "summary": value}, timezone="UTC")
            registry.record_events(path, [event])
        data = snapshot(self.root)
        self.assertIn("path:shared.txt", {c["object"] for c in data["conflicts"]})
        conflict = next(c for c in data["conflicts"] if c["object"] == "stage:same-stage")
        rejected = self.command(self.root, "conflict", "resolve", conflict["conflict_id"], "--fingerprint", conflict["fingerprint"], "--decision", "retain both", "--reason", "need evidence", "--evidence", "two sources", success=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.command(self.root, "conflict", "resolve", conflict["conflict_id"], "--fingerprint", conflict["fingerprint"], "--decision", "retain both", "--reason", "need evidence", "--evidence", "two sources", "--confirmed")
        path = Path(first["worktree"])
        event = new_event("stage.updated", branch=first["branch"], task_id=first["task_id"], payload={"stage_id": "same-stage", "summary": "new evidence"}, timezone="UTC")
        registry.record_events(path, [event])
        conflict = next(c for c in snapshot(self.root)["conflicts"] if c["object"] == "stage:same-stage")
        self.assertEqual(conflict["status"], "pending")
        self.assertTrue(conflict["previous_decision_stale"])

    def test_removed_worktree_keeps_task_and_marks_unverified(self):
        registry = self.activate()
        task = self.start()
        git(self.root, ["worktree", "remove", "--force", task["worktree"]])
        data = snapshot(self.root)
        self.assertEqual(registry.task(task["task_id"])["status"], "active")
        self.assertEqual(data["tasks"][0]["verification"], "状态待核实")
        self.command(self.root, "task", "abandon", "--task-id", task["task_id"], "--reason", "explicitly abandoned")
        self.assertEqual(registry.task(task["task_id"])["status"], "abandoned")

    def test_migration_imports_legacy_active_without_overwriting(self):
        legacy = self.command(self.root, "start", "20261117_legacy_001", "--kind", "docs", "--scope", "old", "--acceptance", "done", "--git-commit", "never")
        original = (self.root / ".project_hooks/active-task.json").read_bytes()
        registry = self.activate()
        self.assertEqual(registry.active(self.root)["task_id"], legacy["task_id"])
        self.assertEqual((self.root / ".project_hooks/active-task.json").read_bytes(), original)

    def test_merge_driver_deduplicates_and_rejects_changed_baseline(self):
        folder = Path(self.temp.name)
        base, ours, theirs = (folder / name for name in ("base", "ours", "theirs"))
        seed = new_event("example", branch="main", task_id=None, payload={}, timezone="UTC")
        left = new_event("example", branch="main", task_id=None, payload={"left": 1}, timezone="UTC")
        right = new_event("example", branch="main", task_id=None, payload={"right": 1}, timezone="UTC")
        for path, events in ((base, [seed]), (ours, [seed, left]), (theirs, [seed, left, right])):
            path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        before = ours.read_bytes()
        merge_events(base, ours, theirs)
        self.assertTrue(ours.read_bytes().startswith(before))
        self.assertEqual(len(load_events(ours)), 3)
        theirs.write_text(json.dumps(right) + "\n")
        with self.assertRaises(RuntimeError):
            merge_events(base, ours, theirs)

    def test_current_process_identity_is_stable(self):
        self.assertEqual(process_identity(os.getpid()), process_identity(os.getpid()))

    def test_duplicate_legacy_ids_and_event_content_are_quarantined(self):
        other = Path(self.temp.name) / "legacy second"
        git(self.root, ["worktree", "add", "-b", "experiment/legacy", str(other)])
        for root in (self.root, other):
            self.command(root, "start", "20261117_duplicate_001", "--kind", "docs", "--scope", "legacy", "--acceptance", "done", "--track", "stable" if root == self.root else "experiment", "--stage", "none", "--without-stage-reason", "test", "--git-commit", "never")
        first = new_event("example", branch="main", task_id=None, payload={"side": "first"}, timezone="UTC")
        second = {**first, "payload": {"side": "second"}}
        append_events(self.root / "maintenance/events.jsonl", self.root / ".project_hooks", [first])
        append_events(other / "maintenance/events.jsonl", other / ".project_hooks", [second])
        before = (other / ".project_hooks/active-task.json").read_bytes()
        preview = enable(self.root)
        kinds = {c["kind"] for c in preview["conflicts"]}
        self.assertIn("duplicate_task_id", kinds)
        self.assertIn("event_content", kinds)
        result = enable(self.root, apply=True)
        self.assertTrue(result["conflicts"])
        self.assertEqual((other / ".project_hooks/active-task.json").read_bytes(), before)
        self.assertIsNone(registry_for(self.root).active(other))

    def test_crash_after_reservation_can_recover_start(self):
        registry = self.activate()
        task = registry.reserve(self.root, "20261117", track="experiment", topic="recover")
        arguments = ["start", task["task_id"], "--kind", "docs", "--scope", "recover", "--acceptance", "done", "--track", "experiment", "--topic", "recover", "--stage", "none", "--without-stage-reason", "test", "--git-commit", "never"]
        registry.prepare(task["task_id"], arguments, git(self.root, ["rev-parse", "HEAD"]).stdout.strip())
        result = self.command(self.root, "task", "recover", "--task-id", task["task_id"])
        self.assertTrue(result["recovered"])
        self.assertEqual(registry.task(task["task_id"])["status"], "active")

    def test_expired_heartbeat_marks_diagnostic_without_changing_state(self):
        registry = self.activate()
        task = self.start()
        with registry.transaction() as connection:
            connection.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE task_id=?", (task["task_id"],))
        self.assertIn("24 小时未更新", snapshot(self.root)["tasks"][0]["issues"])
        self.assertEqual(registry.task(task["task_id"])["status"], "active")
        self.command(self.root, "task", "heartbeat", "--task-id", task["task_id"])
        self.assertNotIn("24 小时未更新", snapshot(self.root)["tasks"][0]["issues"])

    def test_git_squash_invokes_installed_driver_and_preserves_prefix(self):
        self.activate()
        git(self.root, ["-c", "core.hooksPath=", "add", "."])
        git(self.root, ["-c", "core.hooksPath=", "commit", "-m", "enable"])
        task = self.start()
        path = Path(task["worktree"])
        self.command(path, "report", "--current-step", "branch event")
        git(path, ["-c", "core.hooksPath=", "add", "."])
        git(path, ["-c", "core.hooksPath=", "commit", "-m", "task events"])
        event = new_event("example", branch="main", task_id=None, payload={"main": True}, timezone="UTC")
        append_events(self.root / "maintenance/events.jsonl", self.root / ".project_hooks", [event])
        git(self.root, ["-c", "core.hooksPath=", "add", "."])
        git(self.root, ["-c", "core.hooksPath=", "commit", "-m", "main event"])
        before = (self.root / "maintenance/events.jsonl").read_bytes()
        git(self.root, ["-c", "core.hooksPath=", "merge", "--squash", task["branch"]])
        journal = self.root / "maintenance/events.jsonl"
        self.assertTrue(journal.read_bytes().startswith(before))
        events = load_events(journal)
        self.assertEqual(len(events), len({e["event_id"] for e in events}))

    def test_active_worktree_removal_is_refused_and_record_preserved(self):
        registry = self.activate()
        task = self.start()
        rejected = self.command(self.root, "worktree", "remove", task["task_id"], "--confirmed", success=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(registry.task(task["task_id"])["status"], "active")
        self.assertTrue(Path(task["worktree"]).is_dir())

    def test_resource_identity_survives_move_and_parallel_content_edit(self):
        old = "resources/theory/shared.md"
        (self.root / old).write_text("original\n", encoding="utf-8")
        event = new_event("catalog.item_upserted", branch="main", task_id=None,
                          payload={"item_id": "shared-resource", "kind": "theory", "title": "Shared", "path": old}, timezone="UTC")
        append_events(self.root / "maintenance/events.jsonl", self.root / ".project_hooks", [event])
        git(self.root, ["-c", "core.hooksPath=", "add", "."])
        git(self.root, ["-c", "core.hooksPath=", "commit", "-m", "resource baseline"])
        self.activate()
        first, second = self.start("alpha"), self.start("beta")
        left, right = Path(first["worktree"]), Path(second["worktree"])
        renamed = "resources/theory/renamed.md"
        (left / old).rename(left / renamed)
        self.command(left, "catalog", "update", "shared-resource", "--path", renamed)
        (right / old).write_text("other conclusion\n", encoding="utf-8")
        conflict = next(c for c in snapshot(self.root)["conflicts"] if c["object"] == "catalog:shared-resource")
        self.assertEqual(len(conflict["sources"]), 2)
        self.assertTrue(conflict["requires_user_decision"])

        chapter_event = new_event("stage.started", branch=first["branch"], task_id=first["task_id"],
                                  payload={"stage_id": "branch-chapter", "sequence": 1, "title": "Branch chapter",
                                           "goal": "Check evidence", "acceptance": ["Reviewed"]}, timezone="UTC")
        append_events(left / "maintenance/events.jsonl", left / ".project_hooks", [chapter_event])
        result = ProjectSearch(self.root, checkout_model(self.root, "main")).load()
        self.assertFalse(result["search_warnings"])
        resources = [item for item in result["search_index"] if item["record_id"] == "shared-resource"]
        self.assertEqual({item["detail"]["path"] for item in resources}, {old, renamed})
        shared = next(item for item in resources if item["detail"]["path"] == old)
        self.assertIn("main", shared["workspaces"])
        self.assertIn(second["branch"], shared["branches"])
        self.assertEqual(len(resources), 2)
        chapter = next(item for item in result["search_index"] if item["record_id"] == "branch-chapter")
        self.assertEqual(chapter["branches"], [first["branch"]])
        self.assertEqual(sum(item["record_id"] == first["task_id"] for item in result["search_index"]), 1)

    def test_unified_project_reuses_workflow_with_new_task_and_shared_hook(self):
        migrate_project(self.root)
        first = self.start()
        path = Path(first["worktree"])
        self.assertTrue(path.parent.samefile(self.root / ".worktrees"))
        self.assertFalse((path / "workflow-monitor.exe").exists())
        self.assertFalse(self.command(self.root, "workflow", "list")[0]["current_task"])
        self.assertEqual(self.command(self.root, "workflow", "show", first["workflow_id"])["task"]["task_id"],
                         first["task_id"])
        self.command(path, "end", "--result", "completed", "--note", "first", "--attempt-state", "negative",
                     "--evidence", "first evidence", "--conclusion", "first conclusion")
        git(path, ["add", "."])
        git(path, ["commit", "-m", "First exploration task"])
        second_id = "20261117_explicit_056"
        second = self.command(self.root, "start", second_id, "--workflow", first["workflow_id"], "--kind", "docs",
                              "--scope", "second", "--acceptance", "second", "--stage", "none",
                              "--without-stage-reason", "test")
        self.assertEqual(second["task_id"], second_id)
        self.assertEqual(second["worktree"], first["worktree"])
        self.assertEqual(second["branch"], first["branch"])
        self.assertTrue(self.command(path, "start", "--kind", "docs", "--scope", "duplicate",
                                     "--acceptance", "test", success=False).returncode)
        detail = workflow_snapshot(self.root, first["workflow_id"], first["task_id"])
        self.assertEqual({task["task_id"] for task in detail["history"]}, {first["task_id"], second_id})
        self.assertEqual(detail["task"]["note"], "first")
        self.assertFalse(detail["action_matrix"]["state"])
        sources = snapshot(self.root)["conflicts"]
        self.assertFalse(any(len({s["workflow_id"] for s in c["sources"]}) == 1 for c in sources))

    def test_migration_blocks_unfinished_then_preserves_dirty_worktree(self):
        registry = self.activate()
        started = self.start()
        old = Path(started["worktree"])
        self.assertTrue(plan_migration(self.root)["blockers"])
        self.command(old, "end", "--result", "completed", "--note", "finish", "--attempt-state", "negative",
                     "--evidence", "evidence", "--conclusion", "test")
        (old / "shared.txt").write_text("preserved", encoding="utf-8")
        (old / "untracked.txt").write_text("also preserved", encoding="utf-8")
        journal = (old / "maintenance/events.jsonl").read_bytes()
        before = verification_fingerprint(self.root)
        hook = old / ".githooks/pre-commit"
        original = hook.read_bytes()
        hook.write_bytes(original + b"\n# local custom validation\n")
        self.assertIn(str(hook), [item["path"] for item in plan_migration(self.root)["blockers"]])
        hook.write_bytes(original)
        preview = plan_migration(self.root)
        self.assertFalse(preview["blockers"])
        with mutation_lock(self.root / ".project_hooks", command="_apply-update"):
            result = migrate_project(self.root)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(old.exists())
        new = Path(result["moves"][0]["destination"])
        self.assertEqual((new / "shared.txt").read_text(encoding="utf-8"), "preserved")
        self.assertEqual((new / "untracked.txt").read_text(encoding="utf-8"), "also preserved")
        self.assertEqual((new / "maintenance/events.jsonl").read_bytes(), journal)
        self.assertEqual(Path(registry.task(started["task_id"])["path"]), new)
        self.assertEqual(verification_fingerprint(self.root), before)

    def test_closed_exploration_retains_history_and_protects_ignored_outputs(self):
        migrate_project(self.root)
        started = self.start()
        path = Path(started["worktree"])
        self.command(path, "end", "--result", "completed", "--note", "archive result", "--attempt-state", "negative",
                     "--evidence", "durable evidence", "--conclusion", "test")
        git(path, ["add", "."])
        git(path, ["commit", "-m", "Save exploration evidence"])
        output_dir = path / "build"
        output_dir.mkdir()
        output = output_dir / "experiment-result.txt"
        output.write_text("important ignored output", encoding="utf-8")
        workflow_id = started["workflow_id"]
        preview = self.command(self.root, "workflow", "close", workflow_id, "--cleanup")
        self.assertIn("build/experiment-result.txt", preview["ignored"])
        self.assertTrue(preview["blockers"])
        output.unlink()
        closed = self.command(self.root, "workflow", "close", workflow_id, "--cleanup", "--confirmed")
        self.assertTrue(closed["removed"])
        detail = workflow_snapshot(self.root, workflow_id)
        self.assertFalse(detail["workspace_available"])
        self.assertEqual(detail["task"]["note"], "archive result")
        self.assertIn("durable evidence", detail["task"]["evidence"])
        result = ProjectSearch(self.root, checkout_model(self.root, "main")).load()
        self.assertFalse(result["search_warnings"])
        record = next(item for item in result["search_index"] if item["record_id"] == started["task_id"])
        self.assertEqual(record["archive_status_code"], "archived")
        self.assertIn("durable evidence", record["detail"]["evidence"])
        self.assertIn(closed["archive_branch"], record["branches"])


if __name__ == "__main__":
    unittest.main()
