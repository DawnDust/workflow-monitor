import json
import subprocess
import unittest
from tests.integration.persistence import test_reporting as support


class ReviewIntegrationTests(unittest.TestCase):
    setUp = support.ReportingTests.setUp
    tearDown = support.ReportingTests.tearDown
    hooks = support.ReportingTests.hooks
    git = support.ReportingTests.git
    submit = support.ReportingTests.submit
    events = support.ReportingTests.events
    begin = support.ReportingTests.begin
    configure_runner = support.ReportingTests.configure_runner

    @classmethod
    def setUpClass(cls):
        support.ReportingTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        support.ReportingTests.tearDownClass.__func__(cls)
    def create_stage(self):
        self.hooks("install")
        self.begin(track="stable")
        self.hooks("stage", "start", "chapter", "--title", "Chapter", "--goal", "Question", "--acceptance", "Evidence")

    def test_initialize_directory_without_existing_configuration(self):
        fresh = self.root.parent / "fresh"
        fresh.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=fresh, check=True, capture_output=True)
        result = self.hooks("init", str(fresh), check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((fresh / ".codex/project-maintenance-workflow.json").is_file())

    def show_review(self):
        return json.loads(self.hooks("review", "show", "stage:chapter").stdout)

    def test_review_only_batch_and_idempotence(self):
        self.create_stage()
        item = self.show_review()
        data = dict(reviews=[dict(token=item["token"], result="reviewed-no-change")])
        self.submit("report", data)
        coverage = [e for e in self.events() if e["event_type"] == "review.coverage_completed"]
        self.assertEqual(len(coverage), 1)
        reviewed_at = json.loads(self.hooks("context", "--format", "json").stdout)["review_summary"]["last_overall_reviewed_at"]
        self.assertEqual(reviewed_at, coverage[0]["occurred_at"])
        count = len(self.events())
        self.submit("report", data)
        self.assertEqual(len(self.events()), count)
        self.assertFalse(self.show_review()["pending"])
        self.hooks("stage", "update", "chapter", "--summary", "New information")
        summary = json.loads(self.hooks("context", "--format", "json").stdout)["review_summary"]
        self.assertEqual(summary["last_overall_reviewed_at"], reviewed_at)
        self.assertEqual(summary["status"], "review_suggested")

    def test_new_change_between_read_and_report_requires_refresh(self):
        self.create_stage()
        token = self.show_review()["token"]
        self.hooks("stage", "update", "chapter", "--summary", "New information")
        result = self.submit("report", dict(reviews=[dict(token=token, result="reviewed-no-change")]), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.show_review()["pending"])

    def test_deferred_does_not_count_as_review(self):
        self.create_stage()
        token = self.show_review()["token"]
        self.submit("report", dict(reviews=[dict(token=token, result="deferred", reason="Later")]))
        item = self.show_review()
        self.assertTrue(item["deferred"])
        self.assertIsNone(item["last_reviewed_at"])

    def test_check_only_without_completion_fields(self):
        self.hooks("install")
        self.begin()
        before = self.events()
        self.hooks("end", "--check-only", "--compact")
        self.assertEqual(before, self.events())
        failed = self.hooks("end", "--compact", check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(json.loads(failed.stderr)["saved"], "not-started")

    def test_invalid_review_is_atomic_with_progress(self):
        self.create_stage()
        before = self.events()
        failed = self.submit("report", dict(current_step="new", reviews=[dict(token="bad", result="deferred")]), check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(before, self.events())
        self.assertEqual(json.loads(failed.stderr)["saved"], "no-new-events")

    def test_compact_failure_reports_saved_checkpoint_and_logs(self):
        self.hooks("install")
        self.configure_runner()
        self.begin()
        (self.root / "project_hooks/new.py").write_text("x=1", encoding="utf-8")
        (self.root / ".project_hooks/fail").touch()
        failed = self.submit("end", dict(result="completed", note="implemented"), check=False)
        # Test progress is printed before the final JSON receipt on stderr.
        receipt = json.loads(failed.stderr[failed.stderr.index("{\n"):])
        self.assertEqual(receipt["saved"], "yes")
        self.assertTrue(receipt["last_checkpoint"])
        self.assertTrue(receipt["logs"])

    def test_deferred_repeat_does_not_add_events(self):
        self.create_stage()
        data = dict(reviews=[dict(token=self.show_review()["token"], result="deferred", reason="Later")])
        self.submit("report", data)
        before = self.events()
        self.submit("report", data)
        self.assertEqual(before, self.events())

    def test_existing_stage_review_end_is_recorded_once_and_fresh(self):
        self.hooks("install")
        self.git("add", ".")
        self.git("commit", "-m", "installed", check=False)
        self.begin(track="experiment", topic="chapter", stage="new",
                   new_stage=dict(title="Chapter", goal="Question", acceptance=["Evidence"]))
        self.submit("end", dict(result="completed", note="done", attempt_state="negative",
                                conclusion="not supported", evidence=["measurement"], stage_review="updated"))
        self.assertFalse(self.show_review()["pending"])
        self.assertEqual(len([e for e in self.events() if e["event_type"] == "review.recorded"]), 1)
        self.assertEqual(len([e for e in self.events() if e["event_type"] == "review.coverage_completed"]), 1)
        self.assertEqual(self.events()[-1]["event_type"], "task.finished")

    def test_resource_changes_sparks_and_original_gate(self):
        self.hooks("install")
        self.begin()
        (self.root / "resources/sparks/idea.md").write_text("idea", encoding="utf-8")
        path = self.root / "resources/theory/proof.md"
        path.write_text("proof", encoding="utf-8")
        listing = json.loads(self.hooks("review", "list", "--all").stdout)["items"]
        self.assertTrue(any(i["object"] == "path:resources/theory/proof.md" for i in listing))
        self.assertFalse(any("idea.md" in i["object"] for i in listing))
        item = json.loads(self.hooks("review", "show", "path:resources/theory/proof.md").stdout)
        self.submit("report", dict(reviews=[dict(token=item["token"], result="deferred", reason="Later")]))
        self.assertNotEqual(self.hooks("check", check=False).returncode, 0)

    def test_review_list_categories_and_filter(self):
        self.create_stage()
        listing = json.loads(self.hooks("review", "list", "--all").stdout)["items"]
        stage = next(item for item in listing if item["object"] == "stage:chapter")
        self.assertEqual(stage["review_kind"], "stage")
        filtered = json.loads(self.hooks("review", "list", "--all", "--kind", "stage").stdout)["items"]
        self.assertTrue(filtered)
        self.assertTrue(all(item["review_kind"] == "stage" for item in filtered))

    def test_default_show_is_bounded_and_full_is_explicit(self):
        self.create_stage()
        brief = self.show_review()
        full = json.loads(self.hooks("review", "show", "stage:chapter", "--full").stdout)
        self.assertLessEqual(brief["details_shown"], 8)
        self.assertNotIn("changes", brief)
        self.assertIn("changes", full)

    def test_review_projection_rebuild_equivalence(self):
        self.create_stage()
        self.submit("report", dict(reviews=[dict(token=self.show_review()["token"], result="reviewed-no-change")]))
        self.submit("end", dict(result="completed", note="reviewed"))
        before = self.show_review()
        self.hooks("db", "verify")
        # Rebuild a disposable test repository through the supported command.
        self.hooks("db", "rebuild")
        self.assertEqual(before, self.show_review())

    def test_end_consumes_review_without_duplicate_confirmation(self):
        self.create_stage()
        self.submit("end", dict(result="completed", note="created"))
        self.begin(track="stable", stage="chapter")
        token = self.show_review()["token"]
        self.submit("end", dict(result="completed", note="reviewed",
                                reviews=[dict(token=token, result="reviewed-no-change")]))
        self.assertEqual(self.events()[-1]["event_type"], "task.finished")

    def test_deferred_cannot_replace_required_stage_review(self):
        self.create_stage()
        self.submit("end", dict(result="completed", note="created"))
        self.begin(track="stable", stage="chapter")
        token = self.show_review()["token"]
        result = self.submit("end", dict(result="completed", note="later",
                            reviews=[dict(token=token, result="deferred", reason="later")]), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stage-review", result.stderr)

    def test_stage_edit_and_review_share_one_batch(self):
        self.create_stage()
        self.submit("end", dict(result="completed", note="created"))
        self.begin(track="stable", stage="chapter")
        token = self.show_review()["token"]
        self.submit("report", dict(stage_update=dict(summary="new overview"), reviews=[dict(token=token, result="updated")]))
        review = next(e for e in reversed(self.events()) if e["event_type"] == "review.recorded")
        self.assertTrue(review["payload"]["evidence"])
        self.assertFalse(self.show_review()["pending"])
