from __future__ import annotations

from typing import Any

from services.config import config
from services.log_service import log_service

LOG_TYPE_RAW_DEBUG = "raw_debug"
MAX_STRING_CHARS = 200_000
SECRET_KEY_PARTS = (
    "authorization",
    "access_token",
    "api_key",
    "auth-key",
    "cookie",
    "key",
    "password",
    "pat",
    "secret",
    "set-cookie",
    "token",
)


def _is_secret_key(key: object) -> bool:
    normalized = str(key or "").strip().lower().replace("_", "-")
    return any(part in normalized for part in SECRET_KEY_PARTS)


def _clip_text(value: str) -> str | dict[str, object]:
    if len(value) <= MAX_STRING_CHARS:
        return value
    return {
        "truncated": True,
        "chars": len(value),
        "preview": value[:MAX_STRING_CHARS],
    }


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            sanitized[str(key)] = "[REDACTED]" if _is_secret_key(key) else _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _clip_text(value)
    return value


def raw_debug_log(summary: str, detail: dict[str, Any]) -> None:
    if not config.raw_debug_logging:
        return
    log_service.add(LOG_TYPE_RAW_DEBUG, summary, _sanitize(detail))
