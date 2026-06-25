from __future__ import annotations

import importlib
import os
from functools import lru_cache
from typing import Any

from services.providers.base import (
    CODEBUDDY_PROVIDER,
    CATPAW_PROVIDER,
    GEMINI_PROVIDER,
    GPT_PROVIDER,
    GROK_PROVIDER,
    JOYCODE_PROVIDER,
    SUPPORTED_PROVIDERS,
    ChatAdapter,
    ImageAdapter,
    ModelCapability,
    ModelSpec,
    ProviderDefinition,
    AccountAdapter,
)
from services.providers.catpaw.models import CATPAW_IMAGE_MODEL_IDS, CATPAW_MODEL_SPECS, is_catpaw_model_id
from services.providers.codebuddy.models import CODEBUDDY_IMAGE_MODEL_IDS, CODEBUDDY_MODEL_SPECS, is_codebuddy_model_id
from services.providers.gemini.models import GEMINI_IMAGE_MODEL_IDS, GEMINI_MODEL_SPECS
from services.providers.gpt.models import GPT_FALLBACK_MODEL_IDS, GPT_IMAGE_MODEL_IDS, GPT_MODEL_SPECS
from services.providers.grok.models import GROK_IMAGE_MODEL_IDS, GROK_MODEL_SPECS
from services.providers.joycode.models import JOYCODE_IMAGE_MODEL_IDS, JOYCODE_MODEL_SPECS, is_joycode_model_id

_PROVIDER_MODEL_SPECS = {
    GPT_PROVIDER: tuple(GPT_MODEL_SPECS),
    GROK_PROVIDER: tuple(GROK_MODEL_SPECS),
    GEMINI_PROVIDER: tuple(GEMINI_MODEL_SPECS),
    CATPAW_PROVIDER: tuple(CATPAW_MODEL_SPECS),
    JOYCODE_PROVIDER: tuple(JOYCODE_MODEL_SPECS),
    CODEBUDDY_PROVIDER: tuple(CODEBUDDY_MODEL_SPECS),
}
_PROVIDER_OWNERS = {
    GPT_PROVIDER: "chatgpt",
    GROK_PROVIDER: "xai",
    GEMINI_PROVIDER: "google",
    CATPAW_PROVIDER: "catpaw",
    JOYCODE_PROVIDER: "joycode",
    CODEBUDDY_PROVIDER: "codebuddy",
}
_PROVIDER_CAPABILITIES: dict[str, frozenset[ModelCapability]] = {
    GPT_PROVIDER: frozenset({"chat", "image", "image_edit"}),
    GROK_PROVIDER: frozenset({"chat", "image", "image_edit"}),
    GEMINI_PROVIDER: frozenset({"chat", "image"}),
    CATPAW_PROVIDER: frozenset({"chat"}),
    JOYCODE_PROVIDER: frozenset({"chat"}),
    CODEBUDDY_PROVIDER: frozenset({"chat"}),
}

MODEL_REGISTRY = {spec.id: spec for specs in _PROVIDER_MODEL_SPECS.values() for spec in specs}
IMAGE_MODEL_IDS = GPT_IMAGE_MODEL_IDS | GROK_IMAGE_MODEL_IDS | GEMINI_IMAGE_MODEL_IDS | CATPAW_IMAGE_MODEL_IDS | JOYCODE_IMAGE_MODEL_IDS | CODEBUDDY_IMAGE_MODEL_IDS


def normalize_provider(value: object, *, strict: bool = False) -> str:
    provider = str(value or "").strip().lower().replace("_", "-")
    if provider in {"", "openai", "chatgpt", "chat-gpt", "gpt"}:
        return GPT_PROVIDER
    if provider in {"grok", "xai", "x-ai"}:
        return GROK_PROVIDER
    if provider in {"gemini", "google", "bard"}:
        return GEMINI_PROVIDER
    if provider in {"catpaw", "cat-paw", "mcopilot", "meituan"}:
        return CATPAW_PROVIDER
    if provider in {"joycode", "joy-code", "jd", "jingdong"}:
        return JOYCODE_PROVIDER
    if provider in {"codebuddy", "code-buddy", "tx", "tencent"}:
        return CODEBUDDY_PROVIDER
    if strict:
        raise ValueError(f"unsupported provider: {value}")
    return GPT_PROVIDER


def normalize_account_provider(value: object) -> str:
    return normalize_provider(value, strict=True)


def supported_provider_ids() -> tuple[str, ...]:
    return tuple(provider for provider in (GPT_PROVIDER, GROK_PROVIDER, GEMINI_PROVIDER, CATPAW_PROVIDER, JOYCODE_PROVIDER, CODEBUDDY_PROVIDER) if provider in SUPPORTED_PROVIDERS)


def provider_capabilities(provider: object) -> frozenset[ModelCapability]:
    return _PROVIDER_CAPABILITIES[normalize_account_provider(provider)]


def _adapter_module(provider: str, name: str) -> Any:
    return importlib.import_module(f"services.providers.{provider}.{name}")


@lru_cache(maxsize=None)
def account_strategy(provider: object) -> AccountAdapter:
    return _adapter_module(normalize_account_provider(provider), "accounts")


@lru_cache(maxsize=None)
def chat_adapter(provider: object) -> ChatAdapter:
    return _adapter_module(normalize_account_provider(provider), "chat")


@lru_cache(maxsize=None)
def image_adapter(provider: object) -> ImageAdapter:
    return _adapter_module(normalize_account_provider(provider), "images")


def image_generation_outputs(
    spec: ModelSpec,
    request: Any,
    *,
    body: dict[str, Any] | None = None,
    prompt: str = "",
    n: int = 1,
) -> Any:
    adapter = image_adapter(spec.provider)
    if normalize_account_provider(spec.provider) == GROK_PROVIDER:
        return adapter.generation_outputs(body or {}, spec, prompt, n)
    return adapter.generation_outputs(request, spec)


def image_edit_outputs(
    spec: ModelSpec,
    request: Any,
    *,
    body: dict[str, Any] | None = None,
    prompt: str = "",
    images: list[Any] | None = None,
    n: int = 1,
    size: str | None = None,
) -> Any:
    adapter = image_adapter(spec.provider)
    if normalize_account_provider(spec.provider) == GROK_PROVIDER:
        return adapter.edit_outputs(body or {}, spec, prompt, images or [], n, size)
    return adapter.edit_outputs(request, spec)


def response_image_outputs(
    spec: ModelSpec,
    request: Any,
    *,
    body: dict[str, Any] | None = None,
    prompt: str = "",
    n: int = 1,
) -> Any:
    adapter = image_adapter(spec.provider)
    if normalize_account_provider(spec.provider) == GROK_PROVIDER:
        return adapter.generation_outputs(body or {}, spec, prompt, n)
    return adapter.response_image_outputs(request, spec)


@lru_cache(maxsize=None)
def provider_definition(provider: object) -> ProviderDefinition:
    provider_id = normalize_account_provider(provider)
    return ProviderDefinition(
        id=provider_id,
        owned_by=_PROVIDER_OWNERS[provider_id],
        capabilities=_PROVIDER_CAPABILITIES[provider_id],
        account_adapter=account_strategy(provider_id),
        chat_adapter=chat_adapter(provider_id),
        image_adapter=image_adapter(provider_id),
        model_specs=_PROVIDER_MODEL_SPECS[provider_id],
    )


def provider_definitions() -> dict[str, ProviderDefinition]:
    return {provider: provider_definition(provider) for provider in supported_provider_ids()}


def _catpaw_claude_route_enabled() -> bool:
    value = str(os.environ.get("CATPAW_CLAUDE_ROUTE", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def resolve_model(model_id: object) -> ModelSpec:
    model = str(model_id or "auto").strip() or "auto"
    if model.startswith("claude-") and _catpaw_claude_route_enabled():
        return ModelSpec(model, CATPAW_PROVIDER, "catpaw", model)
    spec = MODEL_REGISTRY.get(model)
    if spec is not None:
        return spec
    if model.startswith("grok-"):
        return ModelSpec(model, GROK_PROVIDER, "xai", model)
    if model.startswith("gemini-"):
        return ModelSpec(model, GEMINI_PROVIDER, "google", model)
    if is_catpaw_model_id(model):
        return ModelSpec(model, CATPAW_PROVIDER, "catpaw", model)
    if is_joycode_model_id(model):
        return ModelSpec(model, JOYCODE_PROVIDER, "joycode", model)
    if is_codebuddy_model_id(model):
        return ModelSpec(model, CODEBUDDY_PROVIDER, "codebuddy", model)
    return ModelSpec(model, GPT_PROVIDER, "chatgpt", model)


def is_image_model(model_id: object) -> bool:
    return str(model_id or "").strip() in IMAGE_MODEL_IDS
