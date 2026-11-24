from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_hooks.infrastructure.persistence.project_registry import ProjectRegistry
from project_hooks.infrastructure.system.agent_hooks import handle_hook_payload, handle_stdin
from project_hooks.infrastructure.system.project_manager import PLUGIN_PRE_TOOL_GUARD_PS1


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
            ".worktrees/exp/maintenance/events.jsonl",
            ".worktrees/exp/.project_hooks/active-task.json",
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

        inside_safe = handle_hook_payload(
            {
                "hookEventName": "PreToolUse",
                "cwd": str(self.root),
                "toolCall": {"name": "edit_file", "args": {"Path": str(self.root / "program/run.py")}},
            },
            project_finder=lambda _path: self.root,
            context_loader=lambda _path: "",
        )
        self.assertEqual(inside_safe, {"decision": "allow"})

        outside = handle_hook_payload(
            {
                "hookEventName": "PreToolUse",
                "cwd": str(self.root),
                "toolCall": {"name": "edit_file", "args": {"Path": str(Path(self.temp.name) / "artifacts/plan.md")}},
            },
            project_finder=lambda _path: self.root,
            context_loader=lambda _path: "",
        )
        self.assertEqual(outside, {"decision": "ask"})

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
        self.assertEqual(normal, {"decision": "ask"})

    def test_pre_tool_use_outside_project_and_malformed_return_ask(self) -> None:
        outside = handle_hook_payload(
            {
                "hookEventName": "PreToolUse",
                "cwd": str(self.root / "elsewhere"),
                "toolCall": {"name": "write_file", "args": {"Path": str(self.root / "maintenance/events.jsonl")}},
            },
            project_finder=lambda _path: None,
            context_loader=lambda _path: "",
        )
        self.assertEqual(outside, {"decision": "ask"})

        malformed = handle_hook_payload(
            {"hookEventName": "PreToolUse", "cwd": str(self.root)},
            project_finder=lambda _path: self.root,
            context_loader=lambda _path: "",
        )
        self.assertEqual(malformed, {"decision": "ask"})

    @unittest.skipUnless(os.name == "nt", "PowerShell guard script test requires Windows")
    def test_powershell_pre_tool_guard_script_matches_contract(self) -> None:
        self.assertTrue(PLUGIN_PRE_TOOL_GUARD_PS1.isascii())
        config_dir = self.root / ".workflow-monitor"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "workflow.json").write_text('{"version": 3}\n', encoding="utf-8")
        ps1 = self.root / "pre-tool-guard.ps1"
        ps1.write_text(PLUGIN_PRE_TOOL_GUARD_PS1, encoding="ascii")

        def run_ps1(raw_input: str) -> dict:
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ps1),
                ],
                input=raw_input,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout.strip())

        protected_file = run_ps1(json.dumps({
            "hookEventName": "PreToolUse",
            "cwd": str(self.root),
            "toolCall": {"name": "write_file", "args": {"Path": str(self.root / "maintenance/events.jsonl")}},
        }))
        self.assertEqual(protected_file["decision"], "deny")
        self.assertIn("禁止直接修改", protected_file["reason"])

        wt_protected = run_ps1(json.dumps({
            "hookEventName": "PreToolUse",
            "cwd": str(self.root),
            "toolCall": {"name": "edit_file", "args": {"Path": str(self.root / ".worktrees/exp/.project_hooks/active-task.json")}},
        }))
        self.assertEqual(wt_protected["decision"], "deny")

        safe_file = run_ps1(json.dumps({
            "hookEventName": "PreToolUse",
            "cwd": str(self.root),
            "toolCall": {"name": "edit_file", "args": {"Path": str(self.root / "program/run.py")}},
        }))
        self.assertEqual(safe_file, {"decision": "allow"})

        outside_file = run_ps1(json.dumps({
            "hookEventName": "PreToolUse",
            "cwd": str(self.root),
            "toolCall": {"name": "write_file", "args": {"Path": str(Path(self.temp.name) / "artifacts/plan.md")}},
        }))
        self.assertEqual(outside_file, {"decision": "ask"})

        destructive_cmd = run_ps1(json.dumps({
            "hookEventName": "PreToolUse",
            "cwd": str(self.root),
            "toolCall": {"name": "run_command", "args": {"CommandLine": "Remove-Item .project_hooks/active-task.json"}},
        }))
        self.assertEqual(destructive_cmd["decision"], "deny")
        self.assertIn("禁止通过终端命令", destructive_cmd["reason"])

        high_risk_cmd = run_ps1(json.dumps({
            "hookEventName": "PreToolUse",
            "cwd": str(self.root),
            "toolCall": {"name": "run_command", "args": {"CommandLine": "git push origin main"}},
        }))
        self.assertEqual(high_risk_cmd["decision"], "force_ask")
        self.assertIn("必须获得用户明确确认", high_risk_cmd["reason"])

        normal_cmd = run_ps1(json.dumps({
            "hookEventName": "PreToolUse",
            "cwd": str(self.root),
            "toolCall": {"name": "run_command", "args": {"CommandLine": "git status"}},
        }))
        self.assertEqual(normal_cmd, {"decision": "ask"})

        outside_project = run_ps1(json.dumps({
            "hookEventName": "PreToolUse",
            "cwd": str(Path(self.temp.name)),
            "toolCall": {"name": "write_file", "args": {"Path": str(self.root / "maintenance/events.jsonl")}},
        }))
        self.assertEqual(outside_project, {"decision": "ask"})

        self.assertEqual(run_ps1("{not-json"), {"decision": "ask"})

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
