from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from argparse import Namespace

from tests.integration.persistence import test_workflow as support
from project_hooks.infrastructure.persistence.store import load_events


class ReportingTests(unittest.TestCase):
    setUp = support.ProjectHooksSqliteTests.setUp
    tearDown = support.ProjectHooksSqliteTests.tearDown
    hooks = support.ProjectHooksSqliteTests.hooks
    def git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=self.root, encoding="utf-8", errors="replace",
                              capture_output=True, check=check)
    start = support.ProjectHooksSqliteTests.start

    @classmethod
    def setUpClass(cls):
        support.ProjectHooksSqliteTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        support.ProjectHooksSqliteTests.tearDownClass.__func__(cls)

    def submit(self, command, data, check=True):
        return subprocess.run([sys.executable, "-m", "project_hooks", command, "--input-json", "-", "--compact"],
                              cwd=self.root, input=json.dumps(data, ensure_ascii=False), encoding="utf-8", capture_output=True, check=check)

    def events(self):
        return load_events(self.root / "maintenance/events.jsonl")

    def begin(self, **extra):
        return self.submit("start", dict(kind="docs", scope="research", acceptance=["inspect evidence"],
                                         git_commit="never", **extra))

    def test_short_task_start_end_only(self):
        self.hooks("install")
        self.begin()
        self.submit("end", dict(result="completed", note="done"))
        self.assertEqual([e["event_type"] for e in self.events()],
                         ["task.started", "task.checkpointed", "task.finished"])

    def test_combined_stage_report_reference_and_idempotence(self):
        self.hooks("install")
        self.git("add", ".")
        self.git("commit", "-m", "installed", check=False)
        self.begin(track="experiment", topic="one", stage="new",
                   new_stage=dict(title="One", goal="Investigate", acceptance=["Evidence"]))
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "experiment/one")
        data = dict(current_step="measured", hypothesis="H", conclusion="not supported",
                    evidence=["measurement.csv"], next=["replicate"])
        self.submit("report", data)
        count = len(self.events())
        result = json.loads(self.submit("report", data).stdout)
        self.assertEqual(result["result"], "unchanged")
        self.assertEqual(len(self.events()), count)
        checkpoint = self.events()[-1]["payload"]
        self.assertIn("source_event_id", checkpoint)
        self.assertNotIn("current_step", checkpoint)
        context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertEqual(context["state"]["current_step"], "measured")
        self.submit("end", dict(result="completed", note="measured", attempt_state="negative"))
        self.assertEqual(self.events()[-1]["event_type"], "task.finished")

    def test_invalid_new_stage_is_side_effect_free(self):
        self.hooks("install")
        result = self.submit("start", dict(kind="docs", scope="test", acceptance=["test"],
                                          track="experiment", topic="bad", stage="new",
                                          new_stage={"title": "missing fields"}), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events(), [])
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "main")

    def test_json_conflict_fails_before_start(self):
        result = subprocess.run([sys.executable, "-m", "project_hooks", "start", "--scope", "other", "--input-json", "-"],
                                cwd=self.root, input=json.dumps(dict(scope="test")), encoding="utf-8", capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不能重复", result.stderr)

    def test_explicit_clear_and_omitted_fields(self):
        self.hooks("install")
        self.begin()
        self.submit("report", dict(current_step="working", blocker="waiting"))
        self.submit("report", dict(judgment="continue"))
        context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertEqual(context["state"]["blocker"], "waiting")
        self.submit("report", dict(clear=["blocker"]))
        context = json.loads(self.hooks("context", "--format", "json").stdout)
        self.assertEqual(context["state"]["blocker"], "")

    def configure_runner(self):
        runner = self.root / "scripts/run_tests.py"
        runner.parent.mkdir(exist_ok=True)
        runner.write_text(
            "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path.cwd()))\n"
            "from project_hooks.infrastructure.system.verification import write_test_receipt, iso_now\n"
            "root=Path.cwd()\nsuite=sys.argv[1]\n"
            "with (root/'.project_hooks/calls').open('a') as f: f.write(suite+'\\n')\n"
            "failed=(root/'.project_hooks/fail').exists()\n"
            "write_test_receipt(root,suite=suite,result='failed' if failed else 'passed',tests=1,failures=int(failed),"
            "coverage=None,started_at=iso_now(),finished_at=iso_now())\n"
            "sys.exit(int(failed))\n", encoding="utf-8")
        config_path = self.root / ".codex/project-maintenance-workflow.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["verification_commands"] = {suite: dict(argv=[sys.executable, "scripts/run_tests.py", suite],
                                                       timeout_seconds=30) for suite in ("fast", "full")}
        config_path.write_text(json.dumps(config), encoding="utf-8")

    def test_automatic_tests_failure_retry_and_reuse(self):
        self.hooks("install")
        self.configure_runner()
        self.begin()
        (self.root / "project_hooks/new_input.py").write_text("value=1\n", encoding="utf-8")
        self.submit("report", dict(current_step="implemented", verify="fast"))
        (self.root / ".project_hooks/fail").touch()
        first = self.submit("end", dict(result="completed", note="done"), check=False)
        self.assertNotEqual(first.returncode, 0)
        checkpoint_count = len([e for e in self.events() if e["event_type"] == "task.checkpointed"])
        (self.root / ".project_hooks/fail").unlink()
        self.submit("end", dict(result="completed", note="done"))
        self.assertEqual(len([e for e in self.events() if e["event_type"] == "task.checkpointed"]), checkpoint_count)
        calls = (self.root / ".project_hooks/calls").read_text().splitlines()
        self.assertEqual(calls, ["fast", "full", "full"])

    def test_check_only_does_not_write_or_run_tests(self):
        self.hooks("install")
        self.configure_runner()
        self.begin()
        before = self.events()
        self.submit("end", dict(result="completed", note="done", check_only=True))
        self.assertEqual(self.events(), before)
        self.assertFalse((self.root / ".project_hooks/calls").exists())

    def test_test_runner_input_change_invalidates_its_receipt(self):
        self.hooks("install")
        self.configure_runner()
        runner = self.root / "scripts/run_tests.py"
        code = runner.read_text(encoding="utf-8")
        code = code.replace("failed=(root", "(root/'project_hooks/changed.py').write_text('changed=1')\nfailed=(root")
        runner.write_text(code, encoding="utf-8")
        self.begin()
        (self.root / "project_hooks/new_input.py").write_text("value=1\n", encoding="utf-8")
        failed = self.submit("end", dict(result="completed", note="done"), check=False)
        self.assertNotEqual(failed.returncode, 0)
        receipt = next((self.root / ".project_hooks/test-receipts").glob("*/fast.json"))
        data = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(data["result"], "failed")
        self.assertTrue(data["input_changed"])
        self.assertFalse(any(event["event_type"] == "task.finished" for event in self.events()))

    def test_pending_report_recovers_same_event_ids(self):
        from project_hooks.ui.cli import commands as cli
        from project_hooks.ui.cli.reporting import report
        from project_hooks.infrastructure.persistence.active_task import load_active_state
        self.hooks("install")
        self.begin()
        original = cli.ROOT
        try:
            cli.set_project_root(self.root)
            with patch.object(cli, "persist", side_effect=RuntimeError("interrupted")):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    report(cli, Namespace(current_step="recover this"))
            pending = load_active_state(self.root / ".project_hooks")["record"]["pending_report"]
            self.hooks("task", "recover")
            self.hooks("task", "recover")
            checkpoints = [event for event in self.events() if event["event_type"] == "task.checkpointed"]
            self.assertEqual([e["event_id"] for e in checkpoints], [e["event_id"] for e in pending])
        finally:
            cli.set_project_root(original)

    def test_combined_start_recovers_stage_and_attempt(self):
        from project_hooks.ui.cli import commands as cli
        from project_hooks.ui.cli.parser import build_parser
        self.hooks("install")
        self.git("-c", "core.hooksPath=.git/no-hooks", "commit", "--allow-empty", "-m", "installed")
        args = build_parser().parse_args([
            "start", "20260915_recover_001", "--kind", "docs", "--scope", "recover", "--acceptance", "safe",
            "--track", "experiment", "--topic", "recover", "--stage", "new", "--new-stage",
            json.dumps(dict(title="Recover", goal="Recover", acceptance=["safe"])), "--git-commit", "never"])
        original = cli.ROOT
        try:
            cli.set_project_root(self.root)
            with patch.object(cli, "persist", side_effect=RuntimeError("interrupted")):
                with self.assertRaises(cli.WorkflowError):
                    cli.start_task(args)
            self.hooks("task", "recover")
            self.hooks("task", "recover")
            types = [event["event_type"] for event in self.events()]
            for kind in ("task.started", "stage.started", "attempt.started"):
                self.assertEqual(types.count(kind), 1)
        finally:
            cli.set_project_root(original)
