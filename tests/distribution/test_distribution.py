from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_hooks import EXECUTABLE_NAME, __version__ as CURRENT_VERSION
from project_hooks.ui.windows.icon import PNG_ICON
from project_hooks.infrastructure.git.build_identity import exe_matches_repository, repository_source_identity
from project_hooks.cli import discover_project_root
from project_hooks.infrastructure.system.project_manager import (
    ProjectManagerError,
    apply_project_update,
    initialize_project,
)
from project_hooks.infrastructure.system.updater import (
    UpdateError,
    check_latest_release,
    check_latest_update,
    check_update,
    run_update,
    software_changes_since_release,
    verify_digest,
)
from project_hooks.ui.windows.main import PortableBootstrapError, prepare_portable_project
from scripts.run_tests import validate_release_tag

SOURCE_ROOT = Path(__file__).resolve().parents[2]


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

    def init(self, version: str = CURRENT_VERSION) -> None:
        initialize_project(self.root, version)

    def test_application_icon_assets_are_present(self) -> None:
        self.assertTrue(PNG_ICON.is_file())
        self.assertTrue(PNG_ICON.with_suffix(".ico").is_file())

    def exe_release(self, *, digest_override: str | None = None, build_id: str | None = None) -> Path:
        release = Path(self.temp.name) / "release"
        release.mkdir(exist_ok=True)
        windows_exe = release / EXECUTABLE_NAME
        windows_exe.write_bytes(b"test executable")
        manifest = {
            "version": CURRENT_VERSION,
            "build_identity": {"build_id": build_id} if build_id else {},
            "launcher_min_version": "1.0.0",
            "event_schema": {"minimum": 1, "maximum": 3},
            "windows_exe": {
                "file": windows_exe.name,
                "url": windows_exe.as_uri(),
                "sha256": digest_override or hashlib.sha256(windows_exe.read_bytes()).hexdigest(),
            },
        }
        manifest_path = release / "release-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    def test_init_creates_project_without_copying_software_source(self) -> None:
        result = initialize_project(self.root, CURRENT_VERSION)
        self.assertEqual(result["status"], "initialized")
        self.assertFalse((self.root / "project_hooks").exists())
        self.assertTrue((self.root / ".codex/project-maintenance-workflow.json").is_file())
        self.assertTrue((self.root / ".codex/project-maintenance-installation.json").is_file())
        self.assertIn("workflow.initialized", (self.root / "maintenance/events.jsonl").read_text(encoding="utf-8"))
        for name in ("source", "data", "theory", "analysis", "outputs", "others", "reports"):
            self.assertTrue((self.root / "resources" / name / ".gitkeep").is_file())
        self.assertEqual(
            self.git("config", "--local", "--get", "core.hooksPath").stdout.strip(),
            ".githooks",
        )
        with self.assertRaises(ProjectManagerError):
            initialize_project(self.root, CURRENT_VERSION)

    def test_release_manifest_contains_only_executable_asset(self) -> None:
        dist = Path(self.temp.name) / "dist"
        dist.mkdir()
        executable = dist / EXECUTABLE_NAME
        executable.write_bytes(b"release executable")

        subprocess.run(
            [
                sys.executable, str(SOURCE_ROOT / "scripts/build_release.py"),
                "--version", CURRENT_VERSION, "--dist", str(dist),
            ],
            cwd=SOURCE_ROOT, check=True, capture_output=True,
        )

        manifest = json.loads((dist / "release-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest),
            {"version", "build_identity", "launcher_min_version", "event_schema", "windows_exe"},
        )
        self.assertIn("build_id", manifest["build_identity"])
        self.assertEqual(manifest["windows_exe"]["file"], EXECUTABLE_NAME)

    def test_release_tag_must_match_application_version(self) -> None:
        validate_release_tag(CURRENT_VERSION, f"v{CURRENT_VERSION}")
        with self.assertRaisesRegex(RuntimeError, "tag/version mismatch"):
            validate_release_tag(CURRENT_VERSION, "v999.0.0")

    def test_same_version_different_build_is_reported(self) -> None:
        self.init()
        manifest = {
            "version": CURRENT_VERSION,
            "build_identity": {"build_id": "different-build-id"},
        }
        result = check_update(self.root, manifest)
        self.assertEqual(result["status"], "different-build")
        self.assertIn("不同构建", result["build_warning"])

    @patch("project_hooks.infrastructure.system.updater._load_latest_release")
    def test_latest_release_reports_version_and_publish_time(self, load_release) -> None:
        load_release.return_value = {
            "tag_name": "v1.5.0",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-08-02T11:32:58Z",
            "html_url": "https://example.invalid/releases/v1.5.0",
        }

        result = check_latest_release()

        self.assertEqual(result["release_version"], "1.5.0")
        self.assertEqual(result["published_at"], "2026-08-02T11:32:58Z")

    def test_exe_repository_match_and_post_release_software_changes(self) -> None:
        source = self.root / "project_hooks/__init__.py"
        source.parent.mkdir(parents=True)
        source.write_text("VERSION = 1\n", encoding="utf-8")
        resource = self.root / "resources/data/sample.txt"
        resource.parent.mkdir(parents=True)
        resource.write_text("baseline\n", encoding="utf-8")
        self.commit("software baseline")
        self.git("tag", "v1.5.0")
        repository = repository_source_identity(self.root)
        identity = {
            "source_tree": repository["source_tree"],
            "source_tree_algorithm": repository["source_tree_algorithm"],
        }
        self.assertTrue(exe_matches_repository(self.root, identity))

        source.write_text("VERSION = 2\n", encoding="utf-8")
        resource.write_text("research change\n", encoding="utf-8")

        self.assertFalse(exe_matches_repository(self.root, identity))
        self.assertEqual(
            software_changes_since_release(self.root, "v1.5.0"),
            ["project_hooks/__init__.py"],
        )

    def test_repository_fingerprint_detects_untracked_content_changes(self) -> None:
        source = self.root / "project_hooks/__init__.py"
        source.parent.mkdir(parents=True)
        source.write_text("tracked\n", encoding="utf-8")
        self.commit("tracked source")
        scratch = self.root / "project_hooks/scratch.py"
        scratch.write_text("first\n", encoding="utf-8")
        repository = repository_source_identity(self.root)
        identity = {
            "source_tree": repository["source_tree"],
            "source_tree_algorithm": "git-worktree-v2",
        }
        self.assertTrue(exe_matches_repository(self.root, identity))

        resource = self.root / "resources/data/result.txt"
        resource.parent.mkdir(parents=True)
        resource.write_text("research only\n", encoding="utf-8")
        events = self.root / "maintenance/events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text("dynamic event\n", encoding="utf-8")
        self.assertTrue(exe_matches_repository(self.root, identity))

        scratch.write_text("second\n", encoding="utf-8")

        self.assertFalse(exe_matches_repository(self.root, identity))

    def test_project_root_is_discovered_from_descendant(self) -> None:
        self.init()
        nested = self.root / "resources/analysis/deep"
        nested.mkdir(parents=True)
        self.assertEqual(discover_project_root(nested), self.root.resolve())

    def test_portable_bootstrap_initializes_empty_folder_and_local_hook(self) -> None:
        portable = Path(self.temp.name) / "portable"
        portable.mkdir()
        executable = portable / EXECUTABLE_NAME
        executable.write_bytes(b"placeholder")

        initialized = prepare_portable_project(portable, executable)

        self.assertTrue(initialized)
        self.assertTrue((portable / ".git").is_dir())
        self.assertTrue((portable / ".codex/project-maintenance-workflow.json").is_file())
        self.assertIn("./workflow-monitor.exe pre-commit", (
            portable / ".githooks/pre-commit"
        ).read_text(encoding="utf-8"))
        self.assertIn("/workflow-monitor.exe", (portable / ".gitignore").read_text(encoding="utf-8"))
        self.assertFalse(prepare_portable_project(portable, executable))

    def test_portable_bootstrap_rejects_nonempty_uninitialized_folder(self) -> None:
        portable = Path(self.temp.name) / "occupied"
        portable.mkdir()
        executable = portable / EXECUTABLE_NAME
        executable.write_bytes(b"placeholder")
        (portable / "research.txt").write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(PortableBootstrapError, "不是空文件夹"):
            prepare_portable_project(portable, executable)

        self.assertEqual((portable / "research.txt").read_text(encoding="utf-8"), "keep")
        self.assertFalse((portable / ".git").exists())

    def test_portable_bootstrap_accepts_existing_empty_git_repository(self) -> None:
        portable = Path(self.temp.name) / "empty-git"
        portable.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=portable, check=True, capture_output=True)
        executable = portable / EXECUTABLE_NAME
        executable.write_bytes(b"placeholder")

        self.assertTrue(prepare_portable_project(portable, executable))
        self.assertEqual(
            subprocess.run(
                ["git", "branch", "--show-current"], cwd=portable,
                text=True, encoding="utf-8", capture_output=True, check=True,
            ).stdout.strip(),
            "main",
        )

    def test_portable_bootstrap_reports_missing_git_without_writing(self) -> None:
        portable = Path(self.temp.name) / "missing-git"
        portable.mkdir()
        executable = portable / EXECUTABLE_NAME
        executable.write_bytes(b"placeholder")

        with patch("project_hooks.ui.windows.main._git_available", return_value=False):
            with self.assertRaisesRegex(PortableBootstrapError, "未找到 Git"):
                prepare_portable_project(portable, executable)

        self.assertEqual(sorted(path.name for path in portable.iterdir()), [EXECUTABLE_NAME])

    def test_portable_bootstrap_requires_canonical_executable_name(self) -> None:
        portable = Path(self.temp.name) / "renamed-exe"
        portable.mkdir()
        executable = portable / "workflow-monitor (1).exe"
        executable.write_bytes(b"placeholder")

        with self.assertRaisesRegex(PortableBootstrapError, "必须命名为 workflow-monitor.exe"):
            prepare_portable_project(portable, executable)

    def test_portable_bootstrap_rolls_back_files_created_during_failure(self) -> None:
        portable = Path(self.temp.name) / "failed-portable"
        portable.mkdir()
        executable = portable / EXECUTABLE_NAME
        executable.write_bytes(b"placeholder")

        def fail_after_partial_write(root: Path, version: str) -> None:
            (root / ".codex").mkdir()
            (root / ".codex/partial.json").write_text(version, encoding="utf-8")
            raise ProjectManagerError("injected failure")

        with patch("project_hooks.ui.windows.main.initialize_project", side_effect=fail_after_partial_write):
            with self.assertRaisesRegex(ProjectManagerError, "injected failure"):
                prepare_portable_project(portable, executable)

        self.assertEqual(sorted(path.name for path in portable.iterdir()), [EXECUTABLE_NAME])

    def test_project_update_preserves_existing_journal_bytes_and_rebuilds_database(self) -> None:
        self.init("1.3.0")
        self.commit()
        before = (self.root / "maintenance/events.jsonl").read_bytes()
        result = apply_project_update(self.root, CURRENT_VERSION)
        after = (self.root / "maintenance/events.jsonl").read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertIn(b"workflow.upgraded", after[len(before):])
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["diagnostic_cleanup"]["status"], "cleaned")
        installation = json.loads((self.root / ".codex/project-maintenance-installation.json").read_text(encoding="utf-8"))
        self.assertEqual(installation["application_version"], CURRENT_VERSION)
        self.assertTrue(installation["build_identity"]["build_id"])
        self.assertTrue((self.root / ".project_hooks/maintenance.sqlite3").is_file())

    def test_customized_managed_file_is_preserved_and_reported(self) -> None:
        self.init()
        readme = self.root / "maintenance/README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\n项目自定义内容。\n", encoding="utf-8")
        customized = readme.read_bytes()
        self.commit()
        result = apply_project_update(self.root, CURRENT_VERSION)
        self.assertEqual(readme.read_bytes(), customized)
        self.assertIn("maintenance/README.md", result["conflicts"])
        self.assertTrue(
            (self.root / f".project_hooks/update-conflicts/{CURRENT_VERSION}/maintenance/README.md").is_file()
        )
        self.commit("record customized template")
        repeated = apply_project_update(self.root, CURRENT_VERSION)
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
        with patch("project_hooks.infrastructure.system.diagnostics.cleanup_after_update") as cleanup:
            with self.assertRaises(Exception):
                apply_project_update(self.root, CURRENT_VERSION)
            cleanup.assert_not_called()
        self.assertEqual(hook.read_bytes(), before)
        self.assertEqual(journal.read_text(encoding="utf-8"), "{broken\n")

    def test_digest_mismatch_is_rejected(self) -> None:
        with self.assertRaises(UpdateError):
            verify_digest(b"payload", "0" * 64)
        self.init("0.9.0")
        self.commit()
        journal = self.root / "maintenance/events.jsonl"
        before = journal.read_bytes()
        manifest = self.exe_release(digest_override="0" * 64)
        with patch("project_hooks.infrastructure.system.updater.is_frozen", return_value=True):
            with self.assertRaises(UpdateError):
                run_update(self.root, manifest_url=manifest.as_uri())
        self.assertEqual(journal.read_bytes(), before)
        self.assertFalse((self.root / ".project_hooks/runtime/current.json").exists())

    def test_check_only_does_not_change_project(self) -> None:
        self.init()
        self.commit()
        manifest = self.exe_release()
        before = self.git("status", "--porcelain").stdout
        result = run_update(self.root, check_only=True, manifest_url=manifest.as_uri())
        self.assertEqual(result["status"], "current")
        self.assertEqual(self.git("status", "--porcelain").stdout, before)

    def test_dashboard_update_check_is_read_only_without_update_preflight(self) -> None:
        self.init()
        manifest = self.exe_release()
        (self.root / "uncommitted.txt").write_text("dirty", encoding="utf-8")
        before = (self.root / "maintenance/events.jsonl").read_bytes()
        result = check_latest_update(self.root, manifest.as_uri())
        self.assertEqual(result["status"], "current")
        self.assertEqual((self.root / "maintenance/events.jsonl").read_bytes(), before)

    def test_frozen_update_caches_versioned_executable_and_writes_pointer(self) -> None:
        self.init("0.9.0")
        self.commit()
        manifest = self.exe_release()
        migration = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"status": "updated", "changed": [], "conflicts": []}),
            stderr="",
        )
        real_run = subprocess.run

        def run_portable(command, *args, **kwargs):
            if str(command[0]).endswith(EXECUTABLE_NAME):
                return migration
            return real_run(command, *args, **kwargs)

        with patch("project_hooks.infrastructure.system.updater.is_frozen", return_value=True):
            with patch("project_hooks.infrastructure.system.updater.subprocess.run", side_effect=run_portable) as run:
                result = run_update(self.root, manifest_url=manifest.as_uri())

        pointer = json.loads((
            self.root / ".project_hooks/runtime/current.json"
        ).read_text(encoding="utf-8"))
        selected = Path(pointer["executable"])
        self.assertEqual(result["status"], "updated")
        self.assertTrue(selected.is_file())
        self.assertEqual(selected.read_bytes(), b"test executable")
        self.assertTrue(selected.is_relative_to((self.root / ".project_hooks/runtime").resolve()))
        self.assertEqual(run.call_args.args[0][0], str(selected))

    def test_same_version_different_build_is_reinstalled(self) -> None:
        self.init()
        self.commit()
        manifest = self.exe_release(build_id="release-build")
        migration = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"status": "updated", "changed": [], "conflicts": []}),
            stderr="",
        )
        real_run = subprocess.run

        def run_portable(command, *args, **kwargs):
            if str(command[0]).endswith(EXECUTABLE_NAME):
                return migration
            return real_run(command, *args, **kwargs)

        with patch("project_hooks.infrastructure.system.updater.build_identity", return_value={"build_id": "local-build"}):
            with patch("project_hooks.infrastructure.system.updater.is_frozen", return_value=True):
                with patch("project_hooks.infrastructure.system.updater.subprocess.run", side_effect=run_portable) as run:
                    result = run_update(self.root, manifest_url=manifest.as_uri())
        self.assertEqual(result["status"], "updated")
        self.assertTrue(run.called)
        self.assertTrue((self.root / ".project_hooks/runtime/current.json").is_file())

    def test_non_frozen_update_refuses_to_install_executable(self) -> None:
        self.init("0.9.0")
        self.commit()
        manifest = self.exe_release()

        with patch("project_hooks.infrastructure.system.updater.is_frozen", return_value=False):
            with self.assertRaisesRegex(UpdateError, "只能通过项目根目录"):
                run_update(self.root, manifest_url=manifest.as_uri())

    def test_offline_and_requested_version_mismatch_do_not_change_project(self) -> None:
        self.init("0.9.0")
        self.commit()
        before = (self.root / "maintenance/events.jsonl").read_bytes()
        missing = (Path(self.temp.name) / "missing-manifest.json").as_uri()
        with self.assertRaises(UpdateError):
            run_update(self.root, manifest_url=missing)
        manifest = self.exe_release()
        with self.assertRaises(UpdateError):
            run_update(self.root, target_version="999.0.0", manifest_url=manifest.as_uri())
        self.assertEqual((self.root / "maintenance/events.jsonl").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
