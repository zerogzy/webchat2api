from __future__ import annotations

from typing import Iterator

from services.providers.base import ConversationRequest, ImageGenerationError, ImageOutput, ModelSpec


def unsupported_image_error() -> ImageGenerationError:
    return ImageGenerationError(
        "CodeBuddy does not support image generation",
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
