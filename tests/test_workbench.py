from __future__ import annotations

import unittest

from project_hooks.workbench import (
    WorkbenchError,
    external_tools_from_events,
    normalize_external_tool,
)


class ExternalWorkbenchTests(unittest.TestCase):
    def test_projection_upserts_and_changes_status_without_deleting_history(self) -> None:
        events = [
            {
                "event_id": "e1", "occurred_at": "2026-01-01T00:00:00+00:00",
                "event_type": "workbench.external_upserted", "task_id": "task-1",
                "payload": {
                    "tool_id": "obsidian", "name": "Obsidian", "kind": "notes",
                    "purpose": "管理笔记", "usage_hint": "整理研究笔记", "reference": "Obsidian",
                    "status": "active",
                },
            },
            {
                "event_id": "e2", "occurred_at": "2026-01-02T00:00:00+00:00",
                "event_type": "workbench.external_status_changed", "task_id": "task-1",
                "payload": {"tool_id": "obsidian", "status": "paused", "note": "暂不使用"},
            },
        ]
        tools = external_tools_from_events(events)
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["status"], "paused")
        self.assertEqual(tools[0]["event_id"], "e2")

    def test_external_tool_rejects_absolute_paths_and_credentials(self) -> None:
        base = {
            "tool_id": "zotero", "name": "Zotero", "kind": "literature",
            "purpose": "管理文献", "usage_hint": "维护文献库", "status": "active",
        }
        with self.assertRaisesRegex(WorkbenchError, "绝对路径"):
            normalize_external_tool({**base, "reference": r"C:\\Users\\name\\Zotero"})
        with self.assertRaisesRegex(WorkbenchError, "凭据"):
            normalize_external_tool({**base, "reference": "api_key=secret"})

    def test_external_tool_update_preserves_unsupplied_fields(self) -> None:
        current = normalize_external_tool({
            "tool_id": "mathematica", "name": "Mathematica", "kind": "computation",
            "purpose": "逻辑符号推理", "usage_hint": "用于符号计算", "reference": "Wolfram Mathematica",
            "status": "active",
        })
        updated = normalize_external_tool(
            {"tool_id": "mathematica", "purpose": "符号推理与计算"}, current=current,
        )
        self.assertEqual(updated["name"], "Mathematica")
        self.assertEqual(updated["usage_hint"], "用于符号计算")
        self.assertEqual(updated["purpose"], "符号推理与计算")


if __name__ == "__main__":
    unittest.main()
