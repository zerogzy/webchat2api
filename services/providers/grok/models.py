from __future__ import annotations

from services.providers.base import GROK_PROVIDER, ModelSpec

GROK_MODEL_SPECS = (
    ModelSpec("grok-4.3", GROK_PROVIDER, "xai", "grok-4.3", "high"),
    ModelSpec("grok-4", GROK_PROVIDER, "xai", "grok-4", "high"),
    ModelSpec("grok-4.20", GROK_PROVIDER, "xai", "grok-4.20-reasoning"),
    ModelSpec("grok-4.20-reasoning", GROK_PROVIDER, "xai", "grok-4.20-reasoning"),
    ModelSpec("grok-4.20-non-reasoning", GROK_PROVIDER, "xai", "grok-4.20-non-reasoning"),
    ModelSpec("grok-4.20-multi-agent", GROK_PROVIDER, "xai", "grok-4.20-multi-agent"),
    ModelSpec("grok-4.20-0309-non-reasoning", GROK_PROVIDER, "xai", "grok-4.20-0309", mode_id="fast", model_tier="basic"),
    ModelSpec("grok-4.20-0309", GROK_PROVIDER, "xai", "grok-4.20-0309", mode_id="auto", model_tier="super"),
    ModelSpec("grok-4.20-0309-reasoning", GROK_PROVIDER, "xai", "grok-4.20-0309", mode_id="expert", model_tier="super"),
    ModelSpec("grok-4.20-0309-non-reasoning-super", GROK_PROVIDER, "xai", "grok-4.20-0309", mode_id="fast", model_tier="super"),
    ModelSpec("grok-4.20-0309-super", GROK_PROVIDER, "xai", "grok-4.20-0309", mode_id="auto", model_tier="super"),
    ModelSpec("grok-4.20-0309-reasoning-super", GROK_PROVIDER, "xai", "grok-4.20-0309", mode_id="expert", model_tier="super"),
    ModelSpec("grok-4.20-0309-non-reasoning-heavy", GROK_PROVIDER, "xai", "grok-4.20-0309", mode_id="fast", model_tier="heavy"),
    ModelSpec("grok-4.20-0309-heavy", GROK_PROVIDER, "xai", "grok-4.20-0309", mode_id="auto", model_tier="heavy"),
    ModelSpec("grok-4.20-0309-reasoning-heavy", GROK_PROVIDER, "xai", "grok-4.20-0309", mode_id="expert", model_tier="heavy"),
    ModelSpec("grok-4.20-multi-agent-0309", GROK_PROVIDER, "xai", "grok-4.20-0309", mode_id="heavy", model_tier="heavy"),
    ModelSpec("grok-4.20-fast", GROK_PROVIDER, "xai", "grok-4.20", mode_id="fast", model_tier="basic", prefer_best=True),
    ModelSpec("grok-4.20-auto", GROK_PROVIDER, "xai", "grok-4.20", mode_id="auto", model_tier="super", prefer_best=True),
    ModelSpec("grok-4.20-expert", GROK_PROVIDER, "xai", "grok-4.20", mode_id="expert", model_tier="super", prefer_best=True),
    ModelSpec("grok-4.20-heavy", GROK_PROVIDER, "xai", "grok-4.20", mode_id="heavy", model_tier="heavy", prefer_best=True),
    ModelSpec("grok-4.3-beta", GROK_PROVIDER, "xai", "grok-4.3-beta", mode_id="grok-420-computer-use-sa", model_tier="super"),
    ModelSpec("grok-imagine-image-lite", GROK_PROVIDER, "xai", "grok-imagine-image-lite", mode_id="fast", model_tier="basic", capability="image"),
    ModelSpec("grok-imagine-image", GROK_PROVIDER, "xai", "grok-imagine-image", mode_id="auto", model_tier="super", capability="image"),
    ModelSpec("grok-imagine-image-pro", GROK_PROVIDER, "xai", "grok-imagine-image-pro", mode_id="auto", model_tier="super", capability="image"),
    ModelSpec("grok-imagine-image-edit", GROK_PROVIDER, "xai", "grok-imagine-image-edit", mode_id="auto", model_tier="super", capability="image_edit"),
    ModelSpec("grok-imagine-video", GROK_PROVIDER, "xai", "grok-imagine-video", mode_id="auto", model_tier="super", capability="video"),
)

GROK_IMAGE_MODEL_IDS = {spec.id for spec in GROK_MODEL_SPECS if spec.capability in {"image", "image_edit"}}
SUPPORTED_GROK_APP_CHAT_IMAGE_MODEL_IDS = {spec.id for spec in GROK_MODEL_SPECS if spec.capability == "image"}


def grok_model_metadata() -> list[dict[str, object]]:
    return [spec.model_metadata() for spec in GROK_MODEL_SPECS]


def is_grok_app_chat_model(spec: ModelSpec) -> bool:
    return spec.provider == GROK_PROVIDER and bool(spec.mode_id)


def is_supported_grok_app_chat_image_model(model_id: object) -> bool:
    return str(model_id or "").strip() in SUPPORTED_GROK_APP_CHAT_IMAGE_MODEL_IDS
