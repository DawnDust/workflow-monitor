"""Shared read-only directory guide and material registration view."""

from datetime import datetime, timezone
from pathlib import Path

from .resource_layout import (
    RESOURCE_KIND_LABELS, bundle_contains, is_simulation_bundle,
    resource_directory_snapshot, resource_for_path,
)
from .resource_reconciliation import project_inventory


def folder_index(root: Path, items: list[dict], folders: list[dict]) -> dict:
    root = root.resolve()
    resource_root = root / "resources"
    try:
        resource_root.resolve().relative_to(root)
    except ValueError:
        return {"folders": [], "entries": {}, "error": "resources 指向项目外，请先修复目录位置"}
    directories = resource_directory_snapshot(root, items, folders)
    active = [item for item in items if item.get("path") and item.get("status") != "archived"]
    bundles = [item for item in active if is_simulation_bundle(item)]
    by_path = {item["path"].casefold(): item for item in active}
    by_directory = {directory["path"]: directory for directory in directories}
    # Retain navigation to missing registered materials, even when their parent vanished.
    for item in active:
        parent = Path(item["path"]).parent
        while parent.as_posix().startswith("resources/"):
            relative = parent.as_posix()
            if relative not in by_directory and not any(bundle_contains(b["path"], relative) for b in bundles):
                by_directory[relative] = {
                    "path": relative, "name": parent.name, "label": parent.name,
                    "description": "待登记用途", "kind": resource_for_path(relative).kind,
                    "registration": "unregistered", "folder_id": None,
                    "depth": len(parent.parts) - 2,
                    "exists": (root / relative).is_dir(), "status": "missing" if not (root / relative).is_dir() else "attention",
                    "indexed_items": sum(bundle_contains(relative, i["path"]) for i in active),
                }
            parent = parent.parent
    by_directory["resources"] = {
        "path": "resources", "name": "resources", "label": "资料", "description": "按目录用途放置资料并登记",
        "kind": "other", "registration": "preset", "folder_id": None,
        "depth": -1,
        "exists": resource_root.is_dir(), "status": "ok" if resource_root.is_dir() else "missing",
        "indexed_items": len(active),
    }
    entries = {path: [] for path in by_directory}
    files, physical_directories = project_inventory(resource_root) if resource_root.is_dir() else ([], [])
    file_paths = {path.relative_to(root).as_posix(): path for path in files}
    for item in active:
        if not is_simulation_bundle(item) and item["path"].startswith("resources/"):
            file_paths.setdefault(item["path"], root / item["path"])
    for relative, path in sorted(file_paths.items()):
        try:
            path.resolve().relative_to(resource_root.resolve())
        except ValueError:
            continue
        item = by_path.get(relative.casefold())
        bundle = next((b for b in bundles if bundle_contains(b["path"], relative)), None)
        try:
            stat = path.stat() if path.is_file() else None
        except OSError:
            stat = None
        exists = stat is not None
        kind = item["kind"] if item else "simulation" if bundle else resource_for_path(relative).kind
        entry = {**(item or {}), "entry_type": "file", "path": relative, "name": path.name,
                 "kind": kind, "kind_label": RESOURCE_KIND_LABELS.get(kind, kind), "exists": exists,
                 "registration": "registered" if item else "bundle-content" if bundle else "unregistered",
                 "bundle_item_id": bundle["item_id"] if bundle else None,
                 "status": "missing" if not exists else "active",
                 "size": stat.st_size if stat else (item or {}).get("metadata", {}).get("file_size"),
                 "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else None}
        entries.setdefault(path.parent.relative_to(root).as_posix(), []).append(entry)
    for directory in physical_directories:
        relative = directory.relative_to(root).as_posix()
        if any(bundle_contains(b["path"], relative) for b in bundles) and relative not in by_directory:
            entries.setdefault(directory.parent.relative_to(root).as_posix(), []).append({
                "entry_type": "directory", "path": relative, "name": directory.name, "exists": True,
                "registration": "bundle-content", "status": "ok", "description": "模拟资料包内目录"})
    for relative, directory in by_directory.items():
        if relative == "resources":
            continue
        bundle = next((b for b in bundles if b["path"] == relative), None)
        if bundle:
            directory.update(label=bundle["title"], description=bundle.get("summary") or "模拟资料包整体登记", registration="bundle")
        parent = Path(relative).parent.as_posix()
        entries.setdefault(parent, []).append({**(bundle or {}), **directory, "entry_type": "directory",
                                               "name": directory["label"]})
    for values in entries.values():
        values.sort(key=lambda entry: (entry["entry_type"] != "directory", entry["name"].casefold()))
    return {"folders": sorted(by_directory.values(), key=lambda d: d["path"].casefold()), "entries": entries}


def render_folder_index(index: dict) -> str:
    if index.get("error"):
        return "# 资料文件夹索引\n\n" + index["error"] + "\n"
    lines = ["# 资料文件夹索引", "", "| 路径 | 名称 | 用途 | 层级 | 登记数量 | 登记 | 状态 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for folder in index["folders"]:
        values = [folder["path"], folder["label"], folder["description"], folder["depth"], folder["indexed_items"], folder["registration"], folder["status"]]
        lines.append("| " + " | ".join(str(v).replace("|", chr(92) + "|").replace("\n", " ") for v in values) + " |")
    return "\n".join(lines) + "\n"
