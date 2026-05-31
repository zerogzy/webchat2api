from __future__ import annotations

from typing import Iterator

from fastapi import HTTPException

from services.protocol.conversation import ConversationRequest, ImageOutput
from services.providers.base import ModelSpec


def unsupported_image_error() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "error": "Gemini image chat and image modalities are unsupported in this MVP",
            "type": "invalid_request_error",
            "code": "unsupported_model",
            "param": "model",
        },
    )


def generation_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    raise unsupported_image_error()


def edit_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    raise unsupported_image_error()


def response_image_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    raise unsupported_image_error()
