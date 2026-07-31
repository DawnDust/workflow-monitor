"""Build the Windows x64 single-file project-hooks executable."""

from __future__ import annotations

from pathlib import Path

import PyInstaller.__main__


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    PyInstaller.__main__.run([
        str(root / "project_hooks" / "windows_entry.py"),
        "--name", "project-hooks",
        "--onefile",
        "--console",
        "--hide-console", "hide-early",
        "--clean",
        "--noconfirm",
        "--distpath", str(root / "dist"),
        "--workpath", str(root / "build" / "pyinstaller"),
        "--specpath", str(root / "build" / "pyinstaller"),
        "--paths", str(root),
    ])
    executable = root / "dist" / "project-hooks.exe"
    if not executable.is_file():
        raise SystemExit(f"PyInstaller did not create {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
