from __future__ import annotations

import unittest
from typing import Any, cast
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_tiktoken_stub()

from services.models import resolve_model
from services.openai_backend_api import OpenAIBackendAPI
from services.protocol.conversation import ConversationRequest, ImageGenerationError, ImageOutput, conversation_events
from services.providers.gpt import images as gpt_images


class FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.status_code = 200
        self.text = ""
        self.headers = {}
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeUploadSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.headers: dict[str, str] = {}

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("post", url, kwargs.get("json") or kwargs.get("data")))
        if url.endswith("/backend-api/files"):
            return FakeResponse({"file_id": "file-123", "upload_url": "https://upload.example/blob"})
        return FakeResponse({})

    def put(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("put", url, {"headers": kwargs.get("headers"), "data": kwargs.get("data")}))
        return FakeResponse({})


class GPTImageProviderTests(unittest.TestCase):
    def test_upload_file_uses_three_step_file_flow(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.base_url = "https://chatgpt.com"
        backend.user_agent = "test-agent"
        backend.session = cast(Any, FakeUploadSession())
        fake_session = backend.session

        result = backend._upload_file(b"hello", "context.md", "text/markdown")

        self.assertEqual(result, {"file_id": "file-123", "file_name": "context.md", "file_size": 5, "mime_type": "text/markdown", "use_case": "multimodal", "file_token_size": 0, "non_library_my_files_injest_upload": False})
        self.assertEqual([call[0] for call in fake_session.calls], ["post", "put", "post", "post"])
        self.assertEqual(fake_session.calls[0][2], {"file_name": "context.md", "file_size": 5, "use_case": "multimodal"})
        self.assertEqual(fake_session.calls[1][2]["headers"]["Content-Type"], "text/markdown")
        self.assertEqual(fake_session.calls[2][2], "{}")
        self.assertEqual(fake_session.calls[3][2]["file_id"], "file-123")

    def test_image_alias_routes_to_upstream_model_and_preserves_response_model(self) -> None:
        seen_models: list[str] = []

        def fake_stream(request: ConversationRequest):
            seen_models.append(request.model)
            yield ImageOutput(kind="progress", model=request.model, index=1, total=1, text="working")
            yield ImageOutput(kind="result", model=request.model, index=1, total=1, data=[])

        request = ConversationRequest(prompt="draw", model="team-codex-gpt-image-2")
        spec = resolve_model("team-codex-gpt-image-2")
        with mock.patch.object(gpt_images, "stream_image_outputs_with_pool", fake_stream):
            outputs = list(gpt_images.generation_outputs(request, spec))

        self.assertEqual(seen_models, ["codex-gpt-image-2"])
        self.assertEqual([output.model for output in outputs], ["team-codex-gpt-image-2", "team-codex-gpt-image-2"])

    def test_public_error_message_sanitizes_sensitive_upstream_details(self) -> None:
        cases = [
            "token_invalidated for alice@example.com",
            "Authorization: Bearer secret-token failed",
            "Traceback contains refresh_token=secret",
            "Cloudflare CF-Ray blocked upstream ChatGPT backend",
            "OpenAI-Sentinel-Chat-Requirements-Token leaked with X-Conduit-Token",
            "",
        ]

        for message in cases:
            with self.subTest(message=message):
                self.assertEqual(gpt_images.public_image_error_message(message), "image generation failed")

    def test_public_error_message_preserves_safe_policy_message(self) -> None:
        self.assertEqual(
            gpt_images.public_image_error_message("Image generation was rejected by upstream policy."),
            "Image generation was rejected by upstream policy.",
        )

    def test_public_error_message_maps_tls_failure(self) -> None:
        self.assertEqual(
            gpt_images.public_image_error_message("curl: (35) TLS connect error: OPENSSL_internal"),
            "upstream image connection failed, please retry later",
        )

    def test_outputs_convert_upstream_image_generation_error(self) -> None:
        def fake_stream(request: ConversationRequest):
            raise ImageGenerationError(
                "Authorization: Bearer secret-token failed for alice@example.com",
                status_code=400,
                error_type="invalid_request_error",
                code="content_policy_violation",
                param="prompt",
            )
            yield

        request = ConversationRequest(prompt="draw", model="team-codex-gpt-image-2")
        spec = resolve_model("team-codex-gpt-image-2")
        with mock.patch.object(gpt_images, "stream_image_outputs_with_pool", fake_stream):
            with self.assertRaises(ImageGenerationError) as raised:
                list(gpt_images.generation_outputs(request, spec))

        exc = raised.exception
        self.assertEqual(str(exc), "image generation failed")
        self.assertEqual(exc.status_code, 400)
        self.assertEqual(exc.error_type, "invalid_request_error")
        self.assertEqual(exc.code, "content_policy_violation")
        self.assertEqual(exc.param, "prompt")

    def test_codex_upstream_model_remains_image_mode_for_conversation_events(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeBackend:
            def stream_conversation(self, **kwargs):
                calls.append(kwargs)
                return iter(())

        list(conversation_events(cast(OpenAIBackendAPI, FakeBackend()), prompt="draw", model="codex-gpt-image-2", images=["image-data"]))

        self.assertEqual(calls[0]["model"], "codex-gpt-image-2")
        self.assertEqual(calls[0]["images"], ["image-data"])
        self.assertEqual(calls[0]["system_hints"], ["picture_v2"])


if __name__ == "__main__":
    unittest.main()
