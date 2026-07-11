"""Defense-in-depth redaction for anything that might end up in logs,
audit trails, or outbound events.

The domain model in this service never stores secrets, tokens, or raw
provider payloads by design (only external ids such as ``sourceId`` and
``reviewerUserIds`` cross the boundary). This module is the backstop: if a
caller ever logs a raw request/response body or header map, sensitive keys
are stripped before they reach a sink we do not fully control (stdout logs,
audit JSONB columns, published events).
"""

from typing import Any, Mapping

_REDACTED = "***REDACTED***"

# Key names (matched by substring against a normalized -- lowercased,
# separator-stripped -- form) that must never appear in logs, audit
# snapshots, or published events. Written without separators since
# normalization strips them; this lets one marker catch "storage_key",
# "storageKey", and "storage-key" alike, regardless of which naming
# convention a given payload happens to use.
_SENSITIVE_KEY_MARKERS = (
    "password",
    "secret",
    "token",
    "authorization",
    "cookie",
    "apikey",
    "email",
    "storagekey",
    "signedurl",
    "providerurl",
    "providerpayload",
    "providerresponse",
    "rawpayload",
    "credential",
    "ssn",
    "creditcard",
)


def _normalize_key(key: str) -> str:
    return "".join(ch for ch in key.lower() if ch.isalnum())


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def sanitize_value(value: Any) -> Any:
    """Recursively redact sensitive keys in dicts/lists. Safe on any input."""
    if isinstance(value, Mapping):
        return {
            key: (_REDACTED if _is_sensitive_key(str(key)) else sanitize_value(val))
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_value(item) for item in value]
    return value


def sanitize_headers(headers: Mapping[str, str]) -> dict:
    """Redact sensitive HTTP headers for safe logging."""
    always_redact = {"authorization", "cookie", "set-cookie", "x-api-key"}
    return {
        key: (_REDACTED if key.lower() in always_redact or _is_sensitive_key(key) else val)
        for key, val in headers.items()
    }
