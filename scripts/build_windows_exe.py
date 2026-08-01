"""Build the Windows x64 single-file project-hooks executable."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import PyInstaller.__main__


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def write_build_info(root: Path) -> Path:
    commit = git(root, "rev-parse", "HEAD") or None
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    diff = git(root, "diff", "--binary", "HEAD")
    tree = hashlib.sha256(f"{commit or 'unknown'}\n{status}\n{diff}".encode("utf-8")).hexdigest()
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    build_id = hashlib.sha256(f"{tree}\n{built_at}".encode("utf-8")).hexdigest()[:16]
    value = {
        "build_id": build_id,
        "source_commit": commit,
        "source_tree": tree,
        "built_at": built_at,
        "dirty": bool(status),
        "mode": "frozen",
    }
    path = root / "build" / "build-info.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    build_info = write_build_info(root)
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
        "--add-data", f"{build_info}{os.pathsep}.",
    ])
    executable = root / "dist" / "project-hooks.exe"
    if not executable.is_file():
        raise SystemExit(f"PyInstaller did not create {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
