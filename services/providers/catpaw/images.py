"""CatPaw image adapter stub.

CatPaw is registered with the "chat" capability only (no image generation). This
module exists so `image_adapter("catpaw")` imports cleanly; all entry points raise
an unsupported error. Image *input* (vision) is handled in chat via multiModalContent.
"""
from __future__ import annotations

from typing import Iterator

from services.providers.base import ConversationRequest, ImageGenerationError, ImageOutput, ModelSpec


def unsupported_image_error() -> ImageGenerationError:
    return ImageGenerationError(
        "CatPaw does not support image generation",
        status_code=400,
        error_type="invalid_request_error",
        code="unsupported_model",
        param="model",
    )


def generation_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    raise unsupported_image_error()


def edit_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    raise unsupported_image_error()


def response_image_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    raise unsupported_image_error()
