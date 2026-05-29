from __future__ import annotations

from typing import Any, Iterator

from services.protocol.conversation import ConversationRequest, collect_text, stream_text_deltas, text_backend


def _conversation_request(messages: list[dict[str, Any]], model: str) -> ConversationRequest:
    return ConversationRequest(model=model, messages=messages)


def _resolved_backend(backend: Any = None) -> Any:
    return backend or text_backend()


def chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> str:
    return collect_text(_resolved_backend(backend), _conversation_request(messages, model))


def chat_completion_deltas(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> Iterator[str]:
    yield from stream_text_deltas(_resolved_backend(backend), _conversation_request(messages, model))
