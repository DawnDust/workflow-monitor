"""Redact private values before they enter the public event journal."""

from __future__ import annotations

import json
import re


_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_WINDOWS_PATH = re.compile(
    r"(?i)(?<![\w])(?:"
    r"[a-z]:[\\/][^\\/\s\"'<>，。；;]+(?:[\\/][^\\/\s\"'<>，。；;]+)*"
    r"|\\\\[^\\/\s\"'<>，。；;]+[\\/][^\\/\s\"'<>，。；;]+"
    r"(?:[\\/][^\\/\s\"'<>，。；;]+)*"
    r")"
)
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
_JSON_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_JSON_CREDENTIAL = re.compile(
    r'(?i)"(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|token|secret|authorization)"'
    r'\s*:\s*"((?:\\.|[^"\\])*)"'
)
_JSON_MEMBER = re.compile(
    rf'(?P<key>{_JSON_STRING.pattern})\s*:\s*(?P<value>{_JSON_STRING.pattern})',
    re.DOTALL,
)
_JSON_CREDENTIAL_NAMES = frozenset({
    "api_key", "api-key", "access_token", "access-token", "client_secret",
    "client-secret", "password", "passwd", "token", "secret", "authorization",
})
_BACKSLASH = chr(92)
_MATH_DELIMITER = re.compile(
    r"(?<!" + re.escape(_BACKSLASH) + r")(?:\$|"
    + re.escape(_BACKSLASH + "(") + "|"
    + re.escape(_BACKSLASH + "[") + ")"
)
_LATEX_COMMANDS = frozenset({
    "alpha", "beta", "cdot", "frac", "gamma", "infty", "lambda", "left",
    "mapsto", "mathbb", "mathbf", "mathrm", "nabla", "omega", "operatorname",
    "partial", "right", "sigma", "sqrt", "text", "theta", "times", "to",
})


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


def _is_latex_command_path(match: re.Match[str]) -> bool:
    """Avoid interpreting TeX control words after a drive-like math token as paths."""
    value = match.group()
    drive = re.match(r"(?i)^[a-z]:[\\/](.+)$", value)
    if not drive:
        return False
    command = re.match(r"(?i)([a-z]+)\b", drive.group(1))
    return bool(command and command.group(1).lower() in _LATEX_COMMANDS)


def sensitive_categories(
    value: str, *, scan_paths: bool = True, markdown_math: bool = False,
) -> set[str]:
    """Classify text while keeping syntax escapes from looking like UNC paths."""
    regex_definition = value.startswith("_WINDOWS_PATH = re.compile(")
    value = value.replace("token" + "=" + _TEST_TOKEN, "test-token-placeholder")
    value = value.replace(_TEST_TOKEN, "")
    categories = set()
    if any(not _EXAMPLE_EMAIL.search(match.group()) for match in _EMAIL.finditer(value)):
        categories.add("email")
    if scan_paths:
        paths = [] if regex_definition else list(_WINDOWS_PATH.finditer(value))
        if markdown_math:
            paths = [item for item in paths if not _is_latex_command_path(item)]
        if paths or _HOME_PATH.search(value):
            categories.add("absolute_path")
    if _URL_SECRET.search(value) or _ASSIGNMENT.search(value):
        categories.add("credential")
    if _PRIVATE_KEY.search(value):
        categories.add("private_key")
    if _TOKEN.search(value.replace(_TEST_TOKEN, "")):
        categories.add("token")
    return categories


def added_line_categories(value: str, path: str) -> set[str]:
    """Classify one added line using the file's text syntax where it matters."""
    suffix = path.lower().rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if suffix == "json":
        categories = sensitive_categories(value, scan_paths=False)
        if _JSON_CREDENTIAL.search(value):
            categories.add("credential")
        decoded: list[str] = []
        for match in _JSON_STRING.finditer(value):
            try:
                item = json.loads(match.group())
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(item, str):
                decoded.append(item)
        if decoded:
            for item in decoded:
                math_context = bool(_MATH_DELIMITER.search(item))
                categories.update(sensitive_categories(item, markdown_math=math_context))
        else:
            # Malformed or non-string JSON syntax stays conservative for paths too.
            categories.update(sensitive_categories(value))
        return categories
    if suffix in {"md", "markdown", "mdown", "mkd"}:
        math_context = bool(_MATH_DELIMITER.search(value))
        return sensitive_categories(value, markdown_math=math_context)
    return sensitive_categories(value)


def added_json_findings(document: str, added_lines: dict[int, str]) -> set[tuple[int, str]]:
    """Classify changed JSON lines using parsed string and member boundaries."""
    findings: set[tuple[int, str]] = set()
    for line_number, line in added_lines.items():
        findings.update((line_number, category) for category in added_line_categories(line, "file.json"))
    try:
        json.loads(document)
    except (json.JSONDecodeError, TypeError):
        return findings

    def line_at(offset: int) -> int:
        return document.count("\n", 0, offset) + 1

    for match in _JSON_STRING.finditer(document):
        line_number = line_at(match.start())
        if line_number not in added_lines:
            continue
        try:
            value = json.loads(match.group())
        except json.JSONDecodeError:
            continue
        if not isinstance(value, str):
            continue
        math_context = bool(_MATH_DELIMITER.search(value))
        findings.update(
            (line_number, category)
            for category in sensitive_categories(value, markdown_math=math_context)
        )

    for member in _JSON_MEMBER.finditer(document):
        try:
            key = json.loads(member.group("key"))
        except json.JSONDecodeError:
            continue
        if not isinstance(key, str) or key.lower() not in _JSON_CREDENTIAL_NAMES:
            continue
        key_line = line_at(member.start("key"))
        value_line = line_at(member.start("value"))
        if key_line in added_lines:
            findings.add((key_line, "credential"))
        if value_line in added_lines:
            findings.add((value_line, "credential"))
    return findings
