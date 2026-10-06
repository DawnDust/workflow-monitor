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
            self.assertIn("analysis/<项目>/", content)
            self.assertIn("catalog ID", content)
            self.assertIn("catalog folder index", content)
            self.assertIn("询问用户", content)
        self.assertIn("output/", research)
        self.assertEqual(research, MAINTENANCE_RESEARCH)

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
