from __future__ import annotations

import base64
import time
import uuid
from typing import Any, Iterable, Iterator

from fastapi import HTTPException

from services.models import GROK_PROVIDER, is_grok_app_chat_model, resolve_model
from services.providers import grok
import services.protocol.tool_calls as tool_calls
from services.protocol.conversation import (
    ConversationRequest,
    ImageOutput,
    encode_images,
    stream_image_outputs_with_pool,
    stream_text_deltas,
    text_backend,
)
from utils.helper import extract_image_from_message_content, extract_response_prompt, has_response_image_generation_tool


def is_text_response_request(body: dict[str, Any]) -> bool:
    return not has_response_image_generation_tool(body)


def extract_response_image(input_value: object) -> tuple[bytes, str] | None:
    if isinstance(input_value, dict):
        images = extract_image_from_message_content(input_value.get("content"))
        return images[0] if images else None
    if not isinstance(input_value, list):
        return None
    for item in reversed(input_value):
        if isinstance(item, dict) and str(item.get("type") or "").strip() == "input_image":
            image_url = str(item.get("image_url") or "")
            if image_url.startswith("data:"):
                header, _, data = image_url.partition(",")
                mime = header.split(";")[0].removeprefix("data:")
                return base64.b64decode(data), mime or "image/png"
        if isinstance(item, dict):
            images = extract_image_from_message_content(item.get("content"))
            if images:
                return images[0]
    return None


def messages_from_input(input_value: object, instructions: object = None) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system_text = str(instructions or "").strip()
    if system_text:
        messages.append({"role": "system", "content": system_text})
    if isinstance(input_value, str):
        if input_value.strip():
            messages.append({"role": "user", "content": input_value.strip()})
        return messages
    if isinstance(input_value, dict):
        messages.extend(_messages_from_response_item(input_value))
        return messages
    if isinstance(input_value, list):
        if all(isinstance(item, dict) and item.get("type") for item in input_value):
            text = extract_response_prompt(input_value)
            if text:
                messages.append({"role": "user", "content": text})
            return messages
        for item in input_value:
            if isinstance(item, dict):
                messages.extend(_messages_from_response_item(item))
    return messages


def _messages_from_response_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    item_type = str(item.get("type") or "")
    if item_type == "function_call":
        return [{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": str(item.get("call_id") or ""),
                "type": "function",
                "function": {
                    "name": str(item.get("name") or ""),
                    "arguments": str(item.get("arguments") or "{}"),
                },
            }],
        }]
    if item_type in {"function_call_output", "tool_result"}:
        return [{
            "role": "tool",
            "tool_call_id": str(item.get("call_id") or item.get("tool_call_id") or ""),
            "content": str(item.get("output") or item.get("content") or ""),
        }]
    return [{
        "role": str(item.get("role") or "user"),
        "content": extract_response_prompt([item]) or item.get("content") or "",
    }]


def prepare_response_messages(body: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    messages = messages_from_input(body.get("input"), body.get("instructions"))
    if tool_calls.has_function_tools(body):
        injected = tool_calls.inject_tool_prompt(
            messages,
            body.get("tools"),
            body.get("tool_choice"),
            body.get("parallel_tool_calls"),
        )
        return injected, messages
    return messages, messages


def text_output_item(text: str, item_id: str | None = None, status: str = "completed") -> dict[str, Any]:
    return {
        "id": item_id or f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def response_output_from_text(body: dict[str, Any], text: str) -> list[dict[str, Any]]:
    names = tool_calls.tool_names(body.get("tools"))
    if names:
        parsed = tool_calls.parse_tool_calls(text, names)
        if parsed.calls:
            return tool_calls.response_function_call_items(parsed.calls)
    return [text_output_item(text)]


def image_output_items(prompt: str, data: list[dict[str, Any]], item_id: str | None = None) -> list[dict[str, Any]]:
    output = []
    for item in data:
        b64_json = str(item.get("b64_json") or "").strip()
        if b64_json:
            output.append({
                "id": item_id or f"ig_{len(output) + 1}",
                "type": "image_generation_call",
                "status": "completed",
                "result": b64_json,
                "revised_prompt": str(item.get("revised_prompt") or prompt).strip() or prompt,
            })
    return output


def response_created(response_id: str, model: str, created: int) -> dict[str, Any]:
    return {
        "type": "response.created",
        "response": {
            "id": response_id,
            "object": "response",
            "created_at": created,
            "status": "in_progress",
            "error": None,
            "incomplete_details": None,
            "model": model,
            "output": [],
            "parallel_tool_calls": False,
        },
    }


def response_completed(response_id: str, model: str, created: int, output: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "object": "response",
            "created_at": created,
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "model": model,
            "output": output,
            "parallel_tool_calls": False,
        },
    }


def stream_response_output_items(output: list[dict[str, Any]], response_id: str, model: str, created: int) -> Iterator[dict[str, Any]]:
    for index, item in enumerate(output):
        if item.get("type") == "function_call":
            yield {"type": "response.output_item.added", "output_index": index, "item": {**item, "arguments": "", "status": "in_progress"}}
            yield {"type": "response.function_call_arguments.delta", "item_id": item["id"], "output_index": index, "delta": item["arguments"]}
            yield {"type": "response.function_call_arguments.done", "item_id": item["id"], "output_index": index, "arguments": item["arguments"]}
            yield {"type": "response.output_item.done", "output_index": index, "item": item}
            continue
        yield {"type": "response.output_item.added", "output_index": index, "item": item}
        content = item.get("content") if isinstance(item.get("content"), list) else []
        first = content[0] if content and isinstance(content[0], dict) else {}
        text = str(first.get("text") or "")
        if text:
            yield {"type": "response.output_text.delta", "item_id": item["id"], "output_index": index, "content_index": 0, "delta": text}
        yield {"type": "response.output_text.done", "item_id": item["id"], "output_index": index, "content_index": 0, "text": text}
        yield {"type": "response.output_item.done", "output_index": index, "item": item}
    yield response_completed(response_id, model, created, output)


def stream_text_response(backend, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    model = str(body.get("model") or "auto").strip() or "auto"
    messages, _ = prepare_response_messages(body)
    response_id = f"resp_{uuid.uuid4().hex}"
    created = int(time.time())
    yield response_created(response_id, model, created)
    request = ConversationRequest(model=model, messages=messages)
    full_text = "".join(stream_text_deltas(backend, request))
    yield from stream_response_output_items(response_output_from_text(body, full_text), response_id, model, created)


def stream_grok_console_response(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    model = str(body.get("model") or "auto").strip() or "auto"
    spec = resolve_model(model)
    if is_grok_app_chat_model(spec):
        raise HTTPException(status_code=501, detail={"error": "Grok app-chat is not supported on /v1/responses"})
    messages, _ = prepare_response_messages(body)
    if not messages:
        raise HTTPException(status_code=400, detail={"error": "input text is required"})
    response_id = f"resp_{uuid.uuid4().hex}"
    created = int(time.time())
    yield response_created(response_id, model, created)
    completion = grok.console_chat_completion(body, spec, messages)
    yield from stream_response_output_items(response_output_from_text(body, completion.content), response_id, model, created)


def stream_image_response(image_outputs: Iterable[ImageOutput], prompt: str, model: str) -> Iterator[dict[str, Any]]:
    response_id = f"resp_{uuid.uuid4().hex}"
    created = int(time.time())
    yield response_created(response_id, model, created)
    for output in image_outputs:
        if output.kind == "message":
            text = output.text
            item = text_output_item(text)
            yield {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0, "content_index": 0, "delta": text}
            yield {"type": "response.output_text.done", "item_id": item["id"], "output_index": 0, "content_index": 0, "text": text}
            yield {"type": "response.output_item.done", "output_index": 0, "item": item}
            yield response_completed(response_id, model, created, [item])
            return
        if output.kind != "result":
            continue
        items = image_output_items(prompt, output.data)
        if items:
            item = items[0]
            yield {"type": "response.output_item.done", "output_index": 0, "item": item}
            yield response_completed(response_id, model, created, [item])
            return
    raise RuntimeError("image generation failed")


def collect_response(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    completed = {}
    for event in events:
        if event.get("type") == "response.completed":
            completed = event.get("response") if isinstance(event.get("response"), dict) else {}
    if not completed:
        raise RuntimeError("response generation failed")
    return completed


def response_events(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    if is_text_response_request(body):
        model = str(body.get("model") or "auto").strip() or "auto"
        spec = resolve_model(model)
        if spec.provider == GROK_PROVIDER:
            yield from stream_grok_console_response(body)
            return
        yield from stream_text_response(text_backend(), body)
        return

    prompt = extract_response_prompt(body.get("input"))
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "input text is required"})
    model = str(body.get("model") or "gpt-image-2").strip() or "gpt-image-2"
    image_info = extract_response_image(body.get("input"))
    if image_info:
        image_data, mime_type = image_info
        images = encode_images([(image_data, "image.png", mime_type)])
    else:
        images = None
    image_outputs = stream_image_outputs_with_pool(ConversationRequest(
        prompt=prompt,
        model=model,
        size=None if images else "1:1",
        response_format="b64_json",
        images=images,
    ))
    yield from stream_image_response(image_outputs, prompt, model)


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    events = response_events(body)
    if body.get("stream"):
        return events
    return collect_response(events)
