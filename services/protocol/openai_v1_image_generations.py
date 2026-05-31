from __future__ import annotations

from typing import Any, Iterator

from services.providers.base import ConversationRequest
from services.providers.registry import image_generation_outputs, resolve_model
from services.protocol.conversation import collect_image_outputs, stream_image_chunks


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    prompt = str(body.get("prompt") or "")
    model = str(body.get("model") or "gpt-image-2")
    n = int(body.get("n") or 1)
    size = body.get("size")
    response_format = str(body.get("response_format") or "b64_json")
    base_url = str(body.get("base_url") or "") or None
    spec = resolve_model(model)
    request = ConversationRequest(
        prompt=prompt,
        model=model,
        n=n,
        size=size,
        response_format=response_format,
        base_url=base_url,
        message_as_error=True,
    )
    outputs = image_generation_outputs(spec, request, body=body, prompt=prompt, n=n)
    if body.get("stream"):
        return stream_image_chunks(outputs)
    return collect_image_outputs(outputs)
