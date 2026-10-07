"""Build the Windows x64 single-file project-hooks executable."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from project_hooks import DISPLAY_NAME, EXECUTABLE_NAME, __version__
from project_hooks.infrastructure.git.build_identity import repository_source_identity
from project_hooks.infrastructure.system.build_notes import validate_notes, notes_payload, build_verification


def write_build_info(root: Path, channel: str = "test", notes: Path | None = None) -> Path:
    notes_content = None
    if channel == "test":
        notes = notes or root / "TEST_BUILD_NOTES.md"
        notes_content = validate_notes(notes.read_text(encoding="utf-8"))
    repository = repository_source_identity(root)
    tree = repository["build_input_fingerprint"]
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    build_id = hashlib.sha256(f"{tree}\n{built_at}".encode("utf-8")).hexdigest()[:16]
    value = {
        "build_id": build_id,
        "source_commit": repository["source_commit"],
        "build_input_fingerprint": repository["build_input_fingerprint"],
        "source_tree": tree,
        "source_tree_algorithm": repository["source_tree_algorithm"],
        "built_at": built_at,
        "dirty": repository["dirty"],
        "mode": "frozen",
        "channel": channel,
    }
    if notes_content is not None:
        value["test_notes_sha256"] = hashlib.sha256(notes_content.encode("utf-8")).hexdigest()
    path = root / "build" / "build-info.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if notes_content is not None:
        payload = notes_payload(notes_content, value, build_verification(root))
        (path.parent / "test-build-notes.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_version_info(root: Path) -> Path:
    version = tuple(int(part) for part in __version__.split(".")) + (0,)
    dotted = ", ".join(str(part) for part in version)
    path = root / "build" / "windows-version-info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({dotted}), prodvers=({dotted}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'DawnDust'),
    StringStruct('FileDescription', '{DISPLAY_NAME}'),
    StringStruct('FileVersion', '{__version__}'),
    StringStruct('InternalName', 'workflow-monitor'),
    StringStruct('OriginalFilename', '{EXECUTABLE_NAME}'),
    StringStruct('ProductName', '{DISPLAY_NAME}'),
    StringStruct('ProductVersion', '{__version__}')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)\n""",
        encoding="utf-8",
    )
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Workflow Monitor for Windows.")
    parser.add_argument(
        "--channel", choices=("test", "stable"), default="test",
        help="mark the build as a test build (default) or stable release build",
    )
    parser.add_argument("--notes", type=Path, help="测试说明来源，默认 TEST_BUILD_NOTES.md")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = ROOT
    build_info = write_build_info(root, channel=args.channel, notes=args.notes)
    identity = json.loads(build_info.read_text(encoding="utf-8"))
    version_info = write_version_info(root)
    icon_png = root / "project_hooks" / "ui" / "windows" / "assets" / "workflow_monitor_icon.png"
    icon_ico = root / "project_hooks" / "ui" / "windows" / "assets" / "workflow_monitor_icon.ico"
    web_assets = root / "project_hooks" / "ui" / "web" / "assets"
    physics_roadmap = root / "maintenance" / "PHYSICS_RESEARCH_ROADMAP.md"
    understanding_guide = root / "maintenance" / "RESEARCH_UNDERSTANDING_GUIDE.md"
    notes_data = ["--add-data", f"{root / 'build' / 'test-build-notes.json'}{os.pathsep}."] if args.channel == "test" else []
    PyInstaller.__main__.run([
        str(root / "project_hooks" / "windows_entry.py"),
        "--name", "workflow-monitor",
        "--onefile",
        "--console",
        "--hide-console", "hide-early",
        "--clean",
        "--noconfirm",
        "--distpath", str(root / "dist"),
        "--workpath", str(root / "build" / "pyinstaller"),
        "--specpath", str(root / "build" / "pyinstaller"),
        "--paths", str(root),
        "--icon", str(icon_ico),
        "--version-file", str(version_info),
        "--add-data", f"{build_info}{os.pathsep}.",
        "--add-data", f"{icon_png}{os.pathsep}project_hooks/ui/windows/assets",
        "--add-data", f"{web_assets}{os.pathsep}project_hooks/ui/web/assets",
        "--add-data", f"{physics_roadmap}{os.pathsep}maintenance",
        "--add-data", f"{understanding_guide}{os.pathsep}maintenance",
        *notes_data,
        "--hidden-import", "webview",
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "clr",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt6",
        "--exclude-module", "PySide2",
        "--exclude-module", "PySide6",
        "--exclude-module", "qtpy",
        "--exclude-module", "webview.platforms.qt",
        "--exclude-module", "webview.platforms.gtk",
        "--exclude-module", "webview.platforms.cef",
        "--exclude-module", "webview.platforms.cocoa",
        "--exclude-module", "webview.platforms.android",
        # pywebview's generic server hook collects TLS/template helpers even
        # though this build loads a file:// application with ssl=False.
        "--exclude-module", "cryptography",
        "--exclude-module", "bcrypt",
        "--exclude-module", "jinja2",
    ])
    executable = root / "dist" / EXECUTABLE_NAME
    if not executable.is_file():
        raise SystemExit(f"PyInstaller did not create {executable}")
    if repository_source_identity(root)["build_input_fingerprint"] != identity["build_input_fingerprint"]:
        raise SystemExit("构建期间源码输入发生变化，产物不可交付；请重新构建")
    if args.channel == "test":
        current_notes = validate_notes((args.notes or root / "TEST_BUILD_NOTES.md").read_text(encoding="utf-8"))
        if hashlib.sha256(current_notes.encode("utf-8")).hexdigest() != identity["test_notes_sha256"]:
            raise SystemExit("构建期间测试说明发生变化，请重新构建")
    env = os.environ.copy()
    env["PROJECT_HOOKS_EXE_ACTIVE"] = "1"
    completed = subprocess.run([str(executable), "version"], cwd=root, env=env,
                               text=True, encoding="utf-8", capture_output=True, check=True, timeout=60)
    if json.loads(completed.stdout)["build_identity"] != identity:
        raise SystemExit("生成 EXE 的身份与构建记录不一致")
    if args.channel == "test":
        completed = subprocess.run([str(executable), "build-notes", "--format", "json"], cwd=root, env=env,
                                   text=True, encoding="utf-8", capture_output=True, check=True, timeout=60)
        payload = json.loads(completed.stdout)
        expected = json.loads((root / "build" / "test-build-notes.json").read_text(encoding="utf-8"))
        if payload != expected:
            raise SystemExit("EXE 内测试说明与构建说明不一致")
        (root / "dist" / "test-build-notes.md").write_text(payload["markdown"], encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
