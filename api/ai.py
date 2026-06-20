from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api.image_inputs import parse_image_edit_request, read_image_sources
from api.support import require_identity, resolve_image_base_url
from services.content_filter import check_request, request_text
from services.log_service import LoggedCall
from services.protocol import (
    anthropic_v1_messages,
    openai_search,
    openai_v1_chat_complete,
    openai_v1_complete,
    openai_v1_image_edit,
    openai_v1_image_generations,
    openai_v1_models,
    openai_v1_response,
)


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=4)
    size: str | None = None
    response_format: str = "b64_json"
    history_disabled: bool = True
    stream: bool | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    prompt: str | None = None
    n: int | None = None
    stream: bool | None = None
    modalities: list[str] | None = None
    messages: list[dict[str, object]] | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-5-5"


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    prompt: str | list[str] = Field(..., min_length=1)
    stream: bool | None = None


class ResponseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    input: object | None = None
    tools: list[dict[str, object]] | None = None
    tool_choice: object | None = None
    stream: bool | None = None


class AnthropicMessageRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    messages: list[dict[str, object]] | None = None
    system: object | None = None
    stream: bool | None = None


async def filter_or_log(call: LoggedCall, text: str, *, ai_review: bool = True) -> None:
    try:
        await run_in_threadpool(check_request, text, ai_review=ai_review)
    except HTTPException as exc:
        call.log("调用失败", status="failed", error=str(exc.detail))
        raise


def _sse_image_keepalive(handler: Callable[[dict[str, Any]], dict[str, Any]], payload: dict[str, Any], start_message: str) -> Iterator[str]:
    result_queue: queue.Queue[object] = queue.Queue(maxsize=1)

    def run_handler() -> None:
        try:
            result_queue.put(handler(payload))
        except Exception as exc:
            result_queue.put(exc)

    threading.Thread(target=run_handler, daemon=True).start()
    yield f"data: {json.dumps({'type': 'progress', 'message': start_message}, ensure_ascii=False)}\n\n"
    while True:
        try:
            item = result_queue.get(timeout=5)
            break
        except queue.Empty:
            yield f"data: {json.dumps({'type': 'progress', 'message': ''}, ensure_ascii=False)}\n\n"
    if isinstance(item, Exception):
        error = {
            "error": {
                "message": str(item),
                "type": "server_error",
                "param": None,
                "code": "upstream_error",
            }
        }
        yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
    else:
        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


async def _maybe_stream_image_response(handler: Callable[[dict[str, Any]], dict[str, Any]], payload: dict[str, Any], start_message: str):
    if payload.get("stream"):
        return StreamingResponse(_sse_image_keepalive(handler, payload, start_message), media_type="text/event-stream")
    return await run_in_threadpool(handler, payload)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/openai/v1/models", include_in_schema=False)
    @router.get("/v1/models")
    async def list_models(
            authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ):
        require_identity(authorization or (f"Bearer {x_api_key}" if x_api_key else None))
        try:
            return await run_in_threadpool(openai_v1_models.list_models)
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.get("/openai/v1/models/{model_id}", include_in_schema=False)
    @router.get("/v1/models/{model_id}")
    async def retrieve_model(
            model_id: str,
            authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ):
        require_identity(authorization or (f"Bearer {x_api_key}" if x_api_key else None))
        return await run_in_threadpool(openai_v1_models.get_model, model_id)

    @router.post("/v1/images/generations")
    async def generate_images(
            body: ImageGenerationRequest,
            request: Request,
            authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        payload["base_url"] = resolve_image_base_url(request)
        call = LoggedCall(identity, "/v1/images/generations", body.model, "文生图", request_text=body.prompt)
        await filter_or_log(call, body.prompt)
        if payload.get("stream"):
            return await _maybe_stream_image_response(openai_v1_image_generations.handle, payload, "正在生成图片，请稍候…")
        return await call.run(openai_v1_image_generations.handle, payload)

    @router.post("/v1/images/edits")
    async def edit_images(
            request: Request,
            authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload, image_sources = await parse_image_edit_request(request)
        prompt = str(payload["prompt"])
        model = str(payload["model"])
        call = LoggedCall(identity, "/v1/images/edits", model, "图生图", request_text=prompt)
        await filter_or_log(call, prompt)
        payload["images"] = await read_image_sources(image_sources)
        payload["base_url"] = resolve_image_base_url(request)
        return await _maybe_stream_image_response(openai_v1_image_edit.handle, payload, "正在编辑图片，请稍候…")

    @router.post("/openai/v1/chat/completions", include_in_schema=False)
    @router.post("/v1/chat/completions")
    async def create_chat_completion(
            body: ChatCompletionRequest,
            authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ):
        identity = require_identity(authorization or (f"Bearer {x_api_key}" if x_api_key else None))
        payload = body.model_dump(mode="python")
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("prompt"), payload.get("messages"))
        call = LoggedCall(identity, "/v1/chat/completions", model, "文本生成", request_text=request_preview)
        has_image_input = openai_v1_chat_complete.has_chat_image_input(payload)
        await filter_or_log(call, request_preview, ai_review=not has_image_input)
        return await call.run(openai_v1_chat_complete.handle, payload)

    @router.post("/v1/search")
    async def create_search(
            body: SearchRequest,
            authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ):
        identity = require_identity(authorization or (f"Bearer {x_api_key}" if x_api_key else None))
        payload = body.model_dump(mode="python")
        prompt = str(payload.get("prompt") or "")
        model = str(payload.get("model") or "gpt-5-5")
        call = LoggedCall(identity, "/v1/search", model, "Search", request_text=prompt)
        await filter_or_log(call, prompt)
        return await call.run(openai_search.handle, payload)

    @router.post("/v1/completions")
    @router.post("/v1/complete")
    async def create_completion(
            body: CompletionRequest,
            authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ):
        identity = require_identity(authorization or (f"Bearer {x_api_key}" if x_api_key else None))
        payload = body.model_dump(mode="python")
        model = str(payload.get("model") or "auto")
        prompt = payload.get("prompt")
        request_preview = request_text(prompt)
        call = LoggedCall(identity, "/v1/complete", model, "Completion", request_text=request_preview)
        await filter_or_log(call, request_preview)
        return await call.run(openai_v1_complete.handle, payload)

    @router.post("/v1/responses")
    async def create_response(
            body: ResponseCreateRequest,
            authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ):
        identity = require_identity(authorization or (f"Bearer {x_api_key}" if x_api_key else None))
        payload = body.model_dump(mode="python")
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("input"), payload.get("instructions"))
        call = LoggedCall(identity, "/v1/responses", model, "Responses", request_text=request_preview)
        await filter_or_log(call, request_preview)
        return await call.run(openai_v1_response.handle, payload)

    @router.post("/claude/v1/messages", include_in_schema=False)
    @router.post("/v1/messages")
    async def create_message(
            body: AnthropicMessageRequest,
            request: Request,
            authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None, alias="x-api-key"),
            anthropic_version: str | None = Header(default=None, alias="anthropic-version"),
    ):
        identity = require_identity(authorization or (f"Bearer {x_api_key}" if x_api_key else None))
        payload = body.model_dump(mode="python")
        headers = {str(k).lower(): str(v) for k, v in request.headers.items()}
        payload["_request_headers"] = headers
        _debug_dump_anthropic_headers(headers)
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("system"), payload.get("messages"), payload.get("tools"))
        call = LoggedCall(identity, "/v1/messages", model, "Messages", request_text=request_preview)
        await filter_or_log(call, request_preview)
        return await call.run(anthropic_v1_messages.handle, payload, sse="anthropic")

    @router.post("/claude/v1/messages/count_tokens", include_in_schema=False)
    @router.post("/v1/messages/count_tokens")
    async def count_message_tokens_endpoint(
            body: AnthropicMessageRequest,
            authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None, alias="x-api-key"),
            anthropic_version: str | None = Header(default=None, alias="anthropic-version"),
    ):
        require_identity(authorization or (f"Bearer {x_api_key}" if x_api_key else None))
        payload = body.model_dump(mode="python")
        return await run_in_threadpool(anthropic_v1_messages.count_tokens, payload)

    return router


def _debug_dump_anthropic_headers(headers: dict[str, str]) -> None:
    path = os.environ.get("CATPAW_DEBUG_HEADERS_DUMP")
    if not path:
        return
    try:
        redacted = {}
        for key, value in headers.items():
            if key in {"authorization", "x-api-key", "cookie"}:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = value
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": time.time(), "headers": redacted}, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass
