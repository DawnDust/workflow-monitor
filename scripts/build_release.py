"""Build the EXE-only GitHub Release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from project_hooks import __version__
from project_hooks.store import SCHEMA_VERSION


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if args.version != __version__:
        raise SystemExit(
            f"release version mismatch: tag={args.version}, application={__version__}"
        )
    dist = args.dist.resolve()
    executable = dist / "project-hooks.exe"
    if not executable.is_file():
        raise SystemExit(f"missing Windows executable: {executable}")
    base = (
        "https://github.com/DawnDust/project-maintenance-template/"
        f"releases/download/v{args.version}/"
    )
    build_info_path = ROOT / "build" / "build-info.json"
    try:
        build_identity = json.loads(build_info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        build_identity = {"build_id": "unknown"}
    manifest = {
        "version": args.version,
        "build_identity": build_identity,
        "launcher_min_version": "1.0.0",
        "event_schema": {"minimum": 1, "maximum": SCHEMA_VERSION},
        "windows_exe": {
            "file": executable.name,
            "url": base + executable.name,
            "sha256": digest(executable),
        },
    }
    (dist / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
