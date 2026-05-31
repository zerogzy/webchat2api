from __future__ import annotations

from typing import Any, Iterator

from services.models import is_grok_app_chat_model
from services.providers import grok
from services.providers.base import ModelSpec


def chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> dict[str, Any]:
    if is_grok_app_chat_model(spec):
        return grok.app_chat_completion(body, spec, messages)
    completion = grok.console_chat_completion(body, spec, messages)
    return {
        "content": completion.content,
        "reasoning_content": completion.reasoning_content,
        "raw_response": completion.raw_response,
    }


def chat_completion_events(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    if is_grok_app_chat_model(spec):
        yield from grok.app_chat_completion_events(body, spec, messages)
        return
    yield from grok.console_chat_completion_events(body, spec, messages)


def is_app_chat_model(spec: ModelSpec) -> bool:
    return is_grok_app_chat_model(spec)


def app_chat_image_outputs(body: dict[str, Any], spec: ModelSpec, prompt: str, n: int):
    return grok.app_chat_image_outputs(body, spec, prompt, n)
