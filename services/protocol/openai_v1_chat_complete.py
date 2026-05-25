from __future__ import annotations

import time
import uuid
from typing import Any, Iterable, Iterator

from fastapi import HTTPException

from services.models import GROK_PROVIDER, resolve_model, is_grok_app_chat_model
from services.providers import grok
from services.protocol.conversation import (
    ConversationRequest,
    ImageOutput,
    collect_image_outputs,
    collect_text,
    count_message_tokens,
    count_text_tokens,
    encode_images,
    normalize_messages,
    stream_image_outputs_with_pool,
    stream_text_deltas,
    text_backend,
)
from utils.helper import build_chat_image_markdown_content, extract_chat_image, extract_chat_prompt, is_image_chat_request, parse_image_count


def completion_chunk(model: str, delta: dict[str, Any], finish_reason: str | None = None, completion_id: str = "", created: int | None = None) -> dict[str, Any]:
    return {
        "id": completion_id or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": created or int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def completion_response(
    model: str,
    content: str,
    created: int | None = None,
    messages: list[dict[str, Any]] | None = None,
    reasoning_content: str = "",
) -> dict[str, Any]:
    prompt_tokens = count_message_tokens(messages, model) if messages else 0
    completion_tokens = count_text_tokens(content, model) if messages else 0
    reasoning_tokens = count_text_tokens(reasoning_content, model) if messages and reasoning_content else 0
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created or int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens + reasoning_tokens,
            "total_tokens": prompt_tokens + completion_tokens + reasoning_tokens,
        },
    }


def stream_include_usage(body: dict[str, Any]) -> bool:
    stream_options = body.get("stream_options")
    return isinstance(stream_options, dict) and stream_options.get("include_usage") is True


def completion_usage(
    messages: list[dict[str, Any]],
    model: str,
    content: str,
    reasoning_content: str = "",
) -> dict[str, int]:
    prompt_tokens = count_message_tokens(messages, model) if messages else 0
    completion_tokens = count_text_tokens(content, model) if messages else 0
    reasoning_tokens = count_text_tokens(reasoning_content, model) if messages and reasoning_content else 0
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens + reasoning_tokens,
        "total_tokens": prompt_tokens + completion_tokens + reasoning_tokens,
    }


def completion_usage_chunk(model: str, completion_id: str, created: int, usage: dict[str, int]) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [],
        "usage": usage,
    }


def stream_chunk(chunk: dict[str, Any], include_usage: bool) -> dict[str, Any]:
    if include_usage:
        chunk = dict(chunk)
        chunk["usage"] = None
    return chunk


def stream_text_chat_completion(
    backend,
    messages: list[dict[str, Any]],
    model: str,
    include_usage: bool = False,
) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    content_parts: list[str] = []
    request = ConversationRequest(model=model, messages=messages)
    for delta_text in stream_text_deltas(backend, request):
        content_parts.append(delta_text)
        if not sent_role:
            sent_role = True
            chunk = completion_chunk(model, {"role": "assistant", "content": delta_text}, None, completion_id, created)
        else:
            chunk = completion_chunk(model, {"content": delta_text}, None, completion_id, created)
        yield stream_chunk(chunk, include_usage)
    if not sent_role:
        chunk = completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created)
        yield stream_chunk(chunk, include_usage)
    yield stream_chunk(completion_chunk(model, {}, "stop", completion_id, created), include_usage)
    if include_usage:
        yield completion_usage_chunk(
            model,
            completion_id,
            created,
            completion_usage(messages, model, "".join(content_parts)),
        )


def stream_grok_app_chat_completion(body: dict[str, Any], spec, messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
    include_usage = stream_include_usage(body)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    for event in grok.app_chat_completion_events(body, spec, messages):
        token, thinking = grok.extract_app_chat_token(event)
        if not token:
            if grok.is_app_chat_final_event(event):
                break
            continue
        if thinking:
            reasoning_parts.append(token)
        else:
            content_parts.append(token)
        if not sent_role:
            sent_role = True
            delta: dict[str, Any] = {"role": "assistant"}
            if thinking:
                delta["reasoning_content"] = token
            else:
                delta["content"] = token
            yield stream_chunk(completion_chunk(model, delta, None, completion_id, created), include_usage)
            continue
        if thinking:
            chunk = completion_chunk(model, {"reasoning_content": token}, None, completion_id, created)
        else:
            chunk = completion_chunk(model, {"content": token}, None, completion_id, created)
        yield stream_chunk(chunk, include_usage)
    if not sent_role:
        chunk = completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created)
        yield stream_chunk(chunk, include_usage)
    yield stream_chunk(completion_chunk(model, {}, "stop", completion_id, created), include_usage)
    if include_usage:
        yield completion_usage_chunk(
            model,
            completion_id,
            created,
            completion_usage(messages, model, "".join(content_parts), "".join(reasoning_parts)),
        )


def stream_grok_chat_completion(body: dict[str, Any], spec, messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
    if is_grok_app_chat_model(spec):
        yield from stream_grok_app_chat_completion(body, spec, messages, model)
        return
    include_usage = stream_include_usage(body)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    response = grok.console_chat_completion(body, spec, messages)
    if response.reasoning_content:
        chunk = completion_chunk(
            model,
            {"role": "assistant", "reasoning_content": response.reasoning_content},
            None,
            completion_id,
            created,
        )
        yield stream_chunk(chunk, include_usage)
        yield stream_chunk(completion_chunk(model, {"content": response.content}, None, completion_id, created), include_usage)
    else:
        chunk = completion_chunk(model, {"role": "assistant", "content": response.content}, None, completion_id, created)
        yield stream_chunk(chunk, include_usage)
    yield stream_chunk(completion_chunk(model, {}, "stop", completion_id, created), include_usage)
    if include_usage:
        yield completion_usage_chunk(
            model,
            completion_id,
            created,
            completion_usage(messages, model, response.content, response.reasoning_content),
        )


def collect_chat_content(chunks: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for chunk in chunks:
        choices = chunk.get("choices")
        first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
        content = str(delta.get("content") or "")
        if content:
            parts.append(content)
    return "".join(parts)


def chat_messages_from_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        return [message for message in messages if isinstance(message, dict)]
    prompt = str(body.get("prompt") or "").strip()
    if prompt:
        return [{"role": "user", "content": prompt}]
    raise HTTPException(status_code=400, detail={"error": "messages or prompt is required"})


def chat_image_args(body: dict[str, Any]) -> tuple[str, str, int, list[tuple[bytes, str, str]]]:
    model = str(body.get("model") or "gpt-image-2").strip() or "gpt-image-2"
    prompt = extract_chat_prompt(body)
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required"})
    images = [
        (data, f"image_{idx}.png", mime)
        for idx, (data, mime) in enumerate(extract_chat_image(body), start=1)
    ]
    return model, prompt, parse_image_count(body.get("n")), images


def text_chat_parts(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    model = str(body.get("model") or "auto").strip() or "auto"
    messages = normalize_messages(chat_messages_from_body(body))
    return model, messages


def image_result_content(result: dict[str, Any]) -> str:
    data = result.get("data")
    if isinstance(data, list) and data:
        return build_chat_image_markdown_content(result)
    return str(result.get("message") or "Image generation completed.")


def image_chat_response(body: dict[str, Any]) -> dict[str, Any]:
    model, prompt, n, images = chat_image_args(body)
    spec = resolve_model(model)
    if spec.provider == GROK_PROVIDER:
        if images:
            from services.protocol.conversation import ImageGenerationError
            raise ImageGenerationError("Grok image chat does not support image input", status_code=400, error_type="invalid_request_error", code="unsupported_model", param="model")
        result = collect_image_outputs(grok.app_chat_image_outputs(body, spec, prompt, n))
        return completion_response(model, image_result_content(result), int(result.get("created") or 0) or None)
    result = collect_image_outputs(stream_image_outputs_with_pool(ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        response_format="b64_json",
        images=encode_images(images) or None,
    )))
    return completion_response(model, image_result_content(result), int(result.get("created") or 0) or None)


def image_chat_events(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    model, prompt, n, images = chat_image_args(body)
    spec = resolve_model(model)
    if spec.provider == GROK_PROVIDER:
        if images:
            from services.protocol.conversation import ImageGenerationError
            raise ImageGenerationError("Grok image chat does not support image input", status_code=400, error_type="invalid_request_error", code="unsupported_model", param="model")
        yield from stream_image_chat_completion(grok.app_chat_image_outputs(body, spec, prompt, n), model)
        return
    image_outputs = stream_image_outputs_with_pool(ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        response_format="b64_json",
        images=encode_images(images) or None,
    ))
    yield from stream_image_chat_completion(image_outputs, model)


def stream_image_chat_completion(image_outputs: Iterable[ImageOutput], model: str) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    sent_text = ""
    for output in image_outputs:
        content = ""
        if output.kind == "progress":
            content = output.text
            sent_text += content
        elif output.kind == "result":
            content = build_chat_image_markdown_content({"data": output.data})
        elif output.kind == "message":
            content = output.text[len(sent_text):] if output.text.startswith(sent_text) else output.text
        if not content:
            continue
        if not sent_role:
            sent_role = True
            yield completion_chunk(model, {"role": "assistant", "content": content}, None, completion_id, created)
        else:
            yield completion_chunk(model, {"content": content}, None, completion_id, created)
    if not sent_role:
        yield completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created)
    yield completion_chunk(model, {}, "stop", completion_id, created)


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    if body.get("stream"):
        if is_image_chat_request(body):
            return image_chat_events(body)
        model, messages = text_chat_parts(body)
        spec = resolve_model(model)
        if spec.provider == GROK_PROVIDER:
            return stream_grok_chat_completion(body, spec, messages, model)
        return stream_text_chat_completion(text_backend(), messages, model, stream_include_usage(body))
    if is_image_chat_request(body):
        return image_chat_response(body)
    model, messages = text_chat_parts(body)
    spec = resolve_model(model)
    if spec.provider == GROK_PROVIDER:
        if is_grok_app_chat_model(spec):
            response = grok.app_chat_completion(body, spec, messages)
            return completion_response(
                model,
                response.get("content", ""),
                messages=messages,
                reasoning_content=response.get("reasoning_content", ""),
            )
        response = grok.console_chat_completion(body, spec, messages)
        return completion_response(
            model,
            response.content,
            messages=messages,
            reasoning_content=response.reasoning_content,
        )
    request = ConversationRequest(model=model, messages=messages)
    return completion_response(model, collect_text(text_backend(), request), messages=messages)
