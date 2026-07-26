from __future__ import annotations

from services.providers.base import GPT_PROVIDER, ModelSpec

GPT_FALLBACK_MODEL_IDS = (
    "auto",
    "gpt-5-5",
    "gpt-5-5-instant",
    "gpt-5-5-thinking",
    "gpt-5-6-thinking",
    "gpt-5-3-instant",
    "o3",
)

GPT_IMAGE_MODEL_SPECS = (
    ModelSpec("gpt-image-2", GPT_PROVIDER, "chatgpt", "gpt-image-2", capability="image"),
    ModelSpec("codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", capability="image"),
    ModelSpec("plus-codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", model_tier="plus", capability="image"),
    ModelSpec("team-codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", model_tier="team", capability="image"),
    ModelSpec("pro-codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", model_tier="pro", capability="image"),
)
GPT_IMAGE_MODEL_IDS = {spec.id for spec in GPT_IMAGE_MODEL_SPECS}
GPT_CHAT_MODEL_SPECS = (
    ModelSpec("auto", GPT_PROVIDER, "chatgpt", "gpt-5-5"),
    *(ModelSpec(model_id, GPT_PROVIDER, "chatgpt", model_id) for model_id in GPT_FALLBACK_MODEL_IDS if model_id != "auto"),
)
GPT_MODEL_SPECS = (*GPT_CHAT_MODEL_SPECS, *GPT_IMAGE_MODEL_SPECS)
GPT_UPSTREAM_MODEL_BY_ID = {spec.id: spec.upstream_model or spec.id for spec in GPT_CHAT_MODEL_SPECS}


def gpt_upstream_model_id(model: object) -> str:
    model_id = str(model or "auto").strip() or "auto"
    return GPT_UPSTREAM_MODEL_BY_ID.get(model_id, model_id)


def normalize_gpt_thinking_effort(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "none"}:
        return ""
    if normalized in {"minimal", "low", "medium", "standard"}:
        return "standard"
    if normalized in {"high", "xhigh", "extended"}:
        return "extended"
    return ""


def gpt_fallback_model_metadata() -> list[dict[str, object]]:
    return [spec.model_metadata() for spec in GPT_CHAT_MODEL_SPECS]


def gpt_image_model_metadata() -> list[dict[str, object]]:
    return [spec.model_metadata() for spec in GPT_IMAGE_MODEL_SPECS]
