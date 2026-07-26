from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from project_hooks.cli import discover_project_root
from project_hooks.project_manager import (
    ProjectManagerError,
    apply_project_update,
    initialize_project,
)
from project_hooks.updater import UpdateError, run_update, verify_digest


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "research"
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Project Hooks Test")
        self.git("config", "user.email", "hooks@example.invalid")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=True,
        )

    def commit(self, message: str = "baseline") -> None:
        self.git("add", ".")
        self.git("-c", "core.hooksPath=.git/no-hooks", "commit", "-m", message)

    def init(self, version: str = "1.0.0") -> None:
        initialize_project(self.root, version)

    def core_release(self, *, digest_override: str | None = None) -> Path:
        release = Path(self.temp.name) / "release"
        release.mkdir(exist_ok=True)
        core = release / "project-hooks-core-1.0.0.zip"
        with zipfile.ZipFile(core, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((SOURCE_ROOT / "project_hooks").rglob("*.py")):
                if "__pycache__" not in path.parts:
                    archive.write(path, path.relative_to(SOURCE_ROOT).as_posix())
        wheel = release / "project_maintenance_workflow-1.0.0-py3-none-any.whl"
        wheel.write_bytes(b"test wheel")
        manifest = {
            "version": "1.0.0",
            "launcher_min_version": "1.0.0",
            "event_schema": {"minimum": 1, "maximum": 2},
            "core": {
                "file": core.name,
                "url": core.as_uri(),
                "sha256": digest_override or hashlib.sha256(core.read_bytes()).hexdigest(),
            },
            "wheel": {
                "file": wheel.name,
                "url": wheel.as_uri(),
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            },
        }
        manifest_path = release / "release-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    def test_init_creates_project_without_copying_software_source(self) -> None:
        result = initialize_project(self.root, "1.0.0")
        self.assertEqual(result["status"], "initialized")
        self.assertFalse((self.root / "project_hooks").exists())
        self.assertTrue((self.root / ".codex/project-maintenance-workflow.json").is_file())
        self.assertTrue((self.root / ".codex/project-maintenance-installation.json").is_file())
        self.assertIn("workflow.initialized", (self.root / "maintenance/events.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(
            self.git("config", "--local", "--get", "core.hooksPath").stdout.strip(),
            ".githooks",
        )
        with self.assertRaises(ProjectManagerError):
            initialize_project(self.root, "1.0.0")

    def test_project_root_is_discovered_from_descendant(self) -> None:
        self.init()
        nested = self.root / "analysis/deep"
        nested.mkdir(parents=True)
        self.assertEqual(discover_project_root(nested), self.root.resolve())

    def test_update_preserves_existing_journal_bytes_and_rebuilds_database(self) -> None:
        self.init("0.9.0")
        self.commit()
        before = (self.root / "maintenance/events.jsonl").read_bytes()
        manifest = self.core_release()
        cache = Path(self.temp.name) / "cache"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(cache)}):
            result = run_update(self.root, manifest_url=manifest.as_uri())
        after = (self.root / "maintenance/events.jsonl").read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertIn(b"workflow.upgraded", after[len(before):])
        self.assertEqual(result["status"], "updated")
        self.assertTrue((cache / "project-maintenance-workflow/current.json").is_file())
        self.assertTrue((self.root / ".project_hooks/maintenance.sqlite3").is_file())

    def test_customized_managed_file_is_preserved_and_reported(self) -> None:
        self.init()
        readme = self.root / "maintenance/README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\n项目自定义内容。\n", encoding="utf-8")
        customized = readme.read_bytes()
        self.commit()
        result = apply_project_update(self.root, "1.0.0")
        self.assertEqual(readme.read_bytes(), customized)
        self.assertIn("maintenance/README.md", result["conflicts"])
        self.assertTrue(
            (self.root / ".project_hooks/update-conflicts/1.0.0/maintenance/README.md").is_file()
        )
        self.commit("record customized template")
        repeated = apply_project_update(self.root, "1.0.0")
        self.assertEqual(readme.read_bytes(), customized)
        self.assertIn("maintenance/README.md", repeated["conflicts"])

    def test_failed_migration_restores_managed_files(self) -> None:
        self.init()
        self.commit()
        hook = self.root / ".githooks/pre-commit"
        before = hook.read_bytes()
        journal = self.root / "maintenance/events.jsonl"
        journal.write_text("{broken\n", encoding="utf-8")
        self.commit("corrupt fixture")
        with self.assertRaises(Exception):
            apply_project_update(self.root, "1.0.0")
        self.assertEqual(hook.read_bytes(), before)
        self.assertEqual(journal.read_text(encoding="utf-8"), "{broken\n")

    def test_digest_mismatch_is_rejected(self) -> None:
        with self.assertRaises(UpdateError):
            verify_digest(b"payload", "0" * 64)
        self.init("0.9.0")
        self.commit()
        journal = self.root / "maintenance/events.jsonl"
        before = journal.read_bytes()
        manifest = self.core_release(digest_override="0" * 64)
        cache = Path(self.temp.name) / "bad-cache"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(cache)}):
            with self.assertRaises(UpdateError):
                run_update(self.root, manifest_url=manifest.as_uri())
        self.assertEqual(journal.read_bytes(), before)
        self.assertFalse((cache / "project-maintenance-workflow/current.json").exists())

    def test_check_only_does_not_change_project(self) -> None:
        self.init()
        self.commit()
        manifest = self.core_release()
        before = self.git("status", "--porcelain").stdout
        result = run_update(self.root, check_only=True, manifest_url=manifest.as_uri())
        self.assertEqual(result["status"], "current")
        self.assertEqual(self.git("status", "--porcelain").stdout, before)

    def test_offline_and_requested_version_mismatch_do_not_change_project(self) -> None:
        self.init("0.9.0")
        self.commit()
        before = (self.root / "maintenance/events.jsonl").read_bytes()
        missing = (Path(self.temp.name) / "missing-manifest.json").as_uri()
        with self.assertRaises(UpdateError):
            run_update(self.root, manifest_url=missing)
        manifest = self.core_release()
        with self.assertRaises(UpdateError):
            run_update(self.root, target_version="1.1.0", manifest_url=manifest.as_uri())
        self.assertEqual((self.root / "maintenance/events.jsonl").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
