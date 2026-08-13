from __future__ import annotations

from services.providers.base import GPT_PROVIDER, ModelSpec

GPT_IMAGE_MODEL_SPECS = (
    ModelSpec("gpt-image-2", GPT_PROVIDER, "chatgpt", "gpt-image-2", capability="image"),
    ModelSpec("codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", capability="image"),
    ModelSpec("plus-codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", model_tier="plus", capability="image"),
    ModelSpec("team-codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", model_tier="team", capability="image"),
    ModelSpec("pro-codex-gpt-image-2", GPT_PROVIDER, "chatgpt", "codex-gpt-image-2", model_tier="pro", capability="image"),
)
GPT_IMAGE_MODEL_IDS = {spec.id for spec in GPT_IMAGE_MODEL_SPECS}
GPT_MODEL_SPECS = GPT_IMAGE_MODEL_SPECS


def gpt_upstream_model_id(model: object) -> str:
    model_id = str(model or "auto").strip() or "auto"
    base, separator, suffix = model_id.rpartition("-")
    return base if separator and suffix in {"standard", "extended", "max"} else model_id


def normalize_gpt_thinking_effort(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "none"}:
        return ""
    if normalized in {"minimal", "low", "medium", "standard"}:
        return "standard"
    if normalized in {"high", "xhigh", "extended"}:
        return "extended"
    if normalized == "max":
        return "max"
    return ""


def gpt_effective_thinking_effort(model: object, value: object = "") -> str:
    explicit_effort = normalize_gpt_thinking_effort(value)
    if explicit_effort:
        return explicit_effort
    model_id = str(model or "").strip()
    suffix = model_id.rpartition("-")[2]
    if suffix in {"standard", "extended", "max"}:
        return suffix
    return ""


def gpt_image_model_metadata() -> list[dict[str, object]]:
    return [spec.model_metadata() for spec in GPT_IMAGE_MODEL_SPECS]
