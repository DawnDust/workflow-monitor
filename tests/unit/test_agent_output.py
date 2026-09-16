from copy import deepcopy
import json
import unittest

from project_hooks.ui.cli.agent_output import brief_context, compact_result, markdown_view, verification_view
from project_hooks.ui.cli.commands import markdown_context


def context_fixture(active=True):
    scope = "Improve CLI summaries while preserving task verification and existing interfaces."
    state = dict(goal=scope, current_step="Implement summaries", judgment="Keep full output compatible",
                 breakpoint="Projection ready", blocker="none", next_steps=["Run tests"], status="working")
    receipt = dict(suite="fast", result="passed", tests=103, failures=0, fingerprint="a" * 64,
                   runner_hash="b" * 64, head="c" * 40, task_id="task-1")
    verification = dict(status="passed", profile="auto", fingerprint="a" * 64,
                        required_suites=["fast"], receipts=[receipt], accepted=[receipt], problems=[])
    stage = dict(stage_id="stage-1", title="CLI maintenance", goal="Reduce repeated context",
                 status="active", summary="Backward-compatible agent-facing output",
                 current_step="Review interfaces", next_step="Test compatibility", blocker="none", revisions=[])
    history = [dict(task_id=f"previous-{i}", occurred_at="2026-09-01", task="Previous maintenance",
                    result="Completed and validated", main_goal_change="unchanged") for i in range(3)]
    attempts = [dict(attempt_id=f"attempt-{i}", branch=f"experiment/topic-{i}", state="validated",
                     hypothesis="Compare CLI context designs", evidence=["Unit and integration validation"],
                     conclusion="Preserve protocol compatibility", progress="Completed", current_step="Done",
                     next_step="Observe use") for i in range(8)]
    return dict(branch="main", contract=dict(application_version="2.0.0", schema_version=5, core_read_order=["maintenance/CORE.md"]),
                state=state, overview_state=deepcopy(state), field_sources={},
                project_profile=dict(description="Workflow lifecycle tool", big_goal="Auditable research work"),
                active_task=dict(task_id="task-1", scope=scope, acceptance=["Full output unchanged", "Safety gates retained"],
                                 track="stable", lifecycle_phase="active", changed_paths=["cli.py"]) if active else None,
                checkpoint_status="fresh" if active else "not-applicable", verification=verification if active else None,
                finish_preflight=dict(status="ready", blockers=[], warnings=[], facts=dict(verification=verification)) if active else None,
                required_actions=[], git_state=dict(relation="synced", branch="main", upstream_ref="origin/main",
                                                     local_head="c" * 40, upstream_head="c" * 40),
                current_stage=stage, latest_stage=deepcopy(stage), stages=[deepcopy(stage)],
                attempts=attempts, active_attempts=[], recent_handoffs=history, research_attention=[])


class AgentOutputTests(unittest.TestCase):
    def test_brief_preserves_task_and_does_not_mutate_source(self):
        source = context_fixture()
        original = deepcopy(source)
        result = brief_context(source)
        self.assertEqual(result["task"]["scope"], source["active_task"]["scope"])
        self.assertEqual(result["task"]["acceptance"], source["active_task"]["acceptance"])
        self.assertEqual(result["gate"]["required_suites"], source["verification"]["required_suites"])
        self.assertEqual(source, original)

    def test_idle_does_not_present_old_checkpoint_as_active(self):
        result = brief_context(context_fixture(False))
        self.assertIsNone(result["task"])
        self.assertEqual(result["last_completed"]["task_id"], "previous-0")
        self.assertNotIn("Implement summaries", markdown_view(result, view="brief"))

    def test_blockers_warnings_pause_and_long_acceptance_are_not_truncated(self):
        source = context_fixture()
        source["active_task"]["track"] = "experiment"
        source["active_task"]["acceptance"] = ["详细验收" * 400]
        source["current_stage"].update(status="paused", pause=dict(kind="interruption", note="等待原始数据"))
        blockers = [dict(code=f"BLOCK-{i}", message=f"Failure {i}", next_action=f"repair-{i}") for i in range(12)]
        source["finish_preflight"].update(status="blocked", blockers=blockers, warnings=[dict(code="WARN", message="Review evidence")])
        source["verification"].update(status="blocked", problems=[dict(suite="fast", reason="stale", command="python scripts/run_tests.py fast")])
        source["required_actions"] = [item["next_action"] for item in blockers]
        source["stage_freshness_warning"] = dict(message="篇章需要审阅")
        brief = brief_context(source)
        gate = verification_view(source)
        self.assertEqual(gate["blockers"], blockers)
        self.assertEqual(gate["next_actions"], source["required_actions"])
        self.assertEqual(gate["problems"], source["verification"]["problems"])
        rendered = markdown_view(brief, view="brief")
        for required in ["详细验收" * 400, "等待原始数据", "Review evidence", "篇章需要审阅", "repair-11"]:
            self.assertIn(required, rendered)

    def test_prior_task_progress_not_attributed_to_new_task(self):
        source = context_fixture()
        source["field_sources"] = {"current_step": {"task_id": "previous-0"}}
        self.assertNotIn("current_step", brief_context(source)["task"])

    def test_compact_receipt_preserves_warning_and_result(self):
        source = dict(task_id="task", result="completed", changed_paths=["a", "b"],
                      verification=context_fixture()["verification"]["receipts"],
                      warnings=["must review"], next_actions=["inspect"], evidence="long audit evidence")
        result = compact_result(source)
        self.assertEqual(result["changed_count"], 2)
        self.assertEqual(result["warnings"], source["warnings"])
        self.assertEqual(result["verification"][0]["tests"], 103)
        self.assertNotIn("evidence", result)

    def test_representative_context_size_budget(self):
        for active in (False, True):
            with self.subTest(active=active):
                source = context_fixture(active)
                brief = brief_context(source)
                self.assertLessEqual(len(markdown_view(brief, view="brief")), len(markdown_context(source)) * .4)
                self.assertLessEqual(len(json.dumps(brief, ensure_ascii=False)), len(json.dumps(source, ensure_ascii=False)) * .2)
