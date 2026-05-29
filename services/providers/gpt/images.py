from __future__ import annotations

from typing import Iterator

from services.protocol.conversation import ConversationRequest, ImageOutput, stream_image_outputs_with_pool
from services.providers.base import ModelSpec


def generation_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    yield from stream_image_outputs_with_pool(request)


def edit_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    yield from stream_image_outputs_with_pool(request)


def response_image_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    yield from stream_image_outputs_with_pool(request)
