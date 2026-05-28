from __future__ import annotations

import unittest
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_pydantic_stub, install_starlette_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_pydantic_stub()
install_starlette_stub()
install_tiktoken_stub()

import sys
from typing import Any, cast

FastAPI = cast(Any, getattr(sys.modules["fastapi"], "FastAPI"))
TestClient = cast(Any, getattr(sys.modules["fastapi.testclient"], "TestClient"))

import api.ai as ai_module


X_API_KEY_HEADERS = {"x-api-key": "webchat2api"}


class OpenAIXApiKeyAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_patcher = mock.patch("services.log_service.log_service.add")
        self.log_patcher.start()
        self.addCleanup(self.log_patcher.stop)
        app = FastAPI()
        app.include_router(ai_module.create_router())
        self.client = TestClient(app)

    def test_models_accepts_x_api_key(self) -> None:
        with mock.patch.object(ai_module.openai_v1_models, "list_models", return_value={"object": "list", "data": []}) as list_models:
            response = self.client.get("/v1/models", headers=X_API_KEY_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        list_models.assert_called_once_with()

    def test_chat_completions_accepts_x_api_key(self) -> None:
        with mock.patch.object(ai_module.openai_v1_chat_complete, "handle", return_value={"ok": True}) as handle:
            response = self.client.post(
                "/v1/chat/completions",
                headers=X_API_KEY_HEADERS,
                json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
            )

        self.assertEqual(response.status_code, 200, response.text)
        handle.assert_called_once()

    def test_responses_accepts_x_api_key(self) -> None:
        with mock.patch.object(ai_module.openai_v1_response, "handle", return_value={"ok": True}) as handle:
            response = self.client.post(
                "/v1/responses",
                headers=X_API_KEY_HEADERS,
                json={"model": "auto", "input": "hi"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        handle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
