from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_hooks.core.events import new_event
from project_hooks.core.privacy import sanitize_public_text, sensitive_categories
from project_hooks.infrastructure.persistence.store import append_events, load_events
from project_hooks.ui.cli import commands
from project_hooks.ui.cli.errors import WorkflowError


class PrivacyTests(unittest.TestCase):
    def test_event_payload_redacts_private_values_without_losing_research_text(self) -> None:
        private_path = "C:" + chr(92).join(("", "Users", "researcher", "private", "report.md"))
        private_email = "me" + "@" + "qq.com"
        event = new_event(
            "privacy.test", branch="main", task_id="task-1",
            payload={"evidence": [
                f"实验 7 通过；备份 {private_path}；联系 {private_email}",
                "https://example.org/data?view=summary&" + "token" + "=abc123&part=2",
                "relative/path.md remains useful",
            ]}, timezone="Asia/Shanghai",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            append_events(root / "events.jsonl", root, [event])
            stored = load_events(root / "events.jsonl")[0]
        evidence = stored["payload"]["evidence"]
        self.assertIn("实验 7 通过", evidence[0])
        self.assertNotIn("researcher", evidence[0])
        self.assertNotIn(private_email, evidence[0])
        self.assertIn("view=summary", evidence[1])
        self.assertIn("part=2", evidence[1])
        self.assertNotIn("abc123", evidence[1])
        self.assertEqual(evidence[2], "relative/path.md remains useful")
        self.assertEqual(event["task_id"], "task-1")

    def test_scanner_ignores_exact_fixture_but_detects_actual_values(self) -> None:
        private_email = "me" + "@" + "qq.com"
        private_path = "C:" + chr(92).join(("", "Users", "name", "file.txt"))
        self.assertEqual(sensitive_categories("token=ghp_abcdefghijklmnopqrstuvwxyz1234"), set())
        self.assertIn("token", sensitive_categories("ghp_" + "z" * 32))
        self.assertIn("email", sensitive_categories("contact " + private_email))
        self.assertEqual(sensitive_categories(sanitize_public_text(private_path)), set())

    def test_private_key_block_is_removed_as_a_whole(self) -> None:
        begin = "-----BEGIN " + "PRIVATE KEY-----"
        end = "-----END " + "PRIVATE KEY-----"
        value = "before\n" + begin + "\nsecret-body\n" + end + "\nafter"
        clean = sanitize_public_text(value)
        self.assertIn("before", clean)
        self.assertIn("after", clean)
        self.assertNotIn("secret-body", clean)

    def test_event_keeps_mutable_source_references_for_review_coverage(self) -> None:
        sources = {"event:initial": "digest"}
        payload = {"sources": sources}
        event = new_event("review.recorded", branch="main", task_id="task-1",
                          payload=payload, timezone="Asia/Shanghai")
        sources["event:finish"] = "later-digest"
        self.assertIn("event:finish", event["payload"]["sources"])

    def test_precommit_checks_added_lines_not_existing_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            journal = root / "events.jsonl"
            private_path = "C:" + chr(92).join(("", "Users", "name", "private.txt"))
            private_email = "me" + "@" + "qq.com"
            journal.write_text("old " + private_path + "\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "events.jsonl"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
            journal.write_text(journal.read_text(encoding="utf-8") + "safe new line\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "events.jsonl"], check=True)
            with patch.object(commands, "ROOT", root):
                commands.check_staged_privacy()
                journal.write_text(journal.read_text(encoding="utf-8") + "contact " + private_email + "\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(root), "add", "events.jsonl"], check=True)
                with self.assertRaises(WorkflowError) as error:
                    commands.check_staged_privacy()
            self.assertIn("email", str(error.exception))
            self.assertNotIn(private_email, str(error.exception))


if __name__ == "__main__":
    unittest.main()
