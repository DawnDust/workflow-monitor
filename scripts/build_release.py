"""Build the versioned core archive and release manifest after wheel creation."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    dist = args.dist.resolve()
    dist.mkdir(parents=True, exist_ok=True)
    wheels = sorted(dist.glob(f"project_maintenance_workflow-{args.version}-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one wheel for {args.version}, found {len(wheels)}")
    core = dist / f"project-hooks-core-{args.version}.zip"
    with zipfile.ZipFile(core, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((root / "project_hooks").rglob("*.py")):
            if "__pycache__" not in path.parts:
                info = zipfile.ZipInfo(path.relative_to(root).as_posix())
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    wheel = wheels[0]
    base = (
        "https://github.com/DawnDust/project-maintenance-template/"
        f"releases/download/v{args.version}/"
    )
    manifest = {
        "version": args.version,
        "launcher_min_version": "1.0.0",
        "event_schema": {"minimum": 1, "maximum": 2},
        "core": {
            "file": core.name,
            "url": base + core.name,
            "sha256": digest(core),
        },
        "wheel": {
            "file": wheel.name,
            "url": base + wheel.name,
            "sha256": digest(wheel),
        },
    }
    (dist / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
