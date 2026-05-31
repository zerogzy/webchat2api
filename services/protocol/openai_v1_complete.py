from __future__ import annotations

import time
import uuid
from typing import Any, Iterator

from fastapi import HTTPException

from services.providers.base import ConversationRequest, GEMINI_PROVIDER
from services.providers.registry import chat_adapter, resolve_model
from services.protocol.conversation import collect_text, count_text_tokens, text_backend
from services.protocol.openai_v1_chat_complete import stream_text_chat_completion


gemini_chat = chat_adapter(GEMINI_PROVIDER)


def _prompt_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if str(item).strip()).strip()
    return str(value or "").strip()


def _messages(prompt: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": prompt}]


def _usage(prompt: str, content: str, model: str) -> dict[str, int]:
    prompt_tokens = count_text_tokens(prompt, model)
    completion_tokens = count_text_tokens(content, model)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def completion_response(model: str, prompt: str, content: str) -> dict[str, Any]:
    return {
        "id": f"cmpl-{uuid.uuid4().hex}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "text": content,
            "index": 0,
            "logprobs": None,
            "finish_reason": "stop",
        }],
        "usage": _usage(prompt, content, model),
    }


def _text_completion_chunks(chat_chunks: Iterator[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    completion_id = f"cmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    for chunk in chat_chunks:
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta") if isinstance(choice, dict) else {}
        delta = delta if isinstance(delta, dict) else {}
        text = str(delta.get("content") or "")
        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        if not text and finish_reason is None:
            continue
        yield {
            "id": completion_id,
            "object": "text_completion.chunk",
            "created": created,
            "model": str(chunk.get("model") or ""),
            "choices": [{
                "text": text,
                "index": 0,
                "logprobs": None,
                "finish_reason": finish_reason,
            }],
        }


def _gemini_completion_chunks(body: dict[str, Any], model: str, prompt: str) -> Iterator[dict[str, Any]]:
    from services.protocol.openai_v1_chat_complete import stream_gemini_chat_completion

    spec = resolve_model(model)
    return stream_gemini_chat_completion(body, spec, _messages(prompt), model)


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    model = str(body.get("model") or "auto").strip() or "auto"
    prompt = _prompt_text(body.get("prompt"))
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": "prompt is required"})
    spec = resolve_model(model)
    messages = _messages(prompt)
    if spec.provider == GEMINI_PROVIDER:
        if body.get("stream"):
            return _text_completion_chunks(_gemini_completion_chunks(body, model, prompt))
        completion = gemini_chat.chat_completion(body, spec, messages)
        return completion_response(model, prompt, str(getattr(completion, "content", "")))
    if spec.provider != "gpt":
        raise HTTPException(status_code=400, detail={"error": "/v1/complete currently supports GPT and Gemini text models only"})
    if body.get("stream"):
        return _text_completion_chunks(stream_text_chat_completion(text_backend(), messages, model))
    content = collect_text(text_backend(), ConversationRequest(model=model, messages=messages))
    return completion_response(model, prompt, content)
