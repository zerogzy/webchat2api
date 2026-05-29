from __future__ import annotations

import importlib
from typing import Any

from . import models

_CLIENT_EXPORTS = {
    "GeminiCompletion",
    "GeminiWebClient",
    "GeminiWebError",
    "account_cookie_header",
    "build_prompt",
    "build_web_payload",
    "chat_completion",
    "extract_completion",
    "extract_stream_generate_text",
    "extract_text",
    "list_model_metadata",
    "message_text",
    "parse_cookie_header",
    "parse_web_response_text",
    "synthetic_stream_content",
}


def __getattr__(name: str) -> Any:
    if name == "models":
        return models
    if name in _CLIENT_EXPORTS:
        client = importlib.import_module(f"{__name__}.client")
        value = getattr(client, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
