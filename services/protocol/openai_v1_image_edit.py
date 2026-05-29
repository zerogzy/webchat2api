from __future__ import annotations

from typing import Any, Iterator

from services.models import GEMINI_PROVIDER, GROK_PROVIDER, resolve_model
from services.providers.gemini import images as gemini_images
from services.providers.gpt import images as gpt_images
from services.providers.grok import images as grok_images
from services.protocol.conversation import (
    ConversationRequest,
    ImageGenerationError,
    collect_image_outputs,
    encode_images,
    stream_image_chunks,
)


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    prompt = str(body.get("prompt") or "")
    images = body.get("images") or []
    model = str(body.get("model") or "gpt-image-2")
    n = int(body.get("n") or 1)
    size = body.get("size")
    response_format = str(body.get("response_format") or "b64_json")
    base_url = str(body.get("base_url") or "") or None
    spec = resolve_model(model)
    if spec.provider == GROK_PROVIDER:
        outputs = grok_images.edit_outputs(body, spec, prompt, images, n, size)
        if body.get("stream"):
            return stream_image_chunks(outputs)
        return collect_image_outputs(outputs)
    encoded_images = encode_images(images)
    if not encoded_images:
        raise ImageGenerationError("image is required")
    request = ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        size=size,
        response_format=response_format,
        base_url=base_url,
        images=encoded_images,
        message_as_error=True,
    )
    if spec.provider == GEMINI_PROVIDER:
        outputs = gemini_images.edit_outputs(request, spec)
    else:
        outputs = gpt_images.edit_outputs(request, spec)
    if body.get("stream"):
        return stream_image_chunks(outputs)
    return collect_image_outputs(outputs)
