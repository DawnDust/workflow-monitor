"""Git process boundary used by lifecycle and health services."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run(root: Path, args: list[str], *, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=60, env=env, check=check,
    )


def integration_evidence(
    root: Path, base_ref: str, source_ref: str, *,
    required_path: str | None = None, required_text: str | None = None,
) -> dict:
    """Classify whether a branch tip is fully represented in a base history."""
    base_commit = run(root, ["rev-parse", f"{base_ref}^{{commit}}"]).stdout.strip()
    source_commit = run(root, ["rev-parse", f"{source_ref}^{{commit}}"]).stdout.strip()
    if required_path is not None and required_text is not None:
        source_file = run(root, ["show", f"{source_commit}:{required_path}"], check=False)
        if source_file.returncode != 0 or required_text not in source_file.stdout:
            return {
                "state": "pending_merge", "method": None, "proof_commit": None,
                "reason": "source_record_missing",
            }
    ancestor = run(
        root, ["merge-base", "--is-ancestor", source_commit, base_commit], check=False,
    )
    if ancestor.returncode == 0:
        return {"state": "merged", "method": "ancestor", "proof_commit": source_commit}

    source_tree = run(root, ["rev-parse", f"{source_commit}^{{tree}}"]).stdout.strip()
    history = run(root, ["log", "--format=%H%x09%T", base_commit]).stdout.splitlines()
    for line in history:
        proof_commit, separator, historical_tree = line.partition("\t")
        if separator and historical_tree == source_tree:
            return {
                "state": "merged", "method": "historical-tree-equivalent",
                "proof_commit": proof_commit,
            }

    merged = run(root, ["merge-tree", "--write-tree", base_commit, source_commit], check=False)
    base_tree = run(root, ["rev-parse", f"{base_commit}^{{tree}}"]).stdout.strip()
    merged_tree = merged.stdout.splitlines()[0].strip() if merged.stdout.splitlines() else ""
    if merged.returncode == 0 and merged_tree == base_tree:
        return {"state": "merged", "method": "squash-equivalent", "proof_commit": base_commit}

    merge_base = run(root, ["merge-base", base_commit, source_commit], check=False)
    return {
        "state": "pending_merge" if merge_base.returncode == 0 else "merge_unknown",
        "method": None,
        "proof_commit": None,
    }


def is_repository(root: Path) -> bool:
    try:
        return run(root, ["rev-parse", "--is-inside-work-tree"]).stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def dirty_paths(root: Path) -> list[str]:
    if not is_repository(root):
        return []
    entries = run(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]).stdout.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            if index < len(entries) and entries[index]:
                paths.add(entries[index].replace("\\", "/"))
                index += 1
        paths.add(path.replace("\\", "/"))
    return sorted(paths)


def current_branch(root: Path) -> str | None:
    if not is_repository(root):
        return None
    result = run(root, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def process_environment() -> dict:
    return os.environ.copy()
