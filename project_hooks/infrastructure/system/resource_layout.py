"""Authoritative resource directory layout and read-only consistency helpers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResourceDirectory:
    name: str
    kind: str
    label: str
    description: str
    legacy_name: str | None = None

    @property
    def relative_path(self) -> str:
        return f"resources/{self.name}"


RESOURCE_DIRECTORIES = (
    ResourceDirectory("source", "literature", "文献", "外部原始资料与来源证据", "source"),
    ResourceDirectory("data", "data", "数据", "原始、过程和处理后数据", "data"),
    ResourceDirectory("theory", "theory", "理论", "理论、假设、定义和推导", "theory"),
    ResourceDirectory("analysis", "simulation", "模拟", "分析代码、Notebook、实验与过程记录", "analysis"),
    ResourceDirectory("outputs", "output", "输出", "图表、模型和其他可交付成果", "outputs"),
    ResourceDirectory("others", "other", "其他", "暂时无法可靠分类的资料"),
    ResourceDirectory("reports", "report", "报告", "面向外部受众的项目总结与报告"),
)

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


def resource_directory_snapshot(root: Path, items: list[dict]) -> list[dict]:
    indexed_by_path = {
        str(item.get("path") or "").casefold(): item for item in items if item.get("path")
    }
    result: list[dict] = []
    for resource in RESOURCE_DIRECTORIES:
        files = files_under(root, resource.relative_path)
        paths = [path.relative_to(root).as_posix() for path in files]
        bundles = [
            item for item in items
            if item.get("status") != "archived" and is_simulation_bundle(item)
            and resource_for_path(str(item.get("path"))) == resource
        ]
        covered = {
            path for path in paths
            if any(bundle_contains(str(bundle["path"]), path) for bundle in bundles)
        }
        indexed = [
            indexed_by_path[path.casefold()] for path in paths
            if path.casefold() in indexed_by_path
        ]
        unindexed = [
            path for path in paths
            if path.casefold() not in indexed_by_path and path not in covered
        ]
        mismatched = [
            item["path"] for item in indexed
            if item.get("kind") != resource.kind or item.get("status") == "missing"
        ]
        directory = root / resource.relative_path
        if not directory.is_dir():
            status = "missing"
        elif unindexed or mismatched:
            status = "attention"
        else:
            status = "ok"
        result.append({
            "name": resource.name,
            "kind": resource.kind,
            "label": resource.label,
            "description": resource.description,
            "path": resource.relative_path,
            "exists": directory.is_dir(),
            "actual_files": len(paths),
            "indexed_files": len(indexed) + len(covered),
            "logical_items": len(paths) - len(covered) + len(bundles),
            "indexed_items": sum(
                item.get("kind") == resource.kind and item.get("status") != "archived"
                for item in items
            ),
            "bundle_count": len(bundles),
            "contained_files": len(covered),
            "unindexed_files": unindexed,
            "mismatched_files": mismatched,
            "status": status,
        })
    return result


def catalog_consistency_errors(root: Path, items: list[dict]) -> list[str]:
    errors: list[str] = []
    indexed_by_path: dict[str, dict] = {}
    bundles = [item for item in items if item.get("status") != "archived" and is_simulation_bundle(item)]
    for index, bundle in enumerate(bundles):
        for other in bundles[index + 1:]:
            if (bundle_contains(str(bundle["path"]), str(other["path"]))
                    or bundle_contains(str(other["path"]), str(bundle["path"]))):
                errors.append(
                    f"模拟资料包不能嵌套: {bundle['path']} 与 {other['path']}"
                )
    for item in items:
        relative = item.get("path")
        if not relative:
            continue
        folded = str(relative).casefold()
        if folded in indexed_by_path:
            errors.append(f"资料路径大小写冲突或重复登记: {relative}")
        indexed_by_path[folded] = item
        resource = resource_for_path(relative)
        if resource is None:
            hint = (
                "；请运行 `.\\workflow-monitor.exe catalog migrate-layout --dry-run`"
                if legacy_resource_for_path(relative) else ""
            )
            errors.append(f"资料条目路径不在标准 resources 目录: {item['item_id']} -> {relative}{hint}")
        elif item.get("kind") != resource.kind:
            errors.append(
                f"资料类型与目录不一致: {item['item_id']} 为 {item.get('kind')}，"
                f"但 {relative} 必须为 {resource.kind}"
            )
        else:
            target = root / relative
            if target.is_dir():
                if not is_simulation_bundle(item):
                    errors.append(f"只有 simulation 资料包可以登记目录路径: {relative}")
                elif item.get("status") == "missing":
                    errors.append(f"模拟资料包已恢复但索引仍为 missing: {relative}；请运行 catalog scan")
                else:
                    try:
                        current = simulation_bundle_metadata(
                            target, (item.get("metadata") or {}).get("entrypoint"),
                        )
                    except ValueError as exc:
                        errors.append(str(exc))
                    else:
                        stored = item.get("metadata") or {}
                        if any(stored.get(key) != value for key, value in current.items()):
                            errors.append(f"模拟资料包内容已变化: {relative}；请运行 catalog scan")
            elif target.is_file() and item.get("status") == "missing":
                errors.append(f"资料文件已恢复但索引仍为 missing: {relative}；请运行 catalog scan")
            elif not target.exists() and item.get("status") not in {"missing", "archived"}:
                label = "模拟资料包" if is_simulation_bundle(item) else "资料文件"
                errors.append(f"{label}不存在: {relative}；请运行 catalog scan")

        if item.get("status") != "archived" and not is_simulation_bundle(item):
            containing = next(
                (bundle for bundle in bundles if bundle_contains(str(bundle["path"]), str(relative))),
                None,
            )
            if containing is not None:
                errors.append(
                    f"模拟资料包内文件不得重复登记: {relative}（资料包 {containing['path']}）"
                )

    for resource in RESOURCE_DIRECTORIES:
        directory = root / resource.relative_path
        if not directory.is_dir():
            errors.append(
                f"缺少标准资源目录: {resource.relative_path}；请运行 `.\\workflow-monitor.exe install`"
            )
            continue
        for path in files_under(root, resource.relative_path):
            relative = path.relative_to(root).as_posix()
            covered = any(bundle_contains(str(bundle["path"]), relative) for bundle in bundles)
            if relative.casefold() not in indexed_by_path and not covered:
                errors.append(f"标准资源目录存在未索引文件: {relative}；请运行 catalog scan")

    legacy_files: list[str] = []
    for legacy_name in LEGACY_BY_PATH:
        legacy_files.extend(
            path.relative_to(root).as_posix() for path in files_under(root, legacy_name)
        )
    if legacy_files:
        errors.append(
            "发现旧版根目录资料: " + ", ".join(legacy_files[:5])
            + (" 等" if len(legacy_files) > 5 else "")
            + "；请运行 `.\\workflow-monitor.exe catalog migrate-layout --dry-run`"
        )
    return errors
