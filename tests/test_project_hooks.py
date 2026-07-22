from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class ProjectHooksBranchWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        for name in (".codex", ".githooks", "maintenance", "project_hooks"):
            shutil.copytree(SOURCE_ROOT / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("AGENTS.md", ".gitignore"):
            shutil.copy2(SOURCE_ROOT / name, self.root / name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Project Hooks Test")
        self.git("config", "user.email", "hooks@example.invalid")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.root, text=True, capture_output=True, check=check
        )

    def hooks(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "project_hooks", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=check,
        )

    def start(self, task_id: str, *extra: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.hooks(
            "start",
            task_id,
            "--kind",
            "analysis",
            "--scope",
            "test branch workflow",
            "--acceptance",
            "workflow behaves deterministically",
            "--git-commit",
            "never",
            *extra,
            check=check,
        )

    def append_required_updates(self) -> None:
        for rel in ("maintenance/change_archive.md", "maintenance/current_task.md"):
            path = self.root / rel
            path.write_text(path.read_text(encoding="utf-8") + "\nTest lifecycle update.\n", encoding="utf-8")

    def fill_attempt(self, task_id: str) -> Path:
        path = self.root / "maintenance" / "attempts" / f"{task_id}.md"
        text = path.read_text(encoding="utf-8")
        text = text.replace("## Hypothesis\n\n- Pending", "## Hypothesis\n\n- The proposed route should satisfy the criteria")
        text = text.replace("## Evidence\n\n- Pending", "## Evidence\n\n- Unit test passed")
        text = text.replace("## Conclusion\n\n- Pending", "## Conclusion\n\n- Acceptance criteria satisfied")
        path.write_text(text, encoding="utf-8")
        return path

    def end(self, task_id: str, state: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.hooks(
            "end",
            task_id,
            "--result",
            "completed",
            "--route",
            "unchanged",
            "--methods-action",
            "updated",
            "--main-goal",
            "unchanged",
            "--note",
            "test completed",
            "--attempt-state",
            state,
            check=check,
        )

    def test_legacy_start_defaults_to_stable_on_main(self) -> None:
        result = self.start("20260722_stable_001")
        self.assertEqual(json.loads(result.stdout)["task_id"], "20260722_stable_001")
        status = json.loads(self.hooks("status").stdout)
        self.assertEqual(status["track"], "stable")
        self.assertEqual(status["branch"], "main")

    def test_exploration_start_creates_branch_and_attempt(self) -> None:
        self.start("20260722_research_001", "--track", "research", "--topic", "geometric-shell")
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "research/geometric-shell")
        attempt = self.root / "maintenance/attempts/20260722_research_001.md"
        self.assertTrue(attempt.is_file())
        self.assertIn("- Branch: `research/geometric-shell`", attempt.read_text(encoding="utf-8"))
        status = json.loads(self.hooks("branch-status").stdout)
        self.assertEqual(status["attempt_state"], "active")

    def test_later_task_reuses_attempt_on_existing_exploration_branch(self) -> None:
        first = "20260722_continue_001"
        self.start(first, "--track", "research", "--topic", "continuation")
        self.append_required_updates()
        self.end(first, "active")
        self.git("add", ".")
        self.git("commit", "-m", "record active attempt")

        second = "20260722_continue_002"
        self.start(second)
        active = json.loads((self.root / ".project_hooks/active.json").read_text(encoding="utf-8"))
        self.assertEqual(active["git"]["track"], "research")
        self.assertEqual(active["git"]["attempt_path"], f"maintenance/attempts/{first}.md")
        self.assertFalse((self.root / f"maintenance/attempts/{second}.md").exists())

    def test_unsafe_exploration_starts_leave_no_active_state(self) -> None:
        (self.root / "dirty.txt").write_text("dirty", encoding="utf-8")
        result = self.start("20260722_dirty_001", "--track", "research", "--topic", "dirty", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / ".project_hooks/active.json").exists())
        (self.root / "dirty.txt").unlink()

        result = self.start("20260722_badslug_001", "--track", "research", "--topic", "Bad_Name", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / ".project_hooks/active.json").exists())

        self.git("branch", "research/collision")
        result = self.start("20260722_collision_001", "--track", "research", "--topic", "collision", check=False)
        self.assertNotEqual(result.returncode, 0)

        self.git("checkout", "--detach")
        result = self.start("20260722_detached_001", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / ".project_hooks/active.json").exists())

    def test_diverged_known_origin_main_rejects_branch_creation(self) -> None:
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        marker = self.root / "maintenance/exploration_log.md"
        marker.write_text(marker.read_text(encoding="utf-8") + "\nremote divergence fixture\n", encoding="utf-8")
        self.git("add", str(marker.relative_to(self.root)))
        self.git("commit", "-m", "advance local main")
        result = self.start("20260722_diverged_001", "--track", "research", "--topic", "diverged", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("origin/main", result.stderr)
        self.assertFalse((self.root / ".project_hooks/active.json").exists())
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "main")

    def test_branch_switch_is_rejected_by_status_end_and_pre_commit(self) -> None:
        task_id = "20260722_switch_001"
        self.start(task_id, "--track", "sandbox", "--topic", "switch-check")
        self.git("switch", "main")
        for command in (("status",), ("pre-commit",)):
            result = self.hooks(*command, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("任务启动于分支", result.stderr)
        self.append_required_updates()
        result = self.end(task_id, "active", check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_validated_attempt_prepares_pr_without_network_action(self) -> None:
        task_id = "20260722_validated_001"
        self.start(task_id, "--track", "experiment", "--topic", "solver-check")
        self.fill_attempt(task_id)
        self.append_required_updates()
        log = self.root / "maintenance/exploration_log.md"
        log.write_text(log.read_text(encoding="utf-8") + "\n| experiment/solver-check | test | validated | attempt | pending PR |\n", encoding="utf-8")
        self.end(task_id, "validated")
        self.git("add", ".")
        self.git("commit", "-m", "validate solver attempt")
        result = json.loads(self.hooks("prepare-pr").stdout)
        self.assertTrue(result["ready"])
        self.assertEqual(result["merge_method"], "squash_pr")
        self.assertTrue(result["requires_user_confirmation"])
        self.assertFalse(result["network_actions_performed"])

    def test_negative_attempt_can_be_archived_but_not_prepared(self) -> None:
        task_id = "20260722_negative_001"
        self.start(task_id, "--track", "research", "--topic", "failed-model")
        self.append_required_updates()
        self.end(task_id, "negative")
        self.git("add", ".")
        self.git("commit", "-m", "record negative attempt")
        rejected = self.hooks("prepare-pr", check=False)
        self.assertNotEqual(rejected.returncode, 0)
        result = json.loads(self.hooks("archive-attempt").stdout)
        self.assertEqual(result["branch"], "archive/research/failed-model")
        self.assertFalse(result["network_actions_performed"])
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), result["branch"])

    def test_auto_commit_refreshes_task_paths_and_preserves_unrelated_staging(self) -> None:
        user_note = self.root / "user_note.txt"
        user_note.write_text("baseline\n", encoding="utf-8")
        self.git("add", "user_note.txt")
        self.git("commit", "-m", "add user note")
        user_note.write_text("user staged work\n", encoding="utf-8")
        self.git("add", "user_note.txt")

        task_id = "20260722_autocommit_001"
        self.hooks(
            "start",
            task_id,
            "--kind",
            "governance",
            "--scope",
            "test automatic commit index handling",
            "--acceptance",
            "task paths are clean and user staging is preserved",
            "--task-size",
            "large",
            "--git-commit",
            "always",
        )
        self.append_required_updates()
        result = self.hooks(
            "end",
            task_id,
            "--result",
            "completed",
            "--route",
            "unchanged",
            "--methods-action",
            "updated",
            "--main-goal",
            "unchanged",
            "--note",
            "automatic commit completed",
        )
        self.assertEqual(json.loads(result.stdout)["git"]["status"], "committed")
        porcelain = self.git("status", "--porcelain=v1").stdout.splitlines()
        self.assertEqual(porcelain, ["M  user_note.txt"])


if __name__ == "__main__":
    unittest.main()
