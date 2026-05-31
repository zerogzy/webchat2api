from __future__ import annotations

from services.providers.base import GPT_PROVIDER, ModelSpec

GPT_FALLBACK_MODEL_IDS = (
    "auto",
    "gpt-5",
    "gpt-5-thinking",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-5-1",
    "gpt-5-2",
    "gpt-5-3",
    "gpt-5-3-mini",
    "gpt-5-mini",
)

GPT_IMAGE_MODEL_SPECS = (
    ModelSpec("gpt-image-2", GPT_PROVIDER, "chatgpt", "gpt-image-2", capability="image"),
    ModelSpec("codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", capability="image"),
    ModelSpec("plus-codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", model_tier="plus", capability="image"),
    ModelSpec("team-codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", model_tier="team", capability="image"),
    ModelSpec("pro-codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", model_tier="pro", capability="image"),
)
GPT_IMAGE_MODEL_IDS = {spec.id for spec in GPT_IMAGE_MODEL_SPECS}
GPT_CHAT_MODEL_SPECS = tuple(ModelSpec(model_id, GPT_PROVIDER, "chatgpt", model_id) for model_id in GPT_FALLBACK_MODEL_IDS)
GPT_MODEL_SPECS = (*GPT_CHAT_MODEL_SPECS, *GPT_IMAGE_MODEL_SPECS)


def gpt_fallback_model_metadata() -> list[dict[str, object]]:
    return [spec.model_metadata() for spec in GPT_CHAT_MODEL_SPECS]


def gpt_image_model_metadata() -> list[dict[str, object]]:
    return [spec.model_metadata() for spec in GPT_IMAGE_MODEL_SPECS]
