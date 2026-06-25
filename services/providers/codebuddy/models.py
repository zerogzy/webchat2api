from __future__ import annotations

from typing import Any

from services.providers.base import CODEBUDDY_PROVIDER, ModelSpec

CODEBUDDY_MODELS: tuple[tuple[str, str, str], ...] = (
    ("tx-auto", "auto", "codebuddy"),
    ("tx-deepseek-v3", "deepseek-v3", "deepseek"),
    ("tx-deepseek-v3-0324", "deepseek-v3-0324", "deepseek"),
    ("tx-deepseek-r1", "deepseek-r1", "deepseek"),
    ("tx-glm-5.1", "glm-5.1", "zhipu"),
    ("tx-glm-5.2", "glm-5.2", "zhipu"),
    ("tx-glm-4.6", "glm-4.6", "zhipu"),
    ("tx-minimax-m3", "minimax-m3", "minimax"),
    ("tx-kimi-k2.6", "kimi-k2.6", "moonshot"),
)

UPSTREAM_MODEL_BY_ID = {model_id: upstream for model_id, upstream, _owner in CODEBUDDY_MODELS}
CODEBUDDY_MODEL_IDS = set(UPSTREAM_MODEL_BY_ID)
CODEBUDDY_IMAGE_MODEL_IDS: set[str] = set()
CODEBUDDY_MODEL_SPECS = tuple(
    ModelSpec(model_id, CODEBUDDY_PROVIDER, owner, upstream)
    for model_id, upstream, owner in CODEBUDDY_MODELS
)


def is_codebuddy_model_id(model: str) -> bool:
    return str(model or "").strip() in CODEBUDDY_MODEL_IDS


def codebuddy_model_metadata() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = []
    for spec in CODEBUDDY_MODEL_SPECS:
        item = spec.model_metadata()
        item["root"] = spec.upstream_model or spec.id
        data.append(item)
    return data
