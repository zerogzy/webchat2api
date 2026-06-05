from __future__ import annotations

import importlib
from typing import Any

from . import models

_CLIENT_EXPORTS = {
    "GeminiCompletion",
    "GeminiWebClient",
    "GeminiWebError",
    "account_cookie_header",
    "account_session_token",
    "build_prompt",
    "build_stream_generate_form_payload",
    "build_web_payload",
    "chat_completion",
    "classify_upstream_error",
    "cookie_header_from_mapping",
    "cookie_header_from_response",
    "extract_completion",
    "extract_stream_generate_metadata",
    "extract_stream_generate_text",
    "fetch_authenticated_init_body",
    "extract_text",
    "list_model_metadata",
    "message_text",
    "merge_cookie_headers",
    "merge_response_cookies",
    "parse_cookie_header",
    "parse_web_response_text",
    "sanitize_cookie_header",
    "rotate_psidts_cookie",
    "session_token_from_response",
    "stream_generate_url",
    "synthetic_stream_content",
}

_ACCOUNT_EXPORTS = {
    "gemini_cookie_state",
    "gemini_rotate_cookies_result",
}


def __getattr__(name: str) -> Any:
    if name == "models":
        return models
    if name in _CLIENT_EXPORTS:
        client = importlib.import_module(f"{__name__}.client")
        value = getattr(client, name)
        globals()[name] = value
        return value
    if name in _ACCOUNT_EXPORTS:
        accounts = importlib.import_module(f"{__name__}.accounts")
        value = getattr(accounts, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
