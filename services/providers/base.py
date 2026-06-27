from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Iterator, Literal, Protocol

GPT_PROVIDER = "gpt"
GROK_PROVIDER = "grok"
GEMINI_PROVIDER = "gemini"
CATPAW_PROVIDER = "catpaw"
JOYCODE_PROVIDER = "joycode"
CODEBUDDY_PROVIDER = "codebuddy"
QODER_PROVIDER = "qoder"
SUPPORTED_PROVIDERS = {GPT_PROVIDER, GROK_PROVIDER, GEMINI_PROVIDER, CATPAW_PROVIDER, JOYCODE_PROVIDER, CODEBUDDY_PROVIDER, QODER_PROVIDER}
ModelCapability = Literal["chat", "image", "image_edit", "video"]


class AccountAdapter(Protocol):
    def normalize_access_token(self, item: dict[str, Any]) -> str: ...
    def supports_refresh(self, account: dict[str, Any]) -> bool: ...
    def refresh_error_message(self, exc: Exception) -> str: ...
    def export_filename(self) -> str: ...
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


class RemoteAccountValidator(AccountAdapter, Protocol):
    def validate_remote_info(self, access_token: str, account: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def remote_error_status(self, exc: Exception) -> int | None: ...


class ChatAdapter(Protocol):
    def chat_completion(self, *args: Any, **kwargs: Any) -> Any: ...
    def chat_completion_deltas(self, *args: Any, **kwargs: Any) -> Iterator[Any]: ...
    def chat_completion_events(self, *args: Any, **kwargs: Any) -> Iterator[Any]: ...
    def is_app_chat_model(self, spec: ModelSpec) -> bool: ...
    def extract_app_chat_search_sources(self, event: dict[str, Any]) -> list[dict[str, str]]: ...
    def extract_app_chat_token(self, event: dict[str, Any]) -> tuple[str, bool]: ...
    def is_app_chat_final_event(self, event: dict[str, Any]) -> bool: ...
    def dedupe_search_sources(self, sources: Any) -> list[dict[str, str]]: ...
    def extract_console_stream_delta(self, event: dict[str, Any]) -> Any: ...
    def strip_search_sources_from_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def append_search_sources_suffix(self, content: str, sources: Any) -> str: ...
    stream_text_deltas: Any
    collect_text: Any


@dataclass(frozen=True)
class AccountStateDecisionInput:
    account: dict[str, Any]
    adapter: AccountAdapter
    spec: ModelSpec | None = None
    payload: Any = None
    status_code: int | None = None
    current_time: float | None = None


@dataclass(frozen=True)
class AccountStateDecision:
    unavailable: bool = False
    rate_limited: bool = False
    auth_failed: bool = False
    capability_mismatch: bool = False
    tier_mismatch: bool = False
    matched_tiers: frozenset[str] = frozenset()
    reset_account: dict[str, Any] | None = None
    writeback: dict[str, Any] = field(default_factory=dict)

    @property
    def skip_for_selection(self) -> bool:
        return self.unavailable or self.rate_limited or self.auth_failed or self.capability_mismatch

    def matches_tier(self, requested_tier: str) -> bool:
        if self.tier_mismatch:
            return False
        return requested_tier in self.matched_tiers if self.matched_tiers else bool(requested_tier)


def _coerce_status_code(value: int | str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def decide_account_state(request: AccountStateDecisionInput) -> AccountStateDecision:
    account = request.account if isinstance(request.account, dict) else {}
    adapter = request.adapter
    status_code = _coerce_status_code(request.status_code)
    auth_failed = status_code in {401, 403} or adapter.is_auth_failure_payload(request.payload)
    rate_limited = status_code in {402, 429}
    writeback: dict[str, Any] = {}
    reset_account: dict[str, Any] | None = None

    if auth_failed:
        writeback.update({"status": "异常", "quota": 0})
    elif rate_limited:
        writeback["status"] = "限流"

    current_time = request.current_time
    if current_time is not None:
        reset_account = adapter.reset_console_quota_if_ready(account, current_time)
        if reset_account != account:
            status = reset_account.get("status")
            quota = reset_account.get("quota_console")
            if status is not None:
                writeback["status"] = status
            if quota is not None:
                writeback["quota_console"] = quota

    spec = request.spec
    capability_mismatch = False
    tier_mismatch = False
    matched_tiers: set[str] = set()
    if spec is not None:
        capability_mismatch = not adapter.account_has_capability(account, spec)
        requested = adapter.requested_tiers(spec)
        if requested:
            account_tier = adapter.normalize_tier(account.get("tier") or account.get("model_tier"))
            matched_tiers = {tier for tier in requested if adapter.tier_matches(account_tier, tier)}
            tier_mismatch = not matched_tiers

    unavailable_statuses = (account.get("status"), account.get("account_status"))

    return AccountStateDecision(
        unavailable=any(status in adapter.UNAVAILABLE_STATUSES for status in unavailable_statuses),
        rate_limited=rate_limited,
        auth_failed=auth_failed,
        capability_mismatch=capability_mismatch,
        tier_mismatch=tier_mismatch,
        matched_tiers=frozenset(matched_tiers),
        reset_account=reset_account,
        writeback=writeback,
    )


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


class ImageGenerationError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 502,
        error_type: str = "server_error",
        code: str | None = "upstream_error",
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.param = param

    def to_openai_error(self) -> dict[str, Any]:
        return {
            "error": {
                "message": str(self),
                "type": self.error_type,
                "param": self.param,
                "code": self.code,
            }
        }


@dataclass
class ConversationRequest:
    model: str = "auto"
    prompt: str = ""
    messages: list[dict[str, Any]] | None = None
    images: list[str] | None = None
    n: int = 1
    size: str | None = None
    response_format: str = "b64_json"
    base_url: str | None = None
    message_as_error: bool = False


@dataclass
class ImageOutput:
    kind: str
    model: str
    index: int
    total: int
    created: int = field(default_factory=lambda: int(time.time()))
    text: str = ""
    upstream_event_type: str = ""
    data: list[dict[str, Any]] = field(default_factory=list)

    def to_chunk(self) -> dict[str, Any]:
        chunk: dict[str, Any] = {
            "object": "image.generation.chunk",
            "created": self.created,
            "model": self.model,
            "index": self.index,
            "total": self.total,
            "progress_text": self.text,
            "upstream_event_type": self.upstream_event_type,
            "data": [],
        }
        if self.kind == "message":
            chunk.update({
                "object": "image.generation.message",
                "message": self.text,
            })
            chunk.pop("progress_text", None)
            chunk.pop("upstream_event_type", None)
        elif self.kind == "result":
            chunk.update({
                "object": "image.generation.result",
                "data": self.data,
            })
            chunk.pop("progress_text", None)
            chunk.pop("upstream_event_type", None)
        return chunk


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    owned_by: str
    capabilities: frozenset[ModelCapability]
    account_adapter: AccountAdapter
    chat_adapter: ChatAdapter
    image_adapter: ImageAdapter
    model_specs: tuple[ModelSpec, ...]
