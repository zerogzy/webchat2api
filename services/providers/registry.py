from __future__ import annotations

from services.providers.base import GEMINI_PROVIDER, GPT_PROVIDER, GROK_PROVIDER, ModelSpec
from services.providers.gemini.models import GEMINI_MODEL_SPECS
from services.providers.gpt.models import GPT_FALLBACK_MODEL_IDS, GPT_IMAGE_MODEL_IDS, GPT_MODEL_SPECS
from services.providers.grok.models import GROK_IMAGE_MODEL_IDS, GROK_MODEL_SPECS

MODEL_REGISTRY = {spec.id: spec for spec in (*GROK_MODEL_SPECS, *GEMINI_MODEL_SPECS, *GPT_MODEL_SPECS)}
IMAGE_MODEL_IDS = GPT_IMAGE_MODEL_IDS | GROK_IMAGE_MODEL_IDS


def normalize_provider(value: object, *, strict: bool = False) -> str:
    provider = str(value or "").strip().lower().replace("_", "-")
    if provider in {"", "openai", "chatgpt", "chat-gpt", "gpt"}:
        return GPT_PROVIDER
    if provider in {"grok", "xai", "x-ai"}:
        return GROK_PROVIDER
    if provider in {"gemini", "google", "bard"}:
        return GEMINI_PROVIDER
    if strict:
        raise ValueError(f"unsupported provider: {value}")
    return GPT_PROVIDER


def normalize_account_provider(value: object) -> str:
    return normalize_provider(value, strict=True)


def resolve_model(model_id: object) -> ModelSpec:
    model = str(model_id or "auto").strip() or "auto"
    spec = MODEL_REGISTRY.get(model)
    if spec is not None:
        return spec
    if model.startswith("grok-"):
        return ModelSpec(model, GROK_PROVIDER, "xai", model)
    if model.startswith("gemini-"):
        return ModelSpec(model, GEMINI_PROVIDER, "google", model)
    return ModelSpec(model, GPT_PROVIDER, "chatgpt", model)


def is_image_model(model_id: object) -> bool:
    return str(model_id or "").strip() in IMAGE_MODEL_IDS
