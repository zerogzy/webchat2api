from __future__ import annotations

import json
import sys
import time
import unittest
from typing import Any, cast
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_pydantic_stub, install_starlette_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_pydantic_stub()
install_starlette_stub()
install_tiktoken_stub()

FastAPI = cast(Any, getattr(sys.modules["fastapi"], "FastAPI"))
TestClient = cast(Any, getattr(sys.modules["fastapi.testclient"], "TestClient"))
HTTPException = cast(type[Exception], getattr(sys.modules["fastapi"], "HTTPException"))

from api import gemini as gemini_api
from services import gemini_deep_research
from services.providers import gemini as gemini_provider
from services.protocol import gemini_native, openai_v1_chat_complete

AUTH_HEADERS = {"Authorization": "Bearer webchat2api"}


def _native_body(text: str = "Hello") -> dict[str, Any]:
    return {"contents": [{"role": "user", "parts": [{"text": text}]}]}


def _tool() -> dict[str, Any]:
    return {
        "functionDeclarations": [{
            "name": "get_weather",
            "description": "Get weather.",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        }]
    }


class GeminiNativeProtocolTests(unittest.TestCase):
    def test_models_response_uses_native_shape(self) -> None:
        response = gemini_native.list_models()

        first = response["models"][0]
        self.assertTrue(first["name"].startswith("models/gemini-"))
        self.assertEqual(first["displayName"], first["name"].removeprefix("models/"))
        self.assertEqual(first["supportedGenerationMethods"], ["generateContent", "streamGenerateContent"])

    def test_generate_content_text_response(self) -> None:
        response = gemini_native.generate_content(
            "models/gemini-2.5-pro",
            _native_body("Say hi") | {"generationConfig": {"temperature": 0.2, "topP": 0.9, "maxOutputTokens": 32}},
            completion_func=lambda body, spec, messages: gemini_provider.GeminiCompletion("Hi there"),
        )

        candidate = response["candidates"][0]
        self.assertEqual(candidate["content"]["role"], "model")
        self.assertEqual(candidate["content"]["parts"], [{"text": "Hi there"}])
        self.assertEqual(candidate["finishReason"], "STOP")
        self.assertEqual(response["usageMetadata"]["totalTokenCount"], 0)

    def test_generate_content_native_function_call(self) -> None:
        body = _native_body("weather") | {
            "tools": [_tool()],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["get_weather"]}},
        }
        response = gemini_native.generate_content(
            "gemini-2.5-pro",
            body,
            completion_func=lambda body, spec, messages: gemini_provider.GeminiCompletion('```json\n{"status":"call","tool_calls":[{"name":"get_weather","arguments":{"city":"Paris"}}]}\n```'),
        )

        part = response["candidates"][0]["content"]["parts"][0]
        self.assertEqual(part["functionCall"]["name"], "get_weather")
        self.assertEqual(part["functionCall"]["args"], {"city": "Paris"})

    def test_generate_content_accepts_function_response_only_turn(self) -> None:
        body = {"contents": [{"role": "function", "parts": [{"functionResponse": {"name": "get_weather", "response": {"temp": "20C"}}}]}]}
        response = gemini_native.generate_content(
            "gemini-2.5-pro",
            body,
            completion_func=lambda body, spec, messages: gemini_provider.GeminiCompletion("Thanks"),
        )

        self.assertEqual(response["candidates"][0]["content"]["parts"], [{"text": "Thanks"}])

    def test_stream_generate_content_sse_chunks_and_stop(self) -> None:
        with mock.patch.object(gemini_provider, "synthetic_stream_content", return_value=iter(["Hel", "lo"])):
            chunks = list(gemini_native.stream_generate_content(
                "gemini-2.5-pro",
                _native_body("Hi"),
                completion_func=lambda body, spec, messages: gemini_provider.GeminiCompletion("Hello"),
            ))

        self.assertEqual(chunks[0]["candidates"][0]["content"]["parts"], [{"text": "Hel"}])
        self.assertEqual(chunks[1]["candidates"][0]["content"]["parts"], [{"text": "lo"}])
        self.assertEqual(chunks[-1]["candidates"][0]["finishReason"], "STOP")

    def test_inline_media_without_text_rejects(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            gemini_native.generate_content(
                "gemini-2.5-pro",
                {"contents": [{"role": "user", "parts": [{"inlineData": {"mimeType": "image/png", "data": "AA=="}}]}]},
                completion_func=lambda body, spec, messages: gemini_provider.GeminiCompletion("ignored"),
            )

        self.assertEqual(getattr(raised.exception, "status_code"), 400)
        self.assertIn("inline media", str(getattr(raised.exception, "detail")))


class GeminiDeepResearchTests(unittest.TestCase):
    def test_deepresearch_sync(self) -> None:
        outputs = iter([
            '{"questions":["What is A?"]}',
            '{"summary":"A facts","sources":[{"url":"https://example.test/a","title":"A"}]}',
            '{"summary":"Final report"}',
        ])

        result = gemini_deep_research.run_deep_research({"query": "A"}, completion_func=lambda model, prompt: next(outputs))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"], "Final report")
        self.assertEqual(result["sources"][0]["url"], "https://example.test/a")
        self.assertGreaterEqual(result["duration_ms"], 0)

    def test_deepresearch_stream_events(self) -> None:
        outputs = iter([
            '{"questions":["What is A?"]}',
            '{"summary":"A facts","sources":["https://example.test/a"]}',
            '{"summary":"Final report"}',
        ])

        events = list(gemini_deep_research.stream_deep_research({"query": "A"}, completion_func=lambda model, prompt: next(outputs)))
        names = [name for name, _ in events]

        self.assertEqual(names[0], "progress")
        self.assertIn("step", names)
        self.assertIn("source", names)
        self.assertIn("result", names)
        self.assertEqual(names[-1], "done")

    def test_interactions_create_and_poll(self) -> None:
        outputs = iter([
            '{"questions":["What is A?"]}',
            '{"summary":"A facts","sources":[]}',
            '{"summary":"Final report"}',
        ])
        store = gemini_deep_research.InteractionStore(ttl_seconds=60)
        task = store.create({"query": "A"}, completion_func=lambda model, prompt: next(outputs), owner_id="owner-a")

        deadline = time.time() + 2
        current = store.get(task["id"], "owner-a")
        while current and current["status"] == "in_progress" and time.time() < deadline:
            time.sleep(0.01)
            current = store.get(task["id"], "owner-a")

        self.assertNotIn("owner_id", task)
        self.assertIsNone(store.get(task["id"], "owner-b"))
        self.assertIsNotNone(current)
        self.assertEqual(cast(dict[str, Any], current)["status"], "completed")
        self.assertEqual(cast(dict[str, Any], current)["result"]["summary"], "Final report")


class GeminiNativeRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(gemini_api.create_router())
        self.client = TestClient(app)

    def test_route_registration_in_app_factory(self) -> None:
        app = FastAPI()
        app.include_router(gemini_api.create_router())
        routes = app.routes
        self.assertIn(("GET", "/gemini/v1beta/models"), routes)
        self.assertIn(("POST", "/gemini/v1beta/models/{model}:generateContent"), routes)
        self.assertIn(("POST", "/gemini/v1beta/interactions"), routes)

    def test_models_route_requires_bearer_auth(self) -> None:
        response = self.client.get("/gemini/v1beta/models", headers={"x-api-key": "webchat2api"})

        self.assertEqual(response.status_code, 401)

    def test_models_route_accepts_bearer_auth(self) -> None:
        response = self.client.get("/gemini/v1beta/models", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200)
        payload = cast(dict[str, Any], response.json())
        self.assertIn("models", payload)

    def test_interactions_create_returns_accepted_task(self) -> None:
        with mock.patch.object(gemini_api.interaction_store, "create", return_value={"id": "int_test", "status": "in_progress", "query": "A"}):
            response = self.client.post(
                "/gemini/v1beta/interactions",
                headers=AUTH_HEADERS,
                json={"input": "A"},
            )

        self.assertEqual(response.status_code, 202)
        payload = cast(dict[str, Any], response.json())
        self.assertEqual(payload["id"], "int_test")
        self.assertEqual(payload["status"], "in_progress")

    def test_generate_content_route_registered(self) -> None:
        routes = self.client.app.routes
        self.assertIn(("POST", "/gemini/v1beta/models/{model}:generateContent"), routes)


class OpenAIGeminiToolChoiceTests(unittest.TestCase):
    def test_gemini_required_tool_choice_does_not_fabricate_empty_call(self) -> None:
        with mock.patch.object(gemini_provider, "chat_completion", return_value=gemini_provider.GeminiCompletion("plain text")):
            response = cast(dict[str, Any], openai_v1_chat_complete.handle({
                "model": "gemini-2.5-pro",
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}],
                "tool_choice": "required",
            }))

        message = response["choices"][0]["message"]
        self.assertEqual(response["choices"][0]["finish_reason"], "stop")
        self.assertNotIn("tool_calls", message)
        self.assertEqual(message["content"], "plain text")

    def test_gemini_forced_tool_choice_honors_name(self) -> None:
        with mock.patch.object(gemini_provider, "chat_completion", return_value=gemini_provider.GeminiCompletion('{"status":"call","tool_calls":[{"name":"other","arguments":{}},{"name":"get_weather","arguments":{"city":"Rome"}}]}')):
            response = cast(dict[str, Any], openai_v1_chat_complete.handle({
                "model": "gemini-2.5-pro",
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [
                    {"type": "function", "function": {"name": "other", "parameters": {}}},
                    {"type": "function", "function": {"name": "get_weather", "parameters": {}}},
                ],
                "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
            }))

        call = response["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "get_weather")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"city": "Rome"})

    def test_gemini_tool_choice_none_returns_text(self) -> None:
        with mock.patch.object(gemini_provider, "chat_completion", return_value=gemini_provider.GeminiCompletion('{"status":"call","tool_calls":[{"name":"get_weather","arguments":{}}]}')):
            response = cast(dict[str, Any], openai_v1_chat_complete.handle({
                "model": "gemini-2.5-pro",
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}],
                "tool_choice": "none",
            }))

        message = response["choices"][0]["message"]
        self.assertNotIn("tool_calls", message)
        self.assertIn("tool_calls", message["content"])


if __name__ == "__main__":
    unittest.main()
