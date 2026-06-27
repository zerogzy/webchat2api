from __future__ import annotations

from typing import Any

from services.providers.base import QODER_PROVIDER, ModelSpec

QODER_MODELS: tuple[tuple[str, str, str, str], ...] = (
    ("al-auto", "auto", "qoder", "Auto"),
    ("al-qwen3.7-max", "qmodel_latest", "qwen", "Qwen3.7-Max"),
    ("al-qwen3.7-plus", "qmodel", "qwen", "Qwen3.7-Plus"),
    ("al-glm-5.2", "gm51model", "zhipu", "GLM-5.2"),
    ("al-kimi-k2.6", "kmodel", "moonshot", "Kimi-K2.6"),
    ("al-minimax-m2.7", "mmodel", "minimax", "MiniMax-M2.7"),
)

UPSTREAM_MODEL_BY_ID = {model_id: upstream for model_id, upstream, _owner, _display in QODER_MODELS}
DISPLAY_NAME_BY_ID = {model_id: display for model_id, _upstream, _owner, display in QODER_MODELS}
QODER_MODEL_IDS = set(UPSTREAM_MODEL_BY_ID)
QODER_IMAGE_MODEL_IDS: set[str] = set()
QODER_MODEL_SPECS = tuple(
    ModelSpec(model_id, QODER_PROVIDER, owner, upstream)
    for model_id, upstream, owner, _display in QODER_MODELS
)


def is_qoder_model_id(model: str) -> bool:
    return str(model or "").strip() in QODER_MODEL_IDS


def qoder_model_metadata() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = []
    for spec in QODER_MODEL_SPECS:
        item = spec.model_metadata()
        item["root"] = DISPLAY_NAME_BY_ID.get(spec.id, spec.upstream_model or spec.id)
        data.append(item)
    return data
