"""CatPaw model specs and model-id -> userModelTypeCode mapping.

The type codes below come from `GET /api/chat/get-user-available-models` and
are sent as both `userModelTypeCode` and `agentModeConfig.model.default`.
CatPaw may still route or downgrade server-side; agent auto routing is disabled
by sending `agentModeConfig.model.autoMode = false`.

Only actual CatPaw model IDs (glm-5.1, kimi-k2.6, etc.) are supported.
Claude model names are NOT mapped; the caller must use actual CatPaw model IDs.
"""
from __future__ import annotations

from typing import Any

from services.providers.base import CATPAW_PROVIDER, ModelSpec

# model id -> (userModelTypeCode, owned_by, vision-capable)
_CATPAW_MODELS: tuple[tuple[str, int, str, bool], ...] = (
    ("deepseek-v3.2", 9, "deepseek", False),
    ("longcat-flash", 22, "meituan", False),
    ("kimi-k2.5", 41, "moonshot", True),
    ("glm-5", 46, "zhipu", False),
    ("MiniMax-M2.5", 48, "minimax", False),
    ("MiniMax-M2.7", 56, "minimax", False),
    ("glm-5.1", 59, "zhipu", False),
    ("glm-5v-turbo", 60, "zhipu", True),
    ("kimi-k2.6", 62, "moonshot", True),
)

# Generic default alias; -> glm-5 (agentic flagship).
DEFAULT_TYPE_CODE = 46


CATPAW_MODEL_SPECS = (
    ModelSpec("catpaw", CATPAW_PROVIDER, "meituan-catpaw", "glm-5"),
    *(ModelSpec(mid, CATPAW_PROVIDER, owner, mid) for mid, _tc, owner, _v in _CATPAW_MODELS),
)

CATPAW_TYPE_CODES: dict[str, int] = {"catpaw": DEFAULT_TYPE_CODE}
CATPAW_TYPE_CODES.update({mid: tc for mid, tc, _o, _v in _CATPAW_MODELS})

# Vision (image-input capable) model ids — informational; image generation unsupported.
CATPAW_VISION_MODEL_IDS: set[str] = {mid for mid, _tc, _o, vision in _CATPAW_MODELS if vision}

# No image-generation models.
CATPAW_IMAGE_MODEL_IDS: set[str] = set()

_CATPAW_MODEL_ID_SET: set[str] = {mid for mid, _tc, _o, _v in _CATPAW_MODELS} | {"catpaw"}


def type_code_for(model: str) -> int:
    """Resolve a model name to a CatPaw type code.

    Only actual CatPaw model IDs are accepted. Falls back to DEFAULT_TYPE_CODE
    for unknown models.
    """
    name = str(model or "").strip()
    return CATPAW_TYPE_CODES.get(name, DEFAULT_TYPE_CODE)


def is_catpaw_model_id(model: str) -> bool:
    name = str(model or "").strip()
    return name in _CATPAW_MODEL_ID_SET or name.startswith("catpaw")


def catpaw_model_metadata() -> list[dict[str, Any]]:
    return [spec.model_metadata() for spec in CATPAW_MODEL_SPECS]
