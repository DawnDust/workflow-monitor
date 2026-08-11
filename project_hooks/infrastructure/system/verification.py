"""Task-scoped test receipts and deterministic software-input fingerprints."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

from ..persistence.active_task import atomic_write_json, load_active_state


SOFTWARE_INPUT_PREFIXES = (
    "project_hooks/",
    "scripts/",
    "tests/",
    ".github/",
    ".githooks/",
)
SOFTWARE_INPUT_FILES = {
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "workflow-monitor.spec",
}
EXCLUDED_PARTS = {
    ".git", ".project_hooks", "__pycache__", ".pytest_cache", ".venv",
    "build", "dist", "htmlcov", "node_modules",
}


def is_software_input(relative: str) -> bool:
    path = relative.replace("\\", "/").lstrip("./")
    return path in SOFTWARE_INPUT_FILES or path.startswith(SOFTWARE_INPUT_PREFIXES)


def software_input_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        if is_software_input(relative):
            result.append(path)
    return sorted(result, key=lambda item: item.relative_to(root).as_posix())


def verification_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in software_input_files(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def git_head(root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def active_task(root: Path) -> dict | None:
    marker = root / ".codex/project-maintenance-workflow.json"
    try:
        config = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    state = load_active_state(root / config.get("state_dir", ".project_hooks"))
    return state.get("record") if state else None


def receipt_directory(root: Path, task_id: str) -> Path:
    return root / ".project_hooks/test-receipts" / task_id


def write_test_receipt(
    root: Path, *, suite: str, result: str, tests: int, failures: int,
    coverage: float | None, started_at: str, finished_at: str,
) -> dict | None:
    task = active_task(root)
    if task is None:
        return None
    runner = root / "scripts/run_tests.py"
    receipt = {
        "task_id": task["task_id"],
        "suite": suite,
        "result": result,
        "tests": int(tests),
        "failures": int(failures),
        "coverage": coverage,
        "started_at": started_at,
        "finished_at": finished_at,
        "head": git_head(root),
        "runner_hash": hashlib.sha256(runner.read_bytes()).hexdigest(),
        "fingerprint": verification_fingerprint(root),
    }
    atomic_write_json(receipt_directory(root, task["task_id"]) / f"{suite}.json", receipt)
    return receipt


def load_test_receipts(root: Path, task_id: str) -> list[dict]:
    directory = receipt_directory(root, task_id)
    receipts: list[dict] = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            item = {"suite": path.stem, "result": "invalid"}
        item["path"] = path.relative_to(root).as_posix()
        receipts.append(item)
    return receipts


def receipt_summary(receipt: dict) -> dict:
    return {
        key: receipt.get(key) for key in (
            "task_id", "suite", "result", "tests", "failures", "coverage",
            "started_at", "finished_at", "head", "runner_hash", "fingerprint",
        )
    }


def required_suites(profile: str, changed_paths: list[str]) -> list[str]:
    if profile == "release":
        return ["fast", "release"]
    if any(is_software_input(path) for path in changed_paths):
        return ["fast", "full"]
    return []


def changed_paths_from_baseline(root: Path, baseline: dict) -> list[str]:
    current: dict[str, str] = {}
    for path in software_input_files(root):
        relative = path.relative_to(root).as_posix()
        current[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    before = {
        path: item.get("sha256") for path, item in baseline.items()
        if is_software_input(path)
    }
    return sorted(
        path for path in {*before, *current}
        if before.get(path) != current.get(path)
    )


def verify_receipts(
    root: Path, task_id: str, *, profile: str, changed_paths: list[str],
) -> dict:
    fingerprint = verification_fingerprint(root)
    receipts = load_test_receipts(root, task_id)
    by_suite = {item.get("suite"): item for item in receipts}
    required = required_suites(profile, changed_paths)
    problems: list[dict] = []
    accepted: list[dict] = []
    for suite in required:
        item = by_suite.get(suite)
        reason = None
        if item is None:
            reason = "missing"
        elif item.get("task_id") != task_id:
            reason = "wrong-task"
        elif item.get("result") != "passed":
            reason = "failed"
        elif item.get("fingerprint") != fingerprint:
            reason = "stale"
        if reason:
            problems.append({
                "suite": suite,
                "reason": reason,
                "command": f"python scripts/run_tests.py {suite}",
            })
        else:
            accepted.append(receipt_summary(item))
    return {
        "profile": profile,
        "fingerprint": fingerprint,
        "required_suites": required,
        "receipts": [receipt_summary(item) for item in receipts],
        "accepted": accepted,
        "problems": problems,
        "status": "passed" if not problems else "blocked",
    }


def clear_test_receipts(root: Path, task_id: str) -> None:
    directory = receipt_directory(root, task_id)
    if not directory.is_dir():
        return
    for path in directory.glob("*.json"):
        path.unlink(missing_ok=True)
    try:
        directory.rmdir()
        directory.parent.rmdir()
    except OSError:
        pass


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
