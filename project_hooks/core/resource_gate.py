"""Pure task relevance rules for resource issues and explicit references."""

from __future__ import annotations


def normalize_references(values):
    result = []
    for value in values or []:
        prefix, separator, target = value.partition(":")
        target = target.replace("\\", "/").strip()
        if not separator or prefix not in {"catalog", "path"} or not target:
            raise ValueError("资料引用必须使用 catalog:条目ID 或 path:项目相对路径")
        if prefix == "path" and (target.startswith("/") or ":" in target
                                 or any(part in {"..", ""} for part in target.split("/"))):
            raise ValueError("资料引用必须是项目内的相对路径")
        normalized = prefix + ":" + target
        if normalized not in result:
            result.append(normalized)
    return result


def issue_key(issue):
    return (issue["code"], str(issue.get("item_id") or ""),
            str(issue.get("path") or "").casefold(), str(issue.get("source_path") or "").casefold())


def related_paths(left, right):
    left, right = left.casefold().rstrip("/"), right.casefold().rstrip("/")
    return bool(left and right) and (left == right or left.startswith(right + "/")
                                    or right.startswith(left + "/"))


def task_resource_gate(baseline, issues, items, relations, events, changed_paths, references):
    touched = {e["payload"]["item_id"] for e in events
               if e["event_type"].startswith("catalog.") and e["payload"].get("item_id")}
    touched.update(e["payload"]["source_id"] for e in events
                   if e["event_type"] == "catalog.relation_upserted")
    touched.update(e["payload"]["target_id"] for e in events
                   if e["event_type"] == "catalog.relation_upserted"
                   and e["payload"]["relation_type"] in {"uses", "derived-from"})
    paths = list(changed_paths)
    paths.extend(e["payload"]["path"] for e in events
                 if e["event_type"] == "catalog.folder_upserted" and e["payload"].get("path"))
    for reference in references:
        prefix, target = reference.split(":", 1)
        if prefix == "catalog":
            touched.add(target)
        else:
            paths.append(target)
    for item in items:
        if item.get("path") and any(related_paths(item["path"], path) for path in paths):
            touched.add(item["item_id"])
    direct = {r["target_id"] for r in relations
              if r["source_id"] in touched and r["relation_type"] in {"uses", "derived-from"}}
    touched.update(direct)
    paths.extend(item["path"] for item in items if item["item_id"] in touched and item.get("path"))
    previous = {issue_key(issue): issue for issue in baseline or []}
    blockers, pending = [], []
    for issue in issues:
        old = previous.get(issue_key(issue))
        relevant = (issue.get("item_id") in touched or
                    any(related_paths(str(issue.get("path") or ""), path)
                        or related_paths(str(issue.get("source_path") or ""), path) for path in paths))
        unchanged = old and old.get("evidence") == issue.get("evidence")
        if not issue.get("hard") and unchanged and not relevant:
            pending.append(issue)
        else:
            blockers.append(issue)
    return {"blockers": blockers, "pending": pending, "related_item_ids": sorted(touched)}
