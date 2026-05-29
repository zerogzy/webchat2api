from __future__ import annotations

from typing import Any, Iterator

from services.protocol.conversation import ImageGenerationError, ImageOutput
from services.providers.base import ModelSpec
from services.providers import grok


def generation_outputs(body: dict[str, Any], spec: ModelSpec, prompt: str, n: int) -> Iterator[ImageOutput]:
    yield from grok.app_chat_image_outputs(body, spec, prompt, n)


def edit_outputs(
    body: dict[str, Any],
    spec: ModelSpec,
    prompt: str,
    images: list[tuple[bytes, str, str]],
    n: int,
    size: str | None,
) -> Iterator[ImageOutput]:
    if spec.capability != "image_edit":
        raise ImageGenerationError(
            f"unsupported Grok image model: {spec.id}",
            status_code=400,
            error_type="invalid_request_error",
            code="unsupported_model",
            param="model",
        )
    yield from grok.app_chat_image_edit_outputs(body, spec, prompt, images, n, size)
