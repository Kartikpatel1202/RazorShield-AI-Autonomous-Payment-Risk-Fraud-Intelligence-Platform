"""Structured JSON logging for the backend process.

Every record is one JSON object on one line, carrying the correlation id from
:mod:`app.core.context` so a single transaction can be followed from the request
that submitted it through to the decision that came out.

Two things this module refuses to do:

1. **Emit a secret.** :class:`RedactingFilter` scrubs known-sensitive keys from
   structured fields and known-sensitive patterns from message text, so a
   careless ``logger.info("token=%s", token)`` somewhere in the codebase still
   does not end up in the log stream. It is a backstop, not a licence - the
   correct fix is never to pass the value.
2. **Crash the caller.** A field that will not serialise is stringified rather
   than raising out of a logging call.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.context import get_correlation_id

#: Structured-field names whose values are never printed. Matched
#: case-insensitively against the whole key and against `_`-separated parts, so
#: ``llm_api_key``, `apiKey` and `AUTHORIZATION` are all covered.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "password_hash",
        "secret",
        "jwt_secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "apikey",
        "llm_api_key",
        "anthropic_api_key",
        "openai_api_key",
        "database_url",
        "dsn",
        "credential",
        "credentials",
        "cookie",
        "set-cookie",
        "private_key",
    }
)

REDACTED = "[redacted]"

#: Message-text patterns for the shapes secrets take when someone interpolates
#: one into a string. Each keeps its label and replaces only the value.
_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # key=value / key: value for a sensitive-looking key
    re.compile(
        r"(?i)\b((?:\w*_)?(?:password|secret|token|api[_-]?key|authorization|credential)s?)"
        r"\s*[=:]\s*(\"[^\"]*\"|'[^']*'|\S+)"
    ),
    # Authorization header values
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    # A JWT anywhere in the text: three base64url segments
    re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b"),
    # A database URL with inline credentials
    re.compile(r"(?i)\b([a-z0-9+]+://)[^\s:@/]+:[^\s:@/]+@"),
    # Anthropic-style keys
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),
)

#: `logging.LogRecord` attributes that are plumbing rather than payload; anything
#: else attached to a record is treated as a structured field.
_RESERVED: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | frozenset({"message", "asctime", "taskName"})


def redact_text(text: str) -> str:
    """Replace secret-shaped substrings in a free-text message."""
    redacted = text
    for pattern in _TEXT_PATTERNS:
        if pattern.groups >= 1:
            redacted = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}", redacted)
        else:
            redacted = pattern.sub(REDACTED, redacted)
    return redacted


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SENSITIVE_KEYS:
        return True
    # `llm_api_key` -> {"llm", "api", "key"}; catches compound names without
    # having to enumerate every prefix anyone might invent.
    parts = set(re.split(r"[_\-.]", lowered))
    return bool(parts & {"password", "secret", "token", "authorization", "credential"}) or (
        "key" in parts and "api" in parts
    )


def redact_value(key: str, value: Any) -> Any:
    """Redact ``value`` if ``key`` names something sensitive, recursively."""
    if _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(key, item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class RedactingFilter(logging.Filter):
    """Scrub secrets from a record before any handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED:
                continue
            record.__dict__[key] = redact_value(key, value)
        return True


class JsonFormatter(logging.Formatter):
    """Render a record as a single-line JSON object."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "message": record.getMessage(),
        }

        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id

        for key, value in record.__dict__.items():
            if key in _RESERVED or key in payload:
                continue
            payload[key] = value

        if record.exc_info:
            # The traceback is operator-facing and stays out of HTTP responses;
            # `register_exception_handlers` returns a flat "Internal server
            # error" to the client.
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(
    level: str, *, service: str = "razorshield-backend", json_format: bool = True
) -> None:
    """Install a single stdout handler at the requested level.

    Idempotent, so repeated calls (tests, reloads) do not duplicate output.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    existing = [h for h in root.handlers if getattr(h, "_razorshield", False)]
    if existing:
        for handler in existing:
            handler.setLevel(level.upper())
        return

    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(JsonFormatter(service))
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
        )
    handler.addFilter(RedactingFilter())
    handler._razorshield = True  # type: ignore[attr-defined]
    root.addHandler(handler)
