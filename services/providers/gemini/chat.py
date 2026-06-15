from __future__ import annotations

from typing import Any, Iterator

from services.providers import gemini
from services.providers.base import ModelSpec


def chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> Any:
    return gemini.chat_completion(body, spec, messages)


def chat_completion_deltas(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> Iterator[str]:
    yield from gemini.chat_completion_deltas(body, spec, messages)
