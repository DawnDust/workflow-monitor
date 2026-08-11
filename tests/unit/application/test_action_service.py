from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from project_hooks.application.action_service import (
    AI_FORM_SCHEMA,
    ActionProtocolError,
    ActionRequest,
    WorkflowActionService,
    ai_form_template,
    lifecycle_step,
    parse_ai_form,
    state_token,
)
from project_hooks.composition import build_action_service
from project_hooks.core.lifecycle import finish_preflight
from project_hooks.infrastructure.system.finish_preflight import assemble_finish_preflight


def action_service(project_root: Path, *, state_provider, executor) -> WorkflowActionService:
    return build_action_service(
        project_root, state_provider=state_provider, executor=executor,
    )


def stable_state(**updates):
    value = {
        "application_version": "1.5.0",
        "project_version": "1.5.0",
        "installed": True,
        "frozen": True,
        "branch": "main",
        "classification": {"kind": "stable", "track": "stable", "topic": None},
        "active_task": None,
        "sidecar": None,
        "writer_lock": None,
        "changed_paths": [],
        "dirty_paths": [],
        "worktree_signature": "clean",
        "journal_hash": "journal",
        "git": {"available": True, "relation": "synced", "head": "abc"},
        "attempt": None,
        "health_errors": [],
    }
    value.update(updates)
    return value


class WorkflowActionTests(unittest.TestCase):
    def test_slow_snapshot_cache_starts_after_provider_finishes(self) -> None:
        calls = []

        def provider():
            calls.append(time.monotonic())
            time.sleep(0.3)
            return stable_state()

        with tempfile.TemporaryDirectory() as directory:
            service = action_service(
                Path(directory), state_provider=provider, executor=lambda *_args: {},
            )
            service.snapshot(force=True)
            service.snapshot()
            self.assertEqual(len(calls), 1)

    def test_availability_matrix_uses_one_state_read_and_reports_readonly_statuses(self) -> None:
        calls = []

        def provider():
            calls.append(True)
            return stable_state()

        with tempfile.TemporaryDirectory() as directory:
            service = action_service(
                Path(directory), state_provider=provider, executor=lambda *_args: {},
            )
            matrix = service.availability_matrix(force=True)
            self.assertEqual(len(calls), 1)
            statuses = {item.action_id: item.status for item in matrix.actions}
            self.assertEqual(statuses["task.start"], "needs_input")
            self.assertEqual(statuses["state.update"], "blocked")
            self.assertNotIn("workbench.external.add", statuses)
            self.assertEqual(len({item.availability.state_token for item in matrix.actions}), 1)

    def test_ai_template_only_allows_natural_text_fields(self) -> None:
        template = json.loads(ai_form_template("task.finish"))
        self.assertEqual(template["schema"], AI_FORM_SCHEMA)
        self.assertEqual(template["action"], "task.finish")
        self.assertIn("note", template["fields"])
        self.assertIn("evidence", template["fields"])
        self.assertNotIn("result", template["fields"])
        self.assertNotIn("writer_stopped", template["fields"])

        parsed = parse_ai_form(json.dumps({
            "schema": AI_FORM_SCHEMA,
            "action": "task.finish",
            "fields": {"note": "完成验证", "evidence": ["96 tests"]},
        }), "task.finish")
        self.assertEqual(parsed["note"], "完成验证")
        with self.assertRaisesRegex(ActionProtocolError, "不允许填写字段"):
            parse_ai_form(json.dumps({
                "schema": AI_FORM_SCHEMA,
                "action": "task.finish",
                "fields": {"result": "completed"},
            }), "task.finish")

    def test_ai_protocol_rejects_wrong_action_unknown_and_invalid_json(self) -> None:
        with self.assertRaisesRegex(ActionProtocolError, "动作与当前表单"):
            parse_ai_form(json.dumps({
                "schema": AI_FORM_SCHEMA, "action": "state.update", "fields": {},
            }), "task.finish")
        with self.assertRaisesRegex(ActionProtocolError, "合法 JSON"):
            parse_ai_form("not-json", "task.finish")

    def test_lifecycle_step_maps_sidecar_and_progress(self) -> None:
        self.assertEqual(lifecycle_step({})["key"], "idle")
        self.assertEqual(lifecycle_step({"sidecar": {"phase": "starting"}})["key"], "starting")
        self.assertEqual(lifecycle_step({"sidecar": {"phase": "finishing"}})["key"], "finishing")
        active = {"task_id": "task", "state_updated": False}
        self.assertEqual(lifecycle_step({"active_task": active, "changed_paths": ["a"]})["key"], "checkpoint-required")
        active["state_updated"] = True
        self.assertEqual(lifecycle_step({
            "active_task": active, "checkpoint_status": "fresh",
            "finish_preflight": {"status": "ready", "checkpoint_status": "fresh"},
            "worktree_quiet": True,
        })["key"], "ready")

    def test_finish_preflight_unifies_checkpoint_verification_and_inference(self) -> None:
        blocked = finish_preflight({
            "checkpoint_status": "stale",
            "verification": {"problems": [{
                "suite": "full", "reason": "missing", "command": "python scripts/run_tests.py full",
            }]},
            "linked_stage_id": "stage",
            "decisions_added": 1,
            "project_updated": True,
        })
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(
            {item["code"] for item in blocked["blockers"]},
            {"CHECKPOINT_STALE", "VERIFICATION_MISSING", "STAGE_REVIEW_REQUIRED"},
        )
        self.assertEqual(blocked["inferred"]["route"], "changed")
        self.assertEqual(blocked["inferred"]["main_goal"], "changed")

        ready = finish_preflight({
            "checkpoint_status": "fresh", "verification": {"problems": []},
            "linked_stage_id": "stage", "stage_changed": True,
        })
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["inferred"]["stage_review"], "updated")

    def test_finish_preflight_assembler_owns_checkpoint_and_event_facts(self) -> None:
        record = {
            "task_id": "task-1",
            "declaration": {"verification_profile": "auto"},
        }
        base_events = [
            {"event_type": "decision.recorded", "payload": {}},
            {"event_type": "project.profile_updated", "payload": {}},
            {"event_type": "stage.updated", "payload": {"stage_id": "stage-1"}},
        ]
        cases = {
            "missing": base_events,
            "legacy-unknown": base_events + [{"event_type": "task.checkpointed", "payload": {}}],
            "fresh": base_events + [{"event_type": "task.checkpointed", "payload": {"workspace_fingerprint": "current"}}],
            "stale": base_events + [{"event_type": "task.checkpointed", "payload": {"workspace_fingerprint": "old"}}],
        }
        with patch(
            "project_hooks.infrastructure.system.finish_preflight.work_content_fingerprint",
            return_value="current",
        ), patch(
            "project_hooks.infrastructure.system.finish_preflight.verify_receipts",
            return_value={"problems": []},
        ):
            for expected, events in cases.items():
                with self.subTest(expected=expected):
                    result = assemble_finish_preflight(
                        Path("."), record, events,
                        linked_stage_id="stage-1", changed_paths=["project_hooks/x.py"],
                    )
                    self.assertEqual(result["checkpoint_status"], expected)
                    self.assertEqual(result["facts"]["decisions_added"], 1)
                    self.assertTrue(result["facts"]["project_updated"])
                    self.assertTrue(result["facts"]["stage_changed"])
                    self.assertEqual(result["inferred"]["route"], "changed")
                    self.assertEqual(result["inferred"]["main_goal"], "changed")

    def test_availability_explains_active_branch_version_and_update_blocks(self) -> None:
        state = stable_state()
        with tempfile.TemporaryDirectory() as directory:
            service = action_service(
                Path(directory), state_provider=lambda: dict(state),
                executor=lambda *_args: {"status": "ok"},
            )
            self.assertTrue(service.availability("task.start").enabled)
            state["active_task"] = {
                "task_id": "task-1", "branch": "main", "state_updated": False, "decisions_added": 0,
            }
            state["sidecar"] = {"phase": "active"}
            blocked = service.availability("task.start", force=True)
            self.assertIn("ACTIVE_TASK_EXISTS", {item.code for item in blocked.blockers})
            state["project_version"] = "1.4.1"
            mismatch = service.availability("state.update", force=True)
            self.assertIn("VERSION_MISMATCH", {item.code for item in mismatch.blockers})

    def test_availability_reports_writer_install_recovery_and_branch_rules(self) -> None:
        state = stable_state(
            installed=False,
            writer_lock={"status": "active", "pid": 999999, "command": "state.update"},
            classification={"kind": "exploration", "track": "research", "topic": "x"},
            branch="research/x",
            active_task={
                "task_id": "task", "branch": "main", "state_updated": False, "decisions_added": 0,
            },
            sidecar={"phase": "finishing"},
        )
        with tempfile.TemporaryDirectory() as directory:
            service = action_service(
                Path(directory), state_provider=lambda: dict(state), executor=lambda *_args: {},
            )
            blocked = service.availability("project.update", force=True)
            codes = {item.code for item in blocked.blockers}
            self.assertTrue({
                "WRITER_BUSY", "WORKFLOW_NOT_INSTALLED", "STABLE_BRANCH_REQUIRED",
                "ACTIVE_BRANCH_CHANGED", "RECOVERY_REQUIRED",
            }.issubset(codes))
            recover = service.availability("task.recover", force=True)
            self.assertNotIn("RECOVERY_REQUIRED", {item.code for item in recover.blockers})

    def test_finish_preflight_reports_health_decision_confirmation_and_activity(self) -> None:
        state = stable_state(
            active_task={
                "task_id": "task", "branch": "main", "state_updated": True, "decisions_added": 0,
            },
            sidecar={"phase": "active"},
            health_errors=["database mismatch"],
        )
        fields = {
            "result": "completed", "route": "changed", "methods_action": "updated",
            "main_goal": "unchanged", "note": "done", "writer_stopped": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            service = action_service(
                Path(directory), state_provider=lambda: dict(state), executor=lambda *_args: {},
            )
            blocked = service.availability("task.finish", fields, force=True)
            codes = {item.code for item in blocked.blockers}
            self.assertTrue({
                "HEALTH_CHECK_FAILED", "DECISION_REQUIRED",
                "WRITER_CONFIRMATION_REQUIRED", "WRITE_ACTIVITY_RECENT",
            }.issubset(codes))

    def test_update_preflight_explains_main_dirty_sync_and_frozen_requirements(self) -> None:
        state = stable_state(
            frozen=False, branch="research/x",
            classification={"kind": "exploration", "track": "research", "topic": "x"},
            dirty_paths=["paper.md"],
            git={"available": True, "relation": "ahead", "head": "abc"},
        )
        with tempfile.TemporaryDirectory() as directory:
            service = action_service(
                Path(directory), state_provider=lambda: dict(state), executor=lambda *_args: {},
            )
            blocked = service.availability("update.apply", force=True)
            codes = {item.code for item in blocked.blockers}
            self.assertTrue({
                "FROZEN_EXE_REQUIRED", "STABLE_BRANCH_REQUIRED", "UPDATE_MAIN_REQUIRED",
                "DIRTY_WORKTREE", "UPSTREAM_NOT_SYNCED",
            }.issubset(codes))
            self.assertIn("UPDATE_NOT_CHECKED", {item.code for item in blocked.warnings})

    def test_field_validation_normalizes_lists_booleans_choices_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = action_service(
                Path(directory), state_provider=lambda: stable_state(), executor=lambda *_args: {},
            )
            fields = service.validate_fields("task.start", {
                "kind": "code", "scope": " goal ", "acceptance": "one\n\ntwo",
                "track": "stable", "task_size": "small", "git_commit": "never",
            })
            self.assertEqual(fields["scope"], "goal")
            self.assertEqual(fields["acceptance"], ["one", "two"])
            with self.assertRaisesRegex(ActionProtocolError, "不能为空"):
                service.validate_fields("task.start", {"kind": "code", "scope": "", "acceptance": []})
            with self.assertRaisesRegex(ActionProtocolError, "允许的选项"):
                service.validate_fields("task.start", {
                    "kind": "invalid", "scope": "x", "acceptance": ["y"],
                })
            with self.assertRaisesRegex(ActionProtocolError, "最多"):
                service.validate_fields("project.update", {"description": "x" * 501})

    def test_external_workbench_actions_require_active_stable_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = action_service(
                Path(directory), state_provider=lambda: stable_state(), executor=lambda *_args: {},
            )
            blocked = service.availability("workbench.external.add", force=True)
            self.assertIn("ACTIVE_TASK_REQUIRED", {item.code for item in blocked.blockers})
            fields = service.validate_fields("workbench.external.add", {
                "tool_id": "obsidian", "name": "Obsidian", "kind": "notes",
                "purpose": "管理笔记", "usage_hint": "整理研究笔记", "reference": "Obsidian",
            })
            self.assertEqual(fields["kind"], "notes")
            state = stable_state(active_task={"task_id": "task", "branch": "main"})
            service = action_service(
                Path(directory), state_provider=lambda: state, executor=lambda *_args: {},
            )
            self.assertTrue(service.availability("workbench.external.add", fields, force=True).enabled)

    def test_blocked_request_does_not_execute_or_write_diagnostics(self) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = action_service(
                root, state_provider=lambda: stable_state(active_task={
                    "task_id": "active", "branch": "main", "state_updated": False, "decisions_added": 0,
                }), executor=lambda *args: calls.append(args),
            )
            availability = service.availability("task.start", force=True)
            result = service.execute(ActionRequest("task.start", {
                "kind": "code", "scope": "x", "acceptance": ["y"], "track": "stable",
            }, availability.state_token))
            self.assertEqual(result.status, "blocked")
            self.assertFalse(calls)
            self.assertFalse((root / ".project_hooks/diagnostics/events.jsonl").exists())

    def test_state_token_rejects_stale_form_and_success_uses_writer_lock(self) -> None:
        state = stable_state()
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = action_service(
                root, state_provider=lambda: dict(state),
                executor=lambda action, fields, _progress: calls.append((action, fields)) or {"changed": ["x"]},
            )
            token = service.snapshot(force=True)["state_token"]
            state["git"] = {**state["git"], "head": "def"}
            stale = service.execute(ActionRequest(
                "health.check", {}, token,
            ))
            self.assertEqual(stale.code, "ACTION_STATE_CHANGED")
            self.assertFalse(calls)
            fresh = service.availability("health.check", force=True)
            result = service.execute(ActionRequest("health.check", {}, fresh.state_token))
            self.assertEqual(result.status, "success")
            self.assertEqual(result.changed, ["x"])

    def test_high_risk_requires_confirmation_and_failures_get_incident(self) -> None:
        state = stable_state()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = action_service(
                root, state_provider=lambda: dict(state),
                executor=lambda *_args: (_ for _ in ()).throw(RuntimeError("unexpected failure")),
            )
            availability = service.availability("db.rebuild", force=True)
            unconfirmed = service.execute(ActionRequest("db.rebuild", {}, availability.state_token, False))
            self.assertEqual(unconfirmed.code, "CONFIRMATION_REQUIRED")
            failed = service.execute(ActionRequest("db.rebuild", {}, availability.state_token, True))
            self.assertEqual(failed.status, "failed")
            self.assertTrue(failed.incident_id)
            self.assertTrue((root / ".project_hooks/diagnostics/events.jsonl").is_file())

    def test_expected_action_validation_failure_has_no_incident(self) -> None:
        state = stable_state()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = action_service(
                root, state_provider=lambda: dict(state),
                executor=lambda *_args: (_ for _ in ()).throw(ValueError("bad input")),
            )
            availability = service.availability("health.check", force=True)
            result = service.execute(ActionRequest("health.check", {}, availability.state_token))
            self.assertEqual(result.status, "failed")
            self.assertIsNone(result.incident_id)
            self.assertFalse((root / ".project_hooks/diagnostics/events.jsonl").exists())

    def test_worktree_requires_three_second_quiet_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = action_service(
                Path(directory), state_provider=lambda: stable_state(),
                executor=lambda *_args: {},
            )
            self.assertFalse(service.snapshot(force=True)["worktree_quiet"])
            service._worktree_changed_at -= 4
            self.assertTrue(service.snapshot(force=True)["worktree_quiet"])

    def test_state_token_uses_task_git_and_worktree_identity(self) -> None:
        first = stable_state()
        second = stable_state(worktree_signature="changed")
        self.assertNotEqual(state_token(first), state_token(second))


class WorkflowActionIntegrationTests(unittest.TestCase):
    def test_dashboard_service_runs_real_stable_lifecycle_without_cli_subprocess(self) -> None:
        from project_hooks import __version__, cli
        from project_hooks.infrastructure.system.project_manager import initialize_project

        original_root = cli.ROOT
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Action Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "action@example.invalid"], cwd=root, check=True)
            initialize_project(root, __version__)
            try:
                cli.set_project_root(root)
                service = cli.dashboard_action_service()
                start = service.availability("task.start", force=True)
                started = service.execute(ActionRequest("task.start", {
                    "kind": "analysis",
                    "scope": "dashboard lifecycle",
                    "acceptance": ["events recorded"],
                    "task_size": "small",
                    "git_commit": "auto",
                    "track": "stable",
                }, start.state_token))
                self.assertEqual(started.status, "success")
                progress = service.availability("state.update", force=True)
                updated = service.execute(ActionRequest("state.update", {
                    "status": "ready", "judgment": "validated", "breakpoint": "done",
                    "next": ["finish"], "blocker": "none",
                }, progress.state_token))
                self.assertEqual(updated.status, "success")

                finish_fields = {
                    "result": "completed", "route": "unchanged",
                    "methods_action": "reviewed-no-change", "main_goal": "unchanged",
                    "note": "dashboard lifecycle complete", "evidence": ["integration test"],
                    "writer_stopped": True,
                }
                service.snapshot(force=True)
                service._worktree_changed_at -= 4
                finish = service.availability("task.finish", finish_fields, force=True)
                self.assertTrue(finish.enabled, [item.message for item in finish.blockers])
                finished = service.execute(ActionRequest(
                    "task.finish", finish_fields, finish.state_token, True,
                ))
                self.assertEqual(finished.status, "success", finished.summary)
                self.assertEqual(finished.data["git"]["status"], "not-requested")
                events = [
                    json.loads(line) for line in (root / "maintenance/events.jsonl").read_text(encoding="utf-8").splitlines()
                ]
                types = [event["event_type"] for event in events]
                self.assertIn("task.started", types)
                self.assertIn("task.checkpointed", types)
                self.assertIn("task.finished", types)
                self.assertFalse(service.snapshot(force=True)["active_task"])
                self.assertEqual(service.snapshot()["lifecycle_step"]["key"], "completed")
            finally:
                cli.set_project_root(original_root)


if __name__ == "__main__":
    unittest.main()
