"""Pure branch classification rules."""

from __future__ import annotations

import re


TOPIC_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ATTEMPT_STATES = ("active", "validated", "negative", "inconclusive", "paused")


def classify_branch(branch: str, policy: dict) -> dict:
    if branch == policy["default_branch"]:
        return {"kind": "stable", "track": "stable", "topic": None}
    parts = branch.split("/")
    if len(parts) == 2 and parts[0] in policy["exploration_types"] and TOPIC_RE.fullmatch(parts[1]):
        return {"kind": "exploration", "track": parts[0], "topic": parts[1]}
    if (len(parts) == 3 and parts[0] == policy["archive_prefix"]
            and parts[1] in policy["exploration_types"] and TOPIC_RE.fullmatch(parts[2])):
        return {"kind": "archive", "track": parts[1], "topic": parts[2]}
    return {"kind": "unsupported", "track": None, "topic": None}
