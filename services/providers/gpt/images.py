from __future__ import annotations

from dataclasses import replace
import re
from typing import Iterator

from services.protocol.conversation import ConversationRequest, ImageGenerationError, ImageOutput, stream_image_outputs_with_pool
from services.providers.base import ModelSpec


_IMAGE_ERROR_FALLBACK = "image generation failed"
_IMAGE_CONNECTION_ERROR = "upstream image connection failed, please retry later"
_SENSITIVE_IMAGE_ERROR_MARKERS = (
    "access_token",
    "authorization",
    "bearer ",
    "id_token",
    "oauth",
    "refresh_token",
    "session token",
    "set-cookie",
    "traceback",
)
_CONNECTION_IMAGE_ERROR_MARKERS = (
    "curl: (35)",
    "openssl_internal",
    "tls connect error",
)
_EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_TOKEN_INVALID_RE = re.compile(r"token_(?:invalidated|revoked)|authentication token has been invalidated|invalidated oauth token", re.IGNORECASE)


def public_image_error_message(message: object) -> str:
    text = str(message or "").strip()
    if not text:
        return _IMAGE_ERROR_FALLBACK
    lower = text.lower()
    if any(marker in lower for marker in _CONNECTION_IMAGE_ERROR_MARKERS):
        return _IMAGE_CONNECTION_ERROR
    if _TOKEN_INVALID_RE.search(text) or _EMAIL_RE.search(text) or any(marker in lower for marker in _SENSITIVE_IMAGE_ERROR_MARKERS):
        return _IMAGE_ERROR_FALLBACK
    return text


def public_image_error(exc: ImageGenerationError) -> ImageGenerationError:
    return ImageGenerationError(
        public_image_error_message(str(exc)),
        status_code=exc.status_code,
        error_type=exc.error_type,
        code=exc.code,
        param=exc.param,
    )


def _upstream_request(request: ConversationRequest, spec: ModelSpec) -> ConversationRequest:
    upstream_model = spec.upstream_model or spec.id
    if upstream_model == request.model:
        return request
    return replace(request, model=upstream_model)


def _image_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    try:
        for output in stream_image_outputs_with_pool(_upstream_request(request, spec)):
            if output.model == request.model:
                yield output
            else:
                yield replace(output, model=request.model)
    except ImageGenerationError as exc:
        raise public_image_error(exc) from exc


def generation_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    yield from _image_outputs(request, spec)


def edit_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    yield from _image_outputs(request, spec)


def response_image_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    yield from _image_outputs(request, spec)
