"""Validation and presentation helpers for the project research catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...infrastructure.persistence.database import ProjectDatabase

from ...infrastructure.system.resource_layout import (
    LEGACY_BY_PATH,
    OPTIONAL_CATALOG_KINDS,
    RESOURCE_BY_KIND,
    RESOURCE_DIRECTORIES,
    RESOURCE_KINDS,
    RESOURCE_KIND_LABELS,
    bundle_contains,
    files_under,
    is_simulation_bundle,
    is_indexable_resource_file,
    legacy_resource_for_path,
    resource_for_path,
    simulation_bundle_metadata,
)
from ...core.catalog import (
    CATALOG_KIND_LABELS,
    context_payload,
    decode_item,
    render_context_markdown,
)

CATALOG_KINDS = RESOURCE_KINDS
CATALOG_KIND_PREFIXES = {
    "literature": "lit",
    "data": "data",
    "theory": "theory",
    "simulation": "sim",
    "output": "out",
    "other": "other",
    "report": "report",
    "spark": "spark",
}
CATALOG_DIRECTORIES = {item.relative_path: item.kind for item in RESOURCE_DIRECTORIES}
CATALOG_STATUSES = ("active", "missing", "archived")
RELATION_TYPES = (
    "supports",
    "uses",
    "produces",
    "validates",
    "contradicts",
    "derived-from",
)
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
RELATION_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)


class CatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogRuntime:
    root: Path
    database: Callable[[], Any]
    emit: Callable[..., dict]
    persist: Callable[[list[dict]], None]
    write_context: Callable[[], tuple[dict, str]]
    timestamp: Callable[[], str]
    has_active_task: Callable[[], bool]
    auto_start: Callable[[str], dict]
    auto_finish: Callable[[bool, str, list[str]], dict]


def normalize_project_path(root: Path, value: str, *, require_file: bool = False) -> str:
    candidate = Path(value)
    absolute = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    project_root = root.resolve()
    try:
        relative = absolute.relative_to(project_root)
    except ValueError as exc:
        raise CatalogError("资料路径必须位于项目目录内") from exc
    if relative == Path("."):
        raise CatalogError("资料路径不能是项目根目录")
    if require_file and not absolute.is_file():
        raise CatalogError(f"资料文件不存在: {relative.as_posix()}")
    return relative.as_posix()


def validate_resource_path(relative: str, kind: str) -> str:
    resource = resource_for_path(relative)
    if resource is None:
        legacy = legacy_resource_for_path(relative)
        if legacy is not None:
            raise CatalogError(
                f"旧版资料路径不再接受登记: {relative}；"
                "请运行 catalog migrate-layout --dry-run"
            )
        raise CatalogError(
            "资料文件必须位于 resources/source、resources/data、resources/theory、"
            "resources/analysis、resources/outputs、resources/others 或 resources/reports"
        )
    if resource.kind != kind:
        raise CatalogError(
            f"文件所在目录对应 {resource.kind}，与 --kind {kind} 不一致"
        )
    return relative


def validate_identifier(value: str, *, label: str = "条目 ID") -> str:
    text = value.strip()
    if not text or not IDENTIFIER_RE.fullmatch(text):
        raise CatalogError(f"{label} 只能包含字母、数字、点、下划线和连字符")
    return text


def validate_relation_type(value: str) -> str:
    text = value.strip().casefold()
    if not RELATION_TYPE_RE.fullmatch(text):
        raise CatalogError("关系类型只能包含小写字母、数字、点、下划线和连字符")
    return text


def parse_tags(values: list[str] | None) -> list[str]:
    tags: list[str] = []
    for raw in values or []:
        for value in raw.split(","):
            tag = value.strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def parse_metadata(values: list[str] | None) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise CatalogError(f"扩展字段必须使用 key=value: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise CatalogError("扩展字段的 key 不能为空")
        if key in metadata:
            raise CatalogError(f"扩展字段重复: {key}")
        metadata[key] = value.strip()
    return metadata


def generated_item_id(kind: str, path: str | None = None) -> str:
    prefix = CATALOG_KIND_PREFIXES[kind]
    if path:
        digest = hashlib.sha256(path.casefold().encode("utf-8")).hexdigest()[:12]
        return f"{prefix}-{digest}"
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def generated_relation_id(source_id: str, relation_type: str, target_id: str) -> str:
    raw = f"{source_id}\0{relation_type}\0{target_id}"
    return f"rel-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def file_metadata(path: Path) -> dict[str, str | int]:
    stat = path.stat()
    return {
        "file_size": stat.st_size,
        "file_mtime_ns": stat.st_mtime_ns,
        "extension": path.suffix.casefold(),
    }


def _existing_catalog_path(root: Path, value: str) -> tuple[str, Path]:
    relative = normalize_project_path(root, value)
    absolute = (root / relative).resolve()
    if not absolute.is_file() and not absolute.is_dir():
        raise CatalogError(f"资料路径不存在: {relative}")
    return relative, absolute


def _validate_catalog_path_overlap(
    items: list[dict], path: str, *, bundle: bool, exclude_id: str | None = None,
) -> None:
    for item in items:
        if item.get("item_id") == exclude_id or item.get("status") == "archived" or not item.get("path"):
            continue
        current_path = str(item["path"])
        if current_path.casefold() == path.casefold():
            raise CatalogError(f"资料路径已经登记: {path}")
        if is_simulation_bundle(item) and bundle_contains(current_path, path):
            raise CatalogError(f"资料路径位于已登记模拟资料包内: {current_path}")
        if bundle and bundle_contains(path, current_path):
            raise CatalogError(f"模拟资料包内已有独立资料条目: {current_path}")


def _bundle_metadata(directory: Path, metadata: dict, entrypoint: str | None) -> dict:
    selected_entrypoint = entrypoint if entrypoint is not None else metadata.get("entrypoint")
    try:
        summary = simulation_bundle_metadata(directory, selected_entrypoint)
    except ValueError as exc:
        raise CatalogError(str(exc)) from exc
    result = dict(metadata)
    for key in ("entry_type", "file_count", "total_bytes", "tree_sha256", "entrypoint"):
        result.pop(key, None)
    result.update(summary)
    return result


def _without_bundle_metadata(metadata: dict) -> dict:
    result = dict(metadata)
    for key in ("entry_type", "file_count", "total_bytes", "tree_sha256", "entrypoint"):
        result.pop(key, None)
    return result


def catalog_item(connection: ProjectDatabase, item_id: str) -> dict:
    row = connection.catalog_item(item_id)
    if row is None:
        raise CatalogError(f"找不到科研资料条目: {item_id}")
    return decode_item(row)


def catalog_relations(connection: ProjectDatabase) -> list[dict]:
    return connection.catalog_relations()


def catalog_item_payload(item: dict) -> dict:
    return {
        "item_id": item["item_id"],
        "kind": item["kind"],
        "title": item["title"],
        "summary": item.get("summary", ""),
        "path": item.get("path") or None,
        "status": item.get("status", "active"),
        "tags": list(item.get("tags", [])),
        "source": item.get("source", ""),
        "metadata": dict(item.get("metadata", {})),
        "created_at": item.get("created_at"),
    }


def selected_items(
    connection: ProjectDatabase,
    *,
    ids: list[str] | None = None,
    kind: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
    query: str | None = None,
    related_to: str | None = None,
) -> list[dict]:
    values = [decode_item(row) for row in connection.catalog_items()]
    id_filter = set(ids) if ids else None
    tag_set = set(tags or [])
    if related_to:
        related_ids: set[str] = set()
        for relation in catalog_relations(connection):
            if relation["source_id"] == related_to:
                related_ids.add(relation["target_id"])
            if relation["target_id"] == related_to:
                related_ids.add(relation["source_id"])
        id_filter = related_ids if id_filter is None else id_filter & related_ids
    needle = (query or "").strip().casefold()
    return [
        item for item in values
        if (id_filter is None or item["item_id"] in id_filter)
        and (not kind or item["kind"] == kind)
        and (not status or item["status"] == status)
        and (not tag_set or tag_set.issubset(set(item["tags"])))
        and (not needle or needle in json.dumps(item, ensure_ascii=False, default=str).casefold())
    ]


def render_catalog_list(items: list[dict]) -> str:
    if not items:
        return "没有匹配的科研资料。\n"
    return "\n".join(
        f"- `{item['item_id']}` | {item['kind_label']} | {item['status']} | "
        f"{item['title']} | {item.get('path') or '无项目文件'}"
        for item in items
    ) + "\n"


def add_item(args: argparse.Namespace, runtime: CatalogRuntime) -> dict:
    record, branch = runtime.write_context()
    item_path = None
    absolute_path = None
    if args.path:
        item_path, absolute_path = _existing_catalog_path(runtime.root, args.path)
    if item_path:
        validate_resource_path(item_path, args.kind)
    item_id = validate_identifier(args.id) if args.id else generated_item_id(args.kind, item_path)
    connection = runtime.database()
    try:
        if connection.catalog_item_exists(item_id):
            raise CatalogError(f"科研资料条目已存在: {item_id}")
        existing_items = [decode_item(row) for row in connection.catalog_items()]
        if item_path:
            is_bundle = bool(absolute_path and absolute_path.is_dir())
            if is_bundle and args.kind != "simulation":
                raise CatalogError("只有 simulation 类型可以登记目录资料包")
            if args.entrypoint and not is_bundle:
                raise CatalogError("--entrypoint 只能用于模拟目录资料包")
            _validate_catalog_path_overlap(existing_items, item_path, bundle=is_bundle)
    finally:
        connection.close()
    metadata = parse_metadata(args.meta)
    if absolute_path is not None and absolute_path.is_dir():
        metadata = _bundle_metadata(absolute_path, metadata, args.entrypoint)
    payload = {
        "item_id": item_id,
        "kind": args.kind,
        "title": args.title.strip(),
        "summary": args.summary or "",
        "path": item_path,
        "status": "active",
        "tags": parse_tags(args.tag),
        "source": args.source or "",
        "metadata": metadata,
        "created_at": runtime.timestamp(),
    }
    if not payload["title"]:
        raise CatalogError("标题不能为空")
    runtime.persist([runtime.emit(
        "catalog.item_upserted", branch=branch, task_id=record["task_id"], payload=payload
    )])
    return payload


def update_item(args: argparse.Namespace, runtime: CatalogRuntime) -> dict:
    record, branch = runtime.write_context()
    connection = runtime.database()
    try:
        item = catalog_item(connection, validate_identifier(args.item_id))
        if args.path is not None:
            new_path, _absolute = _existing_catalog_path(runtime.root, args.path)
            item["path"] = new_path
        existing_items = [decode_item(row) for row in connection.catalog_items()]
        if args.clear_path:
            item["path"] = None
    finally:
        connection.close()
    changed_fields = any(
        value is not None for value in (
            args.kind, args.title, args.summary, args.status, args.source, args.path,
            args.tag, args.meta, args.entrypoint,
        )
    ) or args.clear_path or args.clear_summary or args.clear_source or args.clear_tags or args.clear_metadata
    if not changed_fields:
        raise CatalogError("catalog update 至少提供一个更新字段")
    if args.kind is not None:
        item["kind"] = args.kind
    if item.get("path"):
        validate_resource_path(item["path"], item["kind"])
        absolute_path = (runtime.root / item["path"]).resolve()
        is_bundle = absolute_path.is_dir()
        if is_bundle and item["kind"] != "simulation":
            raise CatalogError("只有 simulation 类型可以登记目录资料包")
        if args.entrypoint and not is_bundle:
            raise CatalogError("--entrypoint 只能用于模拟目录资料包")
        _validate_catalog_path_overlap(
            existing_items, item["path"], bundle=is_bundle, exclude_id=item["item_id"],
        )
    if args.title is not None:
        if not args.title.strip():
            raise CatalogError("标题不能为空")
        item["title"] = args.title.strip()
    if args.summary is not None:
        item["summary"] = args.summary
    if args.clear_summary:
        item["summary"] = ""
    if args.status is not None:
        item["status"] = args.status
    if args.source is not None:
        item["source"] = args.source
    if args.clear_source:
        item["source"] = ""
    if args.tag is not None:
        item["tags"] = parse_tags(args.tag)
    if args.clear_tags:
        item["tags"] = []
    if args.meta is not None:
        item["metadata"] = parse_metadata(args.meta)
    if args.clear_metadata:
        item["metadata"] = {}
    if item.get("path") and (runtime.root / item["path"]).is_dir():
        item["metadata"] = _bundle_metadata(
            runtime.root / item["path"], item.get("metadata", {}), args.entrypoint,
        )
    else:
        item["metadata"] = _without_bundle_metadata(item.get("metadata", {}))
    payload = catalog_item_payload(item)
    runtime.persist([runtime.emit(
        "catalog.item_upserted", branch=branch, task_id=record["task_id"], payload=payload
    )])
    return payload


def change_archive(args: argparse.Namespace, runtime: CatalogRuntime, *, restore: bool) -> dict:
    record, branch = runtime.write_context()
    item_id = validate_identifier(args.item_id)
    connection = runtime.database()
    try:
        item = catalog_item(connection, item_id)
    finally:
        connection.close()
    target = "active" if restore else "archived"
    if item["status"] == target:
        raise CatalogError(f"科研资料条目已经是 {target} 状态")
    kind = "catalog.item_restored" if restore else "catalog.item_archived"
    runtime.persist([runtime.emit(
        kind, branch=branch, task_id=record["task_id"], payload={"item_id": item_id}
    )])
    return {"item_id": item_id, "status": target}


def link_items(args: argparse.Namespace, runtime: CatalogRuntime) -> dict:
    record, branch = runtime.write_context()
    source_id = validate_identifier(args.source_id, label="来源条目 ID")
    target_id = validate_identifier(args.target_id, label="目标条目 ID")
    if source_id == target_id:
        raise CatalogError("科研资料不能关联到自身")
    relation_type = validate_relation_type(args.relation_type)
    connection = runtime.database()
    try:
        catalog_item(connection, source_id)
        catalog_item(connection, target_id)
    finally:
        connection.close()
    relation_id = generated_relation_id(source_id, relation_type, target_id)
    payload = {
        "relation_id": relation_id,
        "source_id": source_id,
        "target_id": target_id,
        "relation_type": relation_type,
        "note": args.note or "",
        "created_at": runtime.timestamp(),
    }
    runtime.persist([runtime.emit(
        "catalog.relation_upserted", branch=branch, task_id=record["task_id"], payload=payload
    )])
    return payload


def unlink_items(args: argparse.Namespace, runtime: CatalogRuntime) -> dict:
    record, branch = runtime.write_context()
    relation_id = validate_identifier(args.relation_id, label="关系 ID")
    connection = runtime.database()
    try:
        exists = connection.catalog_relation_exists(relation_id)
    finally:
        connection.close()
    if not exists:
        raise CatalogError(f"找不到科研资料关系: {relation_id}")
    runtime.persist([runtime.emit(
        "catalog.relation_removed", branch=branch, task_id=record["task_id"],
        payload={"relation_id": relation_id},
    )])
    return {"relation_id": relation_id, "removed": True}


def scan_items(args: argparse.Namespace, runtime: CatalogRuntime) -> dict:
    record, branch = runtime.write_context()
    if bool(args.root) != bool(args.kind):
        raise CatalogError("指定扫描目录时必须同时提供 --root 和 --kind")
    roots: list[tuple[Path, str]] = []
    if args.root:
        relative = normalize_project_path(runtime.root, args.root)
        scan_root = runtime.root / relative
        if not scan_root.is_dir():
            raise CatalogError(f"扫描目录不存在: {relative}")
        resource = resource_for_path(relative)
        if resource is None:
            raise CatalogError("扫描目录必须位于标准 resources 目录内")
        if resource.kind != args.kind:
            raise CatalogError(
                f"扫描目录对应 {resource.kind}，与 --kind {args.kind} 不一致"
            )
        roots.append((scan_root, resource.kind))
    else:
        roots.extend(
            (runtime.root / directory, kind)
            for directory, kind in CATALOG_DIRECTORIES.items()
            if kind not in OPTIONAL_CATALOG_KINDS
        )
    connection = runtime.database()
    try:
        existing = {
            item.get("path"): item
            for item in (decode_item(row) for row in connection.catalog_items())
            if item.get("path")
        }
    finally:
        connection.close()
    discovered: dict[str, tuple[Path, str]] = {}
    scanned_prefixes: list[str] = []
    physical_file_count = 0
    bundle_items = [
        item for item in existing.values()
        if is_simulation_bundle(item) and item.get("status") != "archived"
    ]
    for scan_root, kind in roots:
        if not scan_root.exists():
            continue
        scanned_prefixes.append(scan_root.relative_to(runtime.root).as_posix().rstrip("/") + "/")
        for path in scan_root.rglob("*"):
            if path.is_file() and is_indexable_resource_file(path, base=scan_root):
                try:
                    path.resolve().relative_to(runtime.root.resolve())
                except ValueError:
                    continue
                relative = path.relative_to(runtime.root).as_posix()
                physical_file_count += 1
                if any(bundle_contains(str(item["path"]), relative) for item in bundle_items):
                    continue
                discovered[relative] = (path, kind)
    actions: list[dict] = []
    events: list[dict] = []
    for item in bundle_items:
        relative = str(item["path"])
        in_scope = any(
            relative == prefix.rstrip("/") or relative.startswith(prefix)
            for prefix in scanned_prefixes
        )
        if not in_scope:
            continue
        directory = runtime.root / relative
        if directory.is_dir():
            refreshed = dict(item)
            refreshed["metadata"] = _bundle_metadata(
                directory, item.get("metadata", {}), None,
            )
            if refreshed["metadata"] != item.get("metadata") or item.get("status") == "missing":
                refreshed["status"] = "active"
                actions.append({"action": "update", "item_id": item["item_id"], "path": relative})
                events.append(runtime.emit(
                    "catalog.item_upserted", branch=branch, task_id=record["task_id"],
                    payload=catalog_item_payload(refreshed),
                ))
        elif item.get("status") != "missing":
            missing = dict(item)
            missing["status"] = "missing"
            actions.append({"action": "missing", "item_id": item["item_id"], "path": relative})
            events.append(runtime.emit(
                "catalog.item_upserted", branch=branch, task_id=record["task_id"],
                payload=catalog_item_payload(missing),
            ))
    for relative, (path, kind) in sorted(discovered.items()):
        current = existing.get(relative)
        metadata = dict(current.get("metadata", {})) if current else {}
        stats = file_metadata(path)
        changed = current is None or any(metadata.get(key) != value for key, value in stats.items())
        restored = bool(current and current.get("status") == "missing")
        if not changed and not restored:
            continue
        metadata.update(stats)
        item = current or {
            "item_id": generated_item_id(kind, relative),
            "kind": kind,
            "title": path.stem,
            "summary": "",
            "path": relative,
            "status": "active",
            "tags": [],
            "source": "",
            "metadata": {},
            "created_at": runtime.timestamp(),
        }
        item["metadata"] = metadata
        if item.get("status") == "missing":
            item["status"] = "active"
        payload = catalog_item_payload(item)
        actions.append({"action": "add" if current is None else "update", "item_id": item["item_id"], "path": relative})
        events.append(runtime.emit(
            "catalog.item_upserted", branch=branch, task_id=record["task_id"], payload=payload
        ))
    discovered_paths = set(discovered)
    for relative, item in existing.items():
        if is_simulation_bundle(item):
            continue
        in_scope = any(relative.startswith(prefix) for prefix in scanned_prefixes)
        if in_scope and relative not in discovered_paths and item.get("status") not in {"missing", "archived"}:
            item["status"] = "missing"
            payload = catalog_item_payload(item)
            actions.append({"action": "missing", "item_id": item["item_id"], "path": relative})
            events.append(runtime.emit(
                "catalog.item_upserted", branch=branch, task_id=record["task_id"], payload=payload
            ))
    if events and not args.dry_run:
        runtime.persist(events)
    return {
        "dry_run": args.dry_run,
        "scanned_files": physical_file_count,
        "scanned_items": len(discovered) + sum(
            any(str(item["path"]) == prefix.rstrip("/") or str(item["path"]).startswith(prefix)
                for prefix in scanned_prefixes)
            for item in bundle_items
        ),
        "changes": actions,
    }


def read_catalog(args: argparse.Namespace, runtime: CatalogRuntime) -> str | dict | list[dict]:
    connection = runtime.database()
    try:
        if args.catalog_command == "show":
            item = catalog_item(connection, validate_identifier(args.item_id))
            relations = [
                relation for relation in catalog_relations(connection)
                if relation["source_id"] == item["item_id"] or relation["target_id"] == item["item_id"]
            ]
            output = {"item": item, "relations": relations}
            return output if args.format == "json" else render_context_markdown([item], relations)
        items = selected_items(
            connection,
            ids=getattr(args, "id", None),
            kind=getattr(args, "kind", None),
            status=getattr(args, "status", None),
            tags=parse_tags(getattr(args, "tag", None)),
            query=getattr(args, "query", None),
            related_to=getattr(args, "related_to", None),
        )
        relations = catalog_relations(connection)
        if args.catalog_command == "context":
            selected_ids = {item["item_id"] for item in items}
            related_ids: set[str] = set()
            for relation in relations:
                if relation["source_id"] in selected_ids:
                    related_ids.add(relation["target_id"])
                if relation["target_id"] in selected_ids:
                    related_ids.add(relation["source_id"])
            if related_ids:
                expanded = selected_items(connection, ids=sorted(related_ids))
                by_id = {item["item_id"]: item for item in [*items, *expanded]}
                items = sorted(by_id.values(), key=lambda item: (item["kind"], item["title"], item["item_id"]))
            return (
                context_payload(items, relations)
                if args.format == "json"
                else render_context_markdown(items, relations)
            )
        return items if args.format == "json" else render_catalog_list(items)
    finally:
        connection.close()


def _directory_for_kind(root: Path, kind: str) -> Path:
    return root / RESOURCE_BY_KIND[kind].relative_path


def _validate_target_name(value: str) -> str:
    name = value.strip()
    if not name or Path(name).name != name or name in {".", ".."}:
        raise CatalogError("--name 必须是单个合法文件名，不能包含目录")
    return name


def ingest_plan(args: argparse.Namespace, runtime: CatalogRuntime) -> dict:
    source = Path(args.file)
    source = source.resolve() if source.is_absolute() else (runtime.root / source).resolve()
    if not source.is_file():
        raise CatalogError(f"导入文件不存在: {source}")
    root = runtime.root.resolve()
    try:
        relative = source.relative_to(root)
    except ValueError:
        relative = None
    if relative is not None:
        resource = resource_for_path(relative)
        if resource is None:
            raise CatalogError("项目内文件必须位于七个标准 resources 目录之一")
        inferred = resource.kind
        if args.kind and args.kind != resource.kind:
            raise CatalogError(f"文件所在目录对应 {resource.kind}，与 --kind {args.kind} 不一致")
        if args.name and args.name != source.name:
            raise CatalogError("项目内文件不能通过 ingest 改名；请先在文件系统中改名")
        kind, target, copied = inferred, source, False
    else:
        if not args.kind:
            raise CatalogError("项目外文件必须显式提供 --kind")
        kind = args.kind
        target_name = _validate_target_name(args.name or source.name)
        target = (_directory_for_kind(root, kind) / target_name).resolve()
        if target.exists():
            raise CatalogError(f"目标文件已存在，拒绝覆盖: {target.relative_to(root).as_posix()}")
        copied = True
    target_path = target.relative_to(root).as_posix()
    connection = runtime.database()
    try:
        row = connection.catalog_item_by_path(target_path)
        current = decode_item(row) if row else None
    finally:
        connection.close()
    return {
        "source": str(source),
        "target": str(target),
        "target_path": target_path,
        "kind": kind,
        "copied": copied,
        "action": "update" if current else "add",
        "current": current,
    }


def _ingest_payload(args: argparse.Namespace, plan: dict, runtime: CatalogRuntime) -> dict:
    current = plan["current"]
    tags = list(current.get("tags", [])) if current else []
    for tag in parse_tags(args.tag):
        if tag not in tags:
            tags.append(tag)
    metadata = dict(current.get("metadata", {})) if current else {}
    metadata.update(parse_metadata(args.meta))
    metadata.update(file_metadata(Path(plan["target"])))
    item = current or {
        "item_id": generated_item_id(plan["kind"], plan["target_path"]),
        "kind": plan["kind"],
        "title": Path(plan["target"]).stem,
        "summary": "",
        "path": plan["target_path"],
        "status": "active",
        "tags": [],
        "source": "",
        "metadata": {},
        "created_at": runtime.timestamp(),
    }
    item.update({
        "kind": plan["kind"],
        "path": plan["target_path"],
        "status": "active",
        "tags": tags,
        "metadata": metadata,
    })
    if args.title is not None:
        if not args.title.strip():
            raise CatalogError("标题不能为空")
        item["title"] = args.title.strip()
    if args.summary is not None:
        item["summary"] = args.summary
    if args.source is not None:
        item["source"] = args.source
    return catalog_item_payload(item)


def ingest_item(args: argparse.Namespace, runtime: CatalogRuntime) -> dict:
    plan = ingest_plan(args, runtime)
    preview = {
        key: plan[key] for key in ("source", "target_path", "kind", "copied", "action")
    }
    if args.dry_run:
        return {"dry_run": True, **preview}
    auto_started = False
    copied_path: Path | None = None
    registered = False
    try:
        if not runtime.has_active_task():
            runtime.auto_start(Path(plan["target"]).name)
            auto_started = True
        record, branch = runtime.write_context()
        if plan["copied"]:
            target = Path(plan["target"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(plan["source"], target)
            copied_path = target
        payload = _ingest_payload(args, plan, runtime)
        runtime.persist([runtime.emit(
            "catalog.item_upserted", branch=branch, task_id=record["task_id"], payload=payload
        )])
        registered = True
    except Exception as exc:
        if copied_path is not None and copied_path.exists() and not registered:
            copied_path.unlink()
        if auto_started:
            try:
                runtime.auto_finish(False, f"导入失败：{exc}", [str(exc)])
            except Exception as finish_exc:
                raise CatalogError(f"{exc}；自动任务收尾失败: {finish_exc}") from exc
        raise
    task = None
    if auto_started:
        try:
            task = runtime.auto_finish(
                True,
                f"已登记科研资料：{payload['title']}",
                [f"{payload['item_id']} -> {payload.get('path') or '无路径'}"],
            )
        except Exception as exc:
            raise CatalogError(
                f"资料已登记，但自动任务收尾失败: {exc}；请运行 status 并手工结束活动任务"
            ) from exc
    return {"dry_run": False, **preview, "item": payload, "task": task}


def migrate_layout(args: argparse.Namespace, runtime: CatalogRuntime) -> dict:
    root = runtime.root.resolve()
    connection = runtime.database()
    try:
        existing = {
            item["path"].casefold(): item
            for item in (decode_item(row) for row in connection.catalog_items())
            if item.get("path")
        }
    finally:
        connection.close()

    moves: list[dict] = []
    conflicts: list[dict] = []
    planned_targets: set[str] = set()
    for legacy_name, resource in LEGACY_BY_PATH.items():
        for source in files_under(root, legacy_name):
            relative_tail = source.relative_to(root / legacy_name)
            target = root / resource.relative_path / relative_tail
            source_path = source.relative_to(root).as_posix()
            target_path = target.relative_to(root).as_posix()
            folded_target = target_path.casefold()
            conflict = target.exists() or folded_target in planned_targets
            entry = {
                "source": source_path,
                "target": target_path,
                "kind": resource.kind,
                "item_id": existing.get(source_path.casefold(), {}).get("item_id"),
            }
            if conflict:
                conflicts.append(entry)
            else:
                moves.append(entry)
                planned_targets.add(folded_target)

    preview = {
        "dry_run": bool(args.dry_run),
        "moves": moves,
        "conflicts": conflicts,
        "move_count": len(moves),
        "conflict_count": len(conflicts),
    }
    if args.dry_run:
        return preview
    if conflicts:
        raise CatalogError(
            "旧布局迁移存在目标冲突，未移动任何文件: "
            + ", ".join(item["target"] for item in conflicts)
        )
    record, branch = runtime.write_context()
    if branch != "main" or record.get("git", {}).get("track") != "stable":
        raise CatalogError("旧布局迁移只能在 main 的 stable 活动任务中执行")
    if not moves:
        return {**preview, "dry_run": False, "migrated": 0, "indexed": 0}

    moved: list[tuple[Path, Path]] = []
    payloads: list[dict] = []
    try:
        for move in moves:
            source = root / move["source"]
            target = root / move["target"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved.append((source, target))
            current = existing.get(move["source"].casefold())
            metadata = dict(current.get("metadata", {})) if current else {}
            metadata.update(file_metadata(target))
            item = current or {
                "item_id": generated_item_id(move["kind"], move["target"]),
                "title": target.stem,
                "summary": "",
                "tags": [],
                "source": "",
                "created_at": runtime.timestamp(),
            }
            item.update({
                "kind": move["kind"],
                "path": move["target"],
                "status": "active",
                "metadata": metadata,
            })
            move["item_id"] = item["item_id"]
            payloads.append(catalog_item_payload(item))
        runtime.persist([
            runtime.emit(
                "catalog.item_upserted",
                branch=branch,
                task_id=record["task_id"],
                payload=payload,
            )
            for payload in payloads
        ])
    except Exception as exc:
        rollback_errors: list[str] = []
        for source, target in reversed(moved):
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and not source.exists():
                    shutil.move(str(target), str(source))
            except OSError as rollback_exc:
                rollback_errors.append(f"{target} -> {source}: {rollback_exc}")
        if rollback_errors:
            raise CatalogError(
                f"迁移失败且文件回滚不完整: {exc}; " + "; ".join(rollback_errors)
            ) from exc
        raise

    for legacy_name in LEGACY_BY_PATH:
        directory = root / legacy_name
        if not directory.is_dir():
            continue
        for candidate in sorted(
            (path for path in directory.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                candidate.rmdir()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass
    return {
        **preview,
        "dry_run": False,
        "moves": moves,
        "migrated": len(moved),
        "indexed": len(payloads),
    }


def bulk_update(args: argparse.Namespace, runtime: CatalogRuntime) -> dict:
    selectors = any((
        args.id, args.kind, args.status, args.tag, args.query, args.related_to,
    ))
    if args.all and selectors:
        raise CatalogError("--all 不能与筛选条件同时使用")
    if not args.all and not selectors:
        raise CatalogError("批量更新必须提供筛选条件；更新全部资料需显式使用 --all")
    if args.summary is not None and args.clear_summary:
        raise CatalogError("--summary 与 --clear-summary 不能同时使用")
    if args.source is not None and args.clear_source:
        raise CatalogError("--source 与 --clear-source 不能同时使用")
    additions = parse_tags(args.add_tag)
    removals = parse_tags(args.remove_tag)
    overlap = sorted(set(additions) & set(removals))
    if overlap:
        raise CatalogError("同一标签不能同时增加和移除: " + ", ".join(overlap))
    if not any((
        additions, removals, args.summary is not None, args.source is not None,
        args.clear_summary, args.clear_source,
    )):
        raise CatalogError("bulk-update 至少提供一个更新字段")
    write_context = None if args.dry_run else runtime.write_context()
    connection = runtime.database()
    try:
        items = selected_items(
            connection,
            ids=args.id,
            kind=args.kind,
            status=args.status,
            tags=parse_tags(args.tag),
            query=args.query,
            related_to=args.related_to,
        )
    finally:
        connection.close()
    changes: list[dict] = []
    payloads: list[dict] = []
    for item in items:
        before = {
            "tags": list(item.get("tags", [])),
            "summary": item.get("summary", ""),
            "source": item.get("source", ""),
        }
        item["tags"] = [tag for tag in before["tags"] if tag not in removals]
        for tag in additions:
            if tag not in item["tags"]:
                item["tags"].append(tag)
        if args.summary is not None:
            item["summary"] = args.summary
        if args.clear_summary:
            item["summary"] = ""
        if args.source is not None:
            item["source"] = args.source
        if args.clear_source:
            item["source"] = ""
        after = {
            "tags": list(item["tags"]),
            "summary": item.get("summary", ""),
            "source": item.get("source", ""),
        }
        if before != after:
            changes.append({"item_id": item["item_id"], "before": before, "after": after})
            payloads.append(catalog_item_payload(item))
    if payloads and not args.dry_run:
        assert write_context is not None
        record, branch = write_context
        runtime.persist([
            runtime.emit("catalog.item_upserted", branch=branch, task_id=record["task_id"], payload=payload)
            for payload in payloads
        ])
    return {
        "dry_run": args.dry_run,
        "matched": len(items),
        "changed": len(changes),
        "changes": changes,
    }


def add_catalog_filters(parser: argparse.ArgumentParser, *, include_ids: bool = False) -> None:
    if include_ids:
        parser.add_argument("--id", action="append")
    parser.add_argument("--kind", choices=CATALOG_KINDS)
    parser.add_argument("--status", choices=CATALOG_STATUSES)
    parser.add_argument("--tag", action="append")
    parser.add_argument("--query")
    parser.add_argument("--related-to")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")


def configure_catalog_parser(subparsers) -> None:
    catalog = subparsers.add_parser("catalog", help="管理和查询科研资料索引")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", required=True)
    add_parser = catalog_sub.add_parser("add")
    add_parser.add_argument("--id")
    add_parser.add_argument("--kind", required=True, choices=CATALOG_KINDS)
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--summary")
    add_parser.add_argument("--path")
    add_parser.add_argument("--source")
    add_parser.add_argument("--tag", action="append")
    add_parser.add_argument("--meta", action="append")
    add_parser.add_argument("--entrypoint")
    update_parser = catalog_sub.add_parser("update")
    update_parser.add_argument("item_id")
    update_parser.add_argument("--kind", choices=CATALOG_KINDS)
    update_parser.add_argument("--title")
    update_parser.add_argument("--summary")
    update_parser.add_argument("--status", choices=CATALOG_STATUSES)
    update_parser.add_argument("--path")
    update_parser.add_argument("--source")
    update_parser.add_argument("--tag", action="append")
    update_parser.add_argument("--meta", action="append")
    update_parser.add_argument("--entrypoint")
    update_parser.add_argument("--clear-path", action="store_true")
    update_parser.add_argument("--clear-summary", action="store_true")
    update_parser.add_argument("--clear-source", action="store_true")
    update_parser.add_argument("--clear-tags", action="store_true")
    update_parser.add_argument("--clear-metadata", action="store_true")
    for name in ("archive", "restore"):
        catalog_sub.add_parser(name).add_argument("item_id")
    link_parser = catalog_sub.add_parser("link")
    link_parser.add_argument("source_id")
    link_parser.add_argument("relation_type")
    link_parser.add_argument("target_id")
    link_parser.add_argument("--note")
    unlink_parser = catalog_sub.add_parser("unlink")
    unlink_parser.add_argument("relation_id")
    scan_parser = catalog_sub.add_parser("scan")
    scan_parser.add_argument("--root")
    scan_parser.add_argument("--kind", choices=CATALOG_KINDS)
    scan_parser.add_argument("--dry-run", action="store_true")
    migrate_parser = catalog_sub.add_parser("migrate-layout")
    migrate_parser.add_argument("--dry-run", action="store_true")
    list_parser = catalog_sub.add_parser("list")
    add_catalog_filters(list_parser)
    show_parser = catalog_sub.add_parser("show")
    show_parser.add_argument("item_id")
    show_parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    context_parser = catalog_sub.add_parser("context")
    add_catalog_filters(context_parser, include_ids=True)
    ingest_parser = catalog_sub.add_parser("ingest")
    ingest_parser.add_argument("file")
    ingest_parser.add_argument("--kind", choices=CATALOG_KINDS)
    ingest_parser.add_argument("--name")
    ingest_parser.add_argument("--title")
    ingest_parser.add_argument("--summary")
    ingest_parser.add_argument("--source")
    ingest_parser.add_argument("--tag", action="append")
    ingest_parser.add_argument("--meta", action="append")
    ingest_parser.add_argument("--dry-run", action="store_true")
    bulk_parser = catalog_sub.add_parser("bulk-update")
    bulk_parser.add_argument("--id", action="append")
    bulk_parser.add_argument("--kind", choices=CATALOG_KINDS)
    bulk_parser.add_argument("--status", choices=CATALOG_STATUSES)
    bulk_parser.add_argument("--tag", action="append")
    bulk_parser.add_argument("--query")
    bulk_parser.add_argument("--related-to")
    bulk_parser.add_argument("--all", action="store_true")
    bulk_parser.add_argument("--add-tag", action="append")
    bulk_parser.add_argument("--remove-tag", action="append")
    bulk_parser.add_argument("--summary")
    bulk_parser.add_argument("--source")
    bulk_parser.add_argument("--clear-summary", action="store_true")
    bulk_parser.add_argument("--clear-source", action="store_true")
    bulk_parser.add_argument("--dry-run", action="store_true")


def catalog_command(args: argparse.Namespace, runtime: CatalogRuntime) -> str | dict | list[dict]:
    if args.catalog_command == "add":
        return add_item(args, runtime)
    if args.catalog_command == "update":
        return update_item(args, runtime)
    if args.catalog_command == "archive":
        return change_archive(args, runtime, restore=False)
    if args.catalog_command == "restore":
        return change_archive(args, runtime, restore=True)
    if args.catalog_command == "link":
        return link_items(args, runtime)
    if args.catalog_command == "unlink":
        return unlink_items(args, runtime)
    if args.catalog_command == "scan":
        return scan_items(args, runtime)
    if args.catalog_command == "migrate-layout":
        return migrate_layout(args, runtime)
    if args.catalog_command == "ingest":
        return ingest_item(args, runtime)
    if args.catalog_command == "bulk-update":
        return bulk_update(args, runtime)
    return read_catalog(args, runtime)
