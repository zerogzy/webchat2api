from __future__ import annotations

from typing import Any

from services.providers.base import ModelSpec

JOYCODE_PROVIDER = "joycode"

JOYCODE_MODELS: tuple[tuple[str, str, str, bool, bool, int, int], ...] = (
    ("jd-joyai-code", "JoyAI-Code", "joycode", False, False, 200000, 64000),
    ("jd-minimax-m2.7", "MiniMax-M2.7", "minimax", False, True, 200000, 16384),
    ("jd-kimi-k2.6", "Kimi-K2.6", "moonshot", True, True, 200000, 16384),
    ("jd-kimi-k2.5", "Kimi-K2.5", "moonshot", True, False, 200000, 16384),
    ("jd-glm-5.1", "GLM-5.1", "zhipu", False, True, 200000, 16384),
    ("jd-glm-5", "GLM-5", "zhipu", False, False, 200000, 8192),
    ("jd-glm-4.7", "GLM-4.7", "zhipu", False, False, 200000, 8192),
    ("jd-doubao-seed-2.0-pro", "Doubao-Seed-2.0-pro", "bytedance", False, False, 200000, 16384),
)

DEFAULT_MODEL = "jd-joyai-code"
UPSTREAM_MODEL_BY_ID = {model_id: upstream for model_id, upstream, _owner, _vision, _reasoning, _ctx, _max in JOYCODE_MODELS}
REASONING_MODEL_IDS = {model_id for model_id, _upstream, _owner, _vision, reasoning, _ctx, _max in JOYCODE_MODELS if reasoning}
JOYCODE_MODEL_IDS = set(UPSTREAM_MODEL_BY_ID)
JOYCODE_IMAGE_MODEL_IDS: set[str] = set()

JOYCODE_MODEL_SPECS = tuple(
    ModelSpec(model_id, JOYCODE_PROVIDER, owner, upstream)
    for model_id, upstream, owner, _vision, _reasoning, _ctx, _max in JOYCODE_MODELS
)


def is_joycode_model_id(model: str) -> bool:
    return str(model or "").strip() in JOYCODE_MODEL_IDS


def joycode_model_metadata() -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    for model_id, upstream, owner, vision, reasoning, ctx, max_tokens in JOYCODE_MODELS:
        item = ModelSpec(model_id, JOYCODE_PROVIDER, owner, upstream).model_metadata()
        item["root"] = upstream
        item["capabilities"] = {
            "vision": vision,
            "reasoning": reasoning,
            "ctx": ctx,
            "max_tokens": max_tokens,
        }
        metadata.append(item)
    return metadata
