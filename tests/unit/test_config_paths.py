from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_hooks.infrastructure.config_paths import (
    LEGACY_CONFIG_PATH,
    LEGACY_INSTALLATION_PATH,
    PRIMARY_CONFIG_PATH,
    PRIMARY_INSTALLATION_PATH,
    has_project_config,
    read_project_config,
    resolve_config_path,
    resolve_config_relative,
    resolve_installation_path,
    resolve_installation_relative,
)


class ConfigPathsTests(unittest.TestCase):
    def test_defaults_to_primary_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(has_project_config(root))
            self.assertEqual(resolve_config_relative(root), PRIMARY_CONFIG_PATH)
            self.assertEqual(resolve_config_path(root), root / PRIMARY_CONFIG_PATH)
            self.assertEqual(resolve_installation_relative(root), PRIMARY_INSTALLATION_PATH)
            self.assertEqual(resolve_installation_path(root), root / PRIMARY_INSTALLATION_PATH)

    def test_falls_back_to_legacy_codex_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_cfg = root / LEGACY_CONFIG_PATH
            legacy_cfg.parent.mkdir(parents=True)
            legacy_cfg.write_text(json.dumps({"timezone": "UTC"}), encoding="utf-8")
            (root / LEGACY_INSTALLATION_PATH).write_text(json.dumps({"template_version": 19}), encoding="utf-8")

            self.assertTrue(has_project_config(root))
            self.assertEqual(resolve_config_relative(root), LEGACY_CONFIG_PATH)
            self.assertEqual(resolve_installation_relative(root), LEGACY_INSTALLATION_PATH)
            self.assertEqual(read_project_config(root), {"timezone": "UTC"})

    def test_primary_takes_precedence_when_both_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / LEGACY_CONFIG_PATH).parent.mkdir(parents=True)
            (root / LEGACY_CONFIG_PATH).write_text('{"source":"legacy"}\n', encoding="utf-8")
            (root / PRIMARY_CONFIG_PATH).parent.mkdir(parents=True)
            (root / PRIMARY_CONFIG_PATH).write_text('{"source":"primary"}\n', encoding="utf-8")

            self.assertEqual(resolve_config_relative(root), PRIMARY_CONFIG_PATH)
            self.assertEqual(read_project_config(root), {"source": "primary"})


if __name__ == "__main__":
    unittest.main()
