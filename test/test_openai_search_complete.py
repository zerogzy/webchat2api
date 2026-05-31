from __future__ import annotations

import sys
import unittest
from typing import Any, cast
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_pydantic_stub, install_starlette_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pydantic_stub()
install_pybase64_stub()
install_starlette_stub()
install_tiktoken_stub()

FastAPI = cast(Any, getattr(sys.modules["fastapi"], "FastAPI"))
TestClient = cast(Any, getattr(sys.modules["fastapi.testclient"], "TestClient"))
HTTPException = cast(Any, getattr(sys.modules["fastapi"], "HTTPException"))

from api import ai as ai_api
from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import openai_search, openai_v1_complete
from services.providers import gemini as gemini_provider

AUTH_HEADERS = {"Authorization": "Bearer webchat2api"}
API_KEY_HEADERS = {"x-api-key": "webchat2api"}


def _client() -> Any:
    app = FastAPI()
    app.include_router(ai_api.create_router())
    return TestClient(app)


class OpenAISearchProtocolTests(unittest.TestCase):
    def test_search_protocol_uses_gpt_account_and_normalizes_sources(self) -> None:
        backend = mock.Mock()
        backend.search.return_value = {
            "conversation_id": "conv_1",
            "status": "finished_successfully",
            "answer": "Result text",
            "sources": [
                {"title": "Example", "url": "https://example.com", "snippet": "Snippet", "source_type": "webpage"},
                {"title": "Duplicate", "url": "https://example.com"},
                {"title": "Missing URL"},
            ],
            "assistant_message_id": "msg_1",
            "create_time": 12.5,
        }
        with (
            mock.patch.object(openai_search.account_service, "get_text_access_token", return_value="token") as get_token,
            mock.patch.object(openai_search.account_service, "mark_text_used") as mark_used,
            mock.patch.object(openai_search, "OpenAIBackendAPI", return_value=backend),
        ):
            result = openai_search.handle({"prompt": "latest news", "model": "gpt-5-5"})

        get_token.assert_called_once_with(provider="gpt")
        backend.search.assert_called_once_with("latest news", model="gpt-5-5")
        backend.close.assert_called_once()
        mark_used.assert_called_once_with("token")
        self.assertEqual(result["object"], "search.result")
        self.assertEqual(result["answer"], "Result text")
        self.assertEqual(result["sources"], [{"title": "Example", "url": "https://example.com", "snippet": "Snippet", "source_type": "webpage"}])
        self.assertNotIn("_account_email", result)
        self.assertNotIn("email", result)
        self.assertFalse(any(key.startswith("_account") for key in result))

    def test_search_protocol_rejects_missing_account(self) -> None:
        with mock.patch.object(openai_search.account_service, "get_text_access_token", return_value=""):
            with self.assertRaises(HTTPException) as caught:
                openai_search.handle({"prompt": "latest news"})

        self.assertEqual(caught.exception.status_code, 429)


class OpenAICompleteProtocolTests(unittest.TestCase):
    def test_complete_non_stream_maps_prompt_to_text_completion_payload(self) -> None:
        backend = object()
        with (
            mock.patch.object(openai_v1_complete, "text_backend", return_value=backend),
            mock.patch.object(openai_v1_complete, "collect_text", return_value="Completed text") as collect_text,
            mock.patch.object(openai_v1_complete, "count_text_tokens", side_effect=lambda text, model: len(str(text).split())),
        ):
            result = openai_v1_complete.handle({"model": "gpt-4o", "prompt": "Say hi"})

        collect_text.assert_called_once()
        request = collect_text.call_args.args[1]
        self.assertEqual(request.messages, [{"role": "user", "content": "Say hi"}])
        self.assertEqual(result["object"], "text_completion")
        self.assertEqual(result["choices"][0]["text"], "Completed text")
        self.assertEqual(result["usage"], {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4})

    def test_complete_stream_maps_chat_chunks_to_text_completion_chunks(self) -> None:
        chat_chunks = iter([
            {"model": "gpt-4o", "choices": [{"delta": {"role": "assistant", "content": "Hel"}, "finish_reason": None}]},
            {"model": "gpt-4o", "choices": [{"delta": {"content": "lo"}, "finish_reason": None}]},
            {"model": "gpt-4o", "choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])
        with (
            mock.patch.object(openai_v1_complete, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_complete, "stream_text_chat_completion", return_value=chat_chunks),
        ):
            chunks = list(cast(Any, openai_v1_complete.handle({"model": "gpt-4o", "prompt": "Say hi", "stream": True})))

        self.assertEqual([chunk["choices"][0]["text"] for chunk in chunks], ["Hel", "lo", ""])
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
        self.assertTrue(all(chunk["object"] == "text_completion.chunk" for chunk in chunks))

    def test_v1_completions_route_uses_legacy_completion_handler(self) -> None:
        client = _client()
        with (
            mock.patch.object(ai_api.LoggedCall, "log"),
            mock.patch.object(openai_v1_complete, "handle", return_value={"object": "text_completion", "choices": []}) as handle,
        ):
            response = client.post("/v1/completions", headers=AUTH_HEADERS, json={"model": "gpt-4o", "prompt": "Say hi"})

        self.assertEqual(response.status_code, 200, response.text)
        handle.assert_called_once()
        self.assertEqual(handle.call_args.args[0]["prompt"], "Say hi")

    def test_v1_complete_alias_remains_supported(self) -> None:
        backend = object()
        with (
            mock.patch.object(openai_v1_complete, "text_backend", return_value=backend),
            mock.patch.object(openai_v1_complete, "collect_text", return_value="Alias completed") as collect_text,
        ):
            result = openai_v1_complete.handle({"model": "gpt-4o", "prompt": "Say hi"})

        collect_text.assert_called_once()
        request = collect_text.call_args.args[1]
        self.assertEqual(request.messages, [{"role": "user", "content": "Say hi"}])
        self.assertEqual(result["object"], "text_completion")
        self.assertEqual(result["model"], "gpt-4o")
        self.assertEqual(result["choices"][0]["text"], "Alias completed")

    def test_complete_gemini_model_uses_gemini_provider_no_account_path(self) -> None:
        account_service = mock.Mock()
        account_service.get_text_access_token.return_value = ""
        with mock.patch.dict(sys.modules, {"services.account_service": mock.Mock(account_service=account_service)}), \
             self.assertRaises(HTTPException) as caught:
            openai_v1_complete.handle({"model": "gemini-2.5-pro", "prompt": "Say hi"})

        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, {"error": "no available Gemini account"})
        account_service.get_text_access_token.assert_called_once_with(provider="gemini")

    def test_complete_gemini_model_can_be_mocked_without_gpt_backend(self) -> None:
        with mock.patch.object(gemini_provider, "chat_completion", return_value=gemini_provider.GeminiCompletion("Gemini complete")) as chat_completion:
            result = openai_v1_complete.handle({"model": "gemini-2.5-pro", "prompt": "Say hi"})

        called_body, called_spec, called_messages = chat_completion.call_args.args
        self.assertEqual(called_body["model"], "gemini-2.5-pro")
        self.assertEqual(called_spec.provider, "gemini")
        self.assertEqual(called_messages, [{"role": "user", "content": "Say hi"}])
        self.assertEqual(result["object"], "text_completion")
        self.assertEqual(result["choices"][0]["text"], "Gemini complete")


class OpenAISearchCompleteRouteTests(unittest.TestCase):
    def test_search_route_accepts_x_api_key_and_filters_prompt(self) -> None:
        client = _client()
        with (
            mock.patch.object(ai_api, "check_request") as check_request,
            mock.patch.object(ai_api.LoggedCall, "log"),
            mock.patch.object(ai_api.openai_search, "handle", return_value={"object": "search.result", "answer": "ok"}) as handle,
        ):
            response = client.post("/v1/search", headers=API_KEY_HEADERS, json={"prompt": "latest news", "model": "gpt-5-5"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(cast(dict[str, Any], response.json())["object"], "search.result")
        check_request.assert_called_once_with("latest news")
        handle.assert_called_once()
        self.assertEqual(handle.call_args.args[0]["prompt"], "latest news")

    def test_complete_route_accepts_authorization_and_filters_prompt(self) -> None:
        client = _client()
        payload = {"object": "text_completion", "choices": [{"text": "ok"}]}
        with (
            mock.patch.object(ai_api, "check_request") as check_request,
            mock.patch.object(ai_api.LoggedCall, "log"),
            mock.patch.object(ai_api.openai_v1_complete, "handle", return_value=payload) as handle,
        ):
            response = client.post("/v1/complete", headers=AUTH_HEADERS, json={"prompt": "Say hi", "model": "gpt-4o"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(cast(dict[str, Any], response.json())["object"], "text_completion")
        check_request.assert_called_once_with("Say hi")
        handle.assert_called_once()
        self.assertEqual(handle.call_args.args[0]["model"], "gpt-4o")


class OpenAIBackendSearchExtractionTests(unittest.TestCase):
    def test_extract_search_result_prefers_latest_assistant_and_discovers_urls(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        conversation = {
            "mapping": {
                "old": {"message": {"author": {"role": "assistant"}, "create_time": 1, "content": {"parts": ["Old"]}}},
                "new": {
                    "message": {
                        "id": "msg_new",
                        "author": {"role": "assistant"},
                        "create_time": 2,
                        "metadata": {"finish_details": {"type": "finished_successfully"}},
                        "content": {
                            "parts": ["Answer with https://example.org/path."],
                            "citations": [{"title": "Example", "url": "https://example.com", "snippet": "Snippet"}],
                        },
                    }
                },
            }
        }

        result = backend._extract_search_result("conv_1", conversation)

        self.assertEqual(result["conversation_id"], "conv_1")
        self.assertEqual(result["status"], "finished_successfully")
        self.assertEqual(result["assistant_message_id"], "msg_new")
        self.assertEqual(result["answer"], "Answer with https://example.org/path.")
        self.assertEqual([source["url"] for source in result["sources"]], ["https://example.com", "https://example.org/path"])


if __name__ == "__main__":
    unittest.main()
