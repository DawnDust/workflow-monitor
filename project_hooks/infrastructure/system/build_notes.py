"""Read and render the test notes bundled with a particular executable."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from ..git.build_identity import build_identity

REQUIRED_SECTIONS = ("待测变化", "测试步骤与预期结果", "已知限制", "项目升级注意事项", "源码依据")


def validate_notes(content: str) -> str:
    sections = re.split(r"(?m)^##\s+", content)
    found = {}
    for section in sections[1:]:
        heading, _, body = section.partition("\n")
        found[heading.strip()] = body.strip()
    missing = [heading for heading in REQUIRED_SECTIONS if not found.get(heading)]
    if missing:
        raise ValueError("测试说明缺少非空章节: " + "、".join(missing))
    return content.strip() + "\n"


def build_verification(root: Path) -> dict:
    """Attach only receipts accepted for the current software inputs."""
    from .verification import active_task, verify_receipts
    task = active_task(root)
    accepted = []
    if task:
        accepted = verify_receipts(root, task["task_id"], profile="auto",
                                   changed_paths=["project_hooks/"])["accepted"]
    by_suite = {item["suite"]: item for item in accepted}
    return {"suites": [{"suite": suite, "status": "passed" if suite in by_suite else "unverified",
                       "evidence": by_suite.get(suite)} for suite in ("fast", "full")],
            "manual": "未验证：本说明未附人工交互验收证据",
            "frozen_distribution": "未验证：此说明生成后才核验 EXE；结果见独立交付证据"}


def notes_payload(content: str, identity: dict, verification: dict | None = None) -> dict:
    content = validate_notes(content)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    header = "\n".join([
        "# 测试构建身份", "",
        f"- 构建 ID：{identity['build_id']}",
        f"- 构建时间（UTC）：{identity.get('built_at') or '未构建'}",
        f"- 源码提交：{identity.get('source_commit') or '未知'}",
        f"- 含未提交修改：{bool(identity.get('dirty'))}",
        f"- 输入指纹：{identity['build_input_fingerprint']}",
        f"- 说明 SHA-256：{digest}", "",
    ])
    verification = verification or {"suites": [], "manual": "未验证：未提供验证证据"}
    evidence_lines = ["", "## 已有验证证据", ""]
    for item in verification["suites"]:
        evidence = item.get("evidence")
        if evidence:
            evidence_lines.append(f"- {item['suite']}：通过；{evidence['tests']} 项；完成于 {evidence['finished_at']}；"
                                  f"回执 `.project_hooks/test-receipts/{evidence['task_id']}/{item['suite']}.json`；"
                                  f"输入指纹 `{evidence['fingerprint']}`。")
        else:
            evidence_lines.append(f"- {item['suite']}：未验证（缺少当前输入的有效回执）。")
    evidence_lines.extend(f"- {verification[key]}。" for key in ("manual", "frozen_distribution") if key in verification)
    return {"available": True, "build_id": identity["build_id"], "notes_sha256": digest,
            "markdown": header + "\n" + content + "\n".join(evidence_lines) + "\n", "verification": verification}


def read_build_notes() -> dict:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3] / "build"))
    try:
        value = json.loads((base / "test-build-notes.json").read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("available") and value.get("build_id") == build_identity().get("build_id"):
            return value
    except (OSError, ValueError, TypeError):
        pass
    return {"available": False, "markdown": "此构建未提供测试说明", "build_id": build_identity().get("build_id")}
