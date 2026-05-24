from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


GPT_PROVIDER = "gpt"
GROK_PROVIDER = "grok"
SUPPORTED_PROVIDERS = {GPT_PROVIDER, GROK_PROVIDER}
ModelCapability = Literal["chat", "image", "image_edit", "video"]


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


GPT_FALLBACK_MODEL_IDS = (
    "auto",
    "gpt-5",
    "gpt-5-thinking",
    "gpt-4o",
    "gpt-4o-mini",
)

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

GPT_IMAGE_MODEL_IDS = {"gpt-image-2", "codex-gpt-image-2"}
GROK_IMAGE_MODEL_IDS = {spec.id for spec in GROK_MODEL_SPECS if spec.capability in {"image", "image_edit", "video"}}
IMAGE_MODEL_IDS = GPT_IMAGE_MODEL_IDS | GROK_IMAGE_MODEL_IDS
SUPPORTED_GROK_APP_CHAT_IMAGE_MODEL_IDS = {spec.id for spec in GROK_MODEL_SPECS if spec.capability == "image"}

MODEL_REGISTRY = {spec.id: spec for spec in GROK_MODEL_SPECS}
for model_id in GPT_FALLBACK_MODEL_IDS:
    MODEL_REGISTRY[model_id] = ModelSpec(model_id, GPT_PROVIDER, "chatgpt", model_id)


def normalize_provider(value: object) -> str:
    provider = str(value or "").strip().lower().replace("_", "-")
    if provider in {"", "openai", "chatgpt", "chat-gpt", "gpt"}:
        return GPT_PROVIDER
    if provider in {"grok", "xai", "x-ai"}:
        return GROK_PROVIDER
    return GPT_PROVIDER


def resolve_model(model_id: object) -> ModelSpec:
    model = str(model_id or "auto").strip() or "auto"
    spec = MODEL_REGISTRY.get(model)
    if spec is not None:
        return spec
    if model.startswith("grok-"):
        return ModelSpec(model, GROK_PROVIDER, "xai", model)
    return ModelSpec(model, GPT_PROVIDER, "chatgpt", model)


def grok_model_metadata() -> list[dict[str, object]]:
    return [spec.model_metadata() for spec in GROK_MODEL_SPECS]


def gpt_fallback_model_metadata() -> list[dict[str, object]]:
    return [MODEL_REGISTRY[model_id].model_metadata() for model_id in GPT_FALLBACK_MODEL_IDS]


def is_grok_app_chat_model(spec: ModelSpec) -> bool:
    return spec.provider == GROK_PROVIDER and bool(spec.mode_id)


def is_image_model(model_id: object) -> bool:
    return str(model_id or "").strip() in IMAGE_MODEL_IDS


def is_supported_grok_app_chat_image_model(model_id: object) -> bool:
    return str(model_id or "").strip() in SUPPORTED_GROK_APP_CHAT_IMAGE_MODEL_IDS
