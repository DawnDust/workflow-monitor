from datetime import datetime, timedelta
import unittest

from project_hooks.core.reviews import project_review, encode_token, decode_token


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
        self.assertEqual(decode_token(encode_token(snap)), snap)
        for token in (None, "xxx", "e30="):
            with self.assertRaises(ValueError):
                decode_token(token)


if __name__ == "__main__":
    unittest.main()
