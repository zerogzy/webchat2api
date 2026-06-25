from __future__ import annotations

import sys
import types
import unittest
import base64
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
from services.providers.codebuddy import chat as codebuddy_chat
from services.providers.registry import normalize_account_provider, resolve_model
from utils.helper import UpstreamHTTPError


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


def json_chunk(content: str) -> dict[str, object]:
    return {"choices": [{"delta": {"content": content}, "finish_reason": None}]}


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

    def test_client_base64_wraps_system_without_rewriting_content(self) -> None:
        session = FakeSession([b"data: [DONE]"])
        messages = [
            {"role": "system", "content": "Claude Code cwd is /tmp/claude-code-smoke. Anthropic's official CLI for Claude."},
            {"role": "user", "content": "Claude Code should remain here"},
        ]
        with mock.patch("services.providers.codebuddy.client.create_session", return_value=session):
            with CodeBuddyClient({"bearer_token": "fake-codebuddy-key"}) as client:
                client.chat_completion({}, messages, "tx-deepseek-v3")

        self.assertEqual(session.payload["messages"][0]["role"], "system")
        encoded = session.payload["messages"][0]["content"].rsplit(" ", 1)[1]
        decoded = base64.b64decode(encoded).decode()
        self.assertIn("Claude Code cwd is /tmp/claude-code-smoke", decoded)
        self.assertIn("Anthropic's official CLI for Claude", decoded)
        self.assertEqual(session.payload["messages"][1]["content"], "Claude Code should remain here")

    def test_client_preserves_system_reminder_from_user_message(self) -> None:
        session = FakeSession([b"data: [DONE]"])
        messages = [{"role": "user", "content": "<system-reminder>date stuff</system-reminder>\n\nDo the task"}]
        with mock.patch("services.providers.codebuddy.client.create_session", return_value=session):
            with CodeBuddyClient({"bearer_token": "fake-codebuddy-key"}) as client:
                client.chat_completion({}, messages, "tx-deepseek-v3")

        self.assertEqual(session.payload["messages"][1]["content"], "<system-reminder>date stuff</system-reminder>\n\nDo the task")

    def test_client_preserves_tool_schema_descriptions(self) -> None:
        session = FakeSession([b"data: [DONE]"])
        body = {
            "tools": [{
                "type": "function",
                "function": {
                    "name": "Read",
                    "description": "Use with Claude Code by Anthropic",
                    "parameters": {
                        "type": "object",
                        "properties": {"file_path": {"type": "string", "description": "Path from Claude Code"}},
                    },
                },
            }]
        }
        with mock.patch("services.providers.codebuddy.client.create_session", return_value=session):
            with CodeBuddyClient({"bearer_token": "fake-codebuddy-key"}) as client:
                client.chat_completion(body, [{"role": "user", "content": "hello"}], "tx-deepseek-v3")

        self.assertEqual(session.payload["tools"][0]["function"]["description"], "Use with Claude Code by Anthropic")
        self.assertEqual(session.payload["tools"][0]["function"]["parameters"]["properties"]["file_path"]["description"], "Path from Claude Code")

    def test_client_filters_api_errors_and_maps_tool_messages_to_user(self) -> None:
        session = FakeSession([b"data: [DONE]"])
        messages = [
            {"role": "assistant", "content": "Error: API error: refused"},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
        with mock.patch("services.providers.codebuddy.client.create_session", return_value=session):
            with CodeBuddyClient({"bearer_token": "fake-codebuddy-key"}) as client:
                client.chat_completion({}, messages, "tx-deepseek-v3")

        self.assertEqual(session.payload["messages"], [{"role": "user", "tool_call_id": "call_1", "content": "ok"}])

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

    def test_chat_retries_next_account_when_quota_exhausted_before_output(self) -> None:
        accounts = {
            "token-a": {"access_token": "token-a", "bearer_token": "key-a"},
            "token-b": {"access_token": "token-b", "bearer_token": "key-b"},
        }
        selected: list[str] = []
        marked_limited: list[str] = []
        marked_success: list[str] = []

        class FakeAccountService:
            def get_text_access_token(self, excluded_tokens=None, provider=None):
                selected.append(",".join(sorted(excluded_tokens or [])))
                return "token-b" if excluded_tokens else "token-a"

            def get_account(self, token, provider=None):
                return accounts[token]

            def mark_codebuddy_quota_exhausted(self, token, exc):
                marked_limited.append(token)

            def mark_codebuddy_success(self, token):
                marked_success.append(token)

        class FakeClient:
            def __init__(self, account):
                self.account = account

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                pass

            def buffered_chunks(self, body, messages, model):
                if self.account["access_token"] == "token-a":
                    raise UpstreamHTTPError("CodeBuddy chat", 429, {"msg": "quota exhausted"})
                return [json_chunk("ok")]

        with mock.patch.dict("sys.modules", {"services.account_service": types.SimpleNamespace(account_service=FakeAccountService())}):
            with mock.patch("services.providers.codebuddy.client.CodeBuddyClient", FakeClient):
                chunks = codebuddy_chat._run_with_account({}, [{"role": "user", "content": "hi"}], "tx-glm-5.1")

        self.assertEqual(selected, ["", "token-a"])
        self.assertEqual(marked_limited, ["token-a"])
        self.assertEqual(marked_success, ["token-b"])
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], "ok")

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
