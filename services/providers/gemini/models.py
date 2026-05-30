from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from services.providers.base import GEMINI_PROVIDER, ModelSpec

GEMINI_MODEL_SPECS = (
    ModelSpec("gemini-2.5-pro", GEMINI_PROVIDER, "google", "gemini-2.5-pro"),
    ModelSpec("gemini-2.5-flash", GEMINI_PROVIDER, "google", "gemini-2.5-flash"),
    ModelSpec("gemini-pro", GEMINI_PROVIDER, "google", "gemini-2.5-pro"),
)

_GEMINI_MODEL_PATTERN = re.compile(r"gemini-[a-zA-Z0-9][a-zA-Z0-9.-]*")
_GEMINI_REAL_MODEL_PATTERN = re.compile(r"^gemini-(?:\d|advanced)")
_GEMINI_DYNAMIC_MODEL_TTL_SECONDS = 300.0
_GEMINI_DYNAMIC_MODEL_CACHE: tuple[float, tuple[str, ...]] | None = None


def is_discoverable_gemini_model_id(model_id: str) -> bool:
    value = str(model_id or "").strip()
    if not _GEMINI_REAL_MODEL_PATTERN.match(value):
        return False
    if value.startswith(("gemini-u-", "gemini-apps-")):
        return False
    return True


def extract_gemini_model_ids(text: str) -> list[str]:
    seen: set[str] = set()
    models: list[str] = []
    for match in _GEMINI_MODEL_PATTERN.finditer(str(text or "")):
        model_id = match.group(0).rstrip(".,;:)]}>'\"")
        if not is_discoverable_gemini_model_id(model_id) or model_id in seen:
            continue
        seen.add(model_id)
        models.append(model_id)
    return models


def _model_spec_for_id(model_id: str) -> ModelSpec:
    for spec in GEMINI_MODEL_SPECS:
        if spec.id == model_id:
            return spec
    return ModelSpec(model_id, GEMINI_PROVIDER, "google", model_id)


def _dynamic_gemini_model_ids(fetcher: Callable[[], str], now: float | None = None) -> tuple[str, ...]:
    global _GEMINI_DYNAMIC_MODEL_CACHE
    current_time = time.time() if now is None else now
    if _GEMINI_DYNAMIC_MODEL_CACHE is not None:
        cached_at, cached_ids = _GEMINI_DYNAMIC_MODEL_CACHE
        if current_time - cached_at < _GEMINI_DYNAMIC_MODEL_TTL_SECONDS:
            return cached_ids
    try:
        model_ids = tuple(extract_gemini_model_ids(fetcher()))
    except Exception:
        return ()
    if not model_ids:
        return ()
    _GEMINI_DYNAMIC_MODEL_CACHE = (current_time, model_ids)
    return model_ids


def clear_gemini_dynamic_model_cache() -> None:
    global _GEMINI_DYNAMIC_MODEL_CACHE
    _GEMINI_DYNAMIC_MODEL_CACHE = None


def gemini_model_specs(fetcher: Callable[[], str] | None = None, now: float | None = None) -> tuple[ModelSpec, ...]:
    specs_by_id = {spec.id: spec for spec in GEMINI_MODEL_SPECS}
    if fetcher is None:
        try:
            from services.providers.gemini.client import fetch_authenticated_init_body

            fetcher = fetch_authenticated_init_body
        except Exception:
            fetcher = None
    if fetcher is not None:
        for model_id in _dynamic_gemini_model_ids(fetcher, now):
            specs_by_id.setdefault(model_id, _model_spec_for_id(model_id))
    return tuple(specs_by_id.values())


def gemini_model_metadata(fetcher: Callable[[], str] | None = None, now: float | None = None) -> list[dict[str, object]]:
    return [spec.model_metadata() for spec in gemini_model_specs(fetcher, now)]
