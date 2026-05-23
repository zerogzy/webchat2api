from __future__ import annotations

import json
import sys
import types
import unittest
from unittest import mock

if "curl_cffi" not in sys.modules:
    curl_cffi = types.ModuleType("curl_cffi")
    requests_module = types.SimpleNamespace(
        Session=object,
        Response=object,
        exceptions=types.SimpleNamespace(RequestException=Exception),
    )
    curl_cffi.requests = requests_module
    sys.modules["curl_cffi"] = curl_cffi
    sys.modules["curl_cffi.requests"] = requests_module

if "tiktoken" not in sys.modules:
    tiktoken = types.ModuleType("tiktoken")

    class FakeEncoding:
        def encode(self, text: str) -> list[str]:
            return list(text)

    tiktoken.get_encoding = lambda name: FakeEncoding()
    tiktoken.encoding_for_model = lambda model: FakeEncoding()
    sys.modules["tiktoken"] = tiktoken

if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: object = None) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi.HTTPException = HTTPException
    sys.modules["fastapi"] = fastapi

conversation = types.ModuleType("services.protocol.conversation")
conversation.ConversationRequest = object
class FakeImageOutput:
    def __init__(self, kind: str, model: str, index: int, total: int, text: str = "", data: list[dict[str, object]] | None = None, upstream_event_type: str = "") -> None:
        self.kind = kind
        self.model = model
        self.index = index
        self.total = total
        self.text = text
        self.data = data or []
        self.upstream_event_type = upstream_event_type


class FakeImageGenerationError(Exception):
    def __init__(self, message: str, status_code: int = 502, error_type: str = "server_error", code: str | None = "upstream_error", param: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.param = param

    def to_openai_error(self) -> dict[str, object]:
        return {"error": {"message": str(self), "type": self.error_type, "param": self.param, "code": self.code}}


conversation.ImageOutput = FakeImageOutput
conversation.ImageGenerationError = FakeImageGenerationError
conversation.format_image_result = lambda items, prompt, response_format, base_url=None, created=None: {"created": created or 1, "data": items}
conversation.collect_image_outputs = lambda *args, **kwargs: []
conversation.collect_text = lambda *args, **kwargs: ""
conversation.count_message_tokens = lambda *args, **kwargs: 0
conversation.count_text_tokens = lambda *args, **kwargs: 0
conversation.encode_images = lambda images: []
conversation.normalize_messages = lambda messages, system=None: messages
conversation.stream_image_outputs_with_pool = lambda *args, **kwargs: iter(())
conversation.stream_text_deltas = lambda *args, **kwargs: iter(())
conversation.text_backend = lambda: object()
sys.modules["services.protocol.conversation"] = conversation

from services.models import resolve_model
from services.protocol import openai_v1_chat_complete
from services.providers import grok


class GrokProviderTests(unittest.TestCase):
    def test_build_console_payload_converts_chat_messages(self) -> None:
        spec = resolve_model("grok-4.3")
        payload = grok.build_console_payload(
            spec,
            {"temperature": 0.2},
            [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                {"role": "assistant", "content": "Hi"},
            ],
        )

        self.assertEqual(payload["model"], "grok-4.3")
        self.assertEqual(payload["instructions"], "Be concise.")
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertEqual(payload["input"][0]["role"], "user")
        self.assertEqual(payload["input"][0]["content"], [{"type": "input_text", "text": "Hello"}])
        self.assertEqual(payload["input"][1]["content"], [{"type": "output_text", "text": "Hi"}])

    def test_extract_console_text_from_common_shapes(self) -> None:
        self.assertEqual(grok.extract_console_text({"output_text": "direct"}), "direct")
        self.assertEqual(
            grok.extract_console_text({"output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}]}),
            "hello",
        )
        self.assertEqual(
            grok.extract_console_text({"output": [{"type": "output_text", "text": "hello"}, {"type": "text", "text": " world"}]}),
            "hello world",
        )

    def test_extract_console_completion_splits_chinese_visible_thinking(self) -> None:
        response = grok.extract_console_completion({
            "output_text": "**思考摘要**：先判断问题。\n\n继续分析。\n\n**答案**：最终回答。",
            "reasoning": {"effort": "high"},
            "usage": {"output_tokens_details": {"reasoning_tokens": 3}},
        })

        self.assertEqual(response.content, "最终回答。")
        self.assertEqual(response.reasoning_content, "先判断问题。\n\n继续分析。")
        self.assertEqual(response.raw_reasoning, {"effort": "high"})
        self.assertEqual(response.raw_usage, {"output_tokens_details": {"reasoning_tokens": 3}})

    def test_extract_console_completion_strips_bold_answer_marker_with_inner_colon(self) -> None:
        response = grok.extract_console_completion({
            "output_text": "**思考摘要**：先判断问题。\n\n**答案：**  \n8+8等于16。",
        })

        self.assertEqual(response.content, "8+8等于16。")
        self.assertEqual(response.reasoning_content, "先判断问题。")
        self.assertFalse(response.content.startswith("**"))

    def test_extract_console_completion_splits_english_visible_thinking(self) -> None:
        response = grok.extract_console_completion({
            "output_text": "**Thinking summary**: inspect inputs\nvalidate route\n\n**Answer**: use console",
        })

        self.assertEqual(response.content, "use console")
        self.assertEqual(response.reasoning_content, "inspect inputs\nvalidate route")

    def test_extract_console_completion_keeps_plain_content_unchanged(self) -> None:
        response = grok.extract_console_completion({"output_text": "plain answer"})

        self.assertEqual(response.content, "plain answer")
        self.assertEqual(response.reasoning_content, "")

    def test_app_chat_headers_use_grok_app_shape_with_plain_token(self) -> None:
        with (
            mock.patch.object(grok, "_grok_console_profile", return_value=types.SimpleNamespace(
                user_agent="Test UA",
                cf_clearance="profile-clearance",
            )),
            mock.patch.object(grok.uuid, "uuid4", return_value="request-id"),
        ):
            headers = grok.app_chat_headers("plain-token")

        self.assertEqual(headers["Accept"], "*/*")
        self.assertEqual(headers["Accept-Language"], "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Origin"], "https://grok.com")
        self.assertEqual(headers["Referer"], "https://grok.com/")
        self.assertEqual(headers["Sec-Fetch-Dest"], "empty")
        self.assertEqual(headers["Sec-Fetch-Mode"], "cors")
        self.assertEqual(headers["Sec-Fetch-Site"], "same-origin")
        self.assertEqual(headers["User-Agent"], "Test UA")
        self.assertEqual(headers["x-statsig-id"], grok.GROK_APP_CHAT_STATSIG_ID)
        self.assertEqual(headers["x-xai-request-id"], "request-id")
        self.assertEqual(headers["Cookie"], "sso=plain-token; sso-rw=plain-token; cf_clearance=profile-clearance")
        self.assertNotIn("Authorization", headers)

    def test_app_chat_headers_normalize_cookie_token_without_overriding_clearance(self) -> None:
        with mock.patch.object(grok, "_grok_console_profile", return_value=types.SimpleNamespace(
            user_agent="Test UA",
            cf_clearance="profile-clearance",
        )):
            headers = grok.app_chat_headers(" sso=stored ; cf_clearance=stored-clearance ")

        self.assertEqual(headers["Cookie"], "sso=stored; cf_clearance=stored-clearance; sso-rw=stored")
        self.assertNotIn("Authorization", headers)

    def test_build_app_chat_payload_uses_mode_tier_and_image_flags(self) -> None:
        spec = resolve_model("grok-4.20-heavy")
        payload = grok.build_app_chat_payload(
            spec,
            {"n": 2},
            [{"role": "user", "content": "Draw a cat"}],
            image_generation=True,
        )

        self.assertEqual(payload["message"], "Draw a cat")
        self.assertEqual(payload["modeId"], "heavy")
        self.assertEqual(payload["modelTier"], "heavy")
        self.assertTrue(payload["preferBest"])
        self.assertTrue(payload["enableImageGeneration"])
        self.assertTrue(payload["enableImageStreaming"])
        self.assertEqual(payload["imageGenerationCount"], 2)
        self.assertFalse(payload["returnImageBytes"])

    def test_app_chat_reasoning_and_text_extraction(self) -> None:
        events = grok.app_chat_line_events([
            b'data: {"result":{"response":{"token":"plan ","isThinking":true}}}',
            json.dumps({"result": {"response": {"token": "answer", "messageTag": "final"}}}),
            'data: {"result":{"response":{"finalMetadata":{}}}}',
        ])

        response = grok.collect_app_chat_response(events)

        self.assertEqual(response, {"content": "answer", "reasoning_content": "plan "})

    def test_collect_app_chat_response_accumulates_final_tag_tokens(self) -> None:
        events = [
            {"result": {"response": {"token": "Hello", "messageTag": "final"}}},
            {"result": {"response": {"token": " world", "messageTag": "final"}}},
            {"result": {"response": {"isSoftStop": True}}},
        ]

        response = grok.collect_app_chat_response(events)

        self.assertEqual(response, {"content": "Hello world", "reasoning_content": ""})

    def test_message_tag_final_is_not_app_chat_final_event(self) -> None:
        self.assertFalse(grok.is_app_chat_final_event({"result": {"response": {"messageTag": "final"}}}))
        self.assertTrue(grok.is_app_chat_final_event({"result": {"response": {"finalMetadata": {}}}}))
        self.assertTrue(grok.is_app_chat_final_event({"result": {"response": {"isSoftStop": True}}}))

    def test_extract_app_chat_image_url_from_final_chunk(self) -> None:
        event = {
            "result": {
                "response": {
                    "cardAttachment": {
                        "jsonData": {
                            "image_chunk": {
                                "progress": 100,
                                "imageUrl": "generated/cat.png",
                            }
                        }
                    }
                }
            }
        }

        self.assertEqual(grok.extract_app_chat_image_url(event), "https://assets.grok.com/generated/cat.png")

    def test_extract_app_chat_image_url_from_json_string_final_chunk(self) -> None:
        event = {
            "result": {
                "response": {
                    "cardAttachment": {
                        "jsonData": json.dumps({
                            "image_chunk": {
                                "progress": 100,
                                "imageUrl": "/generated/dog.png",
                            }
                        })
                    }
                }
            }
        }

        self.assertEqual(grok.extract_app_chat_image_url(event), "https://assets.grok.com/generated/dog.png")

    def test_streaming_grok_chat_completion_returns_openai_chunks(self) -> None:
        body = {
            "model": "grok-4.20-multi-agent",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with mock.patch.object(grok, "console_chat_completion", return_value=grok.GrokConsoleCompletion(content="Hi there")):
            chunks = list(openai_v1_chat_complete.handle(body))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["object"], "chat.completion.chunk")
        self.assertEqual(chunks[0]["model"], "grok-4.20-multi-agent")
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant", "content": "Hi there"})
        self.assertIsNone(chunks[0]["choices"][0]["finish_reason"])
        self.assertEqual(chunks[1]["choices"][0]["delta"], {})
        self.assertEqual(chunks[1]["choices"][0]["finish_reason"], "stop")

    def test_console_grok_reasoning_model_uses_console_path(self) -> None:
        spec = resolve_model("grok-4.20-reasoning")

        self.assertFalse(openai_v1_chat_complete.is_grok_app_chat_model(spec))

    def test_streaming_grok_console_completion_emits_reasoning_content(self) -> None:
        body = {
            "model": "grok-4.20-reasoning",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with mock.patch.object(
            grok,
            "console_chat_completion",
            return_value=grok.GrokConsoleCompletion(content="Hi", reasoning_content="think"),
        ) as patched_console, mock.patch.object(grok, "app_chat_completion_events") as patched_app_chat:
            chunks = list(openai_v1_chat_complete.handle(body))

        patched_console.assert_called_once()
        patched_app_chat.assert_not_called()
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant", "reasoning_content": "think"})
        self.assertEqual(chunks[1]["choices"][0]["delta"], {"content": "Hi"})
        self.assertEqual(chunks[2]["choices"][0]["finish_reason"], "stop")

    def test_streaming_grok_app_chat_completion_emits_reasoning_content(self) -> None:
        body = {
            "model": "grok-4.20-heavy",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        events = [
            {"result": {"response": {"token": "think", "isThinking": True}}},
            {"result": {"response": {"token": "Hi", "messageTag": "final"}}},
        ]
        with mock.patch.object(grok, "app_chat_completion_events", return_value=iter(events)):
            chunks = list(openai_v1_chat_complete.handle(body))

        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant", "reasoning_content": "think"})
        self.assertEqual(chunks[1]["choices"][0]["delta"], {"content": "Hi"})
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")

    def test_non_streaming_grok_app_chat_completion_includes_reasoning_content(self) -> None:
        body = {
            "model": "grok-4.20-heavy",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with mock.patch.object(grok, "app_chat_completion", return_value={"content": "Hi", "reasoning_content": "think"}):
            response = openai_v1_chat_complete.handle(body)

        message = response["choices"][0]["message"]
        self.assertEqual(message["content"], "Hi")
        self.assertEqual(message["reasoning_content"], "think")

    def test_non_streaming_grok_console_completion_includes_reasoning_content(self) -> None:
        body = {
            "model": "grok-4.20-reasoning",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with mock.patch.object(
            grok,
            "console_chat_completion",
            return_value=grok.GrokConsoleCompletion(content="Hi", reasoning_content="think"),
        ) as patched_console, mock.patch.object(grok, "app_chat_completion") as patched_app_chat:
            response = openai_v1_chat_complete.handle(body)

        patched_console.assert_called_once()
        patched_app_chat.assert_not_called()
        message = response["choices"][0]["message"]
        self.assertEqual(message["content"], "Hi")
        self.assertEqual(message["reasoning_content"], "think")

    def test_grok_image_lite_chat_routes_to_app_chat_image_outputs(self) -> None:
        body = {
            "model": "grok-imagine-image-lite",
            "messages": [{"role": "user", "content": "Draw a cat"}],
        }
        outputs = [FakeImageOutput(kind="result", model="grok-imagine-image-lite", index=1, total=1, data=[{"url": "https://assets.grok.com/cat.png"}])]
        result = {"created": 1, "data": [{"b64_json": "abc", "url": "https://assets.grok.com/cat.png"}]}
        with (
            mock.patch.object(grok, "app_chat_image_outputs", return_value=iter(outputs)) as patched,
            mock.patch.object(openai_v1_chat_complete, "collect_image_outputs", return_value=result),
        ):
            response = openai_v1_chat_complete.handle(body)

        patched.assert_called_once()
        self.assertIn("data:image/png;base64,abc", response["choices"][0]["message"]["content"])

    def test_unsupported_grok_image_model_raises_openai_error(self) -> None:
        spec = resolve_model("grok-imagine-image-pro")
        with self.assertRaises(FakeImageGenerationError) as context:
            list(grok.app_chat_image_outputs({"prompt": "Draw"}, spec, "Draw"))

        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(context.exception.code, "unsupported_model")
        self.assertEqual(context.exception.param, "model")

    def test_grok_console_default_network_profile_matches_existing_behavior(self) -> None:
        with mock.patch.object(grok.config, "data", {}):
            headers = grok._headers("token-value")

        self.assertEqual(headers["User-Agent"], "Mozilla/5.0 (webchat2api grok console)")
        self.assertEqual(headers["Cookie"], "sso=token-value")
        self.assertEqual(headers["Authorization"], "Bearer token-value")

        created: list[dict[str, object]] = []

        class FakeSession:
            headers: dict[str, str] = {}

            def __init__(self, **kwargs: object) -> None:
                created.append(kwargs)

            def close(self) -> None:
                pass

        with mock.patch.object(grok.config, "data", {}), mock.patch("curl_cffi.requests.Session", FakeSession):
            client = grok.GrokConsoleClient("token-value")

        self.assertEqual(created, [{"impersonate": "edge101", "verify": True}])
        self.assertEqual(client.network_profile.timeout, 60)

    def test_grok_console_uses_configured_network_profile(self) -> None:
        settings = {
            "network_profiles": {
                "grok_console": {
                    "impersonate": "chrome136",
                    "user-agent": "Configured Grok UA",
                    "verify": False,
                    "timeout": 12.5,
                }
            }
        }
        created: list[dict[str, object]] = []

        class FakeSession:
            headers: dict[str, str] = {}

            def __init__(self, **kwargs: object) -> None:
                created.append(kwargs)

            def close(self) -> None:
                pass

        with mock.patch.object(grok.config, "data", settings), mock.patch("curl_cffi.requests.Session", FakeSession):
            client = grok.GrokConsoleClient("sso=configured-cookie")
            headers = grok._headers("sso=configured-cookie")

        self.assertEqual(created, [{"impersonate": "chrome136", "verify": False}])
        self.assertEqual(client.network_profile.timeout, 12.5)
        self.assertEqual(headers["User-Agent"], "Configured Grok UA")
        self.assertEqual(headers["Cookie"], "sso=configured-cookie")

    def test_grok_console_session_preserves_proxy_kwargs(self) -> None:
        created: list[dict[str, object]] = []

        class FakeSession:
            headers: dict[str, str] = {}

            def __init__(self, **kwargs: object) -> None:
                created.append(kwargs)

            def close(self) -> None:
                pass

        with (
            mock.patch.object(grok.config, "data", {}),
            mock.patch.object(grok.config, "get_proxy_settings", return_value="http://proxy.local:8080"),
            mock.patch("curl_cffi.requests.Session", FakeSession),
        ):
            grok.GrokConsoleClient("token-value")

        self.assertEqual(created, [{"impersonate": "edge101", "verify": True, "proxy": "http://proxy.local:8080"}])


if __name__ == "__main__":
    unittest.main()
