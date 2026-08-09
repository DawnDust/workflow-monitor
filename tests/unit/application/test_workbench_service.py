from __future__ import annotations

import unittest
import json
import tempfile
import zipfile
from pathlib import Path

from project_hooks.application.workbench_service import (
    WORKBENCH_KINDS,
    WorkbenchError,
    external_tools_from_events,
    normalize_external_tool,
    normalize_workbench_item,
    render_workbench_context,
    sha256_file,
    taxonomy_migration_items,
    workbench_items_from_events,
)
from project_hooks.infrastructure.system.workbench_packages import (
    WorkbenchPackageError,
    ensure_workbench_directories,
    export_package,
    import_package,
    inspect_package,
    migrate_installed_package_manifests,
    workbench_package_consistency_errors,
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

    def test_unified_projection_preserves_legacy_and_marks_new_origin(self) -> None:
        events = [
            {"event_id": "e1", "occurred_at": "2026-01-01", "event_type": "workbench.external_upserted", "task_id": "t1", "payload": {
                "tool_id": "zotero", "name": "Zotero", "kind": "literature",
                "purpose": "管理文献", "usage_hint": "按需使用", "status": "active",
            }},
            {"event_id": "e2", "occurred_at": "2026-01-02", "event_type": "workbench.entry_upserted", "task_id": "t1", "payload": {
                "item_id": "dimensional-analysis", "item_type": "validation_method",
                "title": "量纲分析", "summary": "检查量纲一致性",
                "path": "workbench/local/dimensional-analysis.md", "content_sha256": "0" * 64,
                "tags": ["physics"], "status": "active", "origin": "local",
            }},
        ]
        items = workbench_items_from_events(events)
        self.assertEqual({item["item_id"] for item in items}, {"zotero", "dimensional-analysis"})
        zotero = next(item for item in items if item["item_id"] == "zotero")
        method = next(item for item in items if item["item_id"] == "dimensional-analysis")
        self.assertEqual(zotero["kind"], "tool")
        self.assertEqual(zotero["purposes"], ["literature_review"])
        self.assertEqual(method["kind"], "method")
        self.assertEqual(method["purposes"], ["validation"])
        self.assertEqual(method["review_state"], "needs_review")
        self.assertEqual(method["origin_label"], "本地创建")

    def test_v2_taxonomy_is_mutually_exclusive_and_purposes_are_controlled(self) -> None:
        for kind in WORKBENCH_KINDS:
            item = normalize_workbench_item({
                "item_id": f"item-{kind}", "kind": kind, "purposes": ["validation", "validation"],
                "title": kind, "summary": kind, "path": "" if kind == "tool" else "workbench/local/x.md",
                "content_sha256": "" if kind == "tool" else "0" * 64,
            })
            self.assertEqual(item["kind"], kind)
            self.assertEqual(item["purposes"], ["validation"])
        with self.assertRaisesRegex(WorkbenchError, "科研用途"):
            normalize_workbench_item({
                "item_id": "bad-purpose", "kind": "method", "purposes": ["guessing"],
                "title": "Bad", "summary": "Bad", "path": "workbench/local/x.md",
                "content_sha256": "0" * 64,
            })

    def test_mathematica_asymptotic_validation_splits_without_kind_overlap(self) -> None:
        common = {"title": "A", "summary": "A", "content_sha256": "0" * 64, "origin": "local"}
        tool = normalize_workbench_item({
            **common, "item_id": "mathematica", "kind": "tool", "purposes": ["computation"],
            "path": "workbench/local/tool.md",
        })
        method = normalize_workbench_item({
            **common, "item_id": "asymptotic-method", "kind": "method",
            "purposes": ["theory_derivation", "validation"], "path": "workbench/local/method.md",
        })
        workflow = normalize_workbench_item({
            **common, "item_id": "validation-workflow", "kind": "workflow",
            "purposes": ["theory_derivation", "computation", "validation"],
            "path": "workbench/local/workflow.md",
        })
        self.assertEqual([tool["kind"], method["kind"], workflow["kind"]], ["tool", "method", "workflow"])

    def test_package_round_trip_and_selected_context(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            source_root, target_root = Path(first), Path(second)
            ensure_workbench_directories(source_root)
            markdown = source_root / "workbench/local/check.md"
            markdown.write_text("# 极限检查\n\n验证 $x \\to 0$。\n", encoding="utf-8")
            item = normalize_workbench_item({
                "item_id": "limit-check", "kind": "checklist", "purposes": ["validation"],
                "title": "极限检查", "summary": "检查已知极限",
                "path": "workbench/local/check.md", "content_sha256": sha256_file(markdown),
                "tags": ["physics"], "status": "active", "origin": "local",
            })
            exported = export_package(
                source_root, [item], package_id="physics-methods", name="物理方法",
                version="1.0.0", author="Researcher",
            )
            archive = source_root / exported["output"]
            inspected = inspect_package(archive)
            self.assertEqual(inspected["items"][0]["title"], "极限检查")
            self.assertEqual(inspected["schema_version"], 2)
            self.assertEqual(inspected["items"][0]["kind"], "checklist")
            with self.assertRaisesRegex(WorkbenchPackageError, "不会被覆盖"):
                export_package(
                    source_root, [item], package_id="physics-methods", name="物理方法",
                    version="1.0.0", author="Researcher",
                )
            ensure_workbench_directories(target_root)
            imported = import_package(target_root, archive)
            self.assertEqual(imported["status"], "imported")
            context = render_workbench_context(target_root, imported["items"])
            self.assertIn("未自动验证", context)
            self.assertIn("科研用途：理论、计算或实验验证", context)
            self.assertIn("验证 $x \\to 0$", context)
            self.assertEqual(import_package(target_root, archive)["status"], "unchanged")
            event = {"event_type": "workbench.package_imported", "payload": {
                "package_id": imported["package_id"], "version": imported["version"],
                "package_sha256": imported["package_sha256"],
            }}
            self.assertEqual(workbench_package_consistency_errors(target_root, [event]), [])
            imported_markdown = target_root / imported["items"][0]["path"]
            imported_markdown.write_text("changed", encoding="utf-8")
            self.assertTrue(workbench_package_consistency_errors(target_root, [event]))
            with self.assertRaisesRegex(WorkbenchPackageError, "内容哈希不同"):
                import_package(target_root, archive)

    def test_v1_package_normalizes_to_v2_and_migration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ensure_workbench_directories(root)
            archive = root / "legacy.workbench.zip"
            body = b"# Legacy\n"
            import hashlib
            manifest = {
                "schema_version": 1, "package_id": "legacy", "name": "Legacy", "version": "1.0",
                "author": "Researcher", "items": [{
                    "item_id": "old-check", "item_type": "validation_method", "title": "Old",
                    "summary": "Old validation", "file": "items/old-check.md", "tags": [],
                    "reference": "", "sha256": hashlib.sha256(body).hexdigest(),
                }],
            }
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("manifest.json", json.dumps(manifest))
                bundle.writestr("items/old-check.md", body)
            inspected = inspect_package(archive)
            self.assertEqual(inspected["source_schema_version"], 1)
            self.assertEqual(inspected["items"][0]["kind"], "method")
            self.assertEqual(inspected["items"][0]["review_state"], "needs_review")
            imported = import_package(root, archive)
            stored = json.loads((root / imported["destination"] / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["schema_version"], 2)
            self.assertEqual(stored["source_schema_version"], 1)
            self.assertEqual(workbench_package_consistency_errors(root, [{"event_type": "workbench.package_imported", "payload": {
                "package_id": imported["package_id"], "version": imported["version"],
                "package_sha256": imported["package_sha256"],
            }}]), [])
            self.assertEqual(migrate_installed_package_manifests(root), [])
            events = [{"event_type": "workbench.entry_upserted", "payload": {
                "item_id": "old", "item_type": "other", "title": "Old", "summary": "Old",
                "path": "workbench/local/old.md", "content_sha256": "0" * 64,
            }}]
            migrated = taxonomy_migration_items(events)
            self.assertEqual(migrated[0]["kind"], "reference")
            self.assertEqual(migrated[0]["review_state"], "needs_review")
            self.assertEqual(taxonomy_migration_items([*events, {"event_type": "workbench.taxonomy_migrated", "payload": {"items": migrated}}]), [])

    def test_package_rejects_unsafe_or_unindexed_members(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "unsafe.zip"
            manifest = {
                "schema_version": 1, "package_id": "unsafe", "name": "Unsafe",
                "version": "1.0", "author": "Unknown", "items": [],
            }
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("manifest.json", json.dumps(manifest))
                bundle.writestr("../run.exe", b"bad")
            with self.assertRaises(WorkbenchPackageError):
                inspect_package(archive)


if __name__ == "__main__":
    unittest.main()
