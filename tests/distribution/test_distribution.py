from __future__ import annotations

from project_hooks.core.events import SCHEMA_VERSION

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
from project_hooks.infrastructure.git.build_identity import (
    display_version,
    exe_matches_repository,
    repository_source_identity,
)
from project_hooks.cli import discover_project_root
from project_hooks.infrastructure.system.project_manager import (
    ProjectManagerError,
    apply_project_update,
    initialize_project,
    TEMPLATE_VERSION,
)
from project_hooks.infrastructure.persistence.store import append_events, load_events, new_event
from project_hooks.infrastructure.system.updater import (
    UpdateError,
    check_latest_release,
    check_latest_update,
    check_update,
    run_update,
    version_report,
    software_changes_since_release,
    verify_digest,
)
from project_hooks.ui.cli import commands as cli_commands
from project_hooks.ui.cli.parser import build_parser
from project_hooks.ui.windows import main as windows_main
from project_hooks.ui.windows.main import PortableBootstrapError, prepare_portable_project
from scripts.build_windows_exe import build_parser as build_exe_parser
from scripts.run_tests import validate_release_tag

SOURCE_ROOT = Path(__file__).resolve().parents[2]


class DistributionTests(unittest.TestCase):
    def test_build_notes_only_claim_current_accepted_receipts(self):
        from project_hooks.infrastructure.system.build_notes import build_verification
        from project_hooks.infrastructure.system.verification import write_test_receipt
        self.init()
        runner = self.root / "scripts/run_tests.py"
        runner.parent.mkdir()
        runner.write_text("# test runner", encoding="utf-8")
        with patch("project_hooks.infrastructure.system.verification.active_task", return_value={"task_id":"notes-test"}):
            write_test_receipt(self.root, suite="fast", result="passed", tests=1, failures=0,
                               coverage=None, started_at="start", finished_at="finish")
            result = build_verification(self.root)
            self.assertEqual([x["status"] for x in result["suites"]], ["passed", "unverified"])
            runner.write_text("# changed runner", encoding="utf-8")
            self.assertEqual(build_verification(self.root)["suites"][0]["status"], "unverified")

    def test_local_update_flags_preserve_remote_manifest_compatibility(self):
        from project_hooks.ui.cli.parser import build_parser
        from project_hooks.ui.cli.entrypoint import main
        import io
        args = build_parser().parse_args(["update", "--to", CURRENT_VERSION, "--manifest-url", "file:///manifest.json"])
        self.assertEqual(args.to, CURRENT_VERSION)
        with patch("sys.stderr", new_callable=io.StringIO) as error:
            self.assertEqual(main(["update", "--from-exe", "test.exe", "--manifest-url", "file:///manifest.json"]), 1)
        self.assertIn("--from-exe", error.getvalue())

    def test_understanding_guide_install_update_and_customization(self):
        self.init()
        guide = self.root / "maintenance/RESEARCH_UNDERSTANDING_GUIDE.md"
        original = guide.read_text(encoding="utf-8")
        self.assertIn("不设置必经阶段", original)
        self.commit()
        self.assertEqual(apply_project_update(self.root, CURRENT_VERSION)["conflicts"], [])
        guide.write_text(original + "\n我的学习方法\n", encoding="utf-8")
        self.commit("custom guide")
        result = apply_project_update(self.root, CURRENT_VERSION)
        self.assertEqual(result["status"], "needs-attention")
        self.assertIn("maintenance/RESEARCH_UNDERSTANDING_GUIDE.md", result["conflicts"])
        self.assertIn("我的学习方法", guide.read_text(encoding="utf-8"))

    def test_packaged_guides_and_notes_change_fingerprint(self):
        self.init()
        self.commit()
        identity = repository_source_identity(self.root)
        guide = self.root / "maintenance/RESEARCH_UNDERSTANDING_GUIDE.md"
        guide.write_text(guide.read_text(encoding="utf-8") + "\nnew guidance\n", encoding="utf-8")
        self.assertNotEqual(identity["build_input_fingerprint"], repository_source_identity(self.root)["build_input_fingerprint"])
        notes = self.root / "TEST_BUILD_NOTES.md"
        before = repository_source_identity(self.root)
        notes.write_text("test notes", encoding="utf-8")
        self.assertNotEqual(before["build_input_fingerprint"], repository_source_identity(self.root)["build_input_fingerprint"])

    def test_test_notes_validate_embed_and_legacy_absence(self):
        from scripts.build_windows_exe import write_build_info
        from project_hooks.infrastructure.system.build_notes import read_build_notes, validate_notes
        with self.assertRaises(FileNotFoundError):
            write_build_info(self.root)
        with self.assertRaises(ValueError):
            validate_notes("## 待测变化\n\n## 已知限制\n")
        notes = self.root / "TEST_BUILD_NOTES.md"
        notes.write_text((SOURCE_ROOT / "TEST_BUILD_NOTES.md").read_text(encoding="utf-8"), encoding="utf-8")
        identity = json.loads(write_build_info(self.root).read_text(encoding="utf-8"))
        with patch("sys._MEIPASS", str(self.root / "build"), create=True), patch(
            "project_hooks.infrastructure.system.build_notes.build_identity", return_value=identity
        ):
            payload = read_build_notes()
            self.assertTrue(payload["available"])
            self.assertEqual(payload["build_id"], identity["build_id"])
            self.assertIn(identity["build_input_fingerprint"], payload["markdown"])
            (self.root / "build/test-build-notes.json").unlink()
            self.assertFalse(read_build_notes()["available"])

    def test_local_exe_caches_are_separate_and_old_pointers_work(self):
        from project_hooks.infrastructure.system.updater import cache_executable
        from project_hooks.infrastructure.system.runtime import write_pointer, selected_executable
        first = cache_executable(b"first", CURRENT_VERSION, self.root, build_id="first")
        second = cache_executable(b"second", CURRENT_VERSION, self.root, build_id="second")
        self.assertNotEqual(first, second)
        write_pointer(CURRENT_VERSION, self.root, second, build_id="second")
        self.assertEqual(selected_executable(self.root), second.resolve())
        with patch("project_hooks.infrastructure.git.build_identity.build_identity", return_value={
            "channel":"test", "built_at":"2026-10-07T12:00:00+00:00"
        }):
            self.assertIsNone(selected_executable(self.root))
        second.write_bytes(b"corrupt")
        self.assertIsNone(selected_executable(self.root))
        self.assertEqual(cache_executable(b"second", CURRENT_VERSION, self.root, build_id="second").read_bytes(), b"second")
        legacy = cache_executable(b"legacy", CURRENT_VERSION, self.root)
        write_pointer(CURRENT_VERSION, self.root, legacy)
        self.assertEqual(selected_executable(self.root), legacy.resolve())
        with self.assertRaises(ValueError):
            cache_executable(b"x", CURRENT_VERSION, self.root, build_id="../escape")

    def test_update_failure_restores_templates_journal_and_runtime_pointer(self):
        from project_hooks.infrastructure.system.updater import cache_executable
        from project_hooks.infrastructure.system.runtime import write_pointer, runtime_root
        self.init()
        self.commit()
        old = cache_executable(b"old", CURRENT_VERSION, self.root, build_id="old")
        new = cache_executable(b"new", CURRENT_VERSION, self.root, build_id="new")
        write_pointer(CURRENT_VERSION, self.root, old, build_id="old")
        pointer = runtime_root(self.root) / "current.json"
        before = {p:p.read_bytes() for p in (pointer, self.root / "maintenance/events.jsonl", self.root / ".workflow-monitor/installation.json")}
        with patch("project_hooks.infrastructure.system.project_manager.build_identity", return_value={"build_id":"new"}), patch(
            "project_hooks.infrastructure.system.project_manager.validate_project", side_effect=RuntimeError("validation failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "validation failed"):
                apply_project_update(self.root, CURRENT_VERSION, executable_path=new, cache_build_id="new")
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_local_update_preview_readonly_and_source_mismatch_blocked(self):
        from project_hooks.infrastructure.system.updater import run_local_update, template_hashes
        self.init()
        self.commit()
        exe = Path(self.temp.name) / "candidate.exe"
        exe.write_bytes(b"candidate")
        report = {"application_version":CURRENT_VERSION,"build_identity":{"build_id":"candidate","mode":"frozen"},
                  "template_version":TEMPLATE_VERSION,"template_hashes":template_hashes(),"event_schema":{"minimum":1,"maximum":SCHEMA_VERSION},"schema_version":SCHEMA_VERSION}
        journal = self.root / "maintenance/events.jsonl"
        before = journal.read_bytes()
        with patch("project_hooks.infrastructure.system.updater.inspect_executable", return_value=report):
            preview = run_local_update(self.root, exe, check_only=True)
            self.assertEqual(preview["status"], "ready")
            self.assertEqual(preview["source_sync"], "not-applicable")
            self.assertEqual(journal.read_bytes(), before)
            self.assertFalse((self.root / ".project_hooks/runtime").exists())
            with patch("project_hooks.infrastructure.system.updater.exe_matches_repository", return_value=False):
                preview = run_local_update(self.root, exe, check_only=True)
                self.assertEqual(preview["source_sync"], "source-required")
                with self.assertRaisesRegex(UpdateError, "源码"):
                    run_local_update(self.root, exe)

    def test_local_update_queries_exact_file_and_applies_candidate(self):
        from project_hooks.infrastructure.system.updater import run_local_update, template_hashes
        self.init()
        self.commit()
        exe = Path(self.temp.name) / "candidate.exe"
        exe.write_bytes(b"candidate")
        identity = {"build_id":"candidate","mode":"frozen","channel":"test"}
        report = {"application_version":CURRENT_VERSION,"build_identity":identity,"template_version":TEMPLATE_VERSION,
                  "template_hashes":template_hashes(),"event_schema":{"minimum":1,"maximum":SCHEMA_VERSION},"schema_version":SCHEMA_VERSION}
        real_run = subprocess.run
        def run(command, *args, **kwargs):
            if str(command[0]).endswith(".exe"):
                self.assertEqual(kwargs["env"]["PROJECT_HOOKS_EXE_ACTIVE"], "1")
                if command[-1] == "version":
                    self.assertTrue(Path(command[0]).samefile(exe))
                    return subprocess.CompletedProcess(command, 0, json.dumps(report), "")
                selected = Path(command[command.index("--executable-path")+1])
                with patch("project_hooks.infrastructure.system.project_manager.build_identity", return_value=identity):
                    result = apply_project_update(self.root, CURRENT_VERSION, executable_path=selected, cache_build_id="candidate")
                return subprocess.CompletedProcess(command, 0, json.dumps(result), "")
            return real_run(command, *args, **kwargs)
        with patch("subprocess.run", side_effect=run):
            result = run_local_update(self.root, exe)
        self.assertEqual(result["running_build_id"], "candidate")
        self.assertEqual(result["verification"]["project"], "passed")

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
        initialize_project(self.root, version, unified=False)

    def test_application_icon_assets_are_present(self) -> None:
        self.assertTrue(PNG_ICON.is_file())
        self.assertTrue(PNG_ICON.with_suffix(".ico").is_file())

    def test_frozen_builder_embeds_physics_roadmap_template(self) -> None:
        build_script = (SOURCE_ROOT / "scripts/build_windows_exe.py").read_text(encoding="utf-8")
        self.assertIn('root / "maintenance" / "PHYSICS_RESEARCH_ROADMAP.md"', build_script)
        self.assertIn('f"{physics_roadmap}{os.pathsep}maintenance"', build_script)

    def exe_release(self, *, digest_override: str | None = None, build_id: str | None = None) -> Path:
        release = Path(self.temp.name) / "release"
        release.mkdir(exist_ok=True)
        windows_exe = release / EXECUTABLE_NAME
        windows_exe.write_bytes(b"test executable")
        manifest = {
            "version": CURRENT_VERSION,
            "build_identity": {"build_id": build_id} if build_id else {},
            "launcher_min_version": "1.0.0",
            "event_schema": {"minimum": 1, "maximum": SCHEMA_VERSION},
            "windows_exe": {
                "file": windows_exe.name,
                "url": windows_exe.as_uri(),
                "sha256": digest_override or hashlib.sha256(windows_exe.read_bytes()).hexdigest(),
            },
        }
        manifest_path = release / "release-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    def stable_build_info(self) -> Path:
        path = Path(self.temp.name) / "build-info.json"
        path.write_text(json.dumps({"build_id": "stable-build", "channel": "stable"}), encoding="utf-8")
        return path

    def test_test_build_version_label_uses_channel_date_and_short_id(self) -> None:
        identity = {
            "channel": "test",
            "build_id": "ff977aed87179638",
            "built_at": "2026-10-06T12:33:51+00:00",
        }
        self.assertEqual(
            display_version(CURRENT_VERSION, identity),
            f"{CURRENT_VERSION}-test.20261006.ff977aed",
        )
        self.assertEqual(
            display_version(CURRENT_VERSION, {"channel": "stable", "build_id": "test"}),
            CURRENT_VERSION,
        )
        with patch(
            "project_hooks.infrastructure.system.updater.build_identity",
            return_value=identity,
        ):
            report = version_report(None)
        self.assertEqual(report["application_version"], CURRENT_VERSION)
        self.assertEqual(report["display_version"], f"{CURRENT_VERSION}-test.20261006.ff977aed")
        self.assertEqual(report["build_channel"], "test")

    def test_build_channel_defaults_to_test_and_stable_is_explicit(self) -> None:
        parser = build_exe_parser()
        self.assertEqual(parser.parse_args([]).channel, "test")
        self.assertEqual(parser.parse_args(["--channel", "stable"]).channel, "stable")
        build_script = (SOURCE_ROOT / "scripts/build_windows_exe.py").read_text(encoding="utf-8")
        self.assertIn('"--name", "workflow-monitor"', build_script)

    def test_init_creates_project_without_copying_software_source(self) -> None:
        result = initialize_project(self.root, CURRENT_VERSION)
        self.assertEqual(result["status"], "initialized")
        self.assertFalse((self.root / "project_hooks").exists())
        self.assertTrue((self.root / ".workflow-monitor/workflow.json").is_file())
        self.assertTrue((self.root / ".workflow-monitor/installation.json").is_file())
        self.assertTrue((self.root / ".agents/plugins/workflow-monitor/plugin.json").is_file())
        self.assertTrue((self.root / ".agents/plugins/workflow-monitor/hooks.json").is_file())
        config = json.loads((self.root / ".workflow-monitor/workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(config["core_read_order"], ["maintenance/CORE.md"])
        for name in ("README.md", "CORE.md", "RESEARCH.md", "PHYSICS_RESEARCH_ROADMAP.md", "OPERATIONS.md"):
            self.assertTrue((self.root / "maintenance" / name).is_file())
        self.assertIn("workflow.initialized", (self.root / "maintenance/events.jsonl").read_text(encoding="utf-8"))
        for name in ("source", "data", "theory", "others", "reports", "sparks", "tutorials", "translations", "plans"):
            self.assertTrue((self.root / "resources" / name / ".gitkeep").is_file())
        self.assertTrue((self.root / "program" / ".gitkeep").is_file())
        self.assertTrue(
            Path(self.git("config", "--local", "--get", "core.hooksPath").stdout.strip()).samefile(
                self.root / ".git/workflow-monitor/hooks"
            )
        )
        with self.assertRaises(ProjectManagerError):
            initialize_project(self.root, CURRENT_VERSION)

    def test_generated_maintenance_index_only_lists_installed_guides(self) -> None:
        self.init()
        readme = (self.root / "maintenance/README.md").read_text(encoding="utf-8")
        self.assertNotIn("ARCHITECTURE.md", readme)
        for name in ("CORE.md", "RESEARCH.md", "PHYSICS_RESEARCH_ROADMAP.md", "OPERATIONS.md"):
            self.assertTrue((self.root / "maintenance" / name).is_file())

    def test_release_manifest_contains_only_executable_asset(self) -> None:
        dist = Path(self.temp.name) / "dist"
        dist.mkdir()
        executable = dist / EXECUTABLE_NAME
        executable.write_bytes(b"release executable")

        subprocess.run(
            [
                sys.executable, str(SOURCE_ROOT / "scripts/build_release.py"),
                "--version", CURRENT_VERSION, "--dist", str(dist),
                "--build-info", str(self.stable_build_info()),
            ],
            cwd=SOURCE_ROOT, check=True, capture_output=True,
        )

        manifest = json.loads((dist / "release-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest),
            {"version", "build_identity", "launcher_min_version", "event_schema", "authenticode", "windows_exe"},
        )
        self.assertEqual(manifest["authenticode"], {"status": "unsigned"})
        self.assertIn("build_id", manifest["build_identity"])
        self.assertEqual(manifest["build_identity"]["channel"], "stable")
        self.assertEqual(manifest["windows_exe"]["file"], EXECUTABLE_NAME)
        self.assertEqual(
            manifest["windows_exe"]["url"],
            f"https://github.com/DawnDust/workflow-monitor/releases/download/v{CURRENT_VERSION}/{EXECUTABLE_NAME}",
        )

    def test_release_manifest_can_describe_sbom_and_authenticode(self) -> None:
        dist = Path(self.temp.name) / "dist"
        dist.mkdir()
        (dist / EXECUTABLE_NAME).write_bytes(b"signed executable")
        sbom = dist / "workflow-monitor.spdx.json"
        sbom.write_text('{"spdxVersion":"SPDX-2.3"}\n', encoding="utf-8")
        signature = dist / "authenticode.json"
        signature.write_text(json.dumps({
            "status": "signed", "subject": "CN=Workflow Monitor", "thumbprint": "ABCD",
        }), encoding="utf-8")

        subprocess.run([
            sys.executable, str(SOURCE_ROOT / "scripts/build_release.py"),
            "--version", CURRENT_VERSION, "--dist", str(dist),
            "--build-info", str(self.stable_build_info()),
            "--sbom", str(sbom), "--authenticode-json", str(signature),
        ], cwd=SOURCE_ROOT, check=True, capture_output=True)
        manifest = json.loads((dist / "release-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["sbom"]["file"], sbom.name)
        self.assertEqual(manifest["sbom"]["format"], "spdx-json")
        self.assertEqual(manifest["authenticode"]["status"], "signed")

    def test_release_manifest_rejects_test_channel_build(self) -> None:
        dist = Path(self.temp.name) / "test-dist"
        dist.mkdir()
        (dist / EXECUTABLE_NAME).write_bytes(b"test executable")
        build_info = Path(self.temp.name) / "test-build-info.json"
        build_info.write_text(json.dumps({"build_id": "test-build", "channel": "test"}), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable, str(SOURCE_ROOT / "scripts/build_release.py"),
                "--version", CURRENT_VERSION, "--dist", str(dist),
                "--build-info", str(build_info),
            ],
            cwd=SOURCE_ROOT, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test-channel build", result.stderr)
        self.assertFalse((dist / "release-manifest.json").exists())

    def test_release_tag_must_match_application_version(self) -> None:
        validate_release_tag(CURRENT_VERSION, f"v{CURRENT_VERSION}")
        with self.assertRaisesRegex(RuntimeError, "tag/version mismatch"):
            validate_release_tag(CURRENT_VERSION, "v999.0.0")
        with self.assertRaisesRegex(RuntimeError, "missing from CHANGELOG"):
            validate_release_tag("999.0.0", "v999.0.0")

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
            "build_input_fingerprint": repository["build_input_fingerprint"],
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
            "build_input_fingerprint": repository["build_input_fingerprint"],
            "source_tree": repository["source_tree"],
            "source_tree_algorithm": repository["source_tree_algorithm"],
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

    def test_repository_fingerprint_ignores_documentation_only_commits(self) -> None:
        source = self.root / "project_hooks/__init__.py"
        source.parent.mkdir(parents=True)
        source.write_text("VERSION = 1\n", encoding="utf-8")
        readme = self.root / "README.md"
        readme.write_text("first\n", encoding="utf-8")
        self.commit("baseline")
        before = repository_source_identity(self.root)

        readme.write_text("second\n", encoding="utf-8")
        self.commit("documentation only")
        after = repository_source_identity(self.root)

        self.assertNotEqual(before["source_commit"], after["source_commit"])
        self.assertEqual(before["build_input_fingerprint"], after["build_input_fingerprint"])

    def test_project_root_is_discovered_from_descendant(self) -> None:
        self.init()
        nested = self.root / "resources/analysis/deep"
        nested.mkdir(parents=True)
        self.assertEqual(discover_project_root(nested), self.root.resolve())

    def test_frozen_cli_prefers_working_project_over_executable_directory(self) -> None:
        portable = self.root / "dist"
        portable.mkdir()
        args = ["program", "migrate-analysis"]
        with (
            patch.object(windows_main, "is_frozen", return_value=True),
            patch.object(windows_main, "portable_root", return_value=portable),
            patch.object(windows_main, "run_selected_executable", return_value=None),
            patch("project_hooks.cli.discover_project_root", return_value=self.root),
            patch("project_hooks.cli.main", return_value=0) as cli_main,
        ):
            self.assertEqual(windows_main.main(args), 0)
        cli_main.assert_called_once_with(["--project", str(self.root), *args])

    def test_frozen_cli_keeps_explicit_project_and_portable_fallback(self) -> None:
        portable = self.root / "portable"
        portable.mkdir()
        explicit = self.root / "explicit"
        explicit.mkdir()
        with (
            patch.object(windows_main, "is_frozen", return_value=True),
            patch.object(windows_main, "portable_root", return_value=portable),
            patch.object(windows_main, "run_selected_executable", return_value=None),
            patch("project_hooks.cli.discover_project_root", return_value=None),
            patch("project_hooks.cli.main", return_value=0) as cli_main,
        ):
            self.assertEqual(
                windows_main.main(["--project", str(explicit), "context"]), 0,
            )
            cli_main.assert_called_once_with(["--project", str(explicit), "context"])

        with (
            patch.object(windows_main, "is_frozen", return_value=True),
            patch.object(windows_main, "portable_root", return_value=portable),
            patch.object(windows_main, "run_selected_executable", return_value=None),
            patch("project_hooks.cli.discover_project_root", return_value=None),
            patch("project_hooks.cli.main", return_value=0) as cli_main,
        ):
            self.assertEqual(windows_main.main(["context"]), 0)
            cli_main.assert_called_once_with(["--project", str(portable), "context"])

    def test_frozen_init_uses_requested_path_without_portable_override(self) -> None:
        portable = self.root / "portable"
        target = self.root / "new-project"
        with (
            patch.object(windows_main, "is_frozen", return_value=True),
            patch.object(windows_main, "portable_root", return_value=portable),
            patch.object(windows_main, "run_selected_executable", return_value=None),
            patch("project_hooks.cli.main", return_value=0) as cli_main,
        ):
            self.assertEqual(windows_main.main(["init", str(target)]), 0)
        cli_main.assert_called_once_with(["init", str(target)])

    def test_portable_bootstrap_initializes_empty_folder_and_local_hook(self) -> None:
        portable = Path(self.temp.name) / "portable"
        portable.mkdir()
        executable = portable / EXECUTABLE_NAME
        executable.write_bytes(b"placeholder")

        initialized = prepare_portable_project(portable, executable)

        self.assertTrue(initialized)
        self.assertTrue((portable / ".git").is_dir())
        self.assertTrue((portable / ".workflow-monitor/workflow.json").is_file())
        self.assertTrue((portable / ".agents/plugins/workflow-monitor/plugin.json").is_file())
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
            (root / ".workflow-monitor").mkdir()
            (root / ".workflow-monitor/partial.json").write_text(version, encoding="utf-8")
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
        installation = json.loads((self.root / ".workflow-monitor/installation.json").read_text(encoding="utf-8"))
        self.assertEqual(installation["application_version"], CURRENT_VERSION)
        self.assertTrue(installation["build_identity"]["build_id"])
        self.assertTrue((self.root / ".project_hooks/maintenance.sqlite3").is_file())

    def test_project_update_promotes_legacy_main_goal_version_once(self) -> None:
        self.init("1.6.1")
        journal = self.root / "maintenance/events.jsonl"
        profile = new_event(
            "project.profile_updated", branch="main", task_id="legacy-profile",
            payload={"description": "legacy project", "big_goal": "legacy goal"},
            timezone="Asia/Shanghai",
        )
        profile["schema_version"] = 3
        legacy = new_event(
            "project_state.updated", branch="main", task_id="legacy-state",
            payload={
                "status": "completed", "main_goal_version": "v19", "goal": "legacy goal",
                "judgment": "done", "breakpoint": "done", "next_steps": [], "blocker": "none",
            },
            timezone="Asia/Shanghai",
        )
        legacy["schema_version"] = 3
        append_events(journal, self.root / ".project_hooks", [profile, legacy])
        self.commit()
        apply_project_update(self.root, CURRENT_VERSION)
        events = load_events(journal)
        promoted = [
            event for event in events
            if event["event_type"] == "project.profile_updated"
            and event["payload"].get("migration_source_event_id") == legacy["event_id"]
        ]
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0]["payload"]["main_goal_version"], "v19")
        self.assertEqual(promoted[0]["payload"]["description"], "legacy project")
        self.assertEqual(promoted[0]["payload"]["big_goal"], "legacy goal")

    def test_project_update_leaves_retired_workbench_events_untouched(self) -> None:
        self.init("1.3.0")
        journal = self.root / "maintenance/events.jsonl"
        append_events(journal, self.root / ".project_hooks", [new_event(
            "workbench.entry_upserted", branch="main", task_id=None, timezone="Asia/Shanghai",
            payload={
                "item_id": "old-validation", "item_type": "validation_method",
                "title": "旧验证", "summary": "检查结果", "path": "workbench/local/old.md",
                "content_sha256": "0" * 64, "tags": [], "status": "active", "origin": "local",
            },
        )])
        self.commit()
        apply_project_update(self.root, CURRENT_VERSION)
        events = load_events(journal)
        self.assertEqual(len([event for event in events if event["event_type"] == "workbench.entry_upserted"]), 1)
        self.assertFalse(any(event["event_type"] == "workbench.taxonomy_migrated" for event in events))

    def test_update_preserves_retired_workbench_package_files(self) -> None:
        self.init("1.3.0")
        imported = self.root / "workbench/imported"
        body = b"# Item\n"
        digest = hashlib.sha256(body).hexdigest()
        valid = imported / "a-valid/1.0"
        invalid = imported / "z-invalid/1.0"
        for directory in (valid, invalid):
            (directory / "items").mkdir(parents=True)
            (directory / "items/item.md").write_bytes(body)
        valid_manifest = {
            "schema_version": 1, "package_id": "a-valid", "name": "Valid", "version": "1.0",
            "author": "A", "items": [{"item_id": "item", "item_type": "prompt", "title": "I",
            "summary": "I", "file": "items/item.md", "tags": [], "reference": "", "sha256": digest}],
        }
        invalid_manifest = {**valid_manifest, "package_id": "z-invalid", "name": "Invalid"}
        invalid_manifest["items"] = [{**valid_manifest["items"][0], "sha256": "0" * 64}]
        (valid / "manifest.json").write_text(json.dumps(valid_manifest), encoding="utf-8")
        (invalid / "manifest.json").write_text(json.dumps(invalid_manifest), encoding="utf-8")
        before = (valid / "manifest.json").read_bytes()
        self.commit()
        apply_project_update(self.root, CURRENT_VERSION)
        self.assertEqual((valid / "manifest.json").read_bytes(), before)

    def test_customized_managed_file_is_preserved_and_reported(self) -> None:
        self.init()
        readme = self.root / "maintenance/README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\n项目自定义内容。\n", encoding="utf-8")
        customized = readme.read_bytes()
        roadmap = self.root / "maintenance/PHYSICS_RESEARCH_ROADMAP.md"
        roadmap.write_text(roadmap.read_text(encoding="utf-8") + "\n项目自定义路线图说明。\n", encoding="utf-8")
        customized_roadmap = roadmap.read_bytes()
        self.commit()
        result = apply_project_update(self.root, CURRENT_VERSION)
        self.assertEqual(readme.read_bytes(), customized)
        self.assertEqual(roadmap.read_bytes(), customized_roadmap)
        self.assertIn("maintenance/README.md", result["conflicts"])
        self.assertIn("maintenance/PHYSICS_RESEARCH_ROADMAP.md", result["conflicts"])
        self.assertTrue(
            (self.root / f".project_hooks/update-conflicts/{CURRENT_VERSION}/maintenance/README.md").is_file()
        )
        self.assertTrue(
            (self.root / f".project_hooks/update-conflicts/{CURRENT_VERSION}/maintenance/PHYSICS_RESEARCH_ROADMAP.md").is_file()
        )
        self.commit("record customized template")
        repeated = apply_project_update(self.root, CURRENT_VERSION)
        self.assertEqual(readme.read_bytes(), customized)
        self.assertEqual(roadmap.read_bytes(), customized_roadmap)
        self.assertIn("maintenance/README.md", repeated["conflicts"])
        self.assertIn("maintenance/PHYSICS_RESEARCH_ROADMAP.md", repeated["conflicts"])

    def test_update_installs_and_records_new_physics_roadmap_template(self) -> None:
        self.init("2.0.0")
        roadmap = self.root / "maintenance/PHYSICS_RESEARCH_ROADMAP.md"
        roadmap.unlink()
        installation_path = self.root / ".workflow-monitor/installation.json"
        installation = json.loads(installation_path.read_text(encoding="utf-8"))
        installation["template_version"] = 13
        installation["managed_files"].pop("maintenance/PHYSICS_RESEARCH_ROADMAP.md")
        installation_path.write_text(json.dumps(installation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.commit()

        result = apply_project_update(self.root, CURRENT_VERSION)

        installation = json.loads((self.root / ".workflow-monitor/installation.json").read_text(encoding="utf-8"))
        self.assertIn("maintenance/PHYSICS_RESEARCH_ROADMAP.md", result["changed"])
        self.assertTrue(roadmap.is_file())
        self.assertEqual(
            installation["managed_files"]["maintenance/PHYSICS_RESEARCH_ROADMAP.md"]["hash"],
            hashlib.sha256(roadmap.read_bytes()).hexdigest(),
        )

    def test_update_removes_source_only_architecture_entry_from_default_index(self) -> None:
        self.init("2.0.0")
        readme = self.root / "maintenance/README.md"
        previous = readme.read_text(encoding="utf-8").replace(
            "- [OPERATIONS.md](OPERATIONS.md)：安装、升级、恢复、诊断、交付与发布。\n",
            "- [OPERATIONS.md](OPERATIONS.md)：安装、升级、恢复、诊断、交付与发布。\n"
            "- `ARCHITECTURE.md`：代码边界和读写模型架构。\n",
        )
        readme.write_text(previous, encoding="utf-8", newline="\n")
        installation_path = self.root / ".workflow-monitor/installation.json"
        installation = json.loads(installation_path.read_text(encoding="utf-8"))
        installation["template_version"] = 14
        installation["managed_files"]["maintenance/README.md"] = {
            "hash": hashlib.sha256(previous.encode("utf-8")).hexdigest(),
            "customized": False,
        }
        installation_path.write_text(json.dumps(installation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.commit()

        result = apply_project_update(self.root, CURRENT_VERSION)

        updated = json.loads(installation_path.read_text(encoding="utf-8"))
        self.assertIn("maintenance/README.md", result["changed"])
        self.assertEqual(updated["template_version"], TEMPLATE_VERSION)
        self.assertNotIn("ARCHITECTURE.md", readme.read_text(encoding="utf-8"))

    def test_update_migrates_default_and_preserves_custom_core_read_order(self) -> None:
        self.init("1.6.1")
        config_path = self.root / ".workflow-monitor/workflow.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["core_read_order"] = ["maintenance/README.md"]
        config["context_command"] = ".\\workflow-monitor.exe context --format markdown"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.commit()
        apply_project_update(self.root, CURRENT_VERSION)
        migrated = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["core_read_order"], ["maintenance/CORE.md"])
        self.assertEqual(migrated["context_command"], ".\\workflow-monitor.exe context --view brief --format markdown")

        self.commit("migrate default core order")
        custom = json.loads(config_path.read_text(encoding="utf-8"))
        custom["core_read_order"] = ["custom/RULES.md", "maintenance/README.md", "custom/RULES.md"]
        custom["context_command"] = "custom-context --format json"
        (self.root / "custom").mkdir()
        (self.root / "custom/RULES.md").write_text("# Rules\n", encoding="utf-8")
        config_path.write_text(json.dumps(custom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.commit("customize core order")
        apply_project_update(self.root, CURRENT_VERSION)
        preserved = json.loads(config_path.read_text(encoding="utf-8"))["core_read_order"]
        self.assertEqual(preserved, ["maintenance/CORE.md", "custom/RULES.md", "maintenance/README.md"])
        self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["context_command"], "custom-context --format json")

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

    def test_update_is_the_only_write_command_allowed_across_template_versions(self) -> None:
        update = build_parser().parse_args(["update"])
        check = build_parser().parse_args(["update", "--check"])
        start = build_parser().parse_args([
            "start", "--kind", "code", "--scope", "test", "--acceptance", "works",
        ])
        with patch.object(cli_commands, "ROOT", self.root):
            with patch.object(cli_commands, "installed_project_version", return_value="2.0.0"):
                cli_commands.assert_project_version_compatible(update)
                cli_commands.assert_project_version_compatible(check)
                self.assertFalse(cli_commands.command_requires_project_lock(update))
                self.assertFalse(cli_commands.command_requires_project_lock(check))
                self.assertTrue(cli_commands.command_requires_project_lock(start))
                with self.assertRaisesRegex(cli_commands.WorkflowError, "模板版本为 2.0.0"):
                    cli_commands.assert_project_version_compatible(start)

    def test_frozen_update_caches_versioned_executable_and_writes_pointer(self) -> None:
        self.init("0.9.0")
        self.commit()
        manifest = self.exe_release()
        real_run = subprocess.run

        def run_portable(command, *args, **kwargs):
            if str(command[0]).endswith(EXECUTABLE_NAME):
                self.assertEqual(kwargs["env"].get("PROJECT_HOOKS_UPDATE_CHILD"), "1")
                self.assertIn("--minimum-event-schema", command)
                child_commands.append(command)
                selected = Path(command[command.index("--executable-path") + 1])
                result = apply_project_update(self.root, CURRENT_VERSION, executable_path=selected)
                return subprocess.CompletedProcess(
                    args=command, returncode=0, stdout=json.dumps(result), stderr="",
                )
            return real_run(command, *args, **kwargs)

        child_commands = []
        with patch("project_hooks.infrastructure.system.updater.is_frozen", return_value=True):
            with patch("project_hooks.infrastructure.system.updater.subprocess.run", side_effect=run_portable):
                result = run_update(self.root, manifest_url=manifest.as_uri())

        pointer = json.loads((
            self.root / ".project_hooks/runtime/current.json"
        ).read_text(encoding="utf-8"))
        selected = Path(pointer["executable"])
        self.assertEqual(result["status"], "updated")
        self.assertTrue(selected.is_file())
        self.assertEqual(selected.read_bytes(), b"test executable")
        self.assertTrue(selected.is_relative_to((self.root / ".project_hooks/runtime").resolve()))
        self.assertEqual(child_commands[0][0], str(selected))
        self.assertIn("--executable-path", child_commands[0])

    def test_same_version_different_build_is_reinstalled(self) -> None:
        self.init()
        self.commit()
        manifest = self.exe_release(build_id="release-build")
        real_run = subprocess.run

        def run_portable(command, *args, **kwargs):
            if str(command[0]).endswith(EXECUTABLE_NAME):
                selected = Path(command[command.index("--executable-path") + 1])
                result = apply_project_update(self.root, CURRENT_VERSION, executable_path=selected)
                return subprocess.CompletedProcess(
                    args=command, returncode=0, stdout=json.dumps(result), stderr="",
                )
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

    def test_legacy_codex_project_remains_discoverable_and_updatable(self) -> None:
        self.init("1.6.1")
        legacy_dir = self.root / ".codex"
        legacy_dir.mkdir(exist_ok=True)
        (self.root / ".workflow-monitor/workflow.json").replace(legacy_dir / "project-maintenance-workflow.json")
        (self.root / ".workflow-monitor/installation.json").replace(legacy_dir / "project-maintenance-installation.json")
        (self.root / ".workflow-monitor").rmdir()
        self.commit("legacy codex layout")

        self.assertEqual(discover_project_root(self.root), self.root.resolve())
        result = apply_project_update(self.root, CURRENT_VERSION)
        self.assertEqual(result["status"], "updated")
        self.assertTrue((legacy_dir / "project-maintenance-workflow.json").is_file())
        self.assertTrue((legacy_dir / "project-maintenance-installation.json").is_file())
        self.assertFalse((self.root / ".workflow-monitor").exists())
        self.assertTrue((self.root / ".agents/plugins/workflow-monitor/plugin.json").is_file())


if __name__ == "__main__":
    unittest.main()
