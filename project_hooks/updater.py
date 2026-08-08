"""GitHub Release client for the project-local Windows executable."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import EXECUTABLE_NAME, __version__
from .build_identity import (
    SOFTWARE_SOURCE_PATHS,
    build_identity,
    build_warning,
    exe_matches_repository,
)
from .launcher import ACTIVE_ENV, is_frozen, runtime_root
from .project_manager import INSTALLATION_PATH, apply_project_update, preflight_update
from .store import SCHEMA_VERSION, load_events


LATEST_MANIFEST = (
    "https://github.com/DawnDust/project-maintenance-template/"
    "releases/latest/download/release-manifest.json"
)
VERSIONED_MANIFEST = (
    "https://github.com/DawnDust/project-maintenance-template/"
    "releases/download/v{version}/release-manifest.json"
)
LATEST_RELEASE_API = (
    "https://api.github.com/repos/DawnDust/project-maintenance-template/releases/latest"
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
    required = {"version", "launcher_min_version", "windows_exe"}
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise UpdateError("Release manifest 缺少必要字段")
    version_key(str(manifest["version"]))
    executable = manifest["windows_exe"]
    if not isinstance(executable, dict) or not executable.get("file") or not executable.get("sha256"):
        raise UpdateError("Release manifest 的 Windows EXE 资产信息不完整")
    if version_key(LAUNCHER_VERSION) < version_key(str(manifest["launcher_min_version"])):
        download = executable.get("url") or executable.get("file")
        raise UpdateError(
            "当前稳定启动 EXE 过旧；请从 GitHub Release 下载新版 "
            f"{download} 并替换项目根目录的 {EXECUTABLE_NAME}"
        )
    return manifest


def asset_url(manifest_url: str, asset: dict) -> str:
    if asset.get("url"):
        return str(asset["url"])
    return urllib.parse.urljoin(manifest_url, str(asset["file"]))


def verify_digest(content: bytes, expected: str) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual.lower() != expected.lower():
        raise UpdateError(f"下载摘要不匹配：期望 {expected}，实际 {actual}")


def cache_executable(content: bytes, version: str, project_root: Path) -> Path:
    """Store a verified executable inside the project's ignored runtime directory."""
    folder = runtime_root(project_root) / "executables" / version
    target = folder / EXECUTABLE_NAME
    content_digest = hashlib.sha256(content).hexdigest()
    digest_file = folder / ".exe-sha256"
    if (target.is_file() and digest_file.is_file()
            and digest_file.read_text(encoding="ascii").strip() == content_digest):
        return target
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="workflow-monitor-", suffix=".exe", dir=folder)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
        digest_file.write_text(content_digest + "\n", encoding="ascii")
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return target


def write_pointer(version: str, project_root: Path, executable: Path) -> None:
    runtime = runtime_root(project_root)
    runtime.mkdir(parents=True, exist_ok=True)
    pointer = runtime / "current.json"
    descriptor, name = tempfile.mkstemp(prefix="current-", suffix=".json", dir=runtime)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                {"version": version, "executable": str(executable.resolve())},
                handle, ensure_ascii=False, indent=2,
            )
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
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("application_version") or data.get("core_version")
        return str(value) if value else None
    except (OSError, ValueError):
        return None


def check_update(root: Path, manifest: dict) -> dict:
    current = project_version(root)
    target = str(manifest["version"])
    current_build = build_identity().get("build_id")
    latest_build = (manifest.get("build_identity") or {}).get("build_id")
    build_mismatch = bool(
        current == target == __version__ and latest_build and current_build != latest_build
    )
    return {
        "status": (
            "update-available" if current != target or __version__ != target
            else "different-build" if build_mismatch else "current"
        ),
        "application_version": __version__,
        "project_version": current,
        "latest_version": target,
        "current_build_id": current_build,
        "latest_build_id": latest_build,
        "build_warning": (
            f"同一版本存在不同构建：当前 {current_build}，发布 {latest_build}"
            if build_mismatch else None
        ),
    }


def check_latest_update(root: Path, manifest_url: str | None = None) -> dict:
    """Read the latest release metadata without running update preflight or changing the project."""
    manifest = load_manifest(manifest_url or LATEST_MANIFEST)
    return check_update(root.resolve(), manifest)


def _load_release_api(url: str, gh_path: str) -> dict:
    """Read Release metadata, using an authenticated gh session for private repos."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "project-hooks/" + __version__,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            content = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in {403, 404}:
            raise UpdateError(f"无法查询 GitHub Release: HTTP {exc.code}") from exc
        try:
            completed = subprocess.run(
                [
                    "gh", "api", gh_path,
                ],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as fallback_exc:
            raise UpdateError("无法查询 GitHub Release；匿名访问失败且 gh 不可用") from fallback_exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise UpdateError(f"无法查询 GitHub Release: {detail or 'gh 查询失败'}")
        content = completed.stdout.encode("utf-8")
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"无法查询 GitHub Release: {exc}") from exc
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub Release 元数据不是合法 JSON") from exc
    if not isinstance(value, dict):
        raise UpdateError("GitHub Release 元数据格式无效")
    return value


def _load_latest_release() -> dict:
    return _load_release_api(
        LATEST_RELEASE_API,
        "repos/DawnDust/project-maintenance-template/releases/latest",
    )


def check_latest_release() -> dict:
    """Return the latest non-draft, non-prerelease GitHub Release metadata."""
    release = _load_latest_release()
    if release.get("draft") or release.get("prerelease"):
        raise UpdateError("GitHub latest Release 不是正式发布")
    tag = str(release.get("tag_name") or "")
    if not tag:
        raise UpdateError("GitHub latest Release 缺少标签")
    return {
        "status": "latest-release",
        "release_tag": tag,
        "release_version": tag.removeprefix("v"),
        "published_at": release.get("published_at"),
        "url": release.get("html_url"),
    }


def _git_paths(root: Path, *args: str) -> list[str] | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return [
        item.decode("utf-8", errors="replace").replace("\\", "/")
        for item in completed.stdout.split(b"\0") if item
    ]


def software_changes_since_release(root: Path, release_tag: str) -> list[str] | None:
    """List committed, tracked-worktree and untracked software changes after a Release."""
    root = root.resolve()
    tracked_source = _git_paths(
        root, "ls-files", "--error-unmatch", "-z", "--", "project_hooks/__init__.py",
    )
    if not tracked_source:
        return None
    changed = _git_paths(
        root, "diff", "--name-only", "-z", release_tag, "--", *SOFTWARE_SOURCE_PATHS,
    )
    if changed is None:
        return None
    untracked = _git_paths(
        root, "ls-files", "--others", "--exclude-standard", "-z", "--", *SOFTWARE_SOURCE_PATHS,
    ) or []
    return sorted(set(changed + untracked))


def refresh_software_delivery(root: Path, release: dict) -> dict:
    """Combine cached Release metadata with current local software state."""
    root = root.resolve()
    result = dict(release)
    result["exe_repository_match"] = exe_matches_repository(root)
    changes = software_changes_since_release(root, str(release.get("release_tag") or ""))
    result["software_source_available"] = changes is not None
    result["unreleased_software_changes"] = changes or []
    return result


def software_delivery_report(root: Path) -> dict:
    """Read latest formal Release metadata and current local software state."""
    return refresh_software_delivery(root, check_latest_release())


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
    supported = manifest.get(
        "event_schema", {"minimum": 1, "maximum": SCHEMA_VERSION}
    )
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
    current_build = build_identity().get("build_id")
    latest_build = (manifest.get("build_identity") or {}).get("build_id")
    same_build = not latest_build or current_build == latest_build
    if project_version(root) == target and __version__ == target and same_build:
        return {
            "status": "current",
            "project": str(root),
            "application_version": target,
            "launcher_version": LAUNCHER_VERSION,
            "changed": [],
            "conflicts": [],
        }
    if not is_frozen():
        raise UpdateError(f"更新只能通过项目根目录的 {EXECUTABLE_NAME} 执行")
    executable_asset = manifest["windows_exe"]
    content = fetch_bytes(asset_url(url, executable_asset))
    verify_digest(content, str(executable_asset["sha256"]))
    selected = cache_executable(content, target, root)
    env = os.environ.copy()
    env[ACTIVE_ENV] = "1"
    completed = subprocess.run(
        [
            str(selected), "--project", str(root), "_apply-update",
            "--target-version", target,
        ],
        env=env, text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if completed.returncode != 0:
        raise UpdateError(completed.stderr.strip() or completed.stdout.strip() or "新版 EXE 迁移失败")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise UpdateError("新版 EXE 返回了无效迁移结果") from exc
    write_pointer(target, root, selected)
    result["application_version"] = target
    result["launcher_version"] = LAUNCHER_VERSION
    return result


def version_report(root: Path | None) -> dict:
    identity = build_identity()
    project_build_id = None
    if root:
        try:
            installation = json.loads((root / INSTALLATION_PATH).read_text(encoding="utf-8"))
            project_build_id = (installation.get("build_identity") or {}).get("build_id")
        except (OSError, ValueError):
            pass
    return {
        "application_version": __version__,
        "project_version": project_version(root) if root else None,
        "schema_version": SCHEMA_VERSION,
        "build_identity": identity,
        "project_build_id": project_build_id,
        "build_warning": build_warning(project_build_id),
        "exe_repository_match": exe_matches_repository(root, identity) if root else None,
    }
