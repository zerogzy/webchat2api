from __future__ import annotations

from typing import Any, Iterator

from services.providers.base import ConversationRequest
from services.providers.gpt.models import gpt_effective_thinking_effort, normalize_gpt_thinking_effort
from services.providers.gpt.runtime import collect_text, stream_text_deltas, text_backend


def normalize_thinking_effort(value: object) -> str:
    return normalize_gpt_thinking_effort(value)


def thinking_effort_from_body(body: dict[str, Any], model: str = "") -> str:
    if "thinking_effort" in body:
        return normalize_thinking_effort(body.get("thinking_effort"))
    if "reasoning_effort" in body:
        return normalize_thinking_effort(body.get("reasoning_effort"))
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        return normalize_thinking_effort(reasoning.get("effort"))
    return gpt_effective_thinking_effort(model)


def _conversation_request(body: dict[str, Any], messages: list[dict[str, Any]], model: str) -> ConversationRequest:
    return ConversationRequest(model=model, messages=messages, thinking_effort=thinking_effort_from_body(body, model))


def _resolved_backend(backend: Any = None) -> Any:
    return backend or text_backend()


def chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> str:
    return collect_text(_resolved_backend(backend), _conversation_request(body, messages, model))


def chat_completion_deltas(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> Iterator[str]:
    yield from stream_text_deltas(_resolved_backend(backend), _conversation_request(body, messages, model))
