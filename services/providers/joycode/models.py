from __future__ import annotations

from typing import Any

from services.providers.base import ModelSpec

JOYCODE_PROVIDER = "joycode"

JOYCODE_MODELS: tuple[tuple[str, str, bool, bool, int, int], ...] = (
    ("JoyAI-Code", "joycode", False, False, 200000, 64000),
    ("MiniMax-M2.7", "minimax", False, True, 200000, 16384),
    ("Kimi-K2.6", "moonshot", True, True, 200000, 16384),
    ("Kimi-K2.5", "moonshot", True, False, 200000, 16384),
    ("GLM-5.1", "zhipu", False, True, 200000, 16384),
    ("GLM-5", "zhipu", False, False, 200000, 8192),
    ("GLM-4.7", "zhipu", False, False, 200000, 8192),
    ("Doubao-Seed-2.0-pro", "bytedance", False, False, 200000, 16384),
)

DEFAULT_MODEL = "JoyAI-Code"
REASONING_MODEL_IDS = {model_id for model_id, _owner, _vision, reasoning, _ctx, _max in JOYCODE_MODELS if reasoning}
JOYCODE_MODEL_IDS = {model_id for model_id, _owner, _vision, _reasoning, _ctx, _max in JOYCODE_MODELS}
JOYCODE_IMAGE_MODEL_IDS: set[str] = set()

JOYCODE_MODEL_SPECS = tuple(
    ModelSpec(model_id, JOYCODE_PROVIDER, owner, model_id)
    for model_id, owner, _vision, _reasoning, _ctx, _max in JOYCODE_MODELS
)


def is_joycode_model_id(model: str) -> bool:
    return str(model or "").strip() in JOYCODE_MODEL_IDS


def joycode_model_metadata() -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    for model_id, owner, vision, reasoning, ctx, max_tokens in JOYCODE_MODELS:
        item = ModelSpec(model_id, JOYCODE_PROVIDER, owner, model_id).model_metadata()
        item["capabilities"] = {
            "vision": vision,
            "reasoning": reasoning,
            "ctx": ctx,
            "max_tokens": max_tokens,
        }
        metadata.append(item)
    return metadata
