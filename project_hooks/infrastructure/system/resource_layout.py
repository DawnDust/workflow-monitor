"""Authoritative resource directory layout and read-only consistency helpers."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import unquote
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResourceDirectory:
    name: str
    kind: str
    label: str
    description: str
    legacy_name: str | None = None
    required: bool = True

    @property
    def relative_path(self) -> str:
        return f"resources/{self.name}"


RESOURCE_DIRECTORIES = (
    ResourceDirectory("source", "literature", "文献", "外部原始资料与来源证据", "source"),
    ResourceDirectory("data", "data", "数据", "原始、过程和处理后数据", "data"),
    ResourceDirectory("theory", "theory", "理论", "理论、假设、定义和推导", "theory"),
    ResourceDirectory("analysis", "simulation", "分析与程序", "分析代码、Notebook、实验与过程记录", "analysis"),
    ResourceDirectory("outputs", "output", "输出", "兼容既有输出；新程序使用各自的输出子目录", "outputs", False),
    ResourceDirectory("others", "other", "其他", "暂时无法可靠分类的资料"),
    ResourceDirectory("reports", "report", "报告", "面向外部受众的项目总结与报告"),
    ResourceDirectory("sparks", "spark", "灵感", "自由记录点子、问题、猜想和偶然发现"),
    ResourceDirectory("tutorials", "tutorial", "教程", "文献的教程、解读和学习材料"),
    ResourceDirectory("translations", "translation", "翻译", "文献翻译及对照材料"),
    ResourceDirectory("plans", "plan", "计划", "灵感的下一步行动与实施计划"),
)

OPTIONAL_CATALOG_KINDS = {"spark"}

RESOURCE_BY_KIND = {item.kind: item for item in RESOURCE_DIRECTORIES}
RESOURCE_BY_PATH = {item.relative_path.casefold(): item for item in RESOURCE_DIRECTORIES}
LEGACY_BY_PATH = {
    item.legacy_name.casefold(): item
    for item in RESOURCE_DIRECTORIES
    if item.legacy_name is not None
}
RESOURCE_KINDS = tuple(item.kind for item in RESOURCE_DIRECTORIES)
RESOURCE_KIND_LABELS = {item.kind: item.label for item in RESOURCE_DIRECTORIES}

IGNORED_DIRECTORY_NAMES = {
    ".git", ".project_hooks", "__pycache__", ".pytest_cache", "node_modules", ".venv",
}
IGNORED_FILE_NAMES = {".gitkeep", ".ds_store", "thumbs.db", "desktop.ini"}
IGNORED_FILE_SUFFIXES = (".tmp", ".temp", ".swp", ".swo", ".part")
BUNDLE_ENTRY_TYPE = "bundle"


def ensure_resource_directories(root: Path) -> list[str]:
    """Create the fixed layout and tracked placeholders without touching existing files."""
    created: list[str] = []
    for item in RESOURCE_DIRECTORIES:
        if not item.required:
            continue
        directory = root / item.relative_path
        if not directory.is_dir():
            directory.mkdir(parents=True, exist_ok=True)
            created.append(item.relative_path)
        placeholder = directory / ".gitkeep"
        if not placeholder.exists():
            placeholder.write_text("\n", encoding="utf-8")
            created.append(f"{item.relative_path}/.gitkeep")
    return created


def resource_for_path(relative: str | Path) -> ResourceDirectory | None:
    value = Path(relative).as_posix().strip("/")
    folded = value.casefold()
    for prefix, item in RESOURCE_BY_PATH.items():
        if folded == prefix or folded.startswith(prefix + "/"):
            return item
    parts = Path(value).parts
    if len(parts) >= 2 and parts[0].casefold() == "resources":
        return ResourceDirectory(parts[1], "other", parts[1], "自定义资料目录", required=False)
    return None


def legacy_resource_for_path(relative: str | Path) -> ResourceDirectory | None:
    value = Path(relative).as_posix().strip("/").casefold()
    first = value.split("/", 1)[0]
    return LEGACY_BY_PATH.get(first)


def is_indexable_resource_file(path: Path, *, base: Path) -> bool:
    try:
        relative = path.relative_to(base)
    except ValueError:
        return False
    if any(part.casefold() in IGNORED_DIRECTORY_NAMES for part in relative.parts[:-1]):
        return False
    name = path.name.casefold()
    if name in IGNORED_FILE_NAMES or name.startswith("~$") or name.endswith("~"):
        return False
    return not name.endswith(IGNORED_FILE_SUFFIXES)


def files_under(root: Path, relative_directory: str) -> list[Path]:
    directory = root / relative_directory
    if not directory.is_dir():
        return []
    result: list[Path] = []
    project_root = root.resolve()
    for path in directory.rglob("*"):
        if not path.is_file() or not is_indexable_resource_file(path, base=directory):
            continue
        try:
            path.resolve().relative_to(project_root)
        except ValueError:
            continue
        result.append(path)
    return sorted(result, key=lambda value: value.as_posix().casefold())


def is_simulation_bundle(item: dict) -> bool:
    return (
        item.get("kind") == "simulation"
        and (item.get("metadata") or {}).get("entry_type") == BUNDLE_ENTRY_TYPE
        and bool(item.get("path"))
    )


def bundle_contains(bundle_path: str, candidate_path: str) -> bool:
    bundle = Path(bundle_path)
    candidate = Path(candidate_path)
    try:
        candidate.relative_to(bundle)
    except ValueError:
        return False
    return candidate != bundle


def simulation_bundle_metadata(directory: Path, entrypoint: str | None = None) -> dict:
    if not directory.is_dir():
        raise ValueError(f"模拟资料包目录不存在: {directory}")
    normalized_entrypoint = None
    if entrypoint:
        entry = Path(entrypoint)
        if entry.is_absolute() or entry == Path(".") or ".." in entry.parts:
            raise ValueError("入口文件必须是资料包内的相对文件路径")
        target = (directory / entry).resolve()
        try:
            normalized_entrypoint = target.relative_to(directory.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError("入口文件必须位于模拟资料包内") from exc
        if not target.is_file():
            raise ValueError(f"模拟资料包入口文件不存在: {normalized_entrypoint}")

    digest = hashlib.sha256()
    total_bytes = 0
    files = files_under(directory, ".")
    for path in files:
        relative = path.relative_to(directory).as_posix()
        size = path.stat().st_size
        total_bytes += size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    result = {
        "entry_type": BUNDLE_ENTRY_TYPE,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "tree_sha256": digest.hexdigest(),
    }
    if normalized_entrypoint:
        result["entrypoint"] = normalized_entrypoint
    return result


def resource_directory_snapshot(root: Path, items: list[dict], folders: list[dict] | None = None) -> list[dict]:
    presets = {d.relative_path: d for d in RESOURCE_DIRECTORIES if d.required or (root / d.relative_path).is_dir()}
    registered = {f["path"]: f for f in folders or [] if f["status"] != "archived"}
    bundles = [i for i in items if is_simulation_bundle(i) and i.get("status") != "archived"]
    paths = set(presets) | set(registered)
    base = root / "resources"
    if base.is_dir():
        import os
        for current, names, _files in os.walk(base, followlinks=False):
            directory = Path(current)
            names[:] = [name for name in names if name.casefold() not in IGNORED_DIRECTORY_NAMES
                        and not (directory / name).is_symlink() and not (directory / name).is_junction()]
            for name in names:
                relative = (directory / name).relative_to(root).as_posix()
                if not any(bundle_contains(b["path"], relative) for b in bundles):
                    paths.add(relative)
    result = []
    for relative in sorted(paths, key=str.casefold):
        preset, folder = presets.get(relative), registered.get(relative)
        directory = root / relative
        selected = [i for i in items if i.get("path") and i.get("status") != "archived"
                    and bundle_contains(relative, i["path"])]
        files = files_under(root, relative)
        indexed = {str(i.get("path")).casefold() for i in selected}
        covered = [b for b in bundles if b["path"] == relative or bundle_contains(relative, b["path"])]
        unindexed = [f.relative_to(root).as_posix() for f in files
                     if f.relative_to(root).as_posix().casefold() not in indexed
                     and not any(bundle_contains(b["path"], f.relative_to(root).as_posix()) for b in covered)]
        kind = preset.kind if preset else resource_for_path(relative).kind
        status = "missing" if not directory.is_dir() else "attention" if (unindexed and kind not in OPTIONAL_CATALOG_KINDS) else "ok"
        result.append({"name": Path(relative).name, "path": relative, "kind": kind,
                       "folder_id": folder.get("folder_id") if folder else None,
                       "label": folder["name"] if folder else preset.label if preset else Path(relative).name,
                       "description": folder["purpose"] if folder else preset.description if preset else "待登记用途",
                       "registration": "registered" if folder else "preset" if preset else "unregistered",
                       "depth": len(Path(relative).parts) - 2, "exists": directory.is_dir(),
                       "actual_files": len(files), "indexed_files": len(files) - len(unindexed),
                       "logical_items": len(selected), "indexed_items": len(selected),
                       "bundle_count": len(covered), "contained_files": sum(b.get("metadata", {}).get("file_count", 0) for b in covered),
                       "unindexed_files": unindexed, "mismatched_files": [], "status": status})
    return result


def catalog_consistency_issues(root: Path, items: list[dict], folders: list[dict] | None = None) -> list[dict]:
    issues = []
    def add(code, message, path, item=None, *, hard=False, evidence=None):
        issues.append({"code": code, "message": message, "path": path,
                       "item_id": item.get("item_id") if item else None, "hard": hard,
                       "evidence": evidence or {"status": "unavailable"}})
    try:
        (root / "resources").resolve().relative_to(root.resolve())
    except ValueError:
        add("RESOURCE_ROOT_ESCAPE", "资料根目录指向项目外: resources", "resources", hard=True)
        return issues
    indexed = {}
    bundles = [i for i in items if i.get("status") != "archived" and is_simulation_bundle(i)]
    for index, bundle in enumerate(bundles):
        for other in bundles[index + 1:]:
            if bundle_contains(bundle["path"], other["path"]) or bundle_contains(other["path"], bundle["path"]):
                add("BUNDLE_OVERLAP", f"模拟资料包不能嵌套: {bundle['path']} 与 {other['path']}", bundle["path"], bundle, hard=True)
    for item in items:
        relative = item.get("path")
        if not relative:
            continue
        folded = relative.casefold()
        if folded in indexed:
            add("DUPLICATE_RESOURCE_PATH", f"资料路径大小写冲突或重复登记: {relative}", relative, item, hard=True)
        indexed[folded] = item
        if item.get("status") == "archived":
            continue
        if resource_for_path(relative) is None:
            add("RESOURCE_OUTSIDE_LAYOUT", f"资料条目路径不在标准 resources 目录: {item['item_id']} -> {relative}", relative, item)
        target = root / relative
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError:
            add("RESOURCE_PATH_ESCAPE", f"资料路径指向项目外: {relative}", relative, item, hard=True)
            continue
        if not target.exists():
            label = "模拟资料包" if is_simulation_bundle(item) else "资料文件"
            add("RESOURCE_MISSING", f"{label}不存在: {relative}；请先运行 catalog reconcile", relative, item)
        elif target.is_dir():
            if not is_simulation_bundle(item):
                add("INVALID_RESOURCE_DIRECTORY", f"只有 simulation 资料包可以登记目录路径: {relative}", relative, item, hard=True)
            else:
                try:
                    current = simulation_bundle_metadata(target, item.get("metadata", {}).get("entrypoint"))
                except (ValueError, OSError) as exc:
                    add("BUNDLE_ENTRY_MISSING", str(exc), relative, item)
                else:
                    stored = item.get("metadata") or {}
                    if any(stored.get(key) != value for key, value in current.items()):
                        add("BUNDLE_CHANGED", f"模拟资料包内容已变化: {relative}；请运行 catalog scan", relative, item, evidence=current)
        if target.exists() and item.get("status") == "missing":
            add("RESOURCE_RESTORED", f"资料文件已恢复但索引仍为 missing: {relative}；请运行 catalog scan", relative, item,
                evidence={"status": "exists"})
        if not is_simulation_bundle(item) and any(bundle_contains(b["path"], relative) for b in bundles):
            add("BUNDLE_DUPLICATE_ITEM", f"模拟资料包内文件不得重复登记: {relative}", relative, item, hard=True)
    for folder in folders or []:
        if folder["status"] == "archived":
            continue
        target = root / folder["path"]
        try:
            target.resolve().relative_to(root.resolve())
            target.resolve().relative_to((root / "resources").resolve())
        except ValueError:
            add("FOLDER_PATH_ESCAPE", f"资料目录路径超出 resources/: {folder['path']}", folder["path"], hard=True)
            continue
        if not target.is_dir():
            add("FOLDER_MISSING", f"已登记资料目录不存在: {folder['path']}；请核对目录位置", folder["path"])
    for resource in RESOURCE_DIRECTORIES:
        if resource.required and not (root / resource.relative_path).is_dir():
            add("PRESET_DIRECTORY_MISSING", f"缺少标准资源目录: {resource.relative_path}；请运行 `./workflow-monitor.exe install`", resource.relative_path)
    for path in files_under(root, "resources"):
        relative = path.relative_to(root).as_posix()
        resource = resource_for_path(relative)
        if (resource.kind not in OPTIONAL_CATALOG_KINDS and relative.casefold() not in indexed
                and not any(bundle_contains(b["path"], relative) for b in bundles)):
            stat = path.stat()
            add("RESOURCE_UNINDEXED", f"标准资源目录存在未索引文件: {relative}；请运行 catalog scan", relative,
                evidence={"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    for legacy_name in LEGACY_BY_PATH:
        for path in files_under(root, legacy_name):
            relative = path.relative_to(root).as_posix()
            add("LEGACY_RESOURCE_LAYOUT", f"发现旧版根目录资料: {relative}；请运行 catalog migrate-layout --dry-run", relative)
    maintenance = root / "maintenance"
    for document in maintenance.rglob("*.md") if maintenance.is_dir() else []:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            clean = target.strip().strip("<>")
            if not clean or clean.startswith(("#", "http://", "https://", "mailto:")):
                continue
            resolved = (document.parent / unquote(clean.split("#", 1)[0])).resolve()
            try:
                relative = resolved.relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            if relative.startswith("resources/") and not resolved.exists():
                source = document.relative_to(root).as_posix()
                add("RESOURCE_LINK_MISSING", f"Markdown 断链: {source} -> {clean}", relative,
                    evidence={"source_path": source, "target": clean})
                issues[-1]["source_path"] = source
    return issues


def catalog_consistency_errors(root: Path, items: list[dict]) -> list[str]:
    return [issue["message"] for issue in catalog_consistency_issues(root, items)]
