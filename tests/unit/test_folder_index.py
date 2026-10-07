import os
import tempfile
import unittest
from pathlib import Path

from project_hooks.infrastructure.system.folder_index import folder_index, render_folder_index


class FolderIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        (self.root / "program/outputs").mkdir(parents=True)
        (self.root / "resources/theory/notes").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_direct_contents_empty_folders_and_missing_registration(self):
        (self.root / "program/outputs/result.csv").write_text("1,2")
        (self.root / "resources/analysis").mkdir()
        (self.root / "resources/analysis/legacy.py").write_text("print('ignored')")
        (self.root / "resources/theory/notes/proof.md").write_text("proof")
        folders = [{"folder_id": "missing", "path": "resources/theory/empty", "name": "空目录", "purpose": "待加工", "status": "active"}]
        items = [{"item_id": "gone", "kind": "theory", "path": "resources/theory/vanished/a.md", "title": "Lost", "status": "active", "metadata": {}}]
        result = folder_index(self.root, items, folders)
        self.assertNotIn("program", result["entries"])
        self.assertFalse(any(path.startswith("resources/analysis") for path in result["entries"]))
        self.assertFalse(any(folder["path"].startswith("resources/analysis") for folder in result["folders"]))
        self.assertIn("resources/theory/notes", result["entries"])
        self.assertEqual(result["entries"]["resources/theory/notes"][0]["name"], "proof.md")
        self.assertFalse(result["entries"]["resources/theory/vanished"][0]["exists"])
        indexed = {f["path"]: f for f in result["folders"]}
        self.assertEqual(indexed[folders[0]["path"]]["status"], "missing")
        self.assertNotIn("program/outputs", indexed)
        self.assertNotIn("resources/outputs", indexed)
        self.assertIn("resources/tutorials", indexed)
        self.assertIn("resources/translations", indexed)
        self.assertIn("resources/plans", indexed)
        self.assertIn("待加工", render_folder_index(result))

    def test_empty_actual_folder_and_legacy_outputs_still_visible(self):
        (self.root / "resources/outputs").mkdir()
        (self.root / "resources/theory/empty").mkdir(parents=True)
        result = folder_index(self.root, [], [])
        self.assertEqual(result["entries"]["resources/theory/empty"], [])
        self.assertIn("resources/outputs", {f["path"] for f in result["folders"]})

    def test_program_and_legacy_analysis_files_are_not_indexed(self):
        path = self.root / "program/main.py"
        path.write_text("print('program')")
        legacy = self.root / "resources/analysis/legacy.py"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("print('legacy')")
        item = {"item_id": "legacy", "path": "resources/analysis/legacy.py", "kind": "simulation",
                "title": "legacy", "status": "active", "metadata": {}}
        result = folder_index(self.root, [item], [])
        self.assertFalse(any(path.startswith("program") for path in result["entries"]))
        self.assertFalse(any("main.py" in str(entry) for values in result["entries"].values() for entry in values))
        self.assertFalse(any(path.startswith("resources/analysis") for path in result["entries"]))

    def test_legacy_bundle_is_not_indexed(self):
        path = self.root / "resources/analysis/program/outputs/result.csv"
        path.parent.mkdir(parents=True)
        path.write_text("result")
        item = {"item_id": "bundle", "path": "resources/analysis/program", "kind": "simulation", "title": "计算包", "status": "active", "metadata": {"entry_type": "bundle"}}
        result = folder_index(self.root, [item], [])
        self.assertFalse(any(folder["path"].startswith("resources/analysis") for folder in result["folders"]))
        self.assertFalse(any(path.startswith("resources/analysis") for path in result["entries"]))

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
