"""Artifact sanitiser.

Everything that travels from the execution environment back toward the AI passes
through here. Two layers of defence:

1. **Exact-value redaction.** Every secret the broker resolves is registered in a
   process-local registry. If that literal string ever appears in an artifact it
   is replaced, no matter how it got there (log line, DOM dump, HAR body).
2. **Pattern redaction.** Known credential shapes (JWTs, bearer headers, API key
   prefixes, cookies) are redacted even if we have never seen the value.

The registry holds secrets in memory only and is never serialised.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

REDACTED = "[REDACTED]"

# Values shorter than this are too likely to appear incidentally in normal text
# (e.g. a password of "abc") and blanket-redacting them would mangle artifacts.
_MIN_REDACTABLE_LEN = 4

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b")),
    ("bearer", re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("authz_header", re.compile(r"(?im)^(authorization|proxy-authorization|x-api-key|cookie|set-cookie)\s*:\s*.+$")),
    ("api_key", re.compile(r"\b(sk|pk|rk|api|key|tok|ghp|gho|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b")),
    ("aws_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("conn_string", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s]+")),
    (
        "password_kv",
        re.compile(
            r"(?i)([\"']?\b(?:password|passwd|pwd|secret|token|api[_-]?key)\b[\"']?\s*[=:]\s*[\"']?)"
            r"([^\s\"',;}\]]{4,})"
        ),
    ),
]


class SecretRegistry:
    """Process-local set of literal secret values that must never be emitted."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def register(self, value: str) -> None:
        if value and len(value) >= _MIN_REDACTABLE_LEN:
            self._values.add(value)

    def register_all(self, values: Iterable[str]) -> None:
        for v in values:
            self.register(v)

    def known_values(self) -> list[str]:
        # Longest first so overlapping secrets redact the widest match.
        return sorted(self._values, key=len, reverse=True)

    def clear(self) -> None:
        self._values.clear()


_registry = SecretRegistry()


def registry() -> SecretRegistry:
    return _registry


def scrub_text(text: str) -> str:
    """Redact known secret values and credential-shaped patterns from a string."""
    if not text:
        return text
    out = text
    for value in _registry.known_values():
        if value in out:
            out = out.replace(value, REDACTED)
    for name, pattern in _PATTERNS:
        if name == "password_kv":
            # Keep the key and surrounding syntax; replace only the value.
            out = pattern.sub(lambda m: m.group(1) + REDACTED, out)
        elif name == "authz_header":
            out = pattern.sub(lambda m: f"{m.group(1)}: {REDACTED}", out)
        else:
            out = pattern.sub(REDACTED, out)
    return out


def scrub(obj: Any, _depth: int = 0) -> Any:
    """Recursively sanitise any JSON-ish structure before it reaches the model."""
    if _depth > 24:
        return "[TRUNCATED: max depth]"
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, dict):
        return {scrub_text(str(k)): scrub(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v, _depth + 1) for v in obj]
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return scrub_text(str(obj))


def contains_secret(obj: Any) -> bool:
    """True if a known secret value survives in the structure. Used by tests and
    by the MCP output guard as a last-chance tripwire."""
    text = obj if isinstance(obj, str) else repr(obj)
    return any(v in text for v in _registry.known_values())
