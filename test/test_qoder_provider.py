from __future__ import annotations

import json
import sys
import time
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

from services.protocol import anthropic_v1_messages, openai_v1_chat_complete, openai_v1_models
from services.providers.base import QODER_PROVIDER
from services.providers.qoder.accounts import normalize_account, sanitize_account
from services.providers.qoder.client import QoderClient, QoderError
from services.providers.registry import normalize_account_provider, resolve_model


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict[str, object] | None = None, lines: list[str] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self._lines = lines or []
        self.text = json.dumps(self._payload)
        self.headers: dict[str, str] = {}

    def json(self) -> dict[str, object]:
        return self._payload

    def iter_lines(self):
        return iter(self._lines)


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.posts: list[dict[str, object]] = []
        self.closed = False

    def post(self, url: str, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class QoderProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        from services.providers.qoder import client

        client._TOKEN_CACHE.clear()

    def test_model_routes_to_qoder_with_al_prefix(self) -> None:
        spec = resolve_model("al-qwen3.7-plus")

        self.assertEqual(spec.provider, QODER_PROVIDER)
        self.assertEqual(spec.upstream_model, "qmodel")
        self.assertEqual(normalize_account_provider("al"), QODER_PROVIDER)

    def test_account_sanitization_hides_pat_token(self) -> None:
        account = normalize_account({"provider": "qoder", "access_token": "fake-qoder-pat"})

        self.assertEqual(account["pat_token"], "fake-qoder-pat")
        sanitized = sanitize_account(account)
        self.assertNotIn("pat_token", sanitized)
        self.assertNotIn("fake-qoder-pat", str(sanitized))
        self.assertTrue(sanitized["has_pat_token"])

    def test_models_list_includes_al_models(self) -> None:
        result = openai_v1_models.list_models()
        models = {item["id"]: item for item in result["data"]}

        self.assertEqual(models["al-qwen3.7-plus"]["provider"], QODER_PROVIDER)
        self.assertEqual(models["al-qwen3.7-plus"]["root"], "Qwen3.7-Plus")
        self.assertEqual(models["al-glm-5.2"]["root"], "GLM-5.2")
        self.assertEqual(models["al-kimi-k2.6"]["root"], "Kimi-K2.6")
        self.assertEqual(models["al-minimax-m2.7"]["root"], "MiniMax-M2.7")
        self.assertNotIn("al-kimi-k2.7-code", models)
        self.assertNotIn("al-minimax-m3", models)

    def test_client_uses_pat_token_exchange_and_direct_chat_api(self) -> None:
        session = FakeSession([
            FakeResponse(payload={"token": "fake-job-token"}),
            FakeResponse(lines=[
                'data: {"id":"chatcmpl-test","model":"qmodel","choices":[{"delta":{"content":"OK"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-test","model":"qmodel","choices":[{"delta":{},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ]),
        ])

        with mock.patch("services.providers.qoder.client.create_session", return_value=session), \
             mock.patch("services.providers.qoder.client.QODER_TRANSPORT", "api"):
            response = QoderClient({"access_token": "qoder:abc", "pat_token": "fake-qoder-pat"}).chat_completion(
                {"max_tokens": 8, "tools": [{"type": "function", "function": {"name": "Read"}}]},
                [{"role": "user", "content": "hello"}],
                "al-qwen3.7-plus",
            )

        self.assertEqual(response["choices"][0]["message"]["content"], "OK")
        self.assertEqual(session.posts[0]["url"], "https://openapi.qoder.com.cn/api/v1/jobToken/exchange")
        self.assertEqual(session.posts[0]["json"], {"personal_token": "fake-qoder-pat"})
        self.assertEqual(session.posts[1]["url"], "https://gateway.qoder.com.cn/model/v1/chat/completions")
        self.assertEqual(session.posts[1]["headers"]["Authorization"], "Bearer fake-job-token")
        chat_body = session.posts[1]["json"]
        self.assertEqual(chat_body["model"], "qmodel")
        self.assertEqual(chat_body["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(chat_body["stream"], True)
        self.assertEqual(chat_body["stream_options"], {"include_usage": True})
        self.assertEqual(chat_body["metadata"]["context"]["client_type"], "5")
        self.assertEqual(chat_body["tools"][0]["function"]["name"], "Read")

    def test_auto_transport_falls_back_to_cli_when_direct_api_is_unavailable(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(cmd, capture_output, text, timeout, env):
            captured.update({"cmd": cmd, "env": env, "timeout": timeout})
            return types.SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"result": "OK", "is_error": False}),
                stderr="",
            )

        with mock.patch("services.providers.qoder.client.create_session", return_value=FakeSession([])), \
             mock.patch("services.providers.qoder.client.QoderClient._wasm_chat_completion", side_effect=QoderError("wasm unavailable")), \
             mock.patch("services.providers.qoder.client.shutil.which", return_value="/usr/bin/qoderclicn"), \
             mock.patch("services.providers.qoder.client.subprocess.run", side_effect=fake_run):
            response = QoderClient({"access_token": "qoder:abc", "pat_token": "fake-qoder-pat"}).chat_completion(
                {"max_tokens": 8},
                [{"role": "user", "content": "hello"}],
                "al-qwen3.7-plus",
            )

        self.assertEqual(response["choices"][0]["message"]["content"], "OK")
        self.assertEqual(captured["cmd"][:8], ["/usr/bin/qoderclicn", "--bare", "-p", "--tools", "", "--model", "Qwen3.7-Plus", "--output-format"])
        self.assertEqual(captured["env"]["QODERCN_PERSONAL_ACCESS_TOKEN"], "fake-qoder-pat")
        self.assertEqual(captured["env"]["NO_BROWSER"], "1")

    def test_wasm_transport_invokes_node_helper_and_aggregates_chunks(self) -> None:
        stdout = "\n".join([
            json.dumps({"id": "chatcmpl-wasm", "model": "qmodel", "choices": [{"delta": {"content": "O"}, "finish_reason": None}]}),
            json.dumps({"id": "chatcmpl-wasm", "model": "qmodel", "choices": [{"delta": {"content": "K"}, "finish_reason": None}]}),
            json.dumps({"id": "chatcmpl-wasm", "model": "qmodel", "choices": [{"delta": {}, "finish_reason": "stop"}]}),
        ])
        captured: dict[str, object] = {}

        def fake_run(cmd, input, capture_output, text, timeout, env):
            captured.update({"cmd": cmd, "input": json.loads(input), "env": env, "timeout": timeout})
            return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        with mock.patch("services.providers.qoder.client.create_session", return_value=FakeSession([])), \
             mock.patch("services.providers.qoder.client.shutil.which", return_value="/usr/bin/node"), \
             mock.patch("services.providers.qoder.client.subprocess.run", side_effect=fake_run), \
             mock.patch("services.providers.qoder.client.QODER_TRANSPORT", "wasm"):
            response = QoderClient({"access_token": "qoder:abc", "pat_token": "fake-qoder-pat"}).chat_completion(
                {"max_tokens": 8},
                [{"role": "user", "content": "hello"}],
                "al-qwen3.7-plus",
            )

        self.assertEqual(response["choices"][0]["message"]["content"], "OK")
        self.assertEqual(captured["cmd"][0], "/usr/bin/node")
        self.assertEqual(captured["input"]["pat_token"], "fake-qoder-pat")
        self.assertEqual(captured["input"]["upstream_model"], "qmodel")
        self.assertNotIn("fake-qoder-pat", captured["cmd"])

    def test_cli_fallback_tool_json_is_converted_to_native_tool_calls(self) -> None:
        tool_json = json.dumps({"status": "call", "tool_calls": [{"name": "Read", "arguments": {"file_path": "README.md"}}]})
        with mock.patch("services.providers.qoder.client.QoderClient._cli_chat_text", return_value=tool_json), \
             mock.patch("services.providers.qoder.client.create_session", return_value=FakeSession([])), \
             mock.patch("services.providers.qoder.client.QODER_TRANSPORT", "cli"):
            response = QoderClient({"access_token": "qoder:abc", "pat_token": "fake-qoder-pat"}).chat_completion(
                {"tools": [{"type": "function", "function": {"name": "Read", "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}}}]},
                [{"role": "user", "content": "read README"}],
                "al-qwen3.7-plus",
            )

        message = response["choices"][0]["message"]
        self.assertEqual(response["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "Read")
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"file_path": "README.md"})

    def test_qoder_native_tool_calls_are_returned_to_openai_client(self) -> None:
        body = {
            "model": "al-qwen3.7-plus",
            "messages": [{"role": "user", "content": "read README"}],
            "tools": [{"type": "function", "function": {"name": "Read", "parameters": {"type": "object"}}}],
        }
        raw_response = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_read",
                        "type": "function",
                        "function": {"name": "Read", "arguments": json.dumps({"file_path": "README.md"})},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }

        with mock.patch.object(openai_v1_chat_complete.qoder_chat, "raw_chat_completion", return_value=raw_response):
            response = openai_v1_chat_complete.handle(body)

        message = response["choices"][0]["message"]
        self.assertEqual(response["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "Read")
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"file_path": "README.md"})

    def test_anthropic_messages_routes_al_model_through_qoder_bridge(self) -> None:
        captured: dict[str, object] = {}

        def fake_raw(body, messages, model):
            captured.update({"body": body, "messages": messages, "model": model})
            return {"choices": [{"message": {"content": [{"type": "text", "text": "OK"}]}, "finish_reason": "stop"}], "usage": {}}

        with mock.patch.object(anthropic_v1_messages.qoder_anthropic.qoder_chat, "raw_chat_completion", side_effect=fake_raw), \
             mock.patch.object(anthropic_v1_messages.openai_v1_chat_complete, "handle", side_effect=AssertionError("shared bridge should not be used")):
            response = anthropic_v1_messages.handle({
                "model": "al-qwen3.7-plus",
                "max_tokens": 32,
                "system": [{"type": "text", "text": "You are Claude Code."}],
                "messages": [{"role": "user", "content": "hello"}],
            })

        self.assertEqual(captured["model"], "al-qwen3.7-plus")
        self.assertEqual(captured["messages"][0], {"role": "system", "content": "You are Claude Code."})
        self.assertEqual(response["content"], [{"type": "text", "text": "OK"}])

    def test_qoder_anthropic_decodes_typed_content_json_string(self) -> None:
        raw_response = {
            "choices": [{
                "message": {"content": json.dumps([{"text": "", "type": "text"}], ensure_ascii=False)},
                "finish_reason": "stop",
            }],
            "usage": {},
        }

        with mock.patch.object(anthropic_v1_messages.qoder_anthropic.qoder_chat, "raw_chat_completion", return_value=raw_response):
            response = anthropic_v1_messages.handle({
                "model": "al-qwen3.7-plus",
                "messages": [{"role": "user", "content": "UI界面不好看，改得美观一些"}],
            })

        self.assertEqual(response["content"], [{"type": "text", "text": ""}])

    def test_qoder_anthropic_recovers_malformed_typed_text_wrapper(self) -> None:
        raw_response = {
            "choices": [{
                "message": {"content": '● [{"text":"","type":"text"}]","type":"text"}]'},
                "finish_reason": "stop",
            }],
            "usage": {},
        }

        with mock.patch.object(anthropic_v1_messages.qoder_anthropic.qoder_chat, "raw_chat_completion", return_value=raw_response):
            response = anthropic_v1_messages.handle({
                "model": "al-qwen3.7-plus",
                "messages": [{"role": "user", "content": "hello"}],
            })

        self.assertEqual(response["content"], [{"type": "text", "text": ""}])

    def test_qoder_anthropic_recursively_unwraps_nested_typed_text(self) -> None:
        inner = json.dumps([{"text": "python\nprint('ok')", "type": "text"}], ensure_ascii=False)
        outer = json.dumps([{"text": inner, "type": "text"}], ensure_ascii=False)
        raw_response = {
            "choices": [{
                "message": {"content": json.dumps([{"text": outer, "type": "text"}], ensure_ascii=False)},
                "finish_reason": "stop",
            }],
            "usage": {},
        }

        with mock.patch.object(anthropic_v1_messages.qoder_anthropic.qoder_chat, "raw_chat_completion", return_value=raw_response):
            response = anthropic_v1_messages.handle({
                "model": "al-qwen3.7-plus",
                "messages": [{"role": "user", "content": "hello"}],
            })

        self.assertEqual(response["content"], [{"type": "text", "text": "python\nprint('ok')"}])

    def test_qoder_anthropic_unwraps_nested_typed_text_inside_noisy_wrapper(self) -> None:
        inner = json.dumps([{"text": "python\n# ui.py", "type": "text"}], ensure_ascii=False)
        raw_response = {
            "choices": [{
                "message": {"content": '● [{"text":"' + json.dumps(inner)[1:-1] + '","type":"text"}]","type":"text"}]'},
                "finish_reason": "stop",
            }],
            "usage": {},
        }

        with mock.patch.object(anthropic_v1_messages.qoder_anthropic.qoder_chat, "raw_chat_completion", return_value=raw_response):
            response = anthropic_v1_messages.handle({
                "model": "al-qwen3.7-plus",
                "messages": [{"role": "user", "content": "hello"}],
            })

        self.assertEqual(response["content"], [{"type": "text", "text": "python\n# ui.py"}])

    def test_qoder_anthropic_streams_tool_use_and_tool_result_roundtrip(self) -> None:
        captured: dict[str, object] = {}
        raw_response = {
            "choices": [{
                "message": {
                    "content": [{"type": "text", "text": ""}],
                    "tool_calls": [{
                        "id": "call_read",
                        "type": "function",
                        "function": {"name": "Read", "arguments": json.dumps({"file_path": "README.md"})},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        }

        def fake_raw(body, messages, model):
            captured.update({"messages": messages})
            return raw_response

        with mock.patch.object(anthropic_v1_messages.qoder_anthropic.qoder_chat, "raw_chat_completion", side_effect=fake_raw):
            events = list(anthropic_v1_messages.handle({
                "model": "al-qwen3.7-plus",
                "stream": True,
                "messages": [
                    {"role": "user", "content": "read README"},
                    {"role": "assistant", "content": [{"type": "tool_use", "id": "call_prev", "name": "Read", "input": {"file_path": "README.md"}}]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_prev", "content": [{"type": "text", "text": "# webchat2api"}]}]},
                ],
                "tools": [{"name": "Read", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}}],
            }))

        self.assertIn({"role": "tool", "tool_call_id": "call_prev", "content": "# webchat2api"}, captured["messages"])
        start = next(event for event in events if event.get("type") == "content_block_start" and event.get("content_block", {}).get("type") == "tool_use")
        self.assertEqual(start["content_block"]["name"], "Read")
        delta = next(event for event in events if event.get("type") == "content_block_delta" and event.get("delta", {}).get("type") == "input_json_delta")
        self.assertEqual(json.loads(delta["delta"]["partial_json"]), {"file_path": "README.md"})

    def test_qoder_anthropic_stream_pings_while_waiting_for_full_response(self) -> None:
        def fake_raw(body, messages, model):
            time.sleep(0.03)
            return {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}], "usage": {}}

        with mock.patch.object(anthropic_v1_messages.qoder_anthropic, "STREAM_PING_INTERVAL_SECONDS", 0.01), \
             mock.patch.object(anthropic_v1_messages.qoder_anthropic.qoder_chat, "raw_chat_completion", side_effect=fake_raw):
            events = list(anthropic_v1_messages.handle({
                "model": "al-qwen3.7-plus",
                "stream": True,
                "messages": [{"role": "user", "content": "slow task"}],
            }))

        self.assertEqual(events[0]["type"], "message_start")
        self.assertIn({"type": "ping"}, events)
        self.assertTrue(any(event.get("type") == "content_block_delta" and event.get("delta", {}).get("text") == "OK" for event in events))

    def test_qoder_anthropic_preserves_system_without_tool_hint(self) -> None:
        payload = anthropic_v1_messages.qoder_anthropic.qoder_body({
            "model": "al-qwen3.7-plus",
            "system": [{"type": "text", "text": "You are Claude Code."}],
            "messages": [{"role": "user", "content": "write a file"}],
            "tools": [{"name": "Bash", "input_schema": {"type": "object"}}],
        })

        self.assertEqual(payload["messages"][0], {"role": "system", "content": "You are Claude Code."})

    def test_qoder_anthropic_hoists_mid_conversation_system_messages(self) -> None:
        payload = anthropic_v1_messages.qoder_anthropic.qoder_body({
            "model": "al-qwen3.7-plus",
            "system": [{"type": "text", "text": "top system"}],
            "messages": [
                {"role": "user", "content": "start"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "call_write", "name": "Write", "input": {"file_path": "ui.py"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_write", "content": [{"type": "text", "text": "updated"}]}]},
                {"role": "system", "content": [{"type": "text", "text": "mid reminder"}]},
                {"role": "user", "content": "继续"},
            ],
        })

        self.assertEqual(payload["messages"][0], {"role": "system", "content": "top system\n\nmid reminder"})
        self.assertEqual([message["role"] for message in payload["messages"][1:]], ["user", "assistant", "tool", "user"])

    def test_qoder_anthropic_returns_text_tool_syntax_as_text(self) -> None:
        raw_response = {
            "choices": [{
                "message": {"content": "Read(file_path=/tmp/work/todo_stats.py)"},
                "finish_reason": "stop",
            }],
            "usage": {},
        }

        with mock.patch.object(anthropic_v1_messages.qoder_anthropic.qoder_chat, "raw_chat_completion", return_value=raw_response):
            response = anthropic_v1_messages.handle({
                "model": "al-qwen3.7-plus",
                "messages": [{"role": "user", "content": "read file"}],
                "tools": [{"name": "Read", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}}],
            })

        self.assertEqual(response["stop_reason"], "end_turn")
        self.assertEqual(response["content"], [{"type": "text", "text": "Read(file_path=/tmp/work/todo_stats.py)"}])

    def test_qoder_anthropic_does_not_map_unavailable_write_to_edit(self) -> None:
        raw_response = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_write",
                        "type": "function",
                        "function": {"name": "Write", "arguments": json.dumps({"file_path": "todo.py", "content": "print(1)\n"})},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        }

        with mock.patch.object(anthropic_v1_messages.qoder_anthropic.qoder_chat, "raw_chat_completion", return_value=raw_response):
            response = anthropic_v1_messages.handle({
                "model": "al-qwen3.7-plus",
                "messages": [{"role": "user", "content": "write file"}],
                "tools": [{"name": "Edit", "input_schema": {"type": "object"}}],
            })

        self.assertEqual(response["stop_reason"], "tool_use")
        self.assertEqual(response["content"][0]["name"], "Write")
        self.assertEqual(response["content"][0]["input"], {"file_path": "todo.py", "content": "print(1)\n"})


if __name__ == "__main__":
    unittest.main()
