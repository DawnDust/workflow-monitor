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
    # Version 2 carries a digest instead of every source version.  The current
    # inventory reconstructs the exact range when the receipt is submitted.
    compact = {"v": 2, "branch": snapshot["branch"], "object": snapshot["object"],
               "digest": digest(snapshot["sources"])}
    return base64.urlsafe_b64encode(json.dumps(compact, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode()).decode()


def decode_token(token):
    try:
        value = json.loads(base64.b64decode(token, altchars=b"-_", validate=True))
        old = set(value) == {"branch", "object", "sources"}
        new = set(value) == {"v", "branch", "object", "digest"} and value.get("v") == 2
        if (not isinstance(value, dict) or not (old or new)
                or not isinstance(value["branch"], str) or not isinstance(value["object"], str)
                or (old and (not isinstance(value["sources"], dict)
                    or not all(isinstance(k, str) and isinstance(v, str) for k, v in value["sources"].items())))
                or (new and not isinstance(value["digest"], str))):
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
    exact_reviewed_at = last["occurred_at"] if last else None
    legacy_reviewed_at = obj.get("legacy_reviewed_at")
    reviewed_at = exact_reviewed_at or legacy_reviewed_at
    due = bool(parse_time(exact_reviewed_at) and now >= parse_time(exact_reviewed_at) + timedelta(days=days))
    status = obj.get("status", "active")
    usage = obj.get("usage", "formal")
    reasons = []
    if obj.get("issues"):
        reasons.extend(obj["issues"])
    reason_codes = ["integrity_issue"] if obj.get("issues") else []
    terminal = status in {"paused", "completed", "cancelled", "archived", "validated", "negative", "inconclusive"}
    if not terminal and usage != "example":
        if not last:
            reasons.append("尚未建立新版审阅基线" if legacy_reviewed_at else "尚未审阅")
            reason_codes.append("baseline_missing")
        elif changed:
            reasons.append("存在未审阅变化")
            reason_codes.append("content_changed")
        elif due and usage != "reference":
            reasons.append(f"已超过 {days} 天未审阅")
            reason_codes.append("review_due")
    latest = matching[-1] if matching else None
    deferred = False
    if latest and latest["payload"].get("result") == "deferred":
        payload = latest["payload"]
        # New source versions always break a deferral; no global snooze.
        same = all(payload.get("sources", {}).get(k) == v for k, v in obj["sources"].items())
        until = parse_time(payload.get("until"))
        deferred = same and ((until is not None and now < until)
                             or (payload.get("until") == "resume" and status == "paused"))
    return {**obj, "last_reviewed_at": reviewed_at, "exact_reviewed_at": exact_reviewed_at,
            "last_legacy_reviewed_at": legacy_reviewed_at,
            "pending": bool(obj.get("issues")) or (bool(reasons) and not deferred),
            "deferred": deferred, "deferred_until": latest["payload"].get("until") if deferred else None,
            "reason_codes": reason_codes, "reasons": reasons, "changes": changed,
            "token": encode_token({k: obj[k] for k in ("branch", "object", "sources")})}
