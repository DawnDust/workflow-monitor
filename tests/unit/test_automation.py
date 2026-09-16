import tempfile
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from project_hooks.core.stage_versions import stage_versions, merge_versions, conflicting_stages
from project_hooks.infrastructure.system.automatic_verification import run_missing
from project_hooks.core.lifecycle import finish_preflight, lifecycle_step


class AutomationTests(unittest.TestCase):
    def test_json_stdin_uses_utf8_even_with_legacy_windows_locale(self):
        from project_hooks.ui.cli.structured_input import expand
        text = json.dumps({"scope": "科研路径与验收"}, ensure_ascii=False).encode("utf-8")
        stream = io.TextIOWrapper(io.BytesIO(text), encoding="gbk", errors="surrogateescape")
        with patch("sys.stdin", stream):
            args = expand(["start", "--input-json", "-"])
        self.assertEqual(args, ["start", "--scope", "科研路径与验收"])

    def test_pending_report_is_visible_and_blocks_finish(self):
        gate = finish_preflight(dict(recovery_required=True, checkpoint_status="fresh"))
        self.assertIn("task recover", gate["required_actions"])
        state = dict(active_task={"task_id": "x"}, sidecar={"phase": "active", "record": {"pending_report": [{"event_id": "x"}]}})
        self.assertTrue(lifecycle_step(state)["needs_recovery"])

    def test_branch_versions_preserve_divergence_and_deduplicate_ancestry(self):
        base = [{"event_id": "a", "event_type": "stage.started", "payload": {"stage_id": "s", "goal": "g"}}]
        left = base + [{"event_id": "b", "event_type": "stage.revised", "payload": {"stage_id": "s", "summary": "left"}}]
        right = base + [{"event_id": "c", "event_type": "stage.revised", "payload": {"stage_id": "s", "summary": "right"}}]
        values = merge_versions(stage_versions(base, "main") + stage_versions(left, "left") + stage_versions(left, "origin/left"))
        self.assertEqual(len(values), 2)
        self.assertEqual(conflicting_stages(values), [])
        self.assertEqual(conflicting_stages(values + stage_versions(right, "right")), ["s"])
        self.assertEqual(values[1]["summary"], "left")

    def test_missing_or_escaping_executor_rejected_before_execution(self):
        verification = {"problems": [{"suite": "fast"}], "profile": "auto"}
        with tempfile.TemporaryDirectory() as directory, patch("subprocess.Popen") as process:
            with self.assertRaisesRegex(RuntimeError, "VERIFICATION_CONFIGURATION"):
                run_missing(Path(directory), "task", {}, verification)
            with self.assertRaisesRegex(RuntimeError, "工作目录"):
                run_missing(Path(directory), "task", {"verification_commands": {
                    "fast": {"argv": ["python", "tests.py"], "cwd": ".."}}}, verification)
            process.assert_not_called()
