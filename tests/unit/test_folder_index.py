import os
import tempfile
import unittest
from pathlib import Path

from project_hooks.infrastructure.system.folder_index import folder_index, render_folder_index


class FolderIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        (self.root / "resources/analysis/program/outputs").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_direct_contents_empty_folders_and_missing_registration(self):
        (self.root / "resources/analysis/program/outputs/result.csv").write_text("1,2")
        (self.root / "resources/analysis/program/.gitkeep").touch()
        (self.root / "resources/analysis/program/__pycache__").mkdir()
        (self.root / "resources/analysis/program/__pycache__/hidden.pyc").touch()
        folders = [{"folder_id": "program", "path": "resources/analysis/program", "name": "计算程序", "purpose": "计算用途", "status": "active"},
                   {"folder_id": "missing", "path": "resources/theory/empty", "name": "空目录", "purpose": "待加工", "status": "active"}]
        items = [{"item_id": "gone", "kind": "theory", "path": "resources/theory/vanished/a.md", "title": "Lost", "status": "active", "metadata": {}}]
        result = folder_index(self.root, items, folders)
        program = result["entries"]["resources/analysis/program"]
        self.assertEqual([entry["path"] for entry in program], ["resources/analysis/program/outputs"])
        file = result["entries"]["resources/analysis/program/outputs"][0]
        self.assertEqual(file["registration"], "unregistered")
        self.assertEqual(file["size"], 3)
        self.assertTrue(file["modified_at"])
        self.assertFalse(result["entries"]["resources/theory/vanished"][0]["exists"])
        indexed = {f["path"]: f for f in result["folders"]}
        self.assertEqual(indexed[folders[0]["path"]]["description"], "计算用途")
        self.assertEqual(indexed[folders[1]["path"]]["status"], "missing")
        self.assertEqual(indexed["resources/analysis/program/outputs"]["depth"], 2)
        self.assertNotIn("resources/outputs", indexed)
        self.assertIn("resources/tutorials", indexed)
        self.assertIn("resources/translations", indexed)
        self.assertIn("resources/plans", indexed)
        self.assertIn("计算用途", render_folder_index(result))

    def test_empty_actual_folder_and_legacy_outputs_still_visible(self):
        (self.root / "resources/outputs").mkdir()
        (self.root / "resources/theory/empty").mkdir(parents=True)
        result = folder_index(self.root, [], [])
        self.assertEqual(result["entries"]["resources/theory/empty"], [])
        self.assertIn("resources/outputs", {f["path"] for f in result["folders"]})

    def test_bundle_contents_are_readable_without_duplicate_registration(self):
        path = self.root / "resources/analysis/program/outputs/result.csv"
        path.write_text("result")
        item = {"item_id": "bundle", "path": "resources/analysis/program", "kind": "simulation", "title": "计算包", "status": "active", "metadata": {"entry_type": "bundle"}}
        result = folder_index(self.root, [item], [])
        self.assertNotIn("resources/analysis/program/outputs", {f["path"] for f in result["folders"]})
        self.assertEqual(result["entries"]["resources/analysis/program"][0]["registration"], "bundle-content")
        self.assertEqual(result["entries"]["resources/analysis/program/outputs"][0]["bundle_item_id"], "bundle")
        self.assertEqual(next(f for f in result["folders"] if f["path"] == item["path"])["registration"], "bundle")

    def test_external_links_are_not_scanned(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        linked = self.root / "resources/analysis/linked"
        try:
            os.symlink(outside, linked, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        result = folder_index(self.root, [], [])
        self.assertFalse(any("secret" in str(entry) for entries in result["entries"].values() for entry in entries))
        self.assertNotIn("resources/analysis/linked", {f["path"] for f in result["folders"]})

    def test_external_resource_root_is_rejected(self):
        other = Path(self.temp.name) / "other"
        other.mkdir()
        linked_root = Path(self.temp.name) / "linked-project"
        linked_root.mkdir()
        try:
            os.symlink(other, linked_root / "resources", target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        result = folder_index(linked_root, [], [])
        self.assertEqual(result["entries"], {})
        self.assertIn("error", result)
