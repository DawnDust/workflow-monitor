from __future__ import annotations

import unittest

from project_hooks.core.research_attention import research_attention


class ResearchAttentionTests(unittest.TestCase):
    def test_groups_orders_and_limits_deterministic_signals(self) -> None:
        context = {
            "current_stage": {"stage_id": "chapter-1", "blocker": "waiting for data"},
            "active_attempts": [
                {"attempt_id": "attempt-1", "current_step": "test", "next_step": ""},
                {"attempt_id": "attempt-2", "current_step": "", "next_step": "review"},
            ],
        }
        items = [
            {"item_id": "missing-1", "status": "missing"},
            {"item_id": "missing-2", "status": "missing"},
        ]
        relations = [
            {"relation_id": "r1", "relation_type": "contradicts"},
            {"relation_id": "r2", "relation_type": "supports"},
        ]

        signals = research_attention(context, items, relations)

        self.assertEqual([item["code"] for item in signals], [
            "REGISTERED_RESOURCE_MISSING",
            "EXPLICIT_EVIDENCE_CONTRADICTION",
            "RESEARCH_CHAPTER_BLOCKED",
        ])
        self.assertEqual(signals[0]["source_ids"], ["missing-1", "missing-2"])
        self.assertEqual(signals[1]["source_ids"], ["r1"])
        self.assertNotIn("ACTIVE_EXPLORATION_CONTINUITY_MISSING", [item["code"] for item in signals])

    def test_empty_blocker_values_and_unregistered_evidence_do_not_warn(self) -> None:
        for blocker in (None, "", "无", "无。", "none", "NONE", "暂无"):
            with self.subTest(blocker=blocker):
                signals = research_attention(
                    {"current_stage": {"stage_id": "chapter-1", "blocker": blocker}},
                    [{"item_id": "active", "status": "active"}],
                    [{"relation_id": "r1", "relation_type": "supports"}],
                )
                self.assertEqual(signals, [])

    def test_active_explorations_are_grouped_with_sources(self) -> None:
        signals = research_attention({
            "active_attempts": [
                {"attempt_id": "a1", "current_step": None, "next_step": "next"},
                {"branch": "sandbox/a2", "current_step": "now", "next_step": None},
                {"attempt_id": "complete", "current_step": "now", "next_step": "next"},
            ],
        }, [], [])
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["code"], "ACTIVE_EXPLORATION_CONTINUITY_MISSING")
        self.assertEqual(signals[0]["source_ids"], ["a1", "sandbox/a2"])
        self.assertEqual(signals[0]["target"], "research-stages")


if __name__ == "__main__":
    unittest.main()
