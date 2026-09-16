"""Bounded local verification using explicitly configured argument vectors."""

from __future__ import annotations

import os
import json
import subprocess
import sys
import time
from pathlib import Path

from .verification import active_task, git_head, verification_fingerprint, verify_receipts, receipt_directory
from ..persistence.active_task import atomic_write_json


def run_missing(root: Path, task_id: str, config: dict, verification: dict) -> list[str]:
    executors = config.get("verification_commands") or {}
    pending = [item["suite"] for item in verification["problems"]]
    for suite in pending:
        spec = executors.get(suite) or {}
        argv = spec.get("argv")
        timeout = spec.get("timeout_seconds", 1800)
        if (not isinstance(argv, list) or not argv or
                not all(isinstance(value, str) and value for value in argv) or
                not isinstance(timeout, (int, float)) or timeout <= 0):
            raise RuntimeError(f"VERIFICATION_CONFIGURATION：请配置 verification_commands.{suite} 的 argv 和正数 timeout_seconds")
        cwd = (root / spec.get("cwd", ".")).resolve()
        if not cwd.is_relative_to(root.resolve()) or not cwd.is_dir():
            raise RuntimeError(f"测试工作目录必须位于项目内：{cwd}")
    logs = []
    for suite in pending:
        spec = executors[suite]
        before = verification_fingerprint(root)
        head = git_head(root)
        log = root / ".project_hooks" / "verification-logs" / task_id / f"{suite}-{time.time_ns()}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        print(f"正在验证 {suite}；日志：{log}", file=sys.stderr, flush=True)
        with log.open("wb") as stream:
            process = subprocess.Popen(spec["argv"], cwd=(root / spec.get("cwd", ".")).resolve(),
                                       stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            started = time.monotonic()
            try:
                while True:
                    try:
                        code = process.wait(timeout=min(30, spec.get("timeout_seconds", 1800)))
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() - started >= spec.get("timeout_seconds", 1800):
                            raise RuntimeError(f"{suite} 验证超时；日志：{log}")
                        print(f"{suite} 仍在运行；日志：{log}", file=sys.stderr, flush=True)
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                    else:
                        process.kill()
                    process.wait()
        current = active_task(root)
        if (not current or current["task_id"] != task_id or git_head(root) != head or
                verification_fingerprint(root) != before):
            receipt_path = receipt_directory(root, task_id) / f"{suite}.json"
            if receipt_path.is_file():
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                receipt.update(result="failed", input_changed=True, failures=max(1, receipt.get("failures", 0)))
                atomic_write_json(receipt_path, receipt)
            raise RuntimeError(f"{suite} 验证期间任务或输入发生变化；结果不可用于结束；日志：{log}")
        check = verify_receipts(root, task_id, profile=verification["profile"], changed_paths=[])
        receipt = next((item for item in check["receipts"] if item["suite"] == suite), {})
        if code or receipt.get("result") != "passed" or receipt.get("fingerprint") != before:
            raise RuntimeError(f"{suite} 验证失败或未生成有效回执；请修复后重试；日志：{log}")
        logs.append(str(log))
    return logs
