from __future__ import annotations

import re
from typing import Any

from fastapi.responses import JSONResponse


SENSITIVE_ERROR_FALLBACK = "request failed"
MAX_PUBLIC_ERROR_MESSAGE_LENGTH = 500

_SENSITIVE_MARKERS = (
    "access_token",
    "refresh_token",
    "id_token",
    "authorization",
    "bearer ",
    "set-cookie",
    "cookie",
    "session token",
    "secret_key",
    "oauth",
    "sso",
)
_NOISY_MARKERS = (
    "traceback",
    "upstreamhttperror",
    "backend-api/",
    "chatgpt.com",
    "status=",
    "body=",
    "curl: (",
    "tls connect error",
    "openssl_internal",
    "connection reset",
    "read timed out",
    "connect timeout",
    "max retries exceeded",
    "httpconnectionpool",
    "httpsconnectionpool",
    "clientconnectorerror",
    "serverdisconnectederror",
    "failed to establish a new connection",
)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PYTHON_FRAME_PATTERN = re.compile(r'File "[^"]+", line \d+', re.IGNORECASE)
_EXCEPTION_REPR_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)\s*\(")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:access_token|refresh_token|id_token|authorization|cookie|session_token|secret_key|api_key)\b\s*[:=]"
)


def _message_from_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    message = value.get("message")
    if isinstance(message, str) and message:
        return message
    return _message_from_value(value.get("error"))


def error_message_from_detail(detail: object) -> str:
    if isinstance(detail, list):
        messages = []
        for item in detail:
            if not isinstance(item, dict):
                continue
            location = ".".join(str(part) for part in item.get("loc", []) if part != "body")
            message = str(item.get("msg") or "").strip()
            if location and message:
                messages.append(f"{location}: {message}")
            elif message:
                messages.append(message)
        return "; ".join(messages)
    if isinstance(detail, dict):
        message = _message_from_value(detail.get("error")) or _message_from_value(detail)
        if message:
            return message
    return str(detail or "").strip()


def _default_error_type(status_code: int) -> str:
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 429:
        return "rate_limit_error"
    if 400 <= status_code < 500:
        return "invalid_request_error"
    return "server_error"


def _default_error_code(status_code: int) -> str:
    if status_code == 401:
        return "invalid_api_key"
    if status_code == 403:
        return "permission_denied"
    if status_code == 429:
        return "rate_limit_exceeded"
    if 400 <= status_code < 500:
        return "bad_request"
    return "upstream_error"


_DATA_URL_PATTERN = re.compile(r"data:[-+./\w]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
_LONG_BASE64_PATTERN = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{120,}={0,2}(?![A-Za-z0-9+/=])")


def sanitize_public_error_message(message: object, *, fallback: str = SENSITIVE_ERROR_FALLBACK) -> str:
    value = str(message or "").strip()
    if not value:
        return fallback
    normalized = " ".join(value.split())
    lowered = normalized.lower()
    if (
        any(marker in lowered for marker in _SENSITIVE_MARKERS)
        or any(marker in lowered for marker in _NOISY_MARKERS)
        or _EMAIL_PATTERN.search(normalized)
        or _PYTHON_FRAME_PATTERN.search(normalized)
        or _EXCEPTION_REPR_PATTERN.search(normalized)
        or _SECRET_ASSIGNMENT_PATTERN.search(normalized)
        or _DATA_URL_PATTERN.search(normalized)
        or _LONG_BASE64_PATTERN.search(normalized)
    ):
        return fallback
    if len(normalized) > MAX_PUBLIC_ERROR_MESSAGE_LENGTH:
        return normalized[: MAX_PUBLIC_ERROR_MESSAGE_LENGTH - 3] + "..."
    return normalized


def _sanitize_payload_value(value: object, *, fallback: object | None = None) -> object:
    if not isinstance(value, str):
        return value
    sanitized = sanitize_public_error_message(value, fallback=str(fallback or SENSITIVE_ERROR_FALLBACK))
    if sanitized == SENSITIVE_ERROR_FALLBACK and fallback is not None:
        return fallback
    return sanitized


def sanitize_openai_error_payload(payload: dict[str, Any]) -> dict[str, Any]:
    error = payload.get("error")
    if not isinstance(error, dict):
        return payload
    sanitized_error = dict(error)
    sanitized_error["message"] = sanitize_public_error_message(error.get("message"))
    sanitized_error["param"] = _sanitize_payload_value(error.get("param"))
    sanitized_error["code"] = _sanitize_payload_value(error.get("code"), fallback="upstream_error")
    return {**payload, "error": sanitized_error}


def openai_error_payload(
    detail: object,
    status_code: int,
    *,
    error_type: str | None = None,
    code: object | None = None,
    param: object | None = None,
) -> dict[str, Any]:
    error_detail = detail.get("error") if isinstance(detail, dict) else None
    if isinstance(error_detail, dict):
        payload = {
            "error": {
                "message": error_message_from_detail(error_detail) or "request failed",
                "type": str(error_detail.get("type") or error_type or _default_error_type(status_code)),
                "param": error_detail.get("param", param),
                "code": error_detail.get("code", code if code is not None else _default_error_code(status_code)),
            }
        }
        return sanitize_openai_error_payload(payload)
    payload = {
        "error": {
            "message": error_message_from_detail(detail) or "request failed",
            "type": error_type or _default_error_type(status_code),
            "param": param,
            "code": code if code is not None else _default_error_code(status_code),
        }
    }
    return sanitize_openai_error_payload(payload)


def openai_error_response(
    detail: object,
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
    error_type: str | None = None,
    code: object | None = None,
    param: object | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=openai_error_payload(detail, status_code, error_type=error_type, code=code, param=param),
        headers=headers,
    )


def anthropic_error_payload(detail: object, status_code: int) -> dict[str, Any]:
    error_type = "api_error" if status_code >= 500 else _default_error_type(status_code)
    return {
        "type": "error",
        "error": {
            "type": error_type,
            "message": error_message_from_detail(detail) or "request failed",
        },
    }


def anthropic_error_response(
    detail: object,
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=anthropic_error_payload(detail, status_code),
        headers=headers,
    )
