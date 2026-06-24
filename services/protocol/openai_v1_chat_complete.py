from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any, Iterable, Iterator, cast

from fastapi import HTTPException

from services.providers.base import ConversationRequest, CATPAW_PROVIDER, GEMINI_PROVIDER, GPT_PROVIDER, GROK_PROVIDER, ImageGenerationError, ImageOutput
from services.providers.registry import chat_adapter, image_adapter, image_generation_outputs, resolve_model
from services.config import config
import services.protocol.tool_calls as tool_calls
from services.protocol.chat_completion_cache import (
    cache_key,
    chat_completion_cache,
    is_cacheable_text_request,
    normalize_text_messages,
)
from services.protocol.conversation import (
    collect_image_outputs,
    collect_text,
    count_message_tokens,
    count_text_tokens,
    encode_images,
    maybe_attach_long_text_messages,
    normalize_messages,
    stream_text_deltas,
    text_backend,
)
from utils.helper import build_chat_image_markdown_content, extract_chat_image, extract_chat_prompt, has_image_message_content, is_image_chat_request, parse_image_count
from utils.log import logger


gpt_chat = chat_adapter("gpt")
grok_chat = chat_adapter("grok")
gemini_chat = chat_adapter("gemini")
catpaw_chat = chat_adapter("catpaw")


def is_grok_app_chat_model(spec: Any) -> bool:
    return grok_chat.is_app_chat_model(spec)


def _unsupported_image_error(provider: str) -> HTTPException:
    adapter = image_adapter(provider)
    if hasattr(adapter, "unsupported_image_error"):
        return adapter.unsupported_image_error()
    return HTTPException(status_code=400, detail={"error": f"{provider} image chat is unsupported"})


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


def _text_delta_source(backend, messages: list[dict[str, Any]], model: str, body: dict[str, Any] | None = None) -> Iterator[str]:
    if resolve_model(model).provider == CATPAW_PROVIDER:
        return catpaw_chat.chat_completion_deltas(body=body or {}, messages=messages, model=model)
    if stream_text_deltas is not gpt_chat.stream_text_deltas:
        return stream_text_deltas(backend, ConversationRequest(model=model, messages=messages))
    return gpt_chat.chat_completion_deltas(body={}, messages=messages, model=model, backend=backend)


def stream_text_chat_completion(
    backend,
    messages: list[dict[str, Any]],
    model: str,
    include_usage: bool = False,
    usage_messages: list[dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    content_parts: list[str] = []
    for delta_text in _text_delta_source(backend, messages, model):
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
            completion_usage(usage_messages or messages, model, "".join(content_parts)),
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
    parsed = tool_calls.parse_gemini_openai_tool_response(content, body) if gemini_native_tools else tool_calls.parse_tool_calls_for_tools(content, body.get("tools"))
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
        search_sources.extend(grok_chat.extract_app_chat_search_sources(event))
        token, thinking = grok_chat.extract_app_chat_token(event)
        if not token:
            if grok_chat.is_app_chat_final_event(event):
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
    deduped_sources = grok_chat.dedupe_search_sources(search_sources)
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
        delta = grok_chat.extract_console_stream_delta(event)
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


def _consume_gemini_deltas(deltas: Iterator[str], output: queue.Queue[object]) -> None:
    try:
        for delta in deltas:
            output.put(delta)
    except Exception as exc:
        output.put(exc)
    finally:
        output.put(None)


def stream_gemini_chat_completion(body: dict[str, Any], spec, messages: list[dict[str, Any]], model: str) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    include_usage = stream_include_usage(body)
    content_parts: list[str] = []
    deltas = gemini_chat.chat_completion_deltas(body, spec, messages)
    has_image_input = any(has_image_message_content(message.get("content")) for message in messages if isinstance(message, dict))
    if has_image_input:
        output: queue.Queue[object] = queue.Queue()
        threading.Thread(target=_consume_gemini_deltas, args=(deltas, output), daemon=True).start()
        initial_content = "正在识别图片，请稍候…\n\n"
        content_parts.append(initial_content)
        yield stream_chunk(completion_chunk(model, {"role": "assistant", "content": initial_content}, None, completion_id, created), include_usage)
        while True:
            try:
                item = output.get(timeout=5)
            except queue.Empty:
                content_parts.append("\n")
                yield stream_chunk(completion_chunk(model, {"content": "\n"}, None, completion_id, created), include_usage)
                continue
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            delta_text = str(item)
            content_parts.append(delta_text)
            yield stream_chunk(completion_chunk(model, {"content": delta_text}, None, completion_id, created), include_usage)
    else:
        yield stream_chunk(completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created), include_usage)
        for delta_text in deltas:
            content_parts.append(delta_text)
            yield stream_chunk(completion_chunk(model, {"content": delta_text}, None, completion_id, created), include_usage)
    yield stream_chunk(completion_chunk(model, {}, "stop", completion_id, created), include_usage)
    if include_usage:
        yield completion_usage_chunk(model, completion_id, created, completion_usage(messages, model, "".join(content_parts)))


def stream_catpaw_chat_completion(
    body: dict[str, Any],
    messages: list[dict[str, Any]],
    model: str,
    include_usage: bool = False,
    usage_messages: list[dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream chat completion from CatPaw provider in OpenAI format."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_role = False
    content_parts: list[str] = []
    for delta_text in catpaw_chat.chat_completion_deltas(body=body, messages=messages, model=model):
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
            completion_usage(usage_messages or messages, model, "".join(content_parts)),
        )


_TOOL_MARKER_TOKENS = ("<tool_calls", "<tool_call", "<tool_name", "<function_call", "<invoke", "tool_call>")
_TOOL_MARKER_MAX = max(len(token) for token in _TOOL_MARKER_TOKENS)


def _streamable_tool_text(text: str) -> str:
    lowered = text.lower()
    starts = [lowered.find(token) for token in _TOOL_MARKER_TOKENS if lowered.find(token) >= 0]
    if starts:
        return text[:min(starts)]
    window = max(0, len(text) - _TOOL_MARKER_MAX + 1)
    for i in range(window, len(text)):
        if lowered[i] not in ("<", "t"):
            continue
        tail = lowered[i:]
        if any(token.startswith(tail) for token in _TOOL_MARKER_TOKENS):
            return text[:i]
    return text


def stream_catpaw_tool_chat_completion(
    body: dict[str, Any],
    messages: list[dict[str, Any]],
    model: str,
    include_usage: bool = False,
) -> Iterator[dict[str, Any]]:
    """Stream CatPaw tool-capable chat completion with real streaming.

    Streams text deltas in real-time. If tool calls are detected in the
    accumulated text, emits proper ``delta.tool_calls`` chunks; otherwise
    emits plain ``delta.content`` chunks.  This avoids the blocking
    ``chat_completion`` call that the previous path used.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    full_text_parts: list[str] = []
    tool_detected = False
    sent_role = False
    streamed_text = ""

    for delta_text in catpaw_chat.chat_completion_deltas(body=body, messages=messages, model=model):
        full_text_parts.append(delta_text)
        accumulated = "".join(full_text_parts)
        visible = _streamable_tool_text(accumulated)

        if len(visible) < len(accumulated):
            tool_detected = True
        if visible.startswith(streamed_text) and len(visible) > len(streamed_text):
            piece = visible[len(streamed_text):]
            streamed_text = visible
            if not sent_role:
                sent_role = True
                yield stream_chunk(completion_chunk(model, {"role": "assistant", "content": piece}, None, completion_id, created), include_usage)
            else:
                yield stream_chunk(completion_chunk(model, {"content": piece}, None, completion_id, created), include_usage)
        if tool_detected:
            continue

    full_text = "".join(full_text_parts)
    parsed = tool_calls.parse_tool_calls_for_tools(full_text, body.get("tools"))

    if parsed.calls:
        if not sent_role:
            sent_role = True
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
                model, completion_id, created,
                completion_usage(messages, model, full_text),
            )
        return

    retry_parsed = _retry_catpaw_tool_call(body, messages, model, full_text)
    if retry_parsed.calls:
        if not sent_role:
            sent_role = True
            yield stream_chunk(completion_chunk(model, {"role": "assistant", "content": None}, None, completion_id, created), include_usage)
        for index, call in enumerate(retry_parsed.calls):
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
        return

    plain = tool_calls.strip_tool_markup(full_text) if parsed.saw_tool_syntax else full_text
    if parsed.saw_tool_syntax and not plain.strip() and full_text.strip():
        logger.warning({"event": "catpaw_tool_parse_empty", "text": full_text[:500]})
        plain = full_text
    if visible := plain[len(streamed_text):] if plain.startswith(streamed_text) else plain:
        if not sent_role:
            sent_role = True
            yield stream_chunk(completion_chunk(model, {"role": "assistant", "content": visible}, None, completion_id, created), include_usage)
        else:
            yield stream_chunk(completion_chunk(model, {"content": visible}, None, completion_id, created), include_usage)
    if not sent_role:
        yield stream_chunk(completion_chunk(model, {"role": "assistant", "content": ""}, None, completion_id, created), include_usage)
    yield stream_chunk(completion_chunk(model, {}, "stop", completion_id, created), include_usage)
    if include_usage:
        yield completion_usage_chunk(
            model, completion_id, created,
            completion_usage(messages, model, plain),
        )


def _retry_catpaw_tool_call(body: dict[str, Any], messages: list[dict[str, Any]], model: str, text: str) -> tool_calls.ToolParseResult:
    if not _looks_like_unfinished_tool_intent(text):
        return tool_calls.ToolParseResult()
    retry_messages = [
        *messages,
        {
            "role": "user",
            "content": "You said you would perform the file/command task but did not call a tool. Output ONLY the next Claude Code tool call now, using the required tool schema. No prose.",
        },
    ]
    retry_text = "".join(catpaw_chat.chat_completion_deltas(body=body, messages=retry_messages, model=model))
    parsed = tool_calls.parse_tool_calls_for_tools(retry_text, body.get("tools"))
    if parsed.calls:
        return parsed
    logger.warning({"event": "catpaw_tool_retry_failed", "text": retry_text[:500]})
    return tool_calls.ToolParseResult()


def _looks_like_unfinished_tool_intent(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    return any(marker in text for marker in ("我将", "现在我将", "准备", "接下来", "will", "going to")) and any(
        marker in text for marker in ("创建", "写", "保存", "运行", "文件", "目录", "folder", "file", "write", "run", "create")
    )


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
    raw_messages = grok_chat.strip_search_sources_from_messages(chat_messages_from_body(body))
    if tool_calls.has_function_tools(body):
        injected = tool_calls.inject_tool_prompt(
            raw_messages,
            body.get("tools"),
            body.get("tool_choice"),
            body.get("parallel_tool_calls"),
        )
        return normalize_messages(injected), normalize_messages(raw_messages)
    messages = normalize_text_messages(normalize_messages(raw_messages))
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
    spec = resolve_model(model)
    if spec.provider == GPT_PROVIDER and not tool_calls.has_function_tools(body):
        messages = maybe_attach_long_text_messages(messages, model)
    return model, messages, original_messages


def parsed_chat_tool_response(body: dict[str, Any], model: str, content: str, messages: list[dict[str, Any]], gemini_native_tools: bool = False) -> dict[str, Any] | None:
    names = tool_calls.tool_names(body.get("tools"))
    if not names:
        return None
    parsed = tool_calls.parse_gemini_openai_tool_response(content, body) if gemini_native_tools else tool_calls.parse_tool_calls_for_tools(content, body.get("tools"))
    if not parsed.calls:
        return None
    return tool_calls.chat_tool_call_response(model, parsed.calls, messages=messages)


def image_result_content(result: dict[str, Any]) -> str:
    data = result.get("data")
    if isinstance(data, list) and data:
        return build_chat_image_markdown_content(result)
    return str(result.get("message") or "Image generation completed.")


def has_chat_image_input(body: dict[str, Any]) -> bool:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return False
    return any(isinstance(message, dict) and has_image_message_content(message.get("content")) for message in messages)


def should_route_gemini_vision_request_to_chat(spec: Any, body: dict[str, Any]) -> bool:
    # 这里处理的是 Gemini 普通聊天模型的“图片理解/识图”请求。
    # 它与 gemini-image / gemini-image-pro 图片生成模型不同：
    # - 普通 Gemini 聊天模型带图片输入时，应保留在 /v1/chat/completions 里做视觉理解。
    # - gemini-image / gemini-image-pro 已在 reject_gemini_image_model_in_chat() 中禁止走 chat，只允许 /v1/images/*。
    return spec.provider == GEMINI_PROVIDER and has_chat_image_input(body)


def reject_gemini_image_model_in_chat(spec: Any) -> None:
    if spec.provider == GEMINI_PROVIDER and getattr(spec, "capability", None) == "image":
        raise HTTPException(
            status_code=400,
            detail={"error": "gemini-image and gemini-image-pro can only be used with /v1/images/* endpoints"},
        )


def image_chat_response(body: dict[str, Any]) -> dict[str, Any]:
    model, prompt, n, images = chat_image_args(body)
    spec = resolve_model(model)
    if spec.provider == GROK_PROVIDER:
        if images:
            raise ImageGenerationError("Grok image chat does not support image input", status_code=400, error_type="invalid_request_error", code="unsupported_model", param="model")
        result = collect_image_outputs(image_generation_outputs(spec, None, body=body, prompt=prompt, n=n))
        return completion_response(model, image_result_content(result), int(result.get("created") or 0) or None)
    request = ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        response_format="b64_json",
        images=encode_images(images) or None,
    )
    result = collect_image_outputs(image_generation_outputs(spec, request, body=body, prompt=prompt, n=n))
    return completion_response(model, image_result_content(result), int(result.get("created") or 0) or None)


def image_chat_events(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    model, prompt, n, images = chat_image_args(body)
    spec = resolve_model(model)
    if spec.provider == GROK_PROVIDER:
        if images:
            raise ImageGenerationError("Grok image chat does not support image input", status_code=400, error_type="invalid_request_error", code="unsupported_model", param="model")
        yield from stream_image_chat_completion(image_generation_outputs(spec, None, body=body, prompt=prompt, n=n), model)
        return
    request = ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        response_format="b64_json",
        images=encode_images(images) or None,
    )
    image_outputs = image_generation_outputs(spec, request, body=body, prompt=prompt, n=n)
    yield from stream_image_chat_completion(image_outputs, model)


def _consume_image_outputs(image_outputs: Iterable[ImageOutput], output: queue.Queue[object]) -> None:
    try:
        for item in image_outputs:
            output.put(item)
    except Exception as exc:
        output.put(exc)
    finally:
        output.put(None)


def stream_image_chat_completion(image_outputs: Iterable[ImageOutput], model: str) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    sent_text = "正在生成图片，请稍候…\n\n"
    yield completion_chunk(model, {"role": "assistant", "content": sent_text}, None, completion_id, created)
    output_queue: queue.Queue[object] = queue.Queue()
    threading.Thread(target=_consume_image_outputs, args=(image_outputs, output_queue), daemon=True).start()
    while True:
        try:
            output = output_queue.get(timeout=5)
        except queue.Empty:
            yield completion_chunk(model, {"content": "\n"}, None, completion_id, created)
            continue
        if output is None:
            break
        if isinstance(output, Exception):
            raise output
        if not isinstance(output, ImageOutput):
            continue
        content = ""
        if output.kind == "progress":
            content = output.text
            sent_text += content
        elif output.kind == "result":
            content = build_chat_image_markdown_content({"data": output.data})
        elif output.kind == "message":
            content = output.text[len(sent_text):] if output.text.startswith(sent_text) else output.text
        if content:
            yield completion_chunk(model, {"content": content}, None, completion_id, created)
    yield completion_chunk(model, {}, "stop", completion_id, created)


def non_stream_text_chat_response(body: dict[str, Any], model: str, messages: list[dict[str, Any]], original_messages: list[dict[str, Any]], spec: Any) -> dict[str, Any]:
    if spec.provider == GROK_PROVIDER:
        if grok_chat.is_app_chat_model(spec):
            response = grok_chat.chat_completion(body, spec, messages)
            raw_sources = response.get("search_sources")
            search_sources = cast(list[dict[str, str]], raw_sources) if isinstance(raw_sources, list) else []
            content = str(response.get("content", ""))
            if config.show_search_sources:
                content = grok_chat.append_search_sources_suffix(content, search_sources)
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
    if spec.provider == CATPAW_PROVIDER:
        content = catpaw_chat.chat_completion(body=body, messages=messages, model=model)
        tool_response = parsed_chat_tool_response(body, model, content, messages)
        if tool_response:
            return tool_response
        return completion_response(model, content, messages=original_messages)
    if collect_text is not gpt_chat.collect_text:
        request = ConversationRequest(model=model, messages=messages)
        content = collect_text(text_backend(), request)
    else:
        content = gpt_chat.chat_completion(body, messages, model, backend=text_backend())
    tool_response = parsed_chat_tool_response(body, model, content, messages)
    if tool_response:
        return tool_response
    return completion_response(model, content, messages=original_messages)


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    if body.get("stream"):
        model, messages, original_messages = text_chat_parts(body)
        spec = resolve_model(model)
        reject_gemini_image_model_in_chat(spec)
        if is_image_chat_request(body) and not should_route_gemini_vision_request_to_chat(spec, body):
            return image_chat_events(body)
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
            if spec.provider == CATPAW_PROVIDER:
                return stream_catpaw_tool_chat_completion(
                    body,
                    messages,
                    model,
                    include_usage=stream_include_usage(body),
                )
            return stream_tool_text_chat_completion(text_backend(), body, messages, model, stream_include_usage(body))
        if spec.provider == CATPAW_PROVIDER:
            return stream_catpaw_chat_completion(body, messages, model, stream_include_usage(body), original_messages)
        if spec.provider == GROK_PROVIDER:
            return stream_grok_chat_completion(body, spec, messages, model)
        if spec.provider == GEMINI_PROVIDER:
            return stream_gemini_chat_completion(body, spec, messages, model)
        return stream_text_chat_completion(text_backend(), messages, model, stream_include_usage(body), original_messages)
    model, messages, original_messages = text_chat_parts(body)
    spec = resolve_model(model)
    reject_gemini_image_model_in_chat(spec)
    if is_image_chat_request(body) and not should_route_gemini_vision_request_to_chat(spec, body):
        return image_chat_response(body)
    if is_cacheable_text_request(body, stream=False):
        key = cache_key(body, messages, stream=False)
        return chat_completion_cache.get_or_compute(
            key,
            lambda: non_stream_text_chat_response(body, model, messages, original_messages, spec),
        )
    return non_stream_text_chat_response(body, model, messages, original_messages, spec)
