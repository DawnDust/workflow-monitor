"""Pure, branch-scoped review coverage and reminder rules."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def encode_token(snapshot):
    return base64.urlsafe_b64encode(json.dumps(snapshot, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode()).decode()


def decode_token(token):
    try:
        value = json.loads(base64.b64decode(token, altchars=b"-_", validate=True))
        if (not isinstance(value, dict) or set(value) != {"branch", "object", "sources"}
                or not isinstance(value["branch"], str) or not isinstance(value["object"], str)
                or not isinstance(value["sources"], dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in value["sources"].items())):
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("无效的审阅范围令牌；请重新 review show") from exc


def parse_time(value):
    if not value:
        return None
    # Existing journal timestamps include a human-readable timezone suffix.
    try:
        return datetime.fromisoformat(value[:19])
    except (TypeError, ValueError):
        return None


def project_review(obj, reviews, now, days=14):
    """Coverage is exact source versions, not the timestamp of unrelated work."""
    matching = [r for r in reviews if r["payload"].get("object") == obj["object"]
                and r["branch"] == obj["branch"]]
    completed = [r for r in matching if r["payload"].get("result") != "deferred"]
    last = completed[-1] if completed else None
    covered = {}
    for item in completed:
        covered.update(item["payload"].get("sources", {}))
    changed = [key for key, value in obj["sources"].items() if covered.get(key) != value]
    reviewed_at = last["occurred_at"] if last else None
    due = bool(parse_time(reviewed_at) and now >= parse_time(reviewed_at) + timedelta(days=days))
    status = obj.get("status", "active")
    reasons = []
    if obj.get("issues") and (changed or not last):
        reasons.extend(obj["issues"])
    if status not in {"paused", "completed", "cancelled", "archived", "validated", "negative", "inconclusive"}:
        if not last:
            reasons.append("尚未审阅")
        elif changed:
            reasons.append("存在未审阅变化")
        elif due:
            reasons.append(f"已超过 {days} 天未审阅")
    latest = matching[-1] if matching else None
    deferred = False
    if latest and latest["payload"].get("result") == "deferred":
        payload = latest["payload"]
        # New source versions always break a deferral; no global snooze.
        same = all(payload.get("sources", {}).get(k) == v for k, v in obj["sources"].items())
        until = parse_time(payload.get("until"))
        deferred = same and ((until is not None and now < until)
                             or (payload.get("until") == "resume" and status == "paused"))
    return {**obj, "last_reviewed_at": reviewed_at, "pending": bool(reasons) and not deferred,
            "deferred": deferred, "reasons": reasons, "changes": changed,
            "token": encode_token({k: obj[k] for k in ("branch", "object", "sources")})}
