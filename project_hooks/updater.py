"""GitHub Release client and atomic versioned-core updater."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from . import __version__
from .launcher import ACTIVE_ENV, cache_root
from .project_manager import (
    INSTALLATION_PATH,
    ProjectManagerError,
    apply_project_update,
    preflight_update,
)
from .store import load_events


LATEST_MANIFEST = (
    "https://github.com/DawnDust/project-maintenance-template/"
    "releases/latest/download/release-manifest.json"
)
VERSIONED_MANIFEST = (
    "https://github.com/DawnDust/project-maintenance-template/"
    "releases/download/v{version}/release-manifest.json"
)
LAUNCHER_VERSION = "1.0.0"


class UpdateError(RuntimeError):
    pass


def version_key(value: str) -> tuple[int, int, int]:
    parts = value.removeprefix("v").split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise UpdateError(f"非法语义版本: {value}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def fetch_bytes(url: str) -> bytes:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "project-hooks/" + __version__})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"无法下载 {url}: {exc}") from exc


def load_manifest(url: str) -> dict:
    try:
        manifest = json.loads(fetch_bytes(url).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("Release manifest 不是合法 UTF-8 JSON") from exc
    required = {"version", "launcher_min_version", "core", "wheel"}
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise UpdateError("Release manifest 缺少必要字段")
    version_key(str(manifest["version"]))
    if version_key(LAUNCHER_VERSION) < version_key(str(manifest["launcher_min_version"])):
        wheel = manifest["wheel"].get("url") or manifest["wheel"].get("file")
        raise UpdateError(
            "当前稳定启动器过旧；请执行 "
            f"`pipx install --force {wheel}` 后重试"
        )
    for name in ("core", "wheel"):
        asset = manifest[name]
        if not isinstance(asset, dict) or not asset.get("file") or not asset.get("sha256"):
            raise UpdateError(f"Release manifest 的 {name} 资产信息不完整")
    return manifest


def asset_url(manifest_url: str, asset: dict) -> str:
    if asset.get("url"):
        return str(asset["url"])
    return urllib.parse.urljoin(manifest_url, str(asset["file"]))


def verify_digest(content: bytes, expected: str) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual.lower() != expected.lower():
        raise UpdateError(f"下载摘要不匹配：期望 {expected}，实际 {actual}")


def safe_extract_core(content: bytes, version: str) -> Path:
    cores = cache_root() / "cores"
    target = cores / version
    content_digest = hashlib.sha256(content).hexdigest()
    digest_file = target / ".core-sha256"
    if ((target / "project_hooks/__init__.py").is_file()
            and digest_file.is_file()
            and digest_file.read_text(encoding="ascii").strip() == content_digest):
        return target
    cores.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{version}-", dir=cores))
    archive = staging / "core.zip"
    archive.write_bytes(content)
    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise UpdateError(f"核心包包含不安全路径: {member.filename}")
            bundle.extractall(staging)
        archive.unlink()
        if not (staging / "project_hooks/__init__.py").is_file():
            raise UpdateError("核心包缺少 project_hooks/__init__.py")
        (staging / ".core-sha256").write_text(content_digest + "\n", encoding="ascii")
        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)
        return target
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def write_pointer(version: str) -> None:
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    pointer = root / "current.json"
    descriptor, name = tempfile.mkstemp(prefix="current-", suffix=".json", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"version": version}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, pointer)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def project_version(root: Path) -> str | None:
    path = root / INSTALLATION_PATH
    if not path.is_file():
        return None
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("core_version"))
    except (OSError, ValueError):
        return None


def check_update(root: Path, manifest: dict) -> dict:
    current = project_version(root)
    target = str(manifest["version"])
    return {
        "status": "update-available" if current != target or __version__ != target else "current",
        "installed_core": __version__,
        "project_core": current,
        "latest_core": target,
    }


def run_update(
    root: Path,
    *,
    target_version: str | None = None,
    check_only: bool = False,
    manifest_url: str | None = None,
) -> dict:
    root = root.resolve()
    preflight_update(root)
    normalized_target = target_version.removeprefix("v") if target_version else None
    url = manifest_url or (
        VERSIONED_MANIFEST.format(version=normalized_target)
        if target_version else LATEST_MANIFEST
    )
    manifest = load_manifest(url)
    target = str(manifest["version"])
    if normalized_target and version_key(target) != version_key(normalized_target):
        raise UpdateError(f"Release manifest 版本 {target} 与请求版本 {target_version} 不一致")
    supported = manifest.get("event_schema", {"minimum": 1, "maximum": 2})
    try:
        minimum, maximum = int(supported["minimum"]), int(supported["maximum"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError("Release manifest 的 event_schema 范围无效") from exc
    for event in load_events(root / "maintenance/events.jsonl"):
        if not minimum <= int(event["schema_version"]) <= maximum:
            raise UpdateError(
                f"事件 {event['event_id']} 的 schema_version={event['schema_version']} "
                f"不受目标版本支持（{minimum}..{maximum}）"
            )
    if check_only:
        return check_update(root, manifest)
    if project_version(root) == target and __version__ == target:
        return {
            "status": "current",
            "project": str(root),
            "core_version": target,
            "launcher_version": LAUNCHER_VERSION,
            "changed": [],
            "conflicts": [],
        }
    core_asset = manifest["core"]
    content = fetch_bytes(asset_url(url, core_asset))
    verify_digest(content, str(core_asset["sha256"]))
    core = safe_extract_core(content, target)
    if target == __version__ and Path(__file__).resolve().is_relative_to(core):
        result = apply_project_update(root, target)
    else:
        env = os.environ.copy()
        env[ACTIVE_ENV] = "1"
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(core) + (os.pathsep + existing if existing else "")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "project_hooks",
                "--project",
                str(root),
                "_apply-update",
                "--target-version",
                target,
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise UpdateError(completed.stderr.strip() or completed.stdout.strip() or "项目迁移失败")
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise UpdateError("新版核心返回了无效迁移结果") from exc
    write_pointer(target)
    result["launcher_version"] = LAUNCHER_VERSION
    return result


def version_report(root: Path | None) -> dict:
    return {
        "launcher_version": LAUNCHER_VERSION,
        "core_version": __version__,
        "project_version": project_version(root) if root else None,
        "schema_version": 2,
    }
