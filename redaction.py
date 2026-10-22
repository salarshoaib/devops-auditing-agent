"""Secret redaction.

The agent is allowed to *report* that a credential is committed; it is never
allowed to reproduce the credential itself. Every string that leaves the agent
(tool output fed to the LLM, trace logs, JSON report) passes through
:func:`redact_secrets`.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# Ordered from most specific to most generic.
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,255}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,255}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    re.compile(r"(?i)\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s'\"<>]+:[^\s'\"<>@]+@[^\s'\"<>]+"),
]

# key = "value" / key: value shapes. Keep the key so evidence stays useful.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b([A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|password|passwd|pwd|credential|access[_-]?key|private[_-]?key)[A-Za-z0-9_.-]*)
    (\s*[:=]\s*)
    (["']?)([^\s"',;]{6,})\3
    """
)

# Environment-variable references such as ${TOKEN} are safe and must survive.
_LOOKS_LIKE_REFERENCE = re.compile(
    r"^(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|<[^>]+>|\{\{.*\}\}|%[A-Za-z_][A-Za-z0-9_]*%|os\.getenv|process\.env|env\.)",
)


def _mask_assignment(match: re.Match[str]) -> str:
    key, separator, quote, value = match.groups()
    if _LOOKS_LIKE_REFERENCE.match(value.strip()):
        return match.group(0)
    if value.lower() in {"none", "null", "true", "false", "changeme", "example", "your_token_here"}:
        return match.group(0)
    return f"{key}{separator}{quote}{REDACTED}{quote}"


def redact_secrets(text: str) -> str:
    """Return *text* with credential-looking values replaced by ``[REDACTED]``."""
    if not text:
        return text
    result = text
    for pattern in _PATTERNS:
        result = pattern.sub(REDACTED, result)
    result = _ASSIGNMENT.sub(_mask_assignment, result)
    return result


def contains_secret(text: str) -> bool:
    """True when *text* still carries something that looks like a live secret."""
    return redact_secrets(text) != text
