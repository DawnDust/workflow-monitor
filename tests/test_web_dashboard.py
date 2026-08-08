from __future__ import annotations

import base64
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
from project_hooks.web_dashboard import (
    WEBVIEW2_DOWNLOAD_URL,
    WebDashboardBridge,
    asset_root,
    launch_dashboard_mode,
)
from project_hooks.web_projection import (
    bfs_neighborhood,
    evidence_matrix,
    exploration_comparison,
    research_graph,
    web_snapshot,
)


def sample_snapshot() -> dict:
    return {
        "branch": "experiment/web-dashboard-v2",
        "health": {"status": "passed", "events": 3},
        "context": {"active_task": {"task_id": "task-1", "goal": "Build web UI"}},
        "history": [], "decisions": [], "events": [], "search_index": [],
        "resource_directories": [], "external_tools": [], "diagnostics": {},
        "task_details": {
            "task-1": {"goal": "Build web UI", "branch": "experiment/web-dashboard-v2"},
        },
        "attempt": {
            "attempt_id": "attempt-1", "goal": "Try WebView2", "state": "active",
            "branch": "experiment/web-dashboard-v2", "stage_id": "stage-2",
            "hypothesis": "A local bridge is sufficient", "evidence": ["contract test"],
        },
        "explorations": [{
            "event_id": "explore-1", "goal": "Prior experiment", "result": "negative",
            "branch": "experiment/web-dashboard-v2", "task_id": "task-1", "evidence": "too large",
        }],
        "catalog_items": [
            {"item_id": "t1", "kind": "theory", "kind_label": "理论", "title": "Theory",
             "status": "active", "branch": "main", "task_id": "task-1", "tags": [], "metadata": {}},
            {"item_id": "p1", "kind": "literature", "kind_label": "文献", "title": "Paper",
             "status": "active", "branch": "main", "task_id": "task-1", "tags": [], "metadata": {}},
            {"item_id": "s1", "kind": "simulation", "kind_label": "仿真", "title": "Simulation",
             "status": "active", "branch": "main", "task_id": "task-1", "tags": [], "metadata": {}},
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
    def test_graph_distinguishes_explicit_evidence_from_derived_process_edges(self) -> None:
        graph = research_graph(sample_snapshot())
        explicit = [edge for edge in graph["edges"] if edge["provenance"] == "explicit"]
        derived = [edge for edge in graph["edges"] if edge["provenance"] == "derived"]
        self.assertEqual([edge["relation"] for edge in explicit], ["supports"])
        self.assertTrue(any(edge["relation"] == "task_id" for edge in derived))
        self.assertNotIn("supports", {edge["relation"] for edge in derived})

    def test_bfs_focus_returns_only_requested_neighborhood(self) -> None:
        graph = research_graph(sample_snapshot())
        focused = bfs_neighborhood(graph, "catalog:t1", 1)
        ids = {node["id"] for node in focused["nodes"]}
        self.assertIn("catalog:t1", ids)
        self.assertIn("catalog:p1", ids)
        self.assertIn("task:task-1", ids)
        self.assertNotIn("exploration:explore-1", ids)

    def test_evidence_matrix_uses_only_registered_evidence(self) -> None:
        matrix = evidence_matrix(sample_snapshot())
        row = matrix["rows"][0]
        self.assertEqual(row["cells"]["paper"]["label"], "supports")
        self.assertEqual(row["cells"]["experiment"]["status"], "unregistered")
        self.assertEqual(row["cells"]["experiment"]["label"], "未登记")

    def test_exploration_projection_keeps_current_and_archived_states(self) -> None:
        records = exploration_comparison(sample_snapshot())
        self.assertEqual(records[0]["state"], "active")
        self.assertTrue(records[0]["is_current"])
        self.assertEqual(records[1]["state"], "negative")

    def test_web_snapshot_does_not_mutate_source(self) -> None:
        source = sample_snapshot()
        projected = web_snapshot(source)
        self.assertNotIn("research", source)
        self.assertIn("research", projected)


class WebDashboardBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "resources" / "theory").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_refresh_contract_and_unchanged_token(self) -> None:
        bridge = WebDashboardBridge(FakeProvider(self.root), refresh_seconds=2.5)
        self.assertEqual(bridge.ready()["data"]["refresh_seconds"], 2.5)
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

    def test_path_allowlist_rejects_escape_and_unlisted_root(self) -> None:
        bridge = WebDashboardBridge(FakeProvider(self.root))
        self.assertEqual(bridge.open_project_path("../secret.txt")["error"]["code"], "invalid_path")
        (self.root / "other.txt").write_text("x", encoding="utf-8")
        self.assertEqual(bridge.open_project_path("other.txt")["error"]["code"], "invalid_path")

    def test_diagnostic_export_rejects_path_components(self) -> None:
        bridge = WebDashboardBridge(FakeProvider(self.root))
        result = bridge.export_diagnostics("../diagnostics.zip")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_path")

    def test_bridge_rejects_invalid_structured_parameters(self) -> None:
        bridge = WebDashboardBridge(FakeProvider(self.root))
        self.assertEqual(bridge.refresh_software_delivery(None)["error"]["code"], "invalid_parameters")
        self.assertEqual(bridge.copy_context([1])["error"]["code"], "invalid_parameters")


class WebDashboardIntegrationTests(unittest.TestCase):
    def test_cli_accepts_all_ui_modes_and_auto_defaults(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.parse_args(["dashboard"]).ui, "auto")
        for mode in ("auto", "web", "legacy"):
            self.assertEqual(parser.parse_args(["dashboard", "--ui", mode]).ui, mode)

    def test_explicit_web_failure_notifies_and_falls_back(self) -> None:
        calls: list[str] = []
        notices: list[str] = []
        result = launch_dashboard_mode(
            object(), 0, "web",
            web_launcher=lambda *_: (_ for _ in ()).throw(RuntimeError("runtime missing")),
            legacy_launcher=lambda *_, **__: calls.append("legacy"),
            fallback_notifier=notices.append,
        )
        self.assertTrue(result["fallback"])
        self.assertEqual(calls, ["legacy"])
        self.assertIn("runtime missing", notices[0])
        self.assertIn(WEBVIEW2_DOWNLOAD_URL, notices[0])

    def test_auto_remains_legacy_during_experiment(self) -> None:
        calls: list[str] = []
        launch_dashboard_mode(
            object(), 0, "auto",
            web_launcher=lambda *_: calls.append("web"),
            legacy_launcher=lambda *_, **__: calls.append("legacy"),
        )
        self.assertEqual(calls, ["legacy"])

    def test_static_assets_are_local_es_modules_and_packaged(self) -> None:
        root = asset_root()
        for name in ("index.html", "styles.css", "app.js", "state.js"):
            self.assertTrue((root / name).is_file(), name)
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")
        self.assertIn('type="module"', html)
        self.assertNotIn("https://", html)
        self.assertIn("prefers-color-scheme", script)
        self.assertIn("location.hash.slice(1)", script)
        self.assertIn('data-theme=light', styles)
        build_script = (Path(__file__).resolve().parents[1] / "scripts/build_windows_exe.py").read_text(encoding="utf-8")
        windows_entry = (Path(__file__).resolve().parents[1] / "project_hooks/windows_entry.py").read_text(encoding="utf-8")
        self.assertIn("project_hooks/web_assets", build_script)
        self.assertIn('"--exclude-module", "PyQt5"', build_script)
        self.assertIn('"--exclude-module", "cryptography"', build_script)
        self.assertIn('"--hidden-import", "webview.platforms.edgechromium"', build_script)
        self.assertIn("shown_callback=close_loader", windows_entry)

    @unittest.skipUnless(shutil.which("node"), "Node is a development-only optional test runtime")
    def test_frontend_state_helpers_without_browser(self) -> None:
        source = (asset_root() / "state.js").read_bytes()
        module_url = "data:text/javascript;base64," + base64.b64encode(source).decode("ascii")
        script = f"""
          import {{filterRecords, graphNeighborhood, evidenceLabel}} from {json.dumps(module_url)};
          const rows = filterRecords([{{title:'Alpha'}},{{title:'Beta'}}], 'alp', ['title']);
          const graph = graphNeighborhood({{nodes:[{{id:'a'}},{{id:'b'}},{{id:'c'}}],edges:[{{source:'a',target:'b'}},{{source:'b',target:'c'}}]}}, 'a', 1);
          if (rows.length !== 1 || graph.nodes.length !== 2 || evidenceLabel({{status:'unregistered'}}) !== '未登记') process.exit(1);
        """
        subprocess.run(["node", "--input-type=module", "-e", script], check=True)


if __name__ == "__main__":
    unittest.main()
