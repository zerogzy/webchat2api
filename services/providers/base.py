from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Literal, Protocol

GPT_PROVIDER = "gpt"
GROK_PROVIDER = "grok"
GEMINI_PROVIDER = "gemini"
SUPPORTED_PROVIDERS = {GPT_PROVIDER, GROK_PROVIDER, GEMINI_PROVIDER}
ModelCapability = Literal["chat", "image", "image_edit", "video"]


class AccountAdapter(Protocol):
    def normalize_access_token(self, item: dict[str, Any]) -> str: ...
    def supports_refresh(self, account: dict[str, Any]) -> bool: ...
    def refresh_error_message(self, exc: Exception) -> str: ...
    def build_export_item(self, account: dict[str, Any]) -> dict[str, str] | None: ...
    def sanitize_account(self, item: dict[str, Any]) -> dict[str, Any]: ...
    def normalize_account(self, account: dict[str, Any]) -> dict[str, Any]: ...
    def delete_token_matches_account(self, token: str, account: dict[str, Any]) -> bool: ...
    def is_image_account_available(self, account: dict[str, Any]) -> bool: ...
    def normalize_console_quota(self, value: Any) -> dict[str, Any]: ...
    def reset_console_quota_if_ready(self, account: dict[str, Any], current_time: float) -> dict[str, Any]: ...
    def is_console_account_available(self, account: dict[str, Any], current_time: float) -> bool: ...
    def requested_tiers(self, spec: ModelSpec) -> list[str]: ...
    def account_has_capability(self, account: dict[str, Any], spec: ModelSpec) -> bool: ...
    def tier_matches(self, account_tier: str, requested_tier: str) -> bool: ...
    def normalize_tier(self, value: Any) -> str: ...
    UNAVAILABLE_STATUSES: set[str]
    def is_auth_failure_payload(self, payload: Any) -> bool: ...


class ChatAdapter(Protocol):
    def chat_completion(self, *args: Any, **kwargs: Any) -> Any: ...
    def chat_completion_deltas(self, *args: Any, **kwargs: Any) -> Iterator[Any]: ...
    def chat_completion_events(self, *args: Any, **kwargs: Any) -> Iterator[Any]: ...
    def is_app_chat_model(self, spec: ModelSpec) -> bool: ...
    stream_text_deltas: Any
    collect_text: Any


class ImageAdapter(Protocol):
    def generation_outputs(self, *args: Any, **kwargs: Any) -> Iterator[Any]: ...
    def edit_outputs(self, *args: Any, **kwargs: Any) -> Iterator[Any]: ...
    def response_image_outputs(self, *args: Any, **kwargs: Any) -> Iterator[Any]: ...
    def unsupported_image_error(self) -> Any: ...


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    owned_by: str
    upstream_model: str | None = None
    default_reasoning_effort: str | None = None
    mode_id: str | None = None
    model_tier: str | None = None
    capability: ModelCapability = "chat"
    prefer_best: bool = False

    def model_metadata(self) -> dict[str, object]:
        metadata: dict[str, object] = {
            "id": self.id,
            "object": "model",
            "created": 0,
            "owned_by": self.owned_by,
            "provider": self.provider,
            "permission": [],
            "root": self.id,
            "parent": None,
        }
        if self.capability != "chat":
            metadata["capability"] = self.capability
        return metadata


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    owned_by: str
    capabilities: frozenset[ModelCapability]
    account_adapter: AccountAdapter
    chat_adapter: ChatAdapter
    image_adapter: ImageAdapter
    model_specs: tuple[ModelSpec, ...]
