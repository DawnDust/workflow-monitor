import base64
from datetime import datetime, timedelta
import json
import unittest

from project_hooks.core.reviews import project_review, encode_token, decode_token
from project_hooks.infrastructure.system.reviews import summary


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 15, 12)
        self.obj = dict(object="stage:s", branch="main", sources={"event:1": "a"}, status="active", issues=[])

    def review(self, result="reviewed-no-change", **extra):
        return dict(branch="main", occurred_at=self.now.isoformat(),
                    payload=dict(object="stage:s", sources=dict(self.obj["sources"]), result=result, **extra))

    def test_first_review_and_time_boundary(self):
        self.assertTrue(project_review(self.obj, [], self.now)["pending"])
        record = self.review()
        self.assertFalse(project_review(self.obj, [record], self.now + timedelta(days=14, seconds=-1))["pending"])
        self.assertTrue(project_review(self.obj, [record], self.now + timedelta(days=14))["pending"])

    def test_new_evidence_survives_old_snapshot(self):
        record = self.review()
        self.obj["sources"]["event:2"] = "b"
        projected = project_review(self.obj, [record], self.now)
        self.assertEqual(projected["changes"], ["event:2"])
        self.assertTrue(projected["pending"])

    def test_deferral_breaks_on_new_content(self):
        record = self.review("deferred", until=(self.now + timedelta(days=3)).isoformat())
        self.assertTrue(project_review(self.obj, [record], self.now)["deferred"])
        self.assertTrue(project_review(self.obj, [record], self.now + timedelta(days=3))["pending"])
        self.obj["sources"]["event:2"] = "b"
        self.assertTrue(project_review(self.obj, [record], self.now)["pending"])

    def test_branch_isolation(self):
        record = self.review()
        record["branch"] = "experiment/x"
        self.assertTrue(project_review(self.obj, [record], self.now)["pending"])

    def test_paused_and_archived_do_not_have_periodic_reminders(self):
        for status in ("paused", "completed", "cancelled", "archived", "validated", "negative", "inconclusive"):
            self.obj["status"] = status
            self.assertFalse(project_review(self.obj, [], self.now)["pending"])
        self.obj["status"] = "active"
        self.assertTrue(project_review(self.obj, [], self.now)["pending"])

    def test_usage_controls_routine_reminders_but_not_issues(self):
        self.obj["usage"] = "example"
        self.assertFalse(project_review(self.obj, [], self.now)["pending"])
        self.obj["issues"] = ["missing"]
        self.assertTrue(project_review(self.obj, [], self.now)["pending"])
        self.obj["issues"] = []
        self.obj["usage"] = "reference"
        record = self.review()
        self.assertFalse(project_review(self.obj, [record], self.now + timedelta(days=30))["pending"])
        self.obj["sources"]["event:2"] = "b"
        self.assertTrue(project_review(self.obj, [record], self.now + timedelta(days=30))["pending"])

    def test_legacy_review_is_visible_without_claiming_exact_coverage(self):
        self.obj["legacy_reviewed_at"] = "2026-09-14T10:00:00"
        projected = project_review(self.obj, [], self.now)
        self.assertEqual(projected["last_reviewed_at"], self.obj["legacy_reviewed_at"])
        self.assertIn("尚未建立新版审阅基线", projected["reasons"])

    def test_resume_deferral_and_missing_file_signal(self):
        self.obj["status"] = "paused"
        record = self.review("deferred", until="resume")
        self.assertTrue(project_review(self.obj, [record], self.now)["deferred"])
        self.obj["status"] = "active"
        self.assertTrue(project_review(self.obj, [record], self.now)["pending"])
        self.obj["status"] = "archived"
        self.obj["issues"] = ["missing"]
        self.assertTrue(project_review(self.obj, [], self.now)["pending"])

    def test_token_round_trip_and_invalid_input(self):
        snap = {k: self.obj[k] for k in ("object", "branch", "sources")}
        decoded = decode_token(encode_token(snap))
        self.assertEqual((decoded["branch"], decoded["object"], decoded["v"]), ("main", "stage:s", 2))
        self.assertEqual(len(decoded["digest"]), 64)
        self.assertLess(len(encode_token(snap)), 256)
        legacy = base64.urlsafe_b64encode(json.dumps(snap, separators=(",", ":")).encode()).decode()
        self.assertEqual(decode_token(legacy), snap)
        for token in (None, "xxx", "e30="):
            with self.assertRaises(ValueError):
                decode_token(token)

    def test_summary_only_claims_complete_review_when_all_live_items_covered(self):
        stage = dict(object="stage:s", title="Stage", review_kind="stage", usage="formal",
                     status="active", pending=False, related=True, reasons=[],
                     last_changed_at="2026-09-15T09:00:00", exact_reviewed_at="2026-09-15T10:00:00")
        resource = dict(object="resource:r", title="Resource", review_kind="resource", usage="formal",
                        status="active", pending=True, related=False, reasons=["new file"],
                        last_changed_at="2026-09-16T09:00:00", exact_reviewed_at=None)
        example = dict(object="resource:demo", title="Demo", review_kind="resource", usage="example",
                       status="active", pending=False, related=False, reasons=[],
                       last_changed_at="2026-09-17T09:00:00", exact_reviewed_at=None)
        result = summary([stage, resource, example])
        self.assertIsNone(result["last_overall_reviewed_at"])
        self.assertEqual(result["last_research_changed_at"], "2026-09-16T09:00:00")
        self.assertEqual(result["pending_by_kind"], {"stage": 0, "attempt": 0, "resource": 1})
        resource["exact_reviewed_at"] = "2026-09-16T10:00:00"
        resource["pending"] = False
        self.assertIsNone(summary([stage, resource, example])["last_overall_reviewed_at"])
        boundary = {"event_type": "review.coverage_completed", "branch": "main",
                    "occurred_at": "2026-09-16T10:00:00", "payload": {}}
        self.assertEqual(summary([stage, resource, example], [boundary], "main")["last_overall_reviewed_at"],
                         "2026-09-16T10:00:00")
        resource["pending"] = True
        self.assertEqual(summary([stage, resource, example], [boundary], "main")["last_overall_reviewed_at"],
                         "2026-09-16T10:00:00")

    def test_integrity_issue_survives_deferral_and_completed_review(self):
        self.obj["issues"] = ["missing file"]
        reviewed = self.review("reviewed-no-change")
        deferred = self.review("deferred", until="2026-09-30T00:00:00")
        result = project_review(self.obj, [reviewed, deferred], self.now)
        self.assertTrue(result["pending"])
        self.assertTrue(result["deferred"])
        self.assertIn("integrity_issue", result["reason_codes"])
        self.assertEqual(summary([{**result, "related": True}])["status"], "action_required")


if __name__ == "__main__":
    unittest.main()
