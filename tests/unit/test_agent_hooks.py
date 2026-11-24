from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_hooks.infrastructure.persistence.project_registry import ProjectRegistry
from project_hooks.infrastructure.system.agent_hooks import handle_hook_payload, handle_stdin


class AgentHooksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_session_start_injects_context_and_degrades_outside_project(self) -> None:
        payload = {"hookEventName": "SessionStart", "cwd": str(self.root)}
        result = handle_hook_payload(
            payload,
            project_finder=lambda path: self.root if path == self.root else None,
            context_loader=lambda _path: "# 工作上下文\n- 分支：main\n",
        )
        self.assertEqual(
            result,
            {"injectSteps": [{"systemMessage": {"systemMessage": "# 工作上下文\n- 分支：main"}}]},
        )

        outside = handle_hook_payload(
            {"hookEventName": "SessionStart", "cwd": str(self.root / "elsewhere")},
            project_finder=lambda _path: None,
            context_loader=lambda _path: "ignored",
        )
        self.assertEqual(outside, {})

        broken = handle_hook_payload(
            payload,
            project_finder=lambda _path: self.root,
            context_loader=lambda _path: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        self.assertEqual(broken, {})

    def test_pre_tool_use_guards_protected_files_only(self) -> None:
        for protected in (
            "maintenance/events.jsonl",
            ".project_hooks/maintenance.sqlite3",
            ".project_hooks/active-task.json",
            ".git/workflow-monitor/registry.sqlite3",
        ):
            decision = handle_hook_payload(
                {
                    "hookEventName": "PreToolUse",
                    "cwd": str(self.root),
                    "toolCall": {"name": "write_file", "args": {"Path": str(self.root / protected)}},
                },
                project_finder=lambda _path: self.root,
                context_loader=lambda _path: "",
            )
            self.assertEqual(decision["decision"], "deny", protected)

        for allowed in (
            str(self.root / "program/run.py"),
            str(Path(self.temp.name) / "artifacts/plan.md"),
        ):
            decision = handle_hook_payload(
                {
                    "hookEventName": "PreToolUse",
                    "cwd": str(self.root),
                    "toolCall": {"name": "edit_file", "args": {"Path": allowed}},
                },
                project_finder=lambda _path: self.root,
                context_loader=lambda _path: "",
            )
            self.assertEqual(decision, {}, allowed)

    def test_pre_tool_use_guards_commands_without_over_allowing(self) -> None:
        deny_cmd = handle_hook_payload(
            {
                "hookEventName": "PreToolUse",
                "cwd": str(self.root),
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "Remove-Item .project_hooks/active-task.json"},
                },
            },
            project_finder=lambda _path: self.root,
            context_loader=lambda _path: "",
        )
        self.assertEqual(deny_cmd["decision"], "deny")

        for risky in (
            "git push origin main",
            "git merge experiment/x",
            "gh pr merge 12",
            ".\\workflow-monitor.exe task abandon --reason test",
            ".\\workflow-monitor.exe db rebuild",
        ):
            decision = handle_hook_payload(
                {
                    "hookEventName": "PreToolUse",
                    "cwd": str(self.root),
                    "toolCall": {"name": "run_command", "args": {"CommandLine": risky}},
                },
                project_finder=lambda _path: self.root,
                context_loader=lambda _path: "",
            )
            self.assertEqual(decision["decision"], "force_ask", risky)

        normal = handle_hook_payload(
            {
                "hookEventName": "PreToolUse",
                "cwd": str(self.root),
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": ".\\workflow-monitor.exe context --view brief"},
                },
            },
            project_finder=lambda _path: self.root,
            context_loader=lambda _path: "",
        )
        self.assertEqual(normal, {})

    def test_handle_stdin_returns_empty_json_on_invalid_input(self) -> None:
        self.assertEqual(
            handle_stdin(
                project_finder=lambda _p: self.root,
                context_loader=lambda _p: "",
                stream=io.StringIO("{not-json"),
            ),
            {},
        )

    def test_project_registry_read_uses_readonly_and_immutable_fallback(self) -> None:
        db_path = self.root / "registry.sqlite3"
        registry = ProjectRegistry.create(db_path)
        registry.configure(enabled=True, unified=True)
        self.assertTrue(registry.enabled)

        real_connect = sqlite3.connect
        seen_uris: list[str] = []

        def fake_connect(target, *args, **kwargs):
            target_text = str(target)
            seen_uris.append(target_text)
            if "mode=ro" in target_text and "immutable=1" not in target_text:
                raise sqlite3.OperationalError("unable to open database file")
            return real_connect(target, *args, **kwargs)

        with patch("project_hooks.infrastructure.persistence.project_registry.sqlite3.connect", side_effect=fake_connect):
            self.assertTrue(registry.enabled)
            self.assertTrue(registry.setting("unified"))

        self.assertTrue(any("mode=ro" in uri for uri in seen_uris))
        self.assertTrue(any("immutable=1" in uri for uri in seen_uris))


if __name__ == "__main__":
    unittest.main()
