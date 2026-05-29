from __future__ import annotations

from typing import Any, Iterator

from services.protocol.conversation import ConversationRequest, collect_text, stream_text_deltas, text_backend


def chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> str:
    request = ConversationRequest(model=model, messages=messages)
    return collect_text(backend or text_backend(), request)


def chat_completion_deltas(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> Iterator[str]:
    request = ConversationRequest(model=model, messages=messages)
    yield from stream_text_deltas(backend or text_backend(), request)
