import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from project_hooks.core.resource_gate import task_resource_gate
from project_hooks.infrastructure.system.task_resources import inspect_task_resources
from project_hooks.infrastructure.system.resource_layout import catalog_consistency_issues
from project_hooks.ui.cli.catalog_commands import CatalogError, reconcile_items


class ResourceGateTests(unittest.TestCase):
    def test_new_relation_makes_existing_missing_direct_dependency_relevant(self):
        issue = {"code": "RESOURCE_MISSING", "item_id": "input", "path": "resources/source/input.pdf",
                 "evidence": {"status": "unavailable"}, "hard": False}
        items = [{"item_id": "logical", "path": None}, {"item_id": "input", "path": issue["path"]}]
        for relation_type in ("uses", "derived-from"):
            with self.subTest(relation_type=relation_type):
                relation = {"source_id": "logical", "target_id": "input", "relation_type": relation_type}
                event = {"event_type": "catalog.relation_upserted", "payload": relation}
                gate = task_resource_gate([issue], [issue], items, [relation], [event], [], [])
                self.assertEqual(gate["pending"], [])
                self.assertEqual(gate["blockers"], [issue])
                self.assertIn("input", gate["related_item_ids"])

    def test_removed_new_relation_still_cannot_hide_its_missing_dependency(self):
        issue = {"code": "RESOURCE_MISSING", "item_id": "input", "path": "resources/source/input.pdf",
                 "evidence": {}, "hard": False}
        event = {"event_type": "catalog.relation_upserted", "payload": {
            "source_id": "logical", "target_id": "input", "relation_type": "uses"}}
        gate = task_resource_gate([issue], [issue], [], [], [event], [], [])
        self.assertEqual(gate["blockers"], [issue])

    def test_archived_required_file_cannot_be_replaced_by_a_directory(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            path = "resources/source/input.pdf"
            (root / path).mkdir(parents=True)
            item = {"item_id": "input", "kind": "literature", "path": path, "status": "archived", "metadata": {}}
            with patch("project_hooks.infrastructure.system.task_resources.catalog_consistency_issues", return_value=[]):
                gate = inspect_task_resources(root, {"declaration": {"depends_on": ["catalog:input"]}},
                                              [], [item], [], [], [])
            self.assertEqual(gate["blockers"][0]["code"], "TASK_RESOURCE_MISSING")

    def test_reconcile_checks_the_metadata_hash_that_will_be_recorded(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "resources/source").mkdir(parents=True)
            (root / "resources/source/moved.pdf").write_bytes(b"original")
            original_hash = hashlib.sha256(b"original").hexdigest()
            item = {"item_id": "input", "kind": "literature", "path": "resources/source/input.pdf",
                    "title": "Input", "summary": "", "tags_json": "[]", "source": "",
                    "created_at": "2026-09-30", "status": "active",
                    "metadata_json": json.dumps({"sha256": original_hash})}
            database = Mock()
            database.catalog_items.return_value = [item]
            runtime = SimpleNamespace(root=root, database=lambda: database,
                                      write_context=lambda: ({"task_id": "review"}, "sandbox/test"),
                                      persist=Mock(), emit=Mock())
            plan = {"items": [{"item_id": "input", "old_path": item["path"],
                               "new_path": "resources/source/moved.pdf", "action": "repair",
                               "reason": "unique_hash", "expected_hash": original_hash}], "reserved_paths": []}
            with patch("project_hooks.ui.cli.catalog_commands.reconcile_plan", return_value=plan), \
                    patch("project_hooks.ui.cli.catalog_commands.file_metadata", return_value={"sha256": "changed-hash"}):
                with self.assertRaisesRegex(CatalogError, "候选内容在核对期间变化"):
                    reconcile_items(SimpleNamespace(apply=True), runtime)
            runtime.persist.assert_not_called()
            runtime.emit.assert_not_called()

    def test_external_resource_root_is_a_hard_health_failure(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "project"
            root.mkdir()
            with patch.object(Path, "resolve", autospec=True,
                              side_effect=lambda path: Path(name) / "external" if path.name == "resources" else path):
                issues = catalog_consistency_issues(root, [], [])
            self.assertEqual(issues[0]["code"], "RESOURCE_ROOT_ESCAPE")
            self.assertTrue(issues[0]["hard"])
