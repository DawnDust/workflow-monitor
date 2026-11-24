import json
import unittest
from pathlib import Path

from project_hooks.infrastructure.system.project_manager import (
    AGENTS_BLOCK,
    MAINTENANCE_RESEARCH,
    default_config,
    template_files,
)


class ProjectTemplateTests(unittest.TestCase):
    def test_generated_guides_include_resource_filing_habits(self):
        files = template_files()
        agents = AGENTS_BLOCK
        research = files["maintenance/RESEARCH.md"]

        for content in (agents, research):
            self.assertIn("sparks", content)
            self.assertIn("plans", content)
            self.assertIn("tutorials", content)
            self.assertIn("program/", content)
            self.assertTrue("不登记为资料" in content or "不要登记为资料" in content)
            self.assertIn("catalog ID", content)
            self.assertIn("catalog folder index", content)
            self.assertIn("询问用户", content)
        self.assertIn("program/", research)
        self.assertIn("program/.gitkeep", files)
        self.assertNotIn("resources/analysis/.gitkeep", files)
        self.assertEqual(research, MAINTENANCE_RESEARCH)
        self.assertIn("## AI 代理简短调用", agents)
        self.assertNotIn("## Codex 简短调用", agents)
        self.assertEqual(agents.count("catalog folder index"), 1)

    def test_antigravity_plugin_templates_are_valid(self):
        files = template_files()
        plugin = json.loads(files[".agents/plugins/workflow-monitor/plugin.json"])
        hooks = json.loads(files[".agents/plugins/workflow-monitor/hooks.json"])
        self.assertEqual(plugin["name"], "workflow-monitor")
        session = hooks["workflow-monitor-session"]
        guard = hooks["workflow-monitor-guard"]
        self.assertIn("SessionStart", session)
        self.assertEqual(session["SessionStart"][0]["timeout"], 30)
        self.assertIn("PreToolUse", guard)
        for group in guard["PreToolUse"]:
            self.assertEqual(len(group["hooks"]), 1)
            self.assertIn("hooks\\pre-tool-guard.ps1", group["hooks"][0]["command"])
            self.assertEqual(group["hooks"][0]["timeout"], 10)
        guard_ps1 = files[".agents/plugins/workflow-monitor/hooks/pre-tool-guard.ps1"]
        self.assertTrue(guard_ps1.isascii())
        for skill_name in ("workflow-lifecycle", "research-catalog", "multi-worktree-review"):
            skill_text = files[f".agents/plugins/workflow-monitor/skills/{skill_name}/SKILL.md"]
            self.assertTrue(skill_text.startswith("---\n"))
            self.assertIn(f"name: {skill_name}", skill_text)
            self.assertIn("description:", skill_text)

    def test_physics_roadmap_is_optional_guidance_and_managed_template(self):
        files = template_files()
        roadmap = files["maintenance/PHYSICS_RESEARCH_ROADMAP.md"]
        source = (Path(__file__).parents[2] / "maintenance" / "PHYSICS_RESEARCH_ROADMAP.md").read_text(encoding="utf-8")

        self.assertEqual(roadmap, source)
        self.assertIn("PHYSICS_RESEARCH_ROADMAP.md", files["maintenance/README.md"])
        self.assertNotIn("`ARCHITECTURE.md`", files["maintenance/README.md"])
        for name in ("CORE.md", "RESEARCH.md", "PHYSICS_RESEARCH_ROADMAP.md", "OPERATIONS.md"):
            self.assertIn(f"maintenance/{name}", files)
        self.assertIn("## 路线总览", roadmap)
        self.assertIn("$$", roadmap)
        self.assertIn("理论物理任务", AGENTS_BLOCK)
        self.assertIn("按需阅读 `maintenance/PHYSICS_RESEARCH_ROADMAP.md`", AGENTS_BLOCK)
        self.assertIn("不是强制阶段、验收项或门禁", AGENTS_BLOCK)
        self.assertEqual(default_config()["core_read_order"], ["maintenance/CORE.md"])


if __name__ == "__main__":
    unittest.main()
