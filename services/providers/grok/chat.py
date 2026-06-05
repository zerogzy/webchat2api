from __future__ import annotations

from typing import Any, Iterator

from services.providers.base import ModelSpec
from services.providers.grok import client as grok_client
from services.providers.grok.models import is_grok_app_chat_model


extract_app_chat_search_sources = grok_client.extract_app_chat_search_sources
extract_app_chat_token = grok_client.extract_app_chat_token
is_app_chat_final_event = grok_client.is_app_chat_final_event
dedupe_search_sources = grok_client.dedupe_search_sources
extract_console_stream_delta = grok_client.extract_console_stream_delta
strip_search_sources_from_messages = grok_client.strip_search_sources_from_messages
append_search_sources_suffix = grok_client.append_search_sources_suffix


def chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> dict[str, Any]:
    if is_grok_app_chat_model(spec):
        return grok_client.app_chat_completion(body, spec, messages)
    completion = grok_client.console_chat_completion(body, spec, messages)
    return {
        "content": completion.content,
        "reasoning_content": completion.reasoning_content,
        "raw_response": completion.raw_response,
    }


def chat_completion_events(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    if is_grok_app_chat_model(spec):
        yield from grok_client.app_chat_completion_events(body, spec, messages)
        return
    yield from grok_client.console_chat_completion_events(body, spec, messages)


def is_app_chat_model(spec: ModelSpec) -> bool:
    return is_grok_app_chat_model(spec)


def app_chat_image_outputs(body: dict[str, Any], spec: ModelSpec, prompt: str, n: int):
    return grok_client.app_chat_image_outputs(body, spec, prompt, n)
