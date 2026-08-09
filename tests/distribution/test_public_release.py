from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


class PublicReleaseSurfaceTests(unittest.TestCase):
    def test_public_governance_files_and_readmes_exist(self) -> None:
        for name in (
            "README.md",
            "README.zh-CN.md",
            "LICENSE",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "SECURITY.md",
            "SUPPORT.md",
            "mkdocs.yml",
            "requirements-docs.txt",
        ):
            self.assertTrue((ROOT / name).is_file(), name)
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("DawnDust and contributors", license_text)

    def test_readmes_are_concise_bilingual_landing_pages(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertLess(len(english.splitlines()), 100)
        self.assertLess(len(chinese.splitlines()), 100)
        for text in (english, chinese):
            self.assertIn("DawnDust/workflow-monitor", text)
            self.assertIn("docs/assets/workflow-monitor-overview.png", text)
            self.assertNotIn("project-maintenance-template", text)
        self.assertIn("User-authored content and recorded data are never translated", english)
        self.assertIn("用户填写的内容和数据记载始终保持原文", chinese)

    def test_bilingual_manual_has_symmetric_pages_and_real_images(self) -> None:
        english = {path.name for path in (ROOT / "docs/en").glob("*.md")}
        chinese = {path.name for path in (ROOT / "docs/zh").glob("*.md")}
        self.assertEqual(english, chinese)
        self.assertEqual(len(english), 7)
        for name in ("workflow-monitor-overview.png", "workflow-monitor-settings.png"):
            content = (ROOT / "docs/assets" / name).read_bytes()
            self.assertTrue(content.startswith(b"\x89PNG\r\n\x1a\n"), name)
            self.assertGreater(len(content), 100_000, name)

    def test_mkdocs_and_github_yaml_are_valid(self) -> None:
        config = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))
        self.assertEqual(config["site_url"], "https://dawndust.github.io/workflow-monitor/")
        self.assertTrue(config["strict"])
        locales = config["plugins"][1]["i18n"]["languages"]
        self.assertEqual([item["locale"] for item in locales], ["en", "zh"])
        for path in (ROOT / ".github").rglob("*.yml"):
            self.assertIsNotNone(yaml.safe_load(path.read_text(encoding="utf-8")), path)

    def test_public_runtime_urls_use_canonical_repository(self) -> None:
        paths = (
            ROOT / "scripts/build_release.py",
            ROOT / "project_hooks/infrastructure/system/updater.py",
            ROOT / "project_hooks/infrastructure/system/diagnostics.py",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertIn("DawnDust/workflow-monitor", combined)
        self.assertNotIn("project-maintenance-template", combined)
        headings = re.findall(r"^## (.+)$", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
        self.assertEqual(headings.count("Unreleased"), 1)
        self.assertIn("1.6.0", headings)


if __name__ == "__main__":
    unittest.main()
