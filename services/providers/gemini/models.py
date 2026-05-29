from __future__ import annotations

from services.providers.base import GEMINI_PROVIDER, ModelSpec

GEMINI_MODEL_SPECS = (
    ModelSpec("gemini-2.5-pro", GEMINI_PROVIDER, "google", "gemini-2.5-pro"),
    ModelSpec("gemini-2.5-flash", GEMINI_PROVIDER, "google", "gemini-2.5-flash"),
    ModelSpec("gemini-pro", GEMINI_PROVIDER, "google", "gemini-2.5-pro"),
)


def gemini_model_metadata() -> list[dict[str, object]]:
    return [spec.model_metadata() for spec in GEMINI_MODEL_SPECS]
