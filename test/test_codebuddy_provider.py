from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

if "curl_cffi" not in sys.modules:
    curl_cffi = types.ModuleType("curl_cffi")
    requests_module = types.ModuleType("curl_cffi.requests")
    setattr(requests_module, "Session", object)
    setattr(requests_module, "Response", object)
    setattr(requests_module, "exceptions", types.SimpleNamespace(RequestException=Exception))
    setattr(curl_cffi, "requests", requests_module)
    sys.modules["curl_cffi"] = curl_cffi
    sys.modules["curl_cffi.requests"] = requests_module

from test.optional_stubs import install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_tiktoken_stub

install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_tiktoken_stub()

from services.protocol import anthropic_v1_messages, openai_v1_models
from services.providers.base import CODEBUDDY_PROVIDER
from services.providers.codebuddy.accounts import normalize_account, sanitize_account
from services.providers.codebuddy.client import CodeBuddyClient
from services.providers.registry import normalize_account_provider, resolve_model


class FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}
    text = ""

    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def iter_lines(self):
        return iter(self.lines)

    def json(self):
        return {}


class FakeSession:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines
        self.url = None
        self.payload = None
        self.headers = None

    def post(self, url, headers=None, json=None, stream=False, timeout=None):
        self.url = url
        self.headers = headers
        self.payload = json
        return FakeResponse(self.lines)

    def close(self):
        pass


class CodeBuddyProviderTests(unittest.TestCase):
    def test_model_routes_to_codebuddy_with_tx_prefix(self) -> None:
        spec = resolve_model("tx-deepseek-v3")

        self.assertEqual(spec.provider, CODEBUDDY_PROVIDER)
        self.assertEqual(spec.upstream_model, "deepseek-v3")
        self.assertEqual(normalize_account_provider("tx"), CODEBUDDY_PROVIDER)

    def test_account_sanitization_hides_bearer_token(self) -> None:
        account = normalize_account({"provider": "codebuddy", "access_token": "fake-codebuddy-secret"})

        self.assertEqual(account["bearer_token"], "fake-codebuddy-secret")
        sanitized = sanitize_account(account)
        self.assertNotIn("bearer_token", sanitized)
        self.assertNotIn("fake-codebuddy-secret", str(sanitized))
        self.assertTrue(sanitized["has_bearer_token"])

    def test_models_list_includes_tx_models(self) -> None:
        result = openai_v1_models.list_models()
        models = {item["id"]: item for item in result["data"]}

        self.assertEqual(models["tx-deepseek-v3"]["provider"], CODEBUDDY_PROVIDER)
        self.assertEqual(models["tx-deepseek-v3"]["root"], "deepseek-v3")
        self.assertEqual(models["tx-glm-5.1"]["root"], "glm-5.1")
        self.assertEqual(models["tx-glm-5.2"]["root"], "glm-5.2")
        self.assertEqual(models["tx-glm-4.6"]["root"], "glm-4.6")
        self.assertEqual(models["tx-minimax-m3"]["root"], "minimax-m3")
        self.assertEqual(models["tx-kimi-k2.6"]["root"], "kimi-k2.6")

    def test_client_aggregates_stream_and_strips_tx_prefix_upstream(self) -> None:
        lines = [
            b'data: {"id":"chatcmpl_1","model":"gpt-5","choices":[{"delta":{"content":"hi"},"finish_reason":null}]}',
            b'data: {"id":"chatcmpl_1","model":"gpt-5","choices":[{"delta":{},"finish_reason":"stop"}]}',
            b"data: [DONE]",
        ]
        session = FakeSession(lines)
        with mock.patch("services.providers.codebuddy.client.create_session", return_value=session):
            with CodeBuddyClient({"bearer_token": "fake-codebuddy-key", "user_id": "u"}) as client:
                response = client.chat_completion({}, [{"role": "user", "content": "hello"}], "tx-deepseek-v3")

        self.assertEqual(session.payload["model"], "deepseek-v3")
        self.assertEqual(session.payload["stream"], True)
        self.assertEqual(session.url, "https://www.codebuddy.cn/v2/chat/completions")
        self.assertEqual(session.headers["Host"], "www.codebuddy.cn")
        self.assertEqual(session.headers["X-Domain"], "www.codebuddy.cn")
        self.assertEqual(session.headers["X-API-Key"], "fake-codebuddy-key")
        self.assertNotIn("Authorization", session.headers)
        self.assertEqual(response["choices"][0]["message"]["content"], "hi")

    def test_client_converts_forced_tool_choice_to_codebuddy_string(self) -> None:
        session = FakeSession([b"data: [DONE]"])
        body = {"tool_choice": {"type": "function", "function": {"name": "Read"}}}
        with mock.patch("services.providers.codebuddy.client.create_session", return_value=session):
            with CodeBuddyClient({"bearer_token": "fake-codebuddy-key"}) as client:
                client.chat_completion(body, [{"role": "user", "content": "hello"}], "tx-deepseek-v3")

        self.assertEqual(session.payload["tool_choice"], "Read")

    def test_client_aggregates_tool_calls(self) -> None:
        lines = [
            b'data: {"id":"chatcmpl_1","choices":[{"delta":{"tool_calls":[{"id":"tooluse_abc","type":"function","function":{"name":"Read","arguments":"{\\"file_path\\":"}}]},"finish_reason":null}]}',
            b'data: {"id":"chatcmpl_1","choices":[{"delta":{"tool_calls":[{"function":{"arguments":"\\"README.md\\"}"}}]},"finish_reason":null}]}',
            b'data: {"id":"chatcmpl_1","choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        ]
        with mock.patch("services.providers.codebuddy.client.create_session", return_value=FakeSession(lines)):
            with CodeBuddyClient({"bearer_token": "fake-codebuddy-key"}) as client:
                response = client.chat_completion({"tools": [{"type": "function", "function": {"name": "Read"}}]}, [], "tx-deepseek-v3")

        call = response["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["id"], "call_abc")
        self.assertEqual(call["function"]["name"], "Read")
        self.assertEqual(call["function"]["arguments"], '{"file_path": "README.md"}')

    def test_anthropic_messages_routes_tx_model_through_openai_bridge(self) -> None:
        captured: dict[str, object] = {}

        def fake_handle(payload):
            captured.update(payload)
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}

        with mock.patch.object(anthropic_v1_messages.openai_v1_chat_complete, "handle", side_effect=fake_handle):
            response = anthropic_v1_messages.handle({
                "model": "tx-deepseek-v3",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hello"}],
            })

        self.assertEqual(captured["model"], "tx-deepseek-v3")
        self.assertEqual(response["content"][0]["text"], "ok")


if __name__ == "__main__":
    unittest.main()
