"""Build the Windows x64 single-file project-hooks executable."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from project_hooks import DISPLAY_NAME, EXECUTABLE_NAME, __version__
from project_hooks.infrastructure.git.build_identity import repository_source_identity


def write_build_info(root: Path) -> Path:
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
    }
    path = root / "build" / "build-info.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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


def main() -> int:
    root = ROOT
    build_info = write_build_info(root)
    version_info = write_version_info(root)
    icon_png = root / "project_hooks" / "ui" / "windows" / "assets" / "workflow_monitor_icon.png"
    icon_ico = root / "project_hooks" / "ui" / "windows" / "assets" / "workflow_monitor_icon.ico"
    web_assets = root / "project_hooks" / "ui" / "web" / "assets"
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
