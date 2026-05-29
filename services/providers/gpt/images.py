from __future__ import annotations

from dataclasses import replace
from typing import Iterator

from services.protocol.conversation import ConversationRequest, ImageOutput, stream_image_outputs_with_pool
from services.providers.base import ModelSpec


def _upstream_request(request: ConversationRequest, spec: ModelSpec) -> ConversationRequest:
    upstream_model = spec.upstream_model or spec.id
    if upstream_model == request.model:
        return request
    return replace(request, model=upstream_model)


def _image_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    for output in stream_image_outputs_with_pool(_upstream_request(request, spec)):
        if output.model == request.model:
            yield output
        else:
            yield replace(output, model=request.model)


def generation_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    yield from _image_outputs(request, spec)


def edit_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    yield from _image_outputs(request, spec)


def response_image_outputs(request: ConversationRequest, spec: ModelSpec) -> Iterator[ImageOutput]:
    yield from _image_outputs(request, spec)
