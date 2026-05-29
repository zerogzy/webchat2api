from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

from . import models

_CLIENT_EXPORTS = {
    "APP_CHAT_BASE_URL",
    "APP_CHAT_MEDIA_POST_CREATE_URL",
    "APP_CHAT_NEW_CONVERSATION_URL",
    "APP_CHAT_RATE_LIMITS_URL",
    "APP_CHAT_UPLOAD_FILE_URL",
    "CONSOLE_BASE_URL",
    "CONSOLE_RESPONSES_URL",
    "FlareSolverrClearanceProvider",
    "GROK_APP_CHAT_STATSIG_ID",
    "GROK_ASSET_BASE_URL",
    "GROK_IMAGE_EDIT_MAX_N",
    "GROK_IMAGE_EDIT_MAX_REFERENCES",
    "GROK_IMAGE_EDIT_MEDIA_TYPE",
    "GROK_IMAGE_EDIT_MODEL_KIND",
    "GROK_IMAGE_EDIT_MODEL_NAME",
    "GROK_IMAGE_EDIT_SIZE",
    "GrokAppChatClient",
    "GrokConsoleClient",
    "GrokConsoleCompletion",
    "GrokConsoleError",
    "GrokConsoleStreamDelta",
    "GrokImageEditReference",
    "HTTPException",
    "_BRIDGE_EXPLICIT_CHAT_TIMEOUT",
    "_BRIDGE_HEALTH_TIMEOUT",
    "_app_chat_cookie",
    "_detect_bridge_url",
    "_extract_raw_sso",
    "_grok_app_chat_profile",
    "_headers",
    "app_chat_completion",
    "app_chat_completion_events",
    "app_chat_headers",
    "app_chat_image_edit_outputs",
    "app_chat_image_outputs",
    "app_chat_line_events",
    "append_search_sources_suffix",
    "build_app_chat_payload",
    "build_console_input",
    "build_console_payload",
    "build_grok_image_edit_payload",
    "build_grok_media_post_payload",
    "chat_completion",
    "classify_app_chat_upstream_error",
    "collect_app_chat_response",
    "com",
    "config",
    "console_chat_completion",
    "console_chat_completion_events",
    "create_session",
    "dedupe_search_sources",
    "extract_app_chat_image_url",
    "extract_app_chat_search_sources",
    "extract_app_chat_token",
    "extract_console_completion",
    "extract_console_stream_delta",
    "extract_console_text",
    "extract_grok_image_edit_final_urls",
    "format_search_sources_suffix",
    "is_app_chat_final_event",
    "latest_user_message",
    "parse_app_chat_payload_line",
    "replace_grok_image_placeholders",
    "requests",
    "resolve_grok_asset_reference",
    "split_visible_console_reasoning",
    "strip_search_sources_from_messages",
    "strip_search_sources_suffix",
    "uuid",
    "validate_grok_access_token",
    "validate_grok_image_edit_request",
}


def _client_module() -> ModuleType:
    return importlib.import_module(f"{__name__}.client")


class _GrokProviderModule(ModuleType):
    def __getattribute__(self, name: str) -> Any:
        if name in {"models", "__class__", "__dict__", "__doc__", "__file__", "__loader__", "__name__", "__package__", "__path__", "__spec__"}:
            return ModuleType.__getattribute__(self, name)
        if name in _CLIENT_EXPORTS:
            return getattr(_client_module(), name)
        return ModuleType.__getattribute__(self, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _CLIENT_EXPORTS:
            setattr(_client_module(), name, value)
            return
        ModuleType.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if name in _CLIENT_EXPORTS:
            client = _client_module()
            if hasattr(client, name):
                delattr(client, name)
            return
        ModuleType.__delattr__(self, name)

    def __dir__(self) -> list[str]:
        return sorted(set(ModuleType.__dir__(self)) | _CLIENT_EXPORTS | {"models"})


sys.modules[__name__].__class__ = _GrokProviderModule
