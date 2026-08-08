from __future__ import annotations

import ast
import importlib
import re
import unittest
from pathlib import Path

from project_hooks.core.branches import classify_branch
from project_hooks.core.catalog import context_payload, decode_item, render_context_markdown
from project_hooks.core.events import SCHEMA_VERSION, canonical_json, validate_event
from project_hooks.core.lifecycle import active_task_warning, lifecycle_step


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "project_hooks"


def python_files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*.py") if "__pycache__" not in path.parts)


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


SQL_STATEMENT = re.compile(
    r"SELECT\s|INSERT\s+INTO\s|UPDATE\s+\w+\s+SET\s|DELETE\s+FROM\s|CREATE\s+TABLE\s|PRAGMA\s",
)


def contains_sql(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and SQL_STATEMENT.search(node.value)
        for node in ast.walk(tree)
    )


def package_graph() -> dict[str, set[str]]:
    modules = {
        ".".join(path.relative_to(PACKAGE).with_suffix("").parts): path
        for path in python_files(PACKAGE)
    }
    graph = {name: set() for name in modules}
    for name, path in modules.items():
        package = name.split(".")[:-1]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                base = package[:max(0, len(package) - node.level + 1)]
                target = ".".join(base + ((node.module or "").split(".") if node.module else []))
            elif node.module and node.module.startswith("project_hooks."):
                target = node.module.removeprefix("project_hooks.")
            else:
                continue
            if target in modules:
                graph[name].add(target)
    return graph


class ArchitectureTests(unittest.TestCase):
    def test_core_package_exists(self): self.assertTrue((PACKAGE / "core/__init__.py").is_file())
    def test_application_package_exists(self): self.assertTrue((PACKAGE / "application/__init__.py").is_file())
    def test_infrastructure_package_exists(self): self.assertTrue((PACKAGE / "infrastructure/__init__.py").is_file())
    def test_ui_package_exists(self): self.assertTrue((PACKAGE / "ui/__init__.py").is_file())
    def test_persistence_package_exists(self): self.assertTrue((PACKAGE / "infrastructure/persistence/__init__.py").is_file())
    def test_git_package_exists(self): self.assertTrue((PACKAGE / "infrastructure/git/__init__.py").is_file())
    def test_system_package_exists(self): self.assertTrue((PACKAGE / "infrastructure/system/__init__.py").is_file())
    def test_cli_package_exists(self): self.assertTrue((PACKAGE / "ui/cli/__init__.py").is_file())
    def test_web_package_exists(self): self.assertTrue((PACKAGE / "ui/web/__init__.py").is_file())
    def test_windows_package_exists(self): self.assertTrue((PACKAGE / "ui/windows/__init__.py").is_file())

    def test_cli_compatibility_entry_is_thin(self):
        self.assertLess(len((PACKAGE / "cli.py").read_text(encoding="utf-8").splitlines()), 10)

    def test_launcher_compatibility_entry_is_thin(self):
        self.assertLess(len((PACKAGE / "launcher.py").read_text(encoding="utf-8").splitlines()), 10)

    def test_windows_compatibility_entry_is_thin(self):
        self.assertLess(len((PACKAGE / "windows_entry.py").read_text(encoding="utf-8").splitlines()), 15)

    def test_legacy_dashboard_is_removed(self):
        self.assertFalse((PACKAGE / "dashboard.py").exists())

    def test_architecture_document_exists(self):
        self.assertTrue((ROOT / "maintenance/ARCHITECTURE.md").is_file())

    def test_core_does_not_import_outer_layers(self):
        forbidden = ("project_hooks.application", "project_hooks.infrastructure", "project_hooks.ui")
        for path in python_files(PACKAGE / "core"):
            self.assertFalse(any(name.startswith(forbidden) for name in imports(path)), path)

    def test_core_has_no_io_modules(self):
        forbidden = {"argparse", "pathlib", "sqlite3", "subprocess", "tkinter", "webview"}
        for path in python_files(PACKAGE / "core"):
            self.assertFalse(imports(path) & forbidden, path)

    def test_application_does_not_import_adapters_or_ui(self):
        for path in python_files(PACKAGE / "application"):
            names = imports(path)
            self.assertFalse(any("infrastructure" in name or name.startswith("ui") for name in names), path)

    def test_infrastructure_does_not_import_ui(self):
        for path in python_files(PACKAGE / "infrastructure"):
            self.assertFalse(any(".ui" in name or name.startswith("ui") for name in imports(path)), path)

    def test_web_does_not_import_cli(self):
        for path in python_files(PACKAGE / "ui/web"):
            self.assertFalse(any("cli" in name for name in imports(path)), path)

    def test_argparse_is_confined_to_cli(self):
        offenders = [path for path in python_files(PACKAGE) if "argparse" in imports(path)
                     and ("ui", "cli") != path.relative_to(PACKAGE).parts[:2]]
        self.assertEqual(offenders, [])

    def test_sqlite_is_confined_to_persistence(self):
        offenders = [path for path in python_files(PACKAGE) if "sqlite3" in imports(path)
                     and path.relative_to(PACKAGE).parts[:2] != ("infrastructure", "persistence")]
        self.assertEqual(offenders, [])

    def test_sql_statements_are_confined_to_persistence(self):
        offenders = [path for path in python_files(PACKAGE) if contains_sql(path)
                     and path.relative_to(PACKAGE).parts[:2] != ("infrastructure", "persistence")]
        self.assertEqual(offenders, [])

    def test_subprocess_is_confined_to_infrastructure(self):
        offenders = [path for path in python_files(PACKAGE) if "subprocess" in imports(path)
                     and path.relative_to(PACKAGE).parts[0] != "infrastructure"]
        self.assertEqual(offenders, [])

    def test_package_has_no_dependency_cycles(self):
        graph = package_graph()
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(node: str):
            if node in visiting:
                self.fail(f"dependency cycle at {node}")
            if node in visited:
                return
            visiting.add(node)
            for target in graph[node]:
                visit(target)
            visiting.remove(node)
            visited.add(node)
        for module in graph:
            visit(module)

    def test_composition_root_exists(self): self.assertTrue((PACKAGE / "composition.py").is_file())
    def test_web_assets_are_local(self): self.assertTrue((PACKAGE / "ui/web/assets/index.html").is_file())
    def test_web_script_is_local(self): self.assertTrue((PACKAGE / "ui/web/assets/app.js").is_file())
    def test_web_state_script_is_local(self): self.assertTrue((PACKAGE / "ui/web/assets/state.js").is_file())
    def test_web_styles_are_local(self): self.assertTrue((PACKAGE / "ui/web/assets/styles.css").is_file())
    def test_windows_png_icon_exists(self): self.assertTrue((PACKAGE / "ui/windows/assets/crafting_table_icon.png").is_file())
    def test_windows_ico_icon_exists(self): self.assertTrue((PACKAGE / "ui/windows/assets/crafting_table_icon.ico").is_file())

    def test_core_event_schema_is_three(self): self.assertEqual(SCHEMA_VERSION, 3)
    def test_core_json_is_canonical(self): self.assertEqual(canonical_json({"b": 1, "a": 2}), '{"a":2,"b":1}')
    def test_core_rejects_invalid_event(self):
        with self.assertRaises(RuntimeError): validate_event({"event_id": "bad"})

    def test_core_classifies_stable_branch(self):
        policy = {"default_branch": "main", "exploration_types": ["research", "experiment", "sandbox"], "archive_prefix": "archive"}
        self.assertEqual(classify_branch("main", policy)["kind"], "stable")

    def test_core_classifies_experiment_branch(self):
        policy = {"default_branch": "main", "exploration_types": ["research", "experiment", "sandbox"], "archive_prefix": "archive"}
        self.assertEqual(classify_branch("experiment/modular-architecture", policy)["kind"], "exploration")

    def test_core_lifecycle_idle(self): self.assertEqual(lifecycle_step({})["key"], "idle")
    def test_core_lifecycle_completed(self): self.assertEqual(lifecycle_step({"last_completed": {"task_id": "x"}})["key"], "completed")
    def test_core_active_warning_ignores_missing_time(self): self.assertIsNone(active_task_warning({"task_id": "x"}))

    def test_catalog_decode_is_pure(self):
        row = {"item_id": "x", "kind": "theory", "tags_json": '["core"]', "metadata_json": '{}'}
        self.assertEqual(decode_item(row)["kind_label"], "理论")

    def test_catalog_context_filters_unrelated_relations(self):
        item = {"item_id": "a"}
        relations = [{"source_id": "b", "target_id": "c"}]
        self.assertEqual(context_payload([item], relations)["relations"], [])

    def test_catalog_markdown_handles_empty_input(self):
        self.assertIn("没有匹配的资料", render_context_markdown([], []))

    def test_all_product_modules_import(self):
        for name in (
            "project_hooks.core.actions", "project_hooks.application.action_service",
            "project_hooks.infrastructure.persistence.store", "project_hooks.ui.cli.main",
            "project_hooks.ui.web.bridge", "project_hooks.ui.windows.main",
        ):
            with self.subTest(name=name): importlib.import_module(name)
