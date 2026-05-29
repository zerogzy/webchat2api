from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GPT_PROVIDER = "gpt"
GROK_PROVIDER = "grok"
GEMINI_PROVIDER = "gemini"
SUPPORTED_PROVIDERS = {GPT_PROVIDER, GROK_PROVIDER, GEMINI_PROVIDER}
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
