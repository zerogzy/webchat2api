from __future__ import annotations

from dataclasses import replace
from typing import Iterator

from services.providers.base import ConversationRequest, ImageGenerationError, ImageOutput, ModelSpec
from services.providers.gemini import api_client
from services.providers.gemini.models import GEMINI_IMAGE_MODEL_IDS
from services.providers.gpt.runtime import format_image_result


def _unsupported_image_error(spec: ModelSpec) -> ImageGenerationError:
    return ImageGenerationError(
        f"unsupported Gemini image model: {spec.id}",
        status_code=400,
        error_type="invalid_request_error",
        code="unsupported_model",
        param="model",
    )


def _public_error(exc: api_client.GeminiApiError) -> ImageGenerationError:
    return ImageGenerationError(str(exc), status_code=exc.status_code, code=exc.code)


def unsupported_image_error() -> ImageGenerationError:
    return _unsupported_image_error(ModelSpec("gemini", "gemini", "google"))


def _generation_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    if spec.id not in GEMINI_IMAGE_MODEL_IDS or spec.capability != "image":
        raise _unsupported_image_error(spec)

    from services.account_service import account_service

    access_token = account_service.get_text_access_token(provider="gemini")
    if not access_token:
        raise ImageGenerationError("no available Gemini account", status_code=503, code="no_available_account")
    account = account_service.get_account(access_token, provider="gemini") or {"access_token": access_token, "provider": "gemini"}
    try:
        images, updates = api_client.generate_images(account, spec, request.prompt, request.n, request.size)
    except api_client.GeminiApiError as exc:
        raise _public_error(exc) from exc
    if updates:
        account_service.update_account(access_token, updates, provider="gemini")
    account_service.mark_text_used(access_token)
    result = format_image_result(
        [{"b64_json": item.b64_json, "revised_prompt": item.revised_prompt or request.prompt} for item in images],
        request.prompt,
        request.response_format,
        request.base_url,
    )
    yield ImageOutput(kind="result", model=spec.id, index=1, total=max(1, int(request.n or 1)), data=result.get("data", []))


def generation_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    yield from _generation_outputs(request, spec)


def edit_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    if spec.id not in GEMINI_IMAGE_MODEL_IDS or spec.capability != "image":
        raise _unsupported_image_error(replace(spec, capability="image_edit"))
    if not request.images:
        raise ImageGenerationError("image is required", status_code=400, code="missing_image")

    from services.account_service import account_service

    access_token = account_service.get_text_access_token(provider="gemini")
    if not access_token:
        raise ImageGenerationError("no available Gemini account", status_code=503, code="no_available_account")
    account = account_service.get_account(access_token, provider="gemini") or {"access_token": access_token, "provider": "gemini"}
    try:
        images, updates = api_client.edit_images(account, spec, request.prompt, request.images, request.n, request.size)
    except api_client.GeminiApiError as exc:
        raise _public_error(exc) from exc
    if updates:
        account_service.update_account(access_token, updates, provider="gemini")
    account_service.mark_text_used(access_token)
    result = format_image_result(
        [{"b64_json": item.b64_json, "revised_prompt": item.revised_prompt or request.prompt} for item in images],
        request.prompt,
        request.response_format,
        request.base_url,
    )
    yield ImageOutput(kind="result", model=spec.id, index=1, total=max(1, int(request.n or 1)), data=result.get("data", []))


def response_image_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    yield from _generation_outputs(request, spec)
