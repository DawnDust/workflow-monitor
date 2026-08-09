"""Safe, declarative workbench package inspection, import, and export."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from ...application.workbench_service import (
    LEGACY_ITEM_TYPES,
    WORKBENCH_KINDS,
    WorkbenchError,
    normalize_workbench_item,
    sha256_file,
    validate_item_id,
)


WORKBENCH_ROOT = Path("workbench")
WORKBENCH_LOCAL = WORKBENCH_ROOT / "local"
WORKBENCH_IMPORTED = WORKBENCH_ROOT / "imported"
WORKBENCH_EXPORTS = WORKBENCH_ROOT / "exports"
PACKAGE_SCHEMA_VERSION = 2
SUPPORTED_PACKAGE_SCHEMA_VERSIONS = (1, 2)
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_BYTES = 50 * 1024 * 1024
MAX_MARKDOWN_BYTES = 1024 * 1024
MAX_ITEMS = 200
_PACKAGE_PART = __import__("re").compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


class WorkbenchPackageError(WorkbenchError):
    pass


def ensure_workbench_directories(root: Path) -> None:
    for relative in (WORKBENCH_LOCAL, WORKBENCH_IMPORTED, WORKBENCH_EXPORTS):
        (root / relative).mkdir(parents=True, exist_ok=True)


def _package_part(value: object, label: str) -> str:
    result = str(value or "").strip().lower()
    if not _PACKAGE_PART.fullmatch(result):
        raise WorkbenchPackageError(f"{label}只允许小写字母、数字、点、下划线和连字符，最长 80 个字符")
    return result


def _plain_text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    result = str(value or "").replace("\r", " ").strip()
    if required and not result:
        raise WorkbenchPackageError(f"{label}不能为空")
    if len(result) > maximum:
        raise WorkbenchPackageError(f"{label}不能超过 {maximum} 个字符")
    return result


def _safe_member(name: str) -> PurePosixPath:
    pure = PurePosixPath(name.replace("\\", "/"))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts or any(part in {"", "."} for part in pure.parts):
        raise WorkbenchPackageError(f"包内路径不安全: {name}")
    return pure


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def _canonical_package_digest(manifest: dict) -> str:
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_manifest(manifest: object, markdown: dict[str, bytes]) -> dict:
    if not isinstance(manifest, dict) or manifest.get("schema_version") not in SUPPORTED_PACKAGE_SCHEMA_VERSIONS:
        raise WorkbenchPackageError("工作台包 manifest schema_version 必须为 1 或 2")
    source_schema_version = int(manifest.get("source_schema_version") or manifest["schema_version"])
    package_id = _package_part(manifest.get("package_id"), "包 ID")
    version = _package_part(manifest.get("version"), "包版本")
    name = _plain_text(manifest.get("name"), "包名称", 200, required=True)
    author = _plain_text(manifest.get("author"), "包作者", 200, required=True)
    description = _plain_text(manifest.get("description"), "包说明", 1000)
    license_name = _plain_text(manifest.get("license"), "许可证", 200)
    raw_items = manifest.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise WorkbenchPackageError("工作台包必须至少包含一个条目")
    if len(raw_items) > MAX_ITEMS:
        raise WorkbenchPackageError(f"工作台包不能超过 {MAX_ITEMS} 个条目")
    seen: set[str] = set()
    items = []
    legacy_manifest_items: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise WorkbenchPackageError("工作台包条目必须是对象")
        original_id = validate_item_id(raw.get("item_id"))
        if original_id in seen:
            raise WorkbenchPackageError(f"工作台包条目 ID 重复: {original_id}")
        seen.add(original_id)
        taxonomy: dict
        if manifest["schema_version"] == 1:
            item_type = str(raw.get("item_type") or "")
            if item_type not in LEGACY_ITEM_TYPES:
                raise WorkbenchPackageError(f"未知的工作台旧版条目类型: {item_type}")
            taxonomy = {"item_type": item_type, "source_schema_version": 1}
        else:
            kind = str(raw.get("kind") or "")
            if kind not in WORKBENCH_KINDS:
                raise WorkbenchPackageError(f"未知的工作台主类型: {kind}")
            taxonomy = {
                "kind": kind, "purposes": raw.get("purposes") if isinstance(raw.get("purposes"), list) else [],
                "review_state": raw.get("review_state") or "reviewed",
                "review_note": raw.get("review_note") or "", "source_schema_version": source_schema_version,
            }
        file_name = _safe_member(str(raw.get("file") or "")).as_posix()
        if not file_name.startswith("items/") or not file_name.casefold().endswith(".md"):
            raise WorkbenchPackageError("条目 Markdown 必须位于包内 items/ 目录")
        content = markdown.get(file_name)
        if content is None:
            raise WorkbenchPackageError(f"工作台包缺少 Markdown: {file_name}")
        if len(content) > MAX_MARKDOWN_BYTES:
            raise WorkbenchPackageError(f"单个 Markdown 不能超过 {MAX_MARKDOWN_BYTES} 字节")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkbenchPackageError(f"Markdown 不是 UTF-8: {file_name}") from exc
        digest = hashlib.sha256(content).hexdigest()
        declared = str(raw.get("sha256") or "").lower()
        if declared != digest:
            raise WorkbenchPackageError(f"Markdown 哈希不一致: {file_name}")
        effective = "pkg-" + hashlib.sha256(f"{package_id}:{original_id}".encode("utf-8")).hexdigest()[:24]
        normalized = normalize_workbench_item({
            "item_id": effective,
            **taxonomy,
            "title": _plain_text(raw.get("title"), "标题", 200, required=True),
            "summary": _plain_text(raw.get("summary"), "摘要", 1000, required=True),
            "tags": raw.get("tags") if isinstance(raw.get("tags"), list) else [],
            "reference": _plain_text(raw.get("reference"), "参考信息", 1000),
            "status": "active", "origin": "imported",
            "package_id": package_id, "package_version": version,
            "package_author": author, "original_item_id": original_id,
            "content_sha256": digest,
            "path": (WORKBENCH_IMPORTED / package_id / version / file_name).as_posix(),
        })
        items.append({**normalized, "package_file": file_name})
        if manifest["schema_version"] == 1:
            legacy_manifest_items.append({
                "item_id": original_id, "item_type": item_type, "title": normalized["title"],
                "summary": normalized["summary"], "file": file_name, "tags": normalized["tags"],
                "reference": normalized["reference"], "sha256": digest,
            })
    source_package_sha256 = str(manifest.get("source_package_sha256") or "")
    if manifest["schema_version"] == 1:
        source_package_sha256 = _canonical_package_digest({
            "schema_version": 1, "package_id": package_id, "name": name, "version": version,
            "author": author, "description": description, "license": license_name,
            "items": legacy_manifest_items,
        })
    if source_schema_version == 1 and (len(source_package_sha256) != 64 or any(char not in "0123456789abcdef" for char in source_package_sha256)):
        raise WorkbenchPackageError("v1 来源包缺少有效内容哈希")
    normalized_manifest = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "source_schema_version": source_schema_version,
        "package_id": package_id, "name": name, "version": version,
        "author": author, "description": description, "license": license_name,
        "items": [{
            "item_id": item["original_item_id"], "kind": item["kind"],
            "title": item["title"], "summary": item["summary"],
            "file": item["package_file"], "purposes": item["purposes"], "tags": item["tags"],
            "review_state": item["review_state"], "review_note": item["review_note"],
            "reference": item["reference"], "sha256": item["content_sha256"],
        } for item in items],
    }
    if source_schema_version == 1:
        normalized_manifest["source_package_sha256"] = source_package_sha256
    return {
        **normalized_manifest, "items": items, "normalized_manifest": normalized_manifest,
        "package_sha256": source_package_sha256 or _canonical_package_digest(normalized_manifest),
    }


def inspect_package(archive_path: Path) -> dict:
    archive_path = archive_path.resolve()
    if not archive_path.is_file():
        raise WorkbenchPackageError("没有找到工作台包")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise WorkbenchPackageError(f"工作台包不能超过 {MAX_ARCHIVE_BYTES} 字节")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            files: dict[str, bytes] = {}
            total = 0
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if _is_symlink(info):
                    raise WorkbenchPackageError(f"工作台包不允许符号链接: {info.filename}")
                name = _safe_member(info.filename).as_posix()
                if name != "manifest.json" and not (name.startswith("items/") and name.casefold().endswith(".md")):
                    raise WorkbenchPackageError(f"工作台包包含不允许的文件: {name}")
                if name in files:
                    raise WorkbenchPackageError(f"工作台包路径重复: {name}")
                total += info.file_size
                if total > MAX_EXTRACTED_BYTES:
                    raise WorkbenchPackageError("工作台包解压后体积过大")
                files[name] = archive.read(info)
    except (zipfile.BadZipFile, OSError) as exc:
        raise WorkbenchPackageError("工作台包不是有效 ZIP") from exc
    manifest_bytes = files.pop("manifest.json", None)
    if manifest_bytes is None:
        raise WorkbenchPackageError("工作台包缺少 manifest.json")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkbenchPackageError("manifest.json 不是有效 UTF-8 JSON") from exc
    validated = _validate_manifest(manifest, files)
    allowed = {item["package_file"] for item in validated["items"]}
    extras = sorted(set(files) - allowed)
    if extras:
        raise WorkbenchPackageError("工作台包包含未登记 Markdown: " + ", ".join(extras))
    return validated


def _installed_digest(directory: Path) -> str | None:
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        markdown = {
            item["file"]: (directory / item["file"]).read_bytes()
            for item in manifest.get("items", [])
        }
        return _validate_manifest(manifest, markdown)["package_sha256"]
    except (OSError, ValueError, KeyError, TypeError, WorkbenchError):
        return None


def migrate_installed_package_manifests(root: Path) -> list[str]:
    """Normalize installed v1 manifests to v2 without changing Markdown content."""
    changed: list[str] = []
    imported_root = root / WORKBENCH_IMPORTED
    if not imported_root.is_dir():
        return changed
    for manifest_path in sorted(imported_root.glob("*/*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") == PACKAGE_SCHEMA_VERSION:
            continue
        directory = manifest_path.parent
        markdown = {str(item["file"]): (directory / str(item["file"])).read_bytes() for item in manifest.get("items", [])}
        validated = _validate_manifest(manifest, markdown)
        descriptor, temp_name = tempfile.mkstemp(prefix="manifest-", suffix=".json", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(validated["normalized_manifest"], handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, manifest_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        changed.append(manifest_path.relative_to(root).as_posix())
    return changed


def workbench_package_consistency_errors(root: Path, events: list[dict]) -> list[str]:
    """Validate imported package directories against append-only import receipts."""
    errors: list[str] = []
    expected: dict[tuple[str, str], str] = {}
    for event in events:
        if event.get("event_type") != "workbench.package_imported":
            continue
        payload = event.get("payload") or {}
        key = (str(payload.get("package_id") or ""), str(payload.get("version") or ""))
        expected[key] = str(payload.get("package_sha256") or "")
    imported_root = root / WORKBENCH_IMPORTED
    discovered: set[tuple[str, str]] = set()
    if imported_root.is_dir():
        for package_dir in imported_root.iterdir():
            if not package_dir.is_dir():
                continue
            for version_dir in package_dir.iterdir():
                if not version_dir.is_dir():
                    continue
                key = (package_dir.name, version_dir.name)
                discovered.add(key)
                digest = _installed_digest(version_dir)
                if digest is None:
                    errors.append(f"工作台导入包无效: {version_dir.relative_to(root).as_posix()}")
                elif key not in expected:
                    errors.append(f"工作台导入包未登记: {version_dir.relative_to(root).as_posix()}")
                elif expected[key] != digest:
                    errors.append(f"工作台导入包哈希不一致: {version_dir.relative_to(root).as_posix()}")
                allowed = {"manifest.json"}
                try:
                    manifest = json.loads((version_dir / "manifest.json").read_text(encoding="utf-8"))
                    allowed.update(str(item.get("file") or "") for item in manifest.get("items", []))
                except (OSError, ValueError, TypeError):
                    pass
                actual = {
                    path.relative_to(version_dir).as_posix()
                    for path in version_dir.rglob("*") if path.is_file()
                }
                extras = sorted(actual - allowed)
                if extras:
                    errors.append(f"工作台导入包包含未登记文件: {version_dir.relative_to(root).as_posix()} -> {', '.join(extras)}")
    for key in sorted(set(expected) - discovered):
        errors.append(f"工作台导入包目录缺失: workbench/imported/{key[0]}/{key[1]}")
    return errors


def inspect_package_for_project(root: Path, archive_path: Path) -> dict:
    inspected = inspect_package(archive_path)
    destination = root / WORKBENCH_IMPORTED / inspected["package_id"] / inspected["version"]
    if destination.exists():
        installed = _installed_digest(destination)
        project_status = "already_imported" if installed == inspected["package_sha256"] else "version_conflict"
    elif (root / WORKBENCH_IMPORTED / inspected["package_id"]).is_dir():
        project_status = "new_version"
    else:
        project_status = "new_package"
    return {**inspected, "project_status": project_status, "destination": destination.relative_to(root).as_posix()}


def import_package(root: Path, archive_path: Path) -> dict:
    inspected = inspect_package(archive_path)
    destination = root / WORKBENCH_IMPORTED / inspected["package_id"] / inspected["version"]
    if destination.exists():
        digest = _installed_digest(destination)
        if digest == inspected["package_sha256"]:
            return {**inspected, "status": "unchanged", "destination": destination.relative_to(root).as_posix()}
        raise WorkbenchPackageError("相同包 ID 和版本已存在，但内容哈希不同")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="workbench-import-", dir=destination.parent))
    try:
        with zipfile.ZipFile(archive_path.resolve()) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative = _safe_member(info.filename)
                target = temp.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        (temp / "manifest.json").write_text(
            json.dumps(inspected["normalized_manifest"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        os.replace(temp, destination)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    items = []
    for raw in inspected["items"]:
        record = dict(raw)
        record.pop("package_file", None)
        items.append(normalize_workbench_item(record))
    return {**inspected, "items": items, "status": "imported", "destination": destination.relative_to(root).as_posix()}


def remove_imported_package(root: Path, result: dict) -> None:
    if result.get("status") != "imported":
        return
    destination = (root / str(result.get("destination") or "")).resolve()
    imported = (root / WORKBENCH_IMPORTED).resolve()
    try:
        destination.relative_to(imported)
    except ValueError:
        return
    if destination.is_dir():
        shutil.rmtree(destination)


def export_package(root: Path, items: list[dict], *, package_id: str, name: str, version: str,
                   author: str, description: str = "", license_name: str = "") -> dict:
    if not items:
        raise WorkbenchPackageError("至少选择一个工作台条目")
    package_id = _package_part(package_id, "包 ID")
    version = _package_part(version, "包版本")
    name = _plain_text(name, "包名称", 200, required=True)
    author = _plain_text(author, "包作者", 200, required=True)
    description = _plain_text(description, "包说明", 1000)
    license_name = _plain_text(license_name, "许可证", 200)
    manifest_items = []
    contents: dict[str, bytes] = {}
    used: set[str] = set()
    for item in items:
        original = validate_item_id(item.get("original_item_id") or item.get("item_id"))
        candidate = original
        index = 2
        while candidate in used:
            suffix = f"-{index}"
            candidate = (original[:80 - len(suffix)] + suffix).rstrip("-")
            index += 1
        used.add(candidate)
        path = f"items/{candidate}.md"
        if item.get("path"):
            source = (root / item["path"]).resolve()
            if not source.is_file() or sha256_file(source) != item.get("content_sha256"):
                raise WorkbenchPackageError(f"条目 {item['item_id']} 的 Markdown 缺失或已变化")
            content = source.read_bytes()
        else:
            body = f"# {item['title']}\n\n{item['summary']}\n"
            if item.get("usage_hint"):
                body += f"\n## 使用提示\n\n{item['usage_hint']}\n"
            content = body.encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        contents[path] = content
        manifest_items.append({
            "item_id": candidate, "kind": item["kind"],
            "title": item["title"], "summary": item["summary"], "file": path,
            "purposes": item.get("purposes") or [], "tags": item.get("tags") or [],
            "review_state": item.get("review_state") or "reviewed",
            "review_note": item.get("review_note") or "", "reference": item.get("reference") or "",
            "sha256": digest,
        })
    manifest = {
        "schema_version": PACKAGE_SCHEMA_VERSION, "source_schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": package_id,
        "name": name, "version": version, "author": author,
        "description": description, "license": license_name, "items": manifest_items,
    }
    ensure_workbench_directories(root)
    output = root / WORKBENCH_EXPORTS / f"{package_id}-{version}.workbench.zip"
    descriptor, temp_name = tempfile.mkstemp(prefix="workbench-export-", suffix=".zip", dir=output.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            for path, content in contents.items():
                archive.writestr(path, content)
        if output.exists():
            raise WorkbenchPackageError("导出目标已存在；请使用新版本，现有包不会被覆盖")
        os.replace(temp_name, output)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return {"status": "exported", "output": output.relative_to(root).as_posix(), "package_id": package_id, "version": version, "items": len(items)}
