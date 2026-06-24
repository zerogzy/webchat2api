from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from services.protocol import openai_v1_chat_complete, tool_calls
from services.protocol.conversation import count_message_tokens, count_text_tokens
from services.providers.base import CATPAW_PROVIDER
from services.providers.catpaw import conversation as catpaw_conversation
from services.providers.registry import resolve_model


STREAM_PING_INTERVAL_SECONDS = 10.0
_PING = object()


def reset_catpaw_conversation_cache_for_tests() -> None:
    catpaw_conversation.reset_cache_for_tests()


def _catpaw_session_key_from_headers(headers: object) -> str:
    if not isinstance(headers, Mapping):
        return ""
    for name in (
        "x-claude-code-session-id",
        "claude-code-session-id",
        "x-catpaw-conversation-key",
        "x-catapw-conversation-key",
    ):
        value = str(headers.get(name) or "").strip()
        if value:
            return value
    return ""


def _system_text(system: object) -> str:
    if isinstance(system, str):
        return system.strip()
    if isinstance(system, list):
        parts = []
        for item in system:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part).strip()
    return ""


def _json_args(value: object) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = {}
        return json.dumps(parsed if isinstance(parsed, dict) else {}, ensure_ascii=False)
    return json.dumps(value if isinstance(value, dict) else {}, ensure_ascii=False)


def _tool_result_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = str(item.get("type") or "")
                if item_type == "text":
                    parts.append(str(item.get("text") or ""))
                elif item_type == "image":
                    parts.append("[image]")
                elif item_type == "tool_result":
                    parts.append(_tool_result_text(item.get("content")))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    return "" if content is None else str(content).strip()


def _anthropic_image_to_openai(block: Mapping[str, object]) -> dict[str, object] | None:
    source = block.get("source")
    if not isinstance(source, Mapping):
        return None
    source_type = str(source.get("type") or "")
    if source_type == "base64":
        data = str(source.get("data") or "")
        if not data:
            return None
        media = str(source.get("media_type") or "image/png")
        return {"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}}
    if source_type == "url":
        url = str(source.get("url") or "")
        if url:
            return {"type": "image_url", "image_url": {"url": url}}
    return None


def _assistant_message_from_blocks(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    text_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in blocks:
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block_type == "thinking":
            text = str(block.get("thinking") or "").strip()
            if text:
                text_parts.append(text)
        elif block_type == "tool_use":
            call_id = str(block.get("id") or f"call_{uuid.uuid4().hex}")
            calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": str(block.get("name") or ""),
                    "arguments": _json_args(block.get("input")),
                },
            })
    message: dict[str, Any] = {"role": "assistant", "content": "\n".join(part for part in text_parts if part)}
    if calls:
        message["tool_calls"] = calls
        if not message["content"]:
            message["content"] = None
    return message


def _user_messages_from_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for block in blocks:
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text = str(block.get("text") or "")
            text_parts.append(text)
            parts.append({"type": "text", "text": text})
        elif block_type == "image":
            image = _anthropic_image_to_openai(block)
            if image:
                parts.append(image)
        elif block_type == "tool_result":
            tool_use_id = str(block.get("tool_use_id") or "")
            if tool_use_id:
                messages.append({"role": "tool", "tool_call_id": tool_use_id, "content": _tool_result_text(block.get("content"))})
            else:
                text_parts.append(_tool_result_text(block.get("content")))
    has_image = any(part.get("type") == "image_url" for part in parts)
    if has_image:
        content: object = parts
    else:
        content = "\n".join(part for part in text_parts if part)
    if content or not messages:
        messages.append({"role": "user", "content": content})
    return messages


def _message_to_openai(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = str(message.get("role") or "user")
    content = message.get("content")
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    if not isinstance(content, list):
        return [{"role": role, "content": "" if content is None else str(content)}]
    blocks = [block for block in content if isinstance(block, dict)]
    if role == "assistant":
        return [_assistant_message_from_blocks(blocks)]
    if role == "user":
        return _user_messages_from_blocks(blocks)
    return [{"role": role, "content": "\n".join(str(block.get("text") or "") for block in blocks if block.get("type") == "text")}]


def anthropic_to_openai_body(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system = _system_text(body.get("system"))
    if system:
        messages.append({"role": "system", "content": system})
    raw_messages = body.get("messages")
    if isinstance(raw_messages, list):
        for message in raw_messages:
            if isinstance(message, dict):
                messages.extend(_message_to_openai(message))
    payload: dict[str, Any] = {
        "model": str(body.get("model") or "auto").strip() or "auto",
        "messages": messages,
        "stream": bool(body.get("stream")),
    }
    for src, dst in (
        ("max_tokens", "max_tokens"),
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("stop_sequences", "stop"),
    ):
        if src in body and body.get(src) is not None:
            payload[dst] = body[src]
    if body.get("tools") is not None:
        payload["tools"] = tool_calls.normalize_openai_tools(body.get("tools"))
    tool_choice = _openai_tool_choice(body.get("tool_choice"))
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if resolve_model(str(payload.get("model") or "")).provider == CATPAW_PROVIDER:
        payload["catpaw_conversation_id"] = catpaw_conversation.conversation_id_for_anthropic_request(
            messages,
            str(payload.get("model") or ""),
            body.get("tools"),
            session_key=_catpaw_session_key_from_headers(body.get("_request_headers")),
        )
    return payload


def _openai_tool_choice(choice: object) -> object:
    mode, forced = tool_calls.tool_choice_mode(choice)
    if mode == "none":
        return "none"
    if mode == "required":
        return "required"
    if mode == "forced":
        return {"type": "function", "function": {"name": forced}}
    return None


def count_tokens(body: dict[str, Any]) -> dict[str, Any]:
    payload = anthropic_to_openai_body(dict(body))
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    if tool_calls.has_function_tools(payload):
        messages = tool_calls.inject_tool_prompt(messages, payload.get("tools"), payload.get("tool_choice"), payload.get("parallel_tool_calls"))
    return {"input_tokens": count_message_tokens(messages, str(payload.get("model") or "auto"))}


def _tool_input(arguments: object) -> dict[str, object]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def message_response_from_openai(response: dict[str, Any], request_model: str) -> dict[str, Any]:
    choices = response.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content: list[dict[str, object]] = []
    text = str(message.get("content") or "")
    if text:
        content.append({"type": "text", "text": text})
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = str(function.get("name") or call.get("name") or "")
            if not name:
                continue
            content.append({
                "type": "tool_use",
                "id": str(call.get("id") or f"toolu_{uuid.uuid4().hex}"),
                "name": name,
                "input": _tool_input(function.get("arguments") if "arguments" in function else call.get("arguments")),
            })
    if not content:
        content = [{"type": "text", "text": ""}]
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    finish_reason = str(choice.get("finish_reason") or "")
    return {
        "id": f"msg_{uuid.uuid4()}",
        "type": "message",
        "role": "assistant",
        "model": request_model,
        "content": content,
        "stop_reason": _anthropic_stop_reason(finish_reason),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


def _first_choice(chunk: Mapping[str, object]) -> Mapping[str, object]:
    choices = chunk.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        return choices[0]
    return {}


def _chunks_with_ping(chunks: Iterable[dict[str, object]]) -> Iterator[dict[str, object] | object]:
    items: queue.Queue[tuple[str, object]] = queue.Queue()

    def produce() -> None:
        try:
            for chunk in chunks:
                items.put(("chunk", chunk))
        except Exception as exc:
            items.put(("error", exc))
        finally:
            items.put(("done", None))

    threading.Thread(target=produce, daemon=True).start()
    interval = max(float(STREAM_PING_INTERVAL_SECONDS), 0.001)
    while True:
        try:
            kind, payload = items.get(timeout=interval)
        except queue.Empty:
            yield _PING
            continue
        if kind == "chunk":
            yield payload
        elif kind == "error":
            raise payload
        else:
            return


def stream_events_from_openai(chunks: Iterable[dict[str, object]], model: str, input_tokens: int = 0) -> Iterator[dict[str, object]]:
    message_id = f"msg_{uuid.uuid4()}"
    text_open = False
    block_index = 0
    tool_blocks: dict[int, dict[str, Any]] = {}
    output_text_parts: list[str] = []
    finish_reason = "stop"
    yield {"type": "message_start", "message": {"id": message_id, "type": "message", "role": "assistant", "model": model, "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": input_tokens, "output_tokens": 0}}}
    for chunk in _chunks_with_ping(chunks):
        if chunk is _PING:
            yield {"type": "ping"}
            continue
        if not isinstance(chunk, Mapping):
            continue
        choice = _first_choice(chunk)
        delta = choice.get("delta") if isinstance(choice.get("delta"), Mapping) else {}
        text = delta.get("content")
        if isinstance(text, str) and text:
            if not text_open:
                text_open = True
                yield {"type": "content_block_start", "index": block_index, "content_block": {"type": "text", "text": ""}}
            output_text_parts.append(text)
            yield {"type": "content_block_delta", "index": block_index, "delta": {"type": "text_delta", "text": text}}
        raw_tool_calls = delta.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            if text_open:
                yield {"type": "content_block_stop", "index": block_index}
                text_open = False
                block_index += 1
            for item in raw_tool_calls:
                if not isinstance(item, dict):
                    continue
                idx = int(item.get("index") or 0)
                function = item.get("function") if isinstance(item.get("function"), Mapping) else {}
                state = tool_blocks.setdefault(idx, {"id": "", "name": "", "arguments": "", "block_index": None})
                if item.get("id"):
                    state["id"] = str(item.get("id") or "")
                if function.get("name"):
                    state["name"] = str(function.get("name") or "")
                if function.get("arguments"):
                    state["arguments"] += str(function.get("arguments") or "")
                if state["block_index"] is None:
                    state["block_index"] = block_index
                    yield {"type": "content_block_start", "index": block_index, "content_block": {"type": "tool_use", "id": state["id"] or f"toolu_{uuid.uuid4().hex}", "name": state["name"], "input": {}}}
                    block_index += 1
        if choice.get("finish_reason"):
            finish_reason = str(choice.get("finish_reason") or "stop")
            break
    if text_open:
        yield {"type": "content_block_stop", "index": block_index}
    for idx in sorted(tool_blocks):
        state = tool_blocks[idx]
        block = state.get("block_index")
        if block is None:
            continue
        arguments = state.get("arguments") or "{}"
        if not isinstance(arguments, str) or not arguments.strip():
            arguments = "{}"
        yield {"type": "content_block_delta", "index": int(block), "delta": {"type": "input_json_delta", "partial_json": arguments if _is_json_object(arguments) else "{}"}}
        yield {"type": "content_block_stop", "index": int(block)}
    yield {"type": "message_delta", "delta": {"stop_reason": _anthropic_stop_reason(finish_reason), "stop_sequence": None}, "usage": {"output_tokens": count_text_tokens("".join(output_text_parts), model)}}
    yield {"type": "message_stop"}


def _is_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except Exception:
        return False


def _anthropic_stop_reason(finish_reason: str) -> str:
    if finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return "end_turn"


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    request_model = str(body.get("model") or "auto").strip() or "auto"
    payload = anthropic_to_openai_body(dict(body))
    if payload.get("stream"):
        chunks = openai_v1_chat_complete.handle(payload)
        messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
        return stream_events_from_openai(chunks, request_model, count_message_tokens(messages, request_model))
    response = openai_v1_chat_complete.handle(payload)
    if not isinstance(response, dict):
        response = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {}}
    return message_response_from_openai(response, request_model)
