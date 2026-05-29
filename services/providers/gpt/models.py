from __future__ import annotations

from services.providers.base import GPT_PROVIDER, ModelSpec

GPT_FALLBACK_MODEL_IDS = (
    "auto",
    "gpt-5",
    "gpt-5-thinking",
    "gpt-4o",
    "gpt-4o-mini",
)

GPT_IMAGE_MODEL_IDS = {"gpt-image-2", "codex-gpt-image-2"}
GPT_CHAT_MODEL_SPECS = tuple(ModelSpec(model_id, GPT_PROVIDER, "chatgpt", model_id) for model_id in GPT_FALLBACK_MODEL_IDS)
GPT_IMAGE_MODEL_SPECS = tuple(ModelSpec(model_id, GPT_PROVIDER, "chatgpt", model_id, capability="image") for model_id in GPT_IMAGE_MODEL_IDS)
GPT_MODEL_SPECS = (*GPT_CHAT_MODEL_SPECS, *GPT_IMAGE_MODEL_SPECS)


def gpt_fallback_model_metadata() -> list[dict[str, object]]:
    return [spec.model_metadata() for spec in GPT_CHAT_MODEL_SPECS]


def gpt_image_model_metadata() -> list[dict[str, object]]:
    return [spec.model_metadata() for spec in GPT_IMAGE_MODEL_SPECS]
