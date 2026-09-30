"""Read-only move detection. Catalog mutations are performed by the command service."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .resource_layout import (
    IGNORED_DIRECTORY_NAMES, is_indexable_resource_file, is_simulation_bundle,
    simulation_bundle_metadata, bundle_contains,
)

SEARCH_IGNORES = IGNORED_DIRECTORY_NAMES | {".codex", ".githooks", "diagnostics-export", "dist", "build"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def project_inventory(root: Path):
    root = root.resolve()
    files, directories = [], []
    for current, names, filenames in os.walk(root, followlinks=False):
        base = Path(current)
        names[:] = sorted(name for name in names if name.casefold() not in SEARCH_IGNORES
                          and not (base / name).is_symlink()
                          and not (base / name).is_junction())
        for name in names:
            directories.append(base / name)
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink() or not is_indexable_resource_file(path, base=root):
                continue
            try:
                path.resolve().relative_to(root)
            except ValueError:
                continue
            files.append(path)
    return files, directories


def reconcile_plan(root: Path, items: list[dict], *, item_id=None) -> dict:
    files, directories = project_inventory(root)
    occupied = {str(i["path"]).casefold(): i["item_id"] for i in items if i.get("path")}
    missing = [i for i in items if i.get("path") and i.get("status") != "archived"
               and not (root / i["path"]).exists()]
    hashes = {}
    results = []
    for item in missing:
        metadata = item.get("metadata") or {}
        bundle = is_simulation_bundle(item)
        expected = metadata.get("tree_sha256" if bundle else "sha256")
        candidates = []
        for path in directories if bundle else files:
            relative = path.relative_to(root).as_posix()
            try:
                same_name = path.name.casefold() == Path(item["path"]).name.casefold()
                same_size = not bundle and path.stat().st_size == metadata.get("file_size")
                if expected:
                    key = (relative, bundle, metadata.get("entrypoint") if bundle else None)
                    if key not in hashes:
                        hashes[key] = (simulation_bundle_metadata(path, metadata.get("entrypoint"))["tree_sha256"]
                                       if bundle else file_sha256(path))
                    matched = hashes[key] == expected
                else:
                    matched = False
                if matched or same_name or same_size:
                    candidates.append({"path": relative, "hash_match": matched,
                                       "same_name": same_name, "same_size": bool(same_size),
                                       "registered_item_id": occupied.get(relative.casefold())})
            except (OSError, ValueError):
                continue
        candidates.sort(key=lambda c: (not c["hash_match"], not c["same_name"], c["path"].casefold()))
        matches = [c for c in candidates if c["hash_match"]]
        selected = matches[0] if len(matches) == 1 else None
        reason = "no_candidates" if not candidates else "no_stored_hash" if not expected else "hash_not_matched"
        if len(matches) > 1:
            reason = "ambiguous_hash"
        elif selected:
            reason = ("registered_target" if selected["registered_item_id"] else
                      "outside_resources" if not selected["path"].startswith("resources/") else "unique_hash")
            if any(i["item_id"] != item["item_id"] and i.get("status") != "archived" and i.get("path")
                   and ((is_simulation_bundle(i) and bundle_contains(i["path"], selected["path"]))
                        or (bundle and bundle_contains(selected["path"], i["path"]))) for i in items):
                reason = "registered_target"
        results.append({"item_id": item["item_id"], "old_path": item["path"], "reason": reason,
                        "new_path": selected["path"] if selected else None,
                        "expected_hash": expected, "candidates": candidates})
    claims = {}
    for result in results:
        for candidate in result["candidates"]:
            if candidate["hash_match"]:
                claims.setdefault(candidate["path"].casefold(), set()).add(result["item_id"])
    for result in results:
        if result["new_path"] and len(claims.get(result["new_path"].casefold(), ())) > 1:
            result["reason"] = "shared_candidate"
        result["action"] = "repair" if result["reason"] == "unique_hash" else "needs_input"
        result["next_action"] = ("catalog reconcile --apply" if result["action"] == "repair" else
                                 "请移回 resources 内并选择新路径，或明确确认归档；不会自动删除或归档")
    if item_id:
        results = [r for r in results if r["item_id"] == item_id]
    return {"items": results, "reserved_paths": sorted({c["path"] for r in results for c in r["candidates"]})}
