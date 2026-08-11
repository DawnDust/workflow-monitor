"""Build the EXE-only GitHub Release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from project_hooks import EXECUTABLE_NAME, __version__
from project_hooks.infrastructure.persistence.store import SCHEMA_VERSION


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--sbom", type=Path)
    parser.add_argument("--authenticode-json", type=Path)
    args = parser.parse_args()
    if args.version != __version__:
        raise SystemExit(
            f"release version mismatch: tag={args.version}, application={__version__}"
        )
    dist = args.dist.resolve()
    executable = dist / EXECUTABLE_NAME
    if not executable.is_file():
        raise SystemExit(f"missing Windows executable: {executable}")
    base = (
        "https://github.com/DawnDust/workflow-monitor/"
        f"releases/download/v{args.version}/"
    )
    build_info_path = ROOT / "build" / "build-info.json"
    try:
        build_identity = json.loads(build_info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        build_identity = {"build_id": "unknown"}
    authenticode = {"status": "unsigned"}
    if args.authenticode_json:
        try:
            authenticode = json.loads(args.authenticode_json.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"invalid Authenticode metadata: {exc}") from exc
        if authenticode.get("status") not in {"signed", "unsigned"}:
            raise SystemExit("invalid Authenticode status")
    manifest = {
        "version": args.version,
        "build_identity": build_identity,
        "launcher_min_version": "1.0.0",
        "event_schema": {"minimum": 1, "maximum": SCHEMA_VERSION},
        "authenticode": authenticode,
        "windows_exe": {
            "file": executable.name,
            "url": base + executable.name,
            "sha256": digest(executable),
        },
    }
    if args.sbom:
        sbom = args.sbom.resolve()
        if not sbom.is_file():
            raise SystemExit(f"missing SPDX SBOM: {sbom}")
        manifest["sbom"] = {
            "file": sbom.name,
            "format": "spdx-json",
            "sha256": digest(sbom),
        }
    (dist / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
