from __future__ import annotations

import base64
import copy
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from project_hooks.cli import build_parser
from project_hooks.ui.web.bridge import (
    WEBVIEW2_DOWNLOAD_URL,
    WebDashboardBridge,
    asset_root,
)
from project_hooks.ui.web.glossary import STATUS_GLOSSARY
from project_hooks.ui.web.projections import (
    evidence_matrix,
    research_stages,
    web_snapshot,
)
from project_hooks.ui.windows.main import _hide_loader_window


def sample_snapshot() -> dict:
    return {
        "branch": "experiment/web-dashboard-v2",
        "health": {"status": "passed", "events": 3},
        "context": {
            "active_task": {"task_id": "task-1", "goal": "Build web UI"},
            "stages": [
                {"stage_id": "stage-1", "sequence": 1, "title": "Foundation", "status": "completed",
                 "goal": "Build the foundation", "summary": "Foundation completed",
                 "current_step": "Legacy fallback", "started_at": "2026-01-01 00:00:00",
                 "finished_at": "2026-01-31 23:59:59"},
                {"stage_id": "stage-2", "sequence": 2, "title": "Web", "status": "active",
                 "goal": "Build the workbench", "summary": "", "current_step": "Implement cards",
                 "started_at": "2026-02-01 00:00:00", "finished_at": None},
            ],
            "current_stage": {"stage_id": "stage-2", "sequence": 2, "title": "Web", "status": "active"},
        },
        "history": [], "decisions": [], "events": [
            {"event_id":"v5","occurred_at":"2026-01-01 00:00:00","event_type":"project_state.updated","task_id":"task-0","payload":{"main_goal_version":"v5","goal":"Foundation","judgment":"Foundation accepted"}},
            {"event_id":"v6","occurred_at":"2026-02-01 00:00:00","event_type":"project_state.updated","task_id":"task-1","payload":{"main_goal_version":"v6","goal":"Web","judgment":"Web started","status":"active"}},
            {"event_id":"v6-final","occurred_at":"2026-02-01 12:00:00","event_type":"project_state.updated","task_id":"task-1","payload":{"main_goal_version":"v6","goal":"Maintenance task","judgment":"Web delivered","status":"completed"}},
            {"event_id":"done","occurred_at":"2026-02-02 00:00:00","event_type":"task.finished","task_id":"task-1","payload":{}},
        ], "search_index": [],
        "resource_directories": [{"name":"theory","kind":"theory","path":"resources/theory","label":"理论","description":"理论、假设、定义和推导","actual_files":1,"indexed_files":1,"status":"ok"}], "external_tools": [], "workbench_items": [], "diagnostics": {},
        "task_details": {
            "task-1": {"goal": "Build web UI", "branch": "experiment/web-dashboard-v2",
                       "started_at": "2026-02-02 00:00:00", "finished_at": "2026-02-02 12:00:00"},
        },
        "attempt": {
            "attempt_id": "attempt-1", "goal": "Try WebView2", "state": "active",
            "branch": "experiment/web-dashboard-v2", "stage_id": "stage-2",
            "hypothesis": "A local bridge is sufficient", "evidence": ["contract test"],
        },
        "attempts": [{
            "attempt_id": "attempt-1", "goal": "Try WebView2", "state": "active", "track": "experiment",
            "branch": "experiment/web-dashboard-v2", "stage_id": "stage-2", "created_at": "2026-02-03 00:00:00",
            "conclusion": "", "progress": "Bridge contract works",
        }],
        "explorations": [{
            "event_id": "explore-1", "goal": "Prior experiment", "result": "negative",
            "branch": "experiment/web-dashboard-v2", "task_id": "task-1", "occurred_at":"2026-02-03 00:00:00", "evidence": "too large",
        }],
        "catalog_items": [
            {"item_id": "t1", "kind": "theory", "kind_label": "理论", "title": "Theory",
             "status": "active", "branch": "main", "task_id": "task-1", "path":"resources/theory/theory.md", "created_at":"2026-01-15 00:00:00", "tags": [], "metadata": {}},
            {"item_id": "p1", "kind": "literature", "kind_label": "文献", "title": "Paper",
             "status": "active", "branch": "main", "task_id": "", "created_at":"2026-01-15 00:00:00", "path":"resources/source/paper.pdf", "tags": [], "metadata": {}},
            {"item_id": "s1", "kind": "simulation", "kind_label": "仿真", "title": "Simulation",
             "status": "active", "branch": "main", "task_id": "missing-task", "created_at":"2025-12-01 00:00:00", "path":"resources/analysis/sim.py", "tags": [], "metadata": {}},
        ],
        "catalog_relations": [{
            "relation_id": "r1", "source_id": "p1", "target_id": "t1",
            "relation_type": "supports", "note": "registered evidence",
        }],
    }


class FakeProvider:
    def __init__(self, root: Path, *, fail_after: int | None = None, delay: float = 0):
        self.project_root = root
        self.fail_after = fail_after
        self.delay = delay
        self.loads = 0

    def load(self) -> dict:
        self.loads += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail_after is not None and self.loads > self.fail_after:
            raise RuntimeError("injected refresh failure")
        return sample_snapshot()

    def check_for_updates(self) -> dict:
        return {"status": "current"}

    def refresh_software_delivery(self, release: dict) -> dict:
        return {"status": "refreshed", "release": release}


class WebProjectionTests(unittest.TestCase):
    def test_research_stages_sort_current_and_summary_fallback(self) -> None:
        projection = research_stages(sample_snapshot())
        self.assertEqual([row["sequence"] for row in projection["items"]], [2, 1])
        self.assertEqual(projection["current"]["stage_id"], "stage-2")
        self.assertEqual(projection["items"][0]["work_summary"], "Implement cards")
        self.assertEqual(projection["items"][1]["work_summary"], "Foundation completed")

    def test_research_stages_assign_materials_by_task_then_created_time(self) -> None:
        projection = research_stages(sample_snapshot())
        by_id = {row["stage_id"]: row for row in projection["items"]}
        self.assertEqual([item["item_id"] for item in by_id["stage-2"]["materials"]], ["t1"])
        self.assertEqual([item["item_id"] for item in by_id["stage-1"]["materials"]], ["p1", "s1"])
        self.assertEqual(projection["unassigned_materials"], [])

    def test_research_stages_assign_attempts_explicitly_then_by_time(self) -> None:
        source = sample_snapshot()
        source["attempts"] += [
            {"attempt_id":"historic", "goal":"Historic", "state":"negative", "track":None,
             "branch":"research/historic", "stage_id":None, "created_at":"2026-01-10 00:00:00",
             "conclusion":"No effect", "progress":""},
            {"attempt_id":"orphan", "goal":"Orphan", "state":"paused", "track":"sandbox",
             "branch":"sandbox/orphan", "stage_id":None, "created_at":None,
             "conclusion":"", "progress":"Waiting"},
        ]
        projection = research_stages(source)
        by_id = {row["stage_id"]: row for row in projection["items"]}
        self.assertEqual(by_id["stage-2"]["explorations"][0]["conclusion"], "Bridge contract works")
        self.assertEqual(by_id["stage-1"]["explorations"][0]["attempt_id"], "historic")
        self.assertEqual(by_id["stage-1"]["explorations"][0]["track"], "research")
        self.assertEqual(projection["unassigned_explorations"][0]["attempt_id"], "orphan")

    def test_research_stages_assign_gap_to_following_stage(self) -> None:
        source = sample_snapshot()
        source["context"]["stages"][1].update({
            "status": "completed", "finished_at": "2026-02-10 00:00:00",
        })
        source["context"]["stages"].append({
            "stage_id": "stage-3", "sequence": 3, "title": "Maintain", "status": "active",
            "goal": "Maintain", "summary": "", "current_step": "Refine",
            "started_at": "2026-03-01 00:00:00", "finished_at": None,
        })
        source["attempts"] = [{
            "attempt_id": "gap", "goal": "Gap work", "state": "validated", "track": None,
            "branch": "experiment/gap", "stage_id": None, "created_at": "2026-02-20 00:00:00",
            "conclusion": "done", "progress": "",
        }]
        projection = research_stages(source)
        stage3 = next(stage for stage in projection["items"] if stage["stage_id"] == "stage-3")
        self.assertEqual(stage3["explorations"][0]["attempt_id"], "gap")

    def test_research_stages_choose_highest_sequence_for_overlapping_intervals(self) -> None:
        source = sample_snapshot()
        source["context"]["stages"][0]["finished_at"] = None
        source["catalog_items"][1]["created_at"] = "2026-02-03 00:00:00"
        projection = research_stages(source)
        by_id = {row["stage_id"]: row for row in projection["items"]}
        self.assertEqual([item["item_id"] for item in by_id["stage-2"]["materials"]], ["t1", "p1"])
        self.assertEqual([item["item_id"] for item in by_id["stage-1"]["materials"]], ["s1"])

    def test_research_stages_without_active_stage_has_no_current(self) -> None:
        source = sample_snapshot()
        for stage in source["context"]["stages"]:
            stage["status"] = "completed"
        projection = research_stages(source)
        self.assertIsNone(projection["current"])
        self.assertFalse(any(stage["is_current"] for stage in projection["items"]))

    def test_research_stages_assign_after_last_and_keep_no_stage_records_unassigned(self) -> None:
        source = sample_snapshot()
        source["context"]["stages"][1].update({
            "status": "completed", "finished_at": "2026-02-10 00:00:00",
        })
        source["attempts"] = [{
            "attempt_id": "late", "goal": "Late work", "state": "validated", "track": None,
            "branch": "experiment/late", "stage_id": None, "created_at": "2026-04-01 00:00:00",
            "conclusion": "done", "progress": "",
        }]
        projection = research_stages(source)
        stage2 = next(stage for stage in projection["items"] if stage["stage_id"] == "stage-2")
        self.assertEqual(stage2["explorations"][0]["attempt_id"], "late")

        source["context"]["stages"] = []
        without_stages = research_stages(source)
        self.assertEqual(without_stages["unassigned_explorations"][0]["attempt_id"], "late")
        self.assertEqual(len(without_stages["unassigned_materials"]), 3)

    def test_evidence_matrix_uses_only_registered_evidence(self) -> None:
        matrix = evidence_matrix(sample_snapshot())
        row = matrix["rows"][0]
        self.assertEqual(row["cells"]["paper"]["label"], "supports")
        self.assertEqual(row["cells"]["experiment"]["status"], "unregistered")
        self.assertEqual(row["cells"]["experiment"]["label"], "未登记")

    def test_web_snapshot_does_not_mutate_source(self) -> None:
        source = sample_snapshot()
        original = copy.deepcopy(source)
        projected = web_snapshot(source)
        self.assertEqual(source, original)
        self.assertNotIn("research", source)
        self.assertIn("research", projected)
        self.assertIn("stages", projected["research"])
        self.assertNotIn("graph", projected["research"])
        self.assertNotIn("explorations", projected["research"])

    def test_workbench_items_are_added_to_search_without_entering_context(self) -> None:
        source = sample_snapshot()
        source["workbench_items"] = [{
            "item_id": "limit-check", "kind": "checklist", "kind_label": "检查清单",
            "purposes": ["validation"], "purpose_labels": ["理论、计算或实验验证"],
            "title": "极限检查", "summary": "检查已知极限", "tags": ["physics"],
        }]
        projected = web_snapshot(source)
        result = next(item for item in projected["search_index"] if item["kind"] == "workbench")
        self.assertEqual(result["title"], "极限检查")
        self.assertNotIn("workbench_items", source.get("context", {}))


class WebDashboardBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "resources" / "theory").mkdir(parents=True)
        (self.root / "resources" / "theory" / "theory.md").write_text("x", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_refresh_contract_and_unchanged_token(self) -> None:
        bridge = WebDashboardBridge(FakeProvider(self.root), refresh_seconds=2.5)
        ready = bridge.ready()["data"]
        self.assertEqual(ready["refresh_seconds"], 2.5)
        self.assertEqual(ready["version"], "1.6.1")
        self.assertIn("source_commit", ready["build_identity"])
        first = bridge.bootstrap()
        self.assertTrue(first["ok"])
        token = first["data"]["state_token"]
        second = bridge.refresh(token, False)
        self.assertTrue(second["ok"])
        self.assertTrue(second["data"]["unchanged"])
        self.assertEqual(second["data"]["changed_sections"], [])
        self.assertIsNone(second["data"]["snapshot"])

    def test_failed_refresh_keeps_last_good_snapshot(self) -> None:
        bridge = WebDashboardBridge(FakeProvider(self.root, fail_after=1))
        first = bridge.bootstrap()
        failed = bridge.refresh(first["data"]["state_token"], True)
        self.assertFalse(failed["ok"])
        self.assertTrue(failed["data"]["stale"])
        self.assertEqual(failed["data"]["snapshot"]["branch"], "experiment/web-dashboard-v2")

    def test_refresh_calls_are_coalesced(self) -> None:
        provider = FakeProvider(self.root, delay=.05)
        bridge = WebDashboardBridge(provider)
        results: list[dict] = []
        threads = [threading.Thread(target=lambda: results.append(bridge.refresh())) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(provider.loads, 1)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(result["ok"] for result in results))

    def test_resource_open_contract_rejects_unlisted_and_reveals_registered_file(self) -> None:
        bridge = WebDashboardBridge(FakeProvider(self.root))
        self.assertEqual(bridge.open_resource_directory("../secret")["error"]["code"], "invalid_path")
        with patch("project_hooks.infrastructure.system.external_navigation.subprocess.Popen") as runner:
            self.assertTrue(bridge.open_resource_directory("resources/theory")["ok"])
            self.assertTrue(bridge.reveal_resource_file("t1")["ok"])
            self.assertEqual(runner.call_count, 2)
        self.assertEqual(bridge.reveal_resource_file("fake")["error"]["code"], "not_found")

    def test_resource_reveal_opens_simulation_bundle_directory(self) -> None:
        bundle = self.root / "resources" / "analysis" / "simulation-project"
        bundle.mkdir(parents=True)
        source = sample_snapshot()
        source["catalog_items"].append({
            "item_id": "bundle-1", "kind": "simulation", "kind_label": "模拟资料包",
            "title": "Simulation project", "status": "active",
            "path": "resources/analysis/simulation-project", "metadata": {"entry_type": "bundle"},
        })
        provider = FakeProvider(self.root)
        with patch.object(provider, "load", return_value=source), patch(
            "project_hooks.ui.web.bridge.open_directory"
        ) as opener:
            bridge = WebDashboardBridge(provider)
            bridge.bootstrap()
            result = bridge.reveal_resource_file("bundle-1")
        self.assertTrue(result["ok"])
        opener.assert_called_once()
        self.assertTrue(opener.call_args.args[0].samefile(bundle))

    def test_diagnostic_export_rejects_path_components(self) -> None:
        bridge = WebDashboardBridge(FakeProvider(self.root))
        result = bridge.export_diagnostics("../diagnostics.zip")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_path")

    def test_bridge_rejects_invalid_structured_parameters(self) -> None:
        bridge = WebDashboardBridge(FakeProvider(self.root))
        self.assertEqual(bridge.refresh_software_delivery(None)["error"]["code"], "invalid_parameters")
        self.assertEqual(bridge.copy_context([1])["error"]["code"], "invalid_parameters")
        self.assertEqual(bridge.copy_workbench_items([])["error"]["code"], "invalid_parameters")

    def test_bridge_copies_only_explicitly_selected_workbench_items(self) -> None:
        folder = self.root / "workbench" / "local"
        folder.mkdir(parents=True)
        first = folder / "first.md"
        second = folder / "second.md"
        first.write_text("# First\n\nSelected content.\n", encoding="utf-8")
        second.write_text("# Second\n\nUnselected content.\n", encoding="utf-8")
        import hashlib
        source = sample_snapshot()
        source["workbench_items"] = [
            {"item_id": "first", "kind": "instruction", "kind_label": "AI 指令", "purposes": ["theory_derivation"], "purpose_labels": ["理论与公式推导"], "review_state": "reviewed", "title": "First", "summary": "First summary", "path": "workbench/local/first.md", "content_sha256": hashlib.sha256(first.read_bytes()).hexdigest(), "tags": ["physics"], "reference": "", "status": "active", "origin": "local", "origin_label": "本地创建"},
            {"item_id": "second", "kind": "instruction", "kind_label": "AI 指令", "purposes": [], "purpose_labels": [], "review_state": "reviewed", "title": "Second", "summary": "Second summary", "path": "workbench/local/second.md", "content_sha256": hashlib.sha256(second.read_bytes()).hexdigest(), "tags": [], "reference": "", "status": "active", "origin": "local", "origin_label": "本地创建"},
        ]
        provider = FakeProvider(self.root)
        with patch.object(provider, "load", return_value=source):
            bridge = WebDashboardBridge(provider)
            copied = bridge.copy_workbench_items(["first"])
        self.assertTrue(copied["ok"])
        self.assertIn("Selected content", copied["data"]["text"])
        self.assertIn("主类型：AI 指令", copied["data"]["text"])
        self.assertIn("科研用途：理论与公式推导", copied["data"]["text"])
        self.assertNotIn("Unselected content", copied["data"]["text"])


class WebDashboardIntegrationTests(unittest.TestCase):
    def test_loader_hide_uses_immediate_native_visibility_call(self) -> None:
        calls = []
        _hide_loader_window(
            1234, lambda class_name, title: 5678,
            lambda handle, command: calls.append((handle, command)),
        )
        self.assertEqual(calls, [(5678, 0)])

    def test_dashboard_command_and_legacy_modes_are_removed(self) -> None:
        parser = build_parser()
        with self.assertRaises(Exception):
            parser.parse_args(["dashboard"])

    def test_static_assets_are_local_es_modules_and_packaged(self) -> None:
        root = asset_root()
        for name in ("index.html", "styles.css", "workbench.css", "settings.css", "app.js", "state.js", "i18n.js"):
            self.assertTrue((root / name).is_file(), name)
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")
        settings_styles = (root / "settings.css").read_text(encoding="utf-8")
        self.assertIn('type="module"', html)
        self.assertNotIn("https://", html)
        self.assertIn("prefers-color-scheme", script)
        self.assertIn("location.hash.slice(1)", script)
        self.assertIn('id="settings-button"', html)
        self.assertIn('href="settings.css"', html)
        self.assertIn('href="workflow-monitor-mark.svg"', html)
        self.assertIn('src="workflow-monitor-mark.svg"', html)
        self.assertNotIn('brand-mark"><i', html)
        self.assertIn('from "./i18n.js"', script)
        self.assertIn("copy_workbench_items", script)
        self.assertIn("data-workbench-kind", script)
        self.assertIn("data-workbench-purpose", script)
        self.assertIn("workbench-kind-sidebar", script)
        self.assertIn("workbench-kind-select", script)
        self.assertIn("workbench-purpose-menu", script)
        self.assertIn("clear-workbench-purposes", script)
        self.assertNotIn("purpose-chips", script)
        self.assertNotIn("复制全部登记资料上下文", script)
        self.assertIn('data-theme=light', styles)
        self.assertIn("@media(max-width:720px)", settings_styles)
        self.assertIn(".settings-nav button{justify-content:flex-start", settings_styles)
        build_script = (Path(__file__).resolve().parents[3] / "scripts/build_windows_exe.py").read_text(encoding="utf-8")
        windows_entry = (Path(__file__).resolve().parents[3] / "project_hooks/ui/windows/main.py").read_text(encoding="utf-8")
        self.assertIn("project_hooks/ui/web/assets", build_script)
        self.assertIn('"--exclude-module", "PyQt5"', build_script)
        self.assertIn('"--exclude-module", "cryptography"', build_script)
        self.assertIn('"--hidden-import", "webview.platforms.edgechromium"', build_script)
        self.assertIn("_hide_loader_window(loader_handle)", windows_entry)
        self.assertNotIn("PostMessageW(loader_handle", windows_entry)
        self.assertIn("finally:", windows_entry)
        self.assertNotIn("launch_dashboard_mode", windows_entry)
        self.assertNotIn('args[0] == "dashboard"', windows_entry)

    def test_glossary_and_navigation_are_complete(self) -> None:
        self.assertEqual(len(STATUS_GLOSSARY), 10)
        script = (asset_root() / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('["diagnostics","', script)
        self.assertIn('disclosure("diagnostics"', script)
        self.assertIn("draftQuery", script)
        self.assertIn("committedQuery", script)
        self.assertIn('["status","处置状态","Disposition"]', script)
        self.assertNotIn('["explain","?"', script)
        self.assertNotIn('["advanced","⋯"', script)
        self.assertIn('settingsSection:"language"', script)
        self.assertIn('data-settings-section', script)
        self.assertIn('name="language"', script)
        self.assertIn('["about",l("关于","About")]', script)
        self.assertIn("DawnDust", script)
        self.assertIn("https://github.com/DawnDust/workflow-monitor", script)
        self.assertIn("https://dawndust.github.io/workflow-monitor/", script)
        self.assertIn("security/advisories/new", script)
        self.assertIn('target="_blank" rel="noopener noreferrer"', script)
        self.assertIn('id="about-report-bug"', script)
        self.assertIn("build_identity", script)
        self.assertIn("saveLanguage", script)
        self.assertIn("esc(item.summary)", script)
        self.assertNotIn("knownStatus", script)
        self.assertIn("selectedKind", script)
        self.assertIn("compositionstart", script)
        self.assertIn("changed_sections", script)
        self.assertNotIn('i===0?"open"', script)
        self.assertIn("research?.stages", script)
        self.assertIn('"research-stages"', script)
        self.assertIn("科研阶段", script)
        self.assertIn("stageOpen", script)
        self.assertIn("stageExpansionInitialized", script)
        self.assertIn("details[data-stage-id]", script)
        self.assertIn("当前没有进行中的科研阶段", script)
        self.assertIn("尚未建立科研阶段", script)
        self.assertNotIn("research-map", script)
        self.assertNotIn("data-timeline-node", script)
        self.assertNotIn("<svg", script)
        self.assertNotIn("exploration-compare", script)
        self.assertNotIn("function comparison", script)
        self.assertIn("advancedOpen", script)
        self.assertIn("updateAdvanced", script)
        self.assertIn("selectedResourceKind", script)
        self.assertIn("data-open-resource", script)
        self.assertIn("x.work_summary", script)
        self.assertIn("x.material_count", script)
        self.assertIn("x.exploration_count", script)
        self.assertNotIn('panel("最近完成"', script)
        self.assertNotIn("最近完成：", script)

    @unittest.skipUnless(shutil.which("node"), "Node is a development-only optional test runtime")
    def test_frontend_state_helpers_without_browser(self) -> None:
        source = (asset_root() / "state.js").read_bytes()
        module_url = "data:text/javascript;base64," + base64.b64encode(source).decode("ascii")
        script = f"""
          import {{filterRecords, evidenceLabel, paginate}} from {json.dumps(module_url)};
          const rows = filterRecords([{{title:'Alpha'}},{{title:'Beta'}}], 'alp', ['title']);
          const page = paginate(Array.from({{length: 21}}, (_, i) => i), 3, 10);
          if (rows.length !== 1 || evidenceLabel({{status:'unregistered'}}) !== '未登记' || page.items.length !== 1) process.exit(1);
        """
        subprocess.run(["node", "--input-type=module", "-e", script], check=True)

    @unittest.skipUnless(shutil.which("node"), "Node is a development-only optional test runtime")
    def test_language_preference_helpers_are_local_and_safe(self) -> None:
        source = (asset_root() / "i18n.js").read_bytes()
        module_url = "data:text/javascript;base64," + base64.b64encode(source).decode("ascii")
        script = f"""
          import {{loadLanguage, saveLanguage}} from {json.dumps(module_url)};
          const values = new Map();
          const storage = {{getItem:k => values.get(k), setItem:(k,v) => values.set(k,v)}};
          if (loadLanguage(storage) !== 'zh-CN') process.exit(1);
          if (saveLanguage('en', storage) !== 'en' || loadLanguage(storage) !== 'en') process.exit(2);
          if (saveLanguage('fr', storage) !== 'zh-CN') process.exit(3);
          const broken = {{getItem:() => {{throw new Error('blocked')}}, setItem:() => {{throw new Error('blocked')}}}};
          if (loadLanguage(broken) !== 'zh-CN' || saveLanguage('en', broken) !== 'en') process.exit(4);
        """
        subprocess.run(["node", "--input-type=module", "-e", script], check=True)


if __name__ == "__main__":
    unittest.main()
