from __future__ import annotations

from typing import Any

from services.providers.base import CODEBUDDY_PROVIDER, ModelSpec

CODEBUDDY_MODELS: tuple[tuple[str, str, str], ...] = (
    ("tx-claude-4.0", "claude-4.0", "anthropic"),
    ("tx-claude-3.7", "claude-3.7", "anthropic"),
    ("tx-gpt-5", "gpt-5", "openai"),
    ("tx-gpt-5-mini", "gpt-5-mini", "openai"),
    ("tx-gpt-5-nano", "gpt-5-nano", "openai"),
    ("tx-o4-mini", "o4-mini", "openai"),
    ("tx-gemini-2.5-flash", "gemini-2.5-flash", "google"),
    ("tx-gemini-2.5-pro", "gemini-2.5-pro", "google"),
    ("tx-auto-chat", "auto-chat", "codebuddy"),
)

UPSTREAM_MODEL_BY_ID = {model_id: upstream for model_id, upstream, _owner in CODEBUDDY_MODELS}
CODEBUDDY_MODEL_IDS = set(UPSTREAM_MODEL_BY_ID)
CODEBUDDY_IMAGE_MODEL_IDS: set[str] = set()
CODEBUDDY_MODEL_SPECS = tuple(
    ModelSpec(model_id, CODEBUDDY_PROVIDER, owner, upstream)
    for model_id, upstream, owner in CODEBUDDY_MODELS
)


def is_codebuddy_model_id(model: str) -> bool:
    return str(model or "").strip() in CODEBUDDY_MODEL_IDS


def codebuddy_model_metadata() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = []
    for spec in CODEBUDDY_MODEL_SPECS:
        item = spec.model_metadata()
        item["root"] = spec.upstream_model or spec.id
        data.append(item)
    return data
