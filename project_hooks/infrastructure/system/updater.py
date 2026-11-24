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

from ... import EXECUTABLE_NAME, __version__
from ..config_paths import resolve_installation_path
from ..git.build_identity import (
    SOFTWARE_SOURCE_PATHS,
    build_identity,
    build_warning,
    display_version,
    exe_matches_repository,
)
from .runtime import ACTIVE_ENV, UPDATE_CHILD_ENV, is_frozen, runtime_root, cached_executable
from .project_manager import (INSTALLATION_PATH, TEMPLATE_VERSION, apply_project_update, preflight_update,
                             template_files, AGENTS_BLOCK, GITIGNORE_BLOCK, GITATTRIBUTES_BLOCK,
                             AGENTS_BEGIN, AGENTS_END, GITIGNORE_BEGIN, GITIGNORE_END,
                             GITATTRIBUTES_BEGIN, GITATTRIBUTES_END, extract_block)
from .build_notes import read_build_notes
from ..persistence.store import SCHEMA_VERSION, load_events


LATEST_MANIFEST = (
    "https://github.com/DawnDust/workflow-monitor/"
    "releases/latest/download/release-manifest.json"
)
VERSIONED_MANIFEST = (
    "https://github.com/DawnDust/workflow-monitor/"
    "releases/download/v{version}/release-manifest.json"
)
LATEST_RELEASE_API = (
    "https://api.github.com/repos/DawnDust/workflow-monitor/releases/latest"
)
LAUNCHER_VERSION = "1.0.0"


class UpdateError(RuntimeError):
    pass


class UpdateCompatibilityError(ValueError):
    pass


class UpdateApplyError(RuntimeError):
    def __init__(self, diagnostic_record: dict):
        self.diagnostic_record = diagnostic_record
        super().__init__(str(diagnostic_record.get("summary") or "新版 EXE 迁移失败"))


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


def cache_executable(content: bytes, version: str, project_root: Path, *, build_id: str | None = None) -> Path:
    """Store a verified executable inside the project's ignored runtime directory."""
    target = cached_executable(project_root, version, build_id)
    folder = target.parent
    content_digest = hashlib.sha256(content).hexdigest()
    digest_file = folder / ".exe-sha256"
    if (target.is_file() and digest_file.is_file()
            and digest_file.read_text(encoding="ascii").strip() == content_digest
            and hashlib.sha256(target.read_bytes()).hexdigest() == content_digest):
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


def project_version(root: Path) -> str | None:
    path = resolve_installation_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("application_version") or data.get("core_version")
        return str(value) if value else None
    except (OSError, ValueError):
        return None


def project_template_version(root: Path) -> int | None:
    path = resolve_installation_path(root)
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("template_version"))
    except (OSError, ValueError, TypeError):
        return None


def validate_project_event_schema(root: Path, minimum: int, maximum: int) -> None:
    for event in load_events(root / "maintenance/events.jsonl"):
        if not minimum <= int(event["schema_version"]) <= maximum:
            raise UpdateCompatibilityError(
                f"事件 {event['event_id']} 的 schema_version={event['schema_version']} "
                f"不受目标版本支持（{minimum}..{maximum}）"
            )


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
        "repos/DawnDust/workflow-monitor/releases/latest",
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
    validate_project_event_schema(root, minimum, maximum)
    if check_only:
        return check_update(root, manifest)
    current_build = build_identity().get("build_id")
    latest_build = (manifest.get("build_identity") or {}).get("build_id")
    same_build = not latest_build or current_build == latest_build
    if (project_version(root) == target and __version__ == target and same_build
            and (project_template_version(root) or 0) >= TEMPLATE_VERSION):
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
    env.pop("PROJECT_HOOKS_PORTABLE_ROOT", None)
    env[UPDATE_CHILD_ENV] = "1"
    completed = subprocess.run(
        [
            str(selected), "--project", str(root), "_apply-update",
            "--target-version", target,
            "--minimum-event-schema", str(minimum),
            "--maximum-event-schema", str(maximum),
            "--executable-path", str(selected),
        ],
        env=env, text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if completed.returncode != 0:
        try:
            failure = json.loads(completed.stderr)
        except (json.JSONDecodeError, TypeError):
            failure = None
        if (isinstance(failure, dict)
                and failure.get("format") == "workflow-monitor-update-error"
                and isinstance(failure.get("diagnostic"), dict)):
            raise UpdateApplyError(failure["diagnostic"])
        raise UpdateError(completed.stderr.strip() or completed.stdout.strip() or "新版 EXE 迁移失败")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise UpdateError("新版 EXE 返回了无效迁移结果") from exc
    result["application_version"] = target
    result["launcher_version"] = LAUNCHER_VERSION
    return result


def template_hashes() -> dict[str, str]:
    values = template_files()
    values.update({"AGENTS.md:block": AGENTS_BLOCK, ".gitignore:block": GITIGNORE_BLOCK,
                   ".gitattributes:block": GITATTRIBUTES_BLOCK})
    return {key: hashlib.sha256(value.encode("utf-8")).hexdigest() for key, value in values.items()}


def _template_preview(root: Path, desired: dict[str, str]) -> dict:
    try:
        installed = json.loads(resolve_installation_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        installed = {}
    blocks = {"AGENTS.md:block": (AGENTS_BEGIN, AGENTS_END),
              ".gitignore:block": (GITIGNORE_BEGIN, GITIGNORE_END),
              ".gitattributes:block": (GITATTRIBUTES_BEGIN, GITATTRIBUTES_END)}
    changed, conflicts = [], []
    for key, expected in desired.items():
        if not isinstance(key, str) or not isinstance(expected, str):
            raise UpdateError("目标 EXE 的模板摘要格式错误")
        relative = key.removesuffix(":block")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or Path(relative).is_absolute():
            raise UpdateError("目标 EXE 的模板路径超出项目")
        current = path.read_bytes() if path.is_file() else None
        if key in blocks:
            block = extract_block(current.decode("utf-8") if current else "", *blocks[key])
            current = block.encode("utf-8") if block is not None else None
        actual = hashlib.sha256(current).hexdigest() if current is not None else None
        previous = (installed.get("managed_files") or {}).get(key, {})
        if actual != expected:
            changed.append(key)
            if current is not None and (previous.get("customized") or actual != previous.get("hash")):
                conflicts.append(key)
    return {"changed": sorted(changed), "conflicts": sorted(conflicts)}


def inspect_executable(executable: Path, root: Path) -> dict:
    """Query this exact file, bypassing the project's cached runtime pointer."""
    env = os.environ.copy()
    env[ACTIVE_ENV] = "1"
    completed = subprocess.run([str(executable), "--project", str(root), "version"],
                               env=env, text=True, encoding="utf-8", capture_output=True,
                               timeout=60, check=False)
    if completed.returncode:
        raise UpdateError(completed.stderr.strip() or "无法读取本地 EXE 身份")
    try:
        report = json.loads(completed.stdout)
        identity = report["build_identity"]
        version_key(report["application_version"])
        cached_executable(root, report["application_version"], identity["build_id"])
        if identity.get("mode") != "frozen" or not report.get("template_hashes"):
            raise ValueError("缺少完整的冻结构建或模板信息")
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError("此 EXE 不支持可验证的本地升级；请使用包含构建和模板身份的新版") from exc
    return report


def run_local_update(root: Path, executable: Path, *, check_only: bool = False) -> dict:
    root, executable = root.resolve(), executable.resolve()
    if not executable.is_file():
        raise UpdateError("本地 EXE 不存在")
    # Hold the bytes whose identity is inspected; detect replacement during inspection.
    content = executable.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    report = inspect_executable(executable, root)
    if hashlib.sha256(executable.read_bytes()).hexdigest() != digest:
        raise UpdateError("读取身份期间本地 EXE 发生变化")
    target, identity = report["application_version"], report["build_identity"]
    supported = report.get("event_schema") or {"minimum": 1, "maximum": report["schema_version"]}
    blockers = []
    try:
        preflight_update(root)
        validate_project_event_schema(root, int(supported["minimum"]), int(supported["maximum"]))
        if project_version(root) and version_key(target) < version_key(project_version(root)):
            raise UpdateError("本地升级不支持版本降级")
    except (ValueError, RuntimeError) as exc:
        blockers.append(str(exc))
    match = exe_matches_repository(root, identity)
    if match is False:
        blockers.append("源码与目标 EXE 不一致；需要对应提交或补丁，不能仅替换 EXE 宣称源码已更新")
    preview = {"status": "blocked" if blockers else "ready", "dry_run": check_only,
               "target_build_identity": identity, "application_version": target,
               "source_sync": "already-matched" if match is True else "source-required" if match is False else "not-applicable",
               "source_message": "已一致，无需源码修改" if match is True else "源码尚未同步" if match is False else "此项目不是软件源码仓库",
               "template_version": report["template_version"], "blockers": blockers,
               **_template_preview(root, report["template_hashes"])}
    if report["template_version"] >= 19:
        from .worktree_migration import plan_migration
        layout = plan_migration(root)
        preview["linked_worktree_migration"] = layout
        if layout["blockers"]:
            blockers.extend(str(item.get("reason") or item) for item in layout["blockers"])
            preview["status"] = "blocked"
    if check_only:
        return preview
    if blockers:
        raise UpdateError("；".join(blockers))
    selected = cache_executable(content, target, root, build_id=identity["build_id"])
    env = os.environ.copy()
    env[ACTIVE_ENV], env[UPDATE_CHILD_ENV] = "1", "1"
    completed = subprocess.run([
        str(selected), "--project", str(root), "_apply-update", "--target-version", target,
        "--minimum-event-schema", str(supported["minimum"]), "--maximum-event-schema", str(supported["maximum"]),
        "--executable-path", str(selected), "--cache-build-id", identity["build_id"],
    ], env=env, text=True, encoding="utf-8", capture_output=True, check=False)
    if completed.returncode:
        raise UpdateError(completed.stderr.strip() or "本地 EXE 升级失败，原运行指针保留")
    try:
        result = json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise UpdateError("本地 EXE 升级回执无法解析") from exc
    if result.get("running_build_id") != identity["build_id"]:
        raise UpdateError("升级回执的实际运行构建不匹配")
    result.update({"source_sync": preview["source_sync"], "source_message": preview["source_message"],
                   "target_build_identity": identity, "executable_replacement": "cached-and-selected"})
    return result


def version_report(root: Path | None) -> dict:
    identity = build_identity()
    project_build_id = None
    project_build_input_fingerprint = None
    if root:
        try:
            installation = json.loads(resolve_installation_path(root).read_text(encoding="utf-8"))
            project_identity = installation.get("build_identity") or {}
            project_build_id = project_identity.get("build_id")
            project_build_input_fingerprint = (
                project_identity.get("build_input_fingerprint") or project_identity.get("source_tree")
            )
        except (OSError, ValueError):
            pass
    return {
        "application_version": __version__,
        "display_version": display_version(__version__, identity),
        "build_channel": identity.get("channel") or (
            "source" if identity.get("mode") == "source" else "stable"
        ),
        "project_version": project_version(root) if root else None,
        "schema_version": SCHEMA_VERSION,
        "event_schema": {"minimum": 1, "maximum": SCHEMA_VERSION},
        "template_version": TEMPLATE_VERSION,
        "template_hashes": template_hashes(),
        "test_build_notes_available": read_build_notes()["available"],
        "build_identity": identity,
        "project_build_id": project_build_id,
        "build_warning": build_warning(project_build_id, project_build_input_fingerprint),
        "exe_repository_match": exe_matches_repository(root, identity) if root else None,
    }
