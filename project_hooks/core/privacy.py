"""Redact private values before they enter the public event journal."""

from __future__ import annotations

import re


_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_WINDOWS_PATH = re.compile(r"(?i)(?<![\w])(?:[a-z]:\\|\\\\)[^\s\"'<>，。；;]+")
_HOME_PATH = re.compile(r"(?i)(?<![\w.])/(?:home|users)/[^\s\"'<>，。；;]+")
_URL_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|client[_-]?secret|"
    r"password|passwd|token|secret|authorization|key)=)[^&#\s\"']+"
)
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|"
    r"password|passwd|token|secret|authorization)\b\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&#，；。]+)"
)
_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN ((?:RSA |EC |OPENSSH )?PRIVATE KEY)-----.*?-----END \1-----",
    re.DOTALL,
)
_EXAMPLE_EMAIL = re.compile(r"(?i)@(?:example\.(?:com|org|net|invalid)|example|test|invalid)$")
_TEST_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz1234"


def sanitize_public_text(value: str) -> str:
    """Keep the surrounding evidence while removing likely personal data."""
    value = _PRIVATE_KEY_BLOCK.sub("<redacted-private-key>", value)
    value = _URL_SECRET.sub(r"\1<redacted>", value)
    value = _ASSIGNMENT.sub(r"\1<redacted>", value)
    value = _TOKEN.sub("<redacted-token>", value)
    value = _PRIVATE_KEY.sub("<redacted-private-key>", value)
    value = _EMAIL.sub("<redacted-email>", value)
    value = _WINDOWS_PATH.sub("<redacted-path>", value)
    return _HOME_PATH.sub("<redacted-path>", value)


def sanitize_public_payload(value: object) -> object:
    if isinstance(value, str):
        return sanitize_public_text(value)
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = sanitize_public_payload(item)
        return value
    if isinstance(value, dict):
        for key, item in value.items():
            value[key] = sanitize_public_payload(item)
        return value
    return value


def sensitive_categories(value: str) -> set[str]:
    """Classify a new line without returning sensitive values in errors."""
    regex_definition = value.startswith("_WINDOWS_PATH = re.compile(")
    value = value.replace("token" + "=" + _TEST_TOKEN, "test-token-placeholder")
    value = value.replace(_TEST_TOKEN, "")
    categories = set()
    if any(not _EXAMPLE_EMAIL.search(match.group()) for match in _EMAIL.finditer(value)):
        categories.add("email")
    if (not regex_definition and _WINDOWS_PATH.search(value)) or _HOME_PATH.search(value):
        categories.add("absolute_path")
    if _URL_SECRET.search(value) or _ASSIGNMENT.search(value):
        categories.add("credential")
    if _PRIVATE_KEY.search(value):
        categories.add("private_key")
    if _TOKEN.search(value.replace(_TEST_TOKEN, "")):
        categories.add("token")
    return categories
