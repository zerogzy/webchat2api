from __future__ import annotations

import time
from typing import Any, Iterable, Iterator

import tiktoken

from services.providers.base import ConversationRequest, ImageGenerationError, ImageOutput
from services.providers.gpt.runtime import (
    ConversationState,
    add_unique,
    apply_patch_op,
    apply_text_patch,
    assistant_history_messages,
    assistant_history_text,
    assistant_message_text,
    assistant_text,
    build_image_prompt,
    collect_text,
    conversation_base_event,
    conversation_events,
    encode_images,
    event_assistant_text,
    extract_conversation_ids,
    format_image_result,
    image_stream_error_message,
    is_image_tool_event,
    is_token_invalid_error,
    iter_conversation_payloads,
    message_text,
    normalize_messages,
    prompt_with_global_system,
    save_image_bytes,
    stream_image_outputs,
    stream_image_outputs_with_pool,
    stream_text_deltas,
    strip_history,
    text_backend,
    update_conversation_state,
)


def image_token_estimate(value: Any) -> int:
    if isinstance(value, dict):
        if str(value.get("type") or "") in {"image", "image_url", "input_image"}:
            return 85
        return sum(image_token_estimate(item) for item in value.values())
    if isinstance(value, list):
        return sum(image_token_estimate(item) for item in value)
    return 0


def encoding_for_model(model: str):
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        try:
            return tiktoken.get_encoding("o200k_base")
        except KeyError:
            return tiktoken.get_encoding("cl100k_base")


def count_message_tokens(messages: list[dict[str, Any]], model: str) -> int:
    encoding = encoding_for_model(model)
    total = 0
    for message in messages:
        total += 3
        total += image_token_estimate(message.get("content"))
        for key, value in message.items():
            if not isinstance(value, str):
                continue
            total += len(encoding.encode(value))
            if key == "name":
                total += 1
    return total + 3


def count_text_tokens(text: str, model: str) -> int:
    return len(encoding_for_model(model).encode(text))


def stream_image_chunks(outputs: Iterable[ImageOutput]) -> Iterator[dict[str, Any]]:
    for output in outputs:
        yield output.to_chunk()


def collect_image_outputs(outputs: Iterable[ImageOutput]) -> dict[str, Any]:
    created = None
    data: list[dict[str, Any]] = []
    message = ""
    progress_parts: list[str] = []
    for output in outputs:
        created = created or output.created
        if output.kind == "progress" and output.text:
            progress_parts.append(output.text)
        elif output.kind == "message":
            message = output.text
        elif output.kind == "result":
            data.extend(output.data)

    result: dict[str, Any] = {"created": created or int(time.time()), "data": data}
    if not data:
        text = message or "".join(progress_parts).strip()
        if text:
            result["message"] = text
    return result
