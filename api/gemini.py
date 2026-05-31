from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from api.support import require_identity
from services.content_filter import check_request
from services.gemini_deep_research import interaction_store, run_deep_research, stream_deep_research
from services.log_service import LoggedCall
from services.protocol import gemini_native


class GeminiRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


async def filter_or_log(call: LoggedCall, text: str) -> None:
    if not text:
        return
    try:
        await run_in_threadpool(check_request, text)
    except HTTPException as exc:
        call.log("调用失败", status="failed", error=str(exc.detail))
        raise


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/gemini/v1beta/models")
    async def list_models(
        authorization: str | None = Header(default=None),
    ):
        require_identity(authorization)
        return gemini_native.list_models()

    @router.post("/gemini/v1beta/models/{model}:generateContent")
    async def generate_content(
        model: str,
        body: GeminiRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        request_preview = gemini_native.request_text_from_body(payload)
        call = LoggedCall(identity, "/gemini/v1beta/models:generateContent", model, "Gemini文本生成", request_text=request_preview)
        await filter_or_log(call, request_preview)
        return await call.run(gemini_native.generate_content, model, payload)

    @router.post("/gemini/v1beta/models/{model}:streamGenerateContent")
    async def stream_generate_content(
        model: str,
        body: GeminiRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        request_preview = gemini_native.request_text_from_body(payload)
        call = LoggedCall(identity, "/gemini/v1beta/models:streamGenerateContent", model, "Gemini流式生成", request_text=request_preview)
        await filter_or_log(call, request_preview)

        first_event = await run_in_threadpool(lambda: next(gemini_native.stream_generate_content(model, payload)))

        def events():
            yield from gemini_native.sse_events(("message", item) for item in gemini_native.stream_generate_content(model, payload, first_event=first_event))
            yield "event: done\ndata: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @router.post("/gemini/v1beta/deepresearch")
    async def deepresearch(
        body: GeminiRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        query = str(payload.get("query") or payload.get("prompt") or "")
        call = LoggedCall(identity, "/gemini/v1beta/deepresearch", str(payload.get("model") or "gemini-2.5-pro"), "Gemini深度研究", request_text=query)
        await filter_or_log(call, query)
        return await call.run(run_deep_research, payload)

    @router.post("/gemini/v1beta/deepresearch/stream")
    async def deepresearch_stream(
        body: GeminiRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        query = str(payload.get("query") or payload.get("prompt") or "")
        call = LoggedCall(identity, "/gemini/v1beta/deepresearch/stream", str(payload.get("model") or "gemini-2.5-pro"), "Gemini深度研究流", request_text=query)
        await filter_or_log(call, query)
        return StreamingResponse(gemini_native.sse_events(stream_deep_research(payload)), media_type="text/event-stream")

    @router.post("/gemini/v1beta/interactions")
    async def create_interaction(
        body: GeminiRequest,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        query = str(payload.get("query") or payload.get("prompt") or "")
        call = LoggedCall(identity, "/gemini/v1beta/interactions", str(payload.get("model") or "gemini-2.5-pro"), "Gemini交互研究", request_text=query)
        await filter_or_log(call, query)
        if payload.get("stream") is True:
            return StreamingResponse(gemini_native.sse_events(stream_deep_research(payload)), media_type="text/event-stream")
        try:
            task = await run_in_threadpool(interaction_store.create, payload, owner_id=str(identity.get("id") or ""))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return JSONResponse(content=task, status_code=202)

    @router.get("/gemini/v1beta/interactions/{interaction_id}")
    async def get_interaction(
        interaction_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        task = await run_in_threadpool(interaction_store.get, interaction_id, str(identity.get("id") or ""))
        if task is None:
            raise HTTPException(status_code=404, detail={"error": "interaction not found"})
        return task

    return router
