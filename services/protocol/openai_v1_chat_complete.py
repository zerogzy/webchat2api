from __future__ import annotations

import time
import uuid
from typing import Any, Iterable, Iterator, cast

from fastapi import HTTPException

from services.models import GEMINI_PROVIDER, GROK_PROVIDER, resolve_model, is_grok_app_chat_model
from services.providers import grok
from services.providers.gemini import chat as gemini_chat
from services.providers.gemini import images as gemini_images
from services.providers.gpt import chat as gpt_chat
from services.providers.gpt import images as gpt_images
from services.providers.grok import chat as grok_chat
from services.providers.grok import images as grok_images
from services.config import config
import services.protocol.tool_calls as tool_calls
from services.protocol.conversation import (
    ConversationRequest,
    ImageOutput,
    collect_image_outputs,
    collect_text,
    count_message_tokens,
    count_text_tokens,
    encode_images,
    normalize_messages,
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
    tool_call_messages: list[dict[str, Any]] | None = None,
    search_sources: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    token_messages = tool_call_messages or messages
    prompt_tokens = count_message_tokens(token_messages, model) if token_messages else 0
    completion_tokens = count_text_tokens(content, model) if token_messages else 0
    reasoning_tokens = count_text_tokens(reasoning_content, model) if token_messages and reasoning_content else 0
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    if search_sources:
        message["search_sources"] = search_sources
        message["annotations"] = url_citation_annotations(search_sources)
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


def url_citation_annotations(search_sources: list[dict[str, str]]) -> list[dict[str, Any]]:
    annotations: list[dict[str, Any]] = []
    for source in search_sources:
        url = str(source.get("url") or "").strip()
        if not url:
            continue
        title = str(source.get("title") or url).strip() or url
        annotations.append({
            "type": "url_citation",
            "url_citation": {
                "url": url,
                "title": title,
            },
        })
    return annotations


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
    if stream_text_deltas is not gpt_chat.stream_text_deltas:
        request = ConversationRequest(model=model, messages=messages)
        for delta_text in stream_text_deltas(backend, request):
            content_parts.append(delta_text)
            if not sent_role:
                sent_role = True
                chunk = completion_chunk(model, {"role": "assistant", "content": delta_text}, None, completion_id, created)
            else:
                chunk = completion_chunk(model, {"content": delta_text}, None, completion_id, created)
            yield stream_chunk(chunk, include_usage)
    else:
        for delta_text in gpt_chat.chat_completion_deltas(body={}, messages=messages, model=model, backend=backend):
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


def stream_tool_chat_completion_from_text(
    content: str,
    body: dict[str, Any],
    model: str,
    messages: list[dict[str, Any]],
    completion_id: str | None = None,
    created: int | None = None,
    include_usage: bool = False,
    reasoning_content: str = "",
    gemini_native_tools: bool = False,
) -> Iterator[dict[str, Any]]:
    completion_id = completion_id or f"chatcmpl-{uuid.uuid4().hex}"
    created = created or int(time.time())
    parsed = tool_calls.parse_gemini_openai_tool_response(content, body) if gemini_native_tools else tool_calls.parse_tool_calls(content, tool_calls.tool_names(body.get("tools")))
    if parsed.calls:
        yield stream_chunk(completion_chunk(model, {"role": "assistant", "content": None}, None, completion_id, created), include_usage)
        for index, call in enumerate(parsed.calls):
            yield stream_chunk(completion_chunk(
                model,
                {
                    "tool_calls": [{
                        "index": index,
                        "id": call.call_id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }],
                },
                None,
                completion_id,
                created,
            ), include_usage)
        yield stream_chunk(completion_chunk(model, {}, "tool_calls", completion_id, created), include_usage)
        if include_usage:
            yield completion_usage_chunk(
                model,
                completion_id,
                created,
                completion_usage(messages, model, content, reasoning_content),
            )
        return
    plain = tool_calls.strip_tool_markup(content) if parsed.saw_tool_syntax else content
    yield stream_chunk(completion_chunk(model, {"role": "assistant", "content": plain}, None, completion_id, created), include_usage)
    yield stream_chunk(completion_chunk(model, {}, "stop", completion_id, created), include_usage)
    if include_usage:
        yield completion_usage_chunk(
            model,
            completion_id,
            created,
            completion_usage(messages, model, plain, reasoning_content),
        )


def stream_tool_text_chat_completion(
    backend,
    body: dict[str, Any],
    messages: list[dict[str, Any]],
    model: str,
    include_usage: bool = False,
) -> Iterator[dict[str, Any]]:
    request = ConversationRequest(model=model, messages=messages)
    if stream_text_deltas is not gpt_chat.stream_text_deltas:
        content = "".join(stream_text_deltas(backend, request))
    elif collect_text is not gpt_chat.collect_text:
        content = collect_text(backend, request)
    else:
        content = gpt_chat.chat_completion(body, messages, model, backend=backend)
    yield from stream_tool_chat_completion_from_text(content, body, model, messages, include_usage=include_usage)


def stream_grok_app_chat_completion(body: dict[str, Any], spec, messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
    include_usage = stream_include_usage(body)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    search_sources: list[dict[str, str]] = []
    for event in grok_chat.chat_completion_events(body, spec, messages):
        search_sources.extend(grok.extract_app_chat_search_sources(event))
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
    final_delta: dict[str, Any] = {}
    deduped_sources = grok.dedupe_search_sources(search_sources)
    if deduped_sources:
        final_delta["search_sources"] = deduped_sources
        final_delta["annotations"] = url_citation_annotations(deduped_sources)
    yield stream_chunk(completion_chunk(model, final_delta, "stop", completion_id, created), include_usage)
    if include_usage:
        yield completion_usage_chunk(
            model,
            completion_id,
            created,
            completion_usage(messages, model, "".join(content_parts), "".join(reasoning_parts)),
        )


def stream_grok_chat_completion(body: dict[str, Any], spec, messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
    if grok_chat.is_app_chat_model(spec):
        yield from stream_grok_app_chat_completion(body, spec, messages, model)
        return
    include_usage = stream_include_usage(body)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    for event in grok_chat.chat_completion_events(body, spec, messages):
        delta = grok.extract_console_stream_delta(event)
        if not delta.content and not delta.reasoning_content:
            continue
        if delta.reasoning_content:
            reasoning_parts.append(delta.reasoning_content)
        else:
            content_parts.append(delta.content)
        if not sent_role:
            sent_role = True
            first_delta: dict[str, Any] = {"role": "assistant"}
            if delta.reasoning_content:
                first_delta["reasoning_content"] = delta.reasoning_content
            else:
                first_delta["content"] = delta.content
            yield stream_chunk(completion_chunk(model, first_delta, None, completion_id, created), include_usage)
            continue
        if delta.reasoning_content:
            chunk = completion_chunk(model, {"reasoning_content": delta.reasoning_content}, None, completion_id, created)
        else:
            chunk = completion_chunk(model, {"content": delta.content}, None, completion_id, created)
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


def stream_gemini_chat_completion(body: dict[str, Any], spec, messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    content_parts: list[str] = []
    for delta_text in gemini_chat.chat_completion_deltas(body, spec, messages):
        content_parts.append(delta_text)
        if not sent_role:
            sent_role = True
            yield stream_chunk(completion_chunk(model, {"role": "assistant", "content": delta_text}, None, completion_id, created), stream_include_usage(body))
        else:
            yield stream_chunk(completion_chunk(model, {"content": delta_text}, None, completion_id, created), stream_include_usage(body))
    if not sent_role:
        yield stream_chunk(completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created), stream_include_usage(body))
    yield stream_chunk(completion_chunk(model, {}, "stop", completion_id, created), stream_include_usage(body))
    if stream_include_usage(body):
        yield completion_usage_chunk(model, completion_id, created, completion_usage(messages, model, "".join(content_parts)))


def collect_chat_content(chunks: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for chunk in chunks:
        choices = chunk.get("choices")
        first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        raw_delta = first.get("delta")
        delta = raw_delta if isinstance(raw_delta, dict) else {}
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


def prepare_text_messages(body: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_messages = grok.strip_search_sources_from_messages(chat_messages_from_body(body))
    if tool_calls.has_function_tools(body):
        injected = tool_calls.inject_tool_prompt(
            raw_messages,
            body.get("tools"),
            body.get("tool_choice"),
            body.get("parallel_tool_calls"),
        )
        return normalize_messages(injected), normalize_messages(raw_messages)
    messages = normalize_messages(raw_messages)
    return messages, messages



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


def text_chat_parts(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    model = str(body.get("model") or "auto").strip() or "auto"
    messages, original_messages = prepare_text_messages(body)
    return model, messages, original_messages


def parsed_chat_tool_response(body: dict[str, Any], model: str, content: str, messages: list[dict[str, Any]], gemini_native_tools: bool = False) -> dict[str, Any] | None:
    names = tool_calls.tool_names(body.get("tools"))
    if not names:
        return None
    parsed = tool_calls.parse_gemini_openai_tool_response(content, body) if gemini_native_tools else tool_calls.parse_tool_calls(content, names)
    if not parsed.calls:
        return None
    return tool_calls.chat_tool_call_response(model, parsed.calls, messages=messages)


def image_result_content(result: dict[str, Any]) -> str:
    data = result.get("data")
    if isinstance(data, list) and data:
        return build_chat_image_markdown_content(result)
    return str(result.get("message") or "Image generation completed.")


def gemini_image_chat_unsupported() -> HTTPException:
    return gemini_images.unsupported_image_error()


def image_chat_response(body: dict[str, Any]) -> dict[str, Any]:
    model, prompt, n, images = chat_image_args(body)
    spec = resolve_model(model)
    if spec.provider == GROK_PROVIDER:
        if images:
            from services.protocol.conversation import ImageGenerationError
            raise ImageGenerationError("Grok image chat does not support image input", status_code=400, error_type="invalid_request_error", code="unsupported_model", param="model")
        result = collect_image_outputs(grok_images.generation_outputs(body, spec, prompt, n))
        return completion_response(model, image_result_content(result), int(result.get("created") or 0) or None)
    request = ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        response_format="b64_json",
        images=encode_images(images) or None,
    )
    if spec.provider == GEMINI_PROVIDER:
        result = collect_image_outputs(gemini_images.generation_outputs(request, spec))
    else:
        result = collect_image_outputs(gpt_images.generation_outputs(request, spec))
    return completion_response(model, image_result_content(result), int(result.get("created") or 0) or None)


def image_chat_events(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    model, prompt, n, images = chat_image_args(body)
    spec = resolve_model(model)
    if spec.provider == GROK_PROVIDER:
        if images:
            from services.protocol.conversation import ImageGenerationError
            raise ImageGenerationError("Grok image chat does not support image input", status_code=400, error_type="invalid_request_error", code="unsupported_model", param="model")
        yield from stream_image_chat_completion(grok_images.generation_outputs(body, spec, prompt, n), model)
        return
    request = ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        response_format="b64_json",
        images=encode_images(images) or None,
    )
    if spec.provider == GEMINI_PROVIDER:
        image_outputs = gemini_images.generation_outputs(request, spec)
    else:
        image_outputs = gpt_images.generation_outputs(request, spec)
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
        model, messages, _ = text_chat_parts(body)
        spec = resolve_model(model)
        if tool_calls.has_function_tools(body):
            if spec.provider == GROK_PROVIDER:
                if grok_chat.is_app_chat_model(spec):
                    response = grok_chat.chat_completion(body, spec, messages)
                    return stream_tool_chat_completion_from_text(
                        response.get("content", ""),
                        body,
                        model,
                        messages,
                        include_usage=stream_include_usage(body),
                        reasoning_content=response.get("reasoning_content", ""),
                    )
                response = grok_chat.chat_completion(body, spec, messages)
                return stream_tool_chat_completion_from_text(
                    response["content"],
                    body,
                    model,
                    messages,
                    include_usage=stream_include_usage(body),
                    reasoning_content=response["reasoning_content"],
                )
            if spec.provider == GEMINI_PROVIDER:
                response = gemini_chat.chat_completion(body, spec, messages)
                return stream_tool_chat_completion_from_text(
                    response.content,
                    body,
                    model,
                    messages,
                    include_usage=stream_include_usage(body),
                    gemini_native_tools=True,
                )
            return stream_tool_text_chat_completion(text_backend(), body, messages, model, stream_include_usage(body))
        if spec.provider == GROK_PROVIDER:
            return stream_grok_chat_completion(body, spec, messages, model)
        if spec.provider == GEMINI_PROVIDER:
            return stream_gemini_chat_completion(body, spec, messages, model)
        return stream_text_chat_completion(text_backend(), messages, model, stream_include_usage(body))
    if is_image_chat_request(body):
        return image_chat_response(body)
    model, messages, original_messages = text_chat_parts(body)
    spec = resolve_model(model)
    if spec.provider == GROK_PROVIDER:
        if grok_chat.is_app_chat_model(spec):
            response = grok_chat.chat_completion(body, spec, messages)
            raw_sources = response.get("search_sources")
            search_sources = cast(list[dict[str, str]], raw_sources) if isinstance(raw_sources, list) else []
            content = str(response.get("content", ""))
            if config.show_search_sources:
                content = grok.append_search_sources_suffix(content, search_sources)
            tool_response = parsed_chat_tool_response(body, model, content, messages)
            if tool_response:
                return tool_response
            return completion_response(
                model,
                content,
                messages=original_messages,
                tool_call_messages=messages,
                reasoning_content=response.get("reasoning_content", ""),
                search_sources=search_sources,
            )
        response = grok_chat.chat_completion(body, spec, messages)
        tool_response = parsed_chat_tool_response(body, model, response["content"], messages)
        if tool_response:
            return tool_response
        return completion_response(
            model,
            response["content"],
            messages=original_messages,
            tool_call_messages=messages,
            reasoning_content=response["reasoning_content"],
        )
    if spec.provider == GEMINI_PROVIDER:
        response = gemini_chat.chat_completion(body, spec, messages)
        tool_response = parsed_chat_tool_response(body, model, response.content, messages, gemini_native_tools=True)
        if tool_response:
            return tool_response
        return completion_response(model, response.content, messages=original_messages, tool_call_messages=messages)
    if collect_text is not gpt_chat.collect_text:
        request = ConversationRequest(model=model, messages=messages)
        content = collect_text(text_backend(), request)
    else:
        content = gpt_chat.chat_completion(body, messages, model, backend=text_backend())
    tool_response = parsed_chat_tool_response(body, model, content, messages)
    if tool_response:
        return tool_response
    return completion_response(model, content, messages=original_messages, tool_call_messages=messages)
