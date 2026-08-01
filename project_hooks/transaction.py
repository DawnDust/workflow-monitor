"""Cross-process single-writer coordination for project mutations."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


class MutationLockError(RuntimeError):
    pass


_LOCAL = threading.local()


def _held() -> dict[str, int]:
    value = getattr(_LOCAL, "held", None)
    if value is None:
        value = {}
        _LOCAL.held = value
    return value


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            query_limited_information = 0x1000
            still_active = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(query_limited_information, False, pid)
            if not handle:
                # Access denied means the process exists but cannot be inspected.
                return kernel32.GetLastError() == 5
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def read_writer_lock(state_dir: Path) -> dict | None:
    path = state_dir / "mutation.lock"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"status": "unreadable", "path": str(path)}
    value["status"] = "active" if _pid_alive(int(value.get("pid", 0))) else "stale"
    return value


def _publish_lock(path: Path, metadata: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="mutation-", suffix=".lock", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(metadata, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
            return True
        except FileExistsError:
            return False
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def mutation_lock(
    state_dir: Path,
    *,
    command: str,
    task_id: str | None = None,
    timeout: float = 10.0,
) -> Iterator[dict]:
    """Hold the one project writer lock; nested calls in one thread are reentrant."""
    state_dir = state_dir.resolve()
    key = str(state_dir).casefold()
    held = _held()
    if held.get(key, 0):
        held[key] += 1
        try:
            yield {"reentrant": True, "pid": os.getpid(), "command": command, "task_id": task_id}
        finally:
            held[key] -= 1
        return

    token = uuid.uuid4().hex
    metadata = {
        "format": 1,
        "token": token,
        "pid": os.getpid(),
        "process_started_at": datetime.fromtimestamp(
            _process_started_at(), timezone.utc,
        ).isoformat(timespec="seconds"),
        "acquired_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": command,
        "task_id": task_id,
    }
    path = state_dir / "mutation.lock"
    deadline = time.monotonic() + timeout
    while not _publish_lock(path, metadata):
        owner = read_writer_lock(state_dir)
        if owner and owner.get("status") == "stale" and owner.get("pid"):
            try:
                path.unlink()
                continue
            except FileNotFoundError:
                continue
            except OSError:
                pass
        if time.monotonic() >= deadline:
            detail = "无法读取持有者" if not owner else (
                f"PID={owner.get('pid')} command={owner.get('command')} "
                f"task={owner.get('task_id') or 'none'} acquired={owner.get('acquired_at')}"
            )
            raise MutationLockError(f"项目正被另一个写命令占用：{detail}；请稍后重试或运行 task recover")
        time.sleep(0.05)
    held[key] = 1
    try:
        yield metadata
    finally:
        held.pop(key, None)
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("token") == token:
                path.unlink()
        except (FileNotFoundError, OSError, ValueError):
            pass


def _process_started_at() -> float:
    """Best available process start marker; PID liveness remains the reclaim authority."""
    try:
        return os.path.getctime(f"/proc/{os.getpid()}")
    except OSError:
        return time.time()
