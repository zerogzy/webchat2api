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

from fastapi import HTTPException

from services.models import resolve_model
from services.network import flaresolverr
from services.protocol import openai_v1_chat_complete, openai_v1_response
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

    def test_build_console_payload_defaults_web_search_tool(self) -> None:
        spec = resolve_model("grok-4.3")
        payload = grok.build_console_payload(
            spec,
            {},
            [{"role": "user", "content": "Search the web."}],
        )

        self.assertEqual(payload["tools"], [{"type": "web_search"}])

    def test_build_console_payload_preserves_supported_search_tools(self) -> None:
        spec = resolve_model("grok-4.3")
        web_search = {"type": "web_search", "allowed_websites": ["example.com"]}
        x_search = {"type": "x_search", "post_favorite_count": 10}
        payload = grok.build_console_payload(
            spec,
            {"tools": [web_search, {"type": "image_generation"}, x_search]},
            [{"role": "user", "content": "Search the web."}],
        )

        self.assertEqual(payload["tools"], [web_search, x_search])

    def test_build_console_payload_preserves_response_tool_controls(self) -> None:
        spec = resolve_model("grok-4.3")
        payload = grok.build_console_payload(
            spec,
            {
                "tools": [{"type": "web_search"}],
                "tool_choice": "auto",
                "parallel_tool_calls": True,
            },
            [{"role": "user", "content": "Search the web"}],
        )

        self.assertEqual(payload["tools"], [{"type": "web_search"}])
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertTrue(payload["parallel_tool_calls"])

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

    def test_extract_console_stream_delta_from_output_text_delta(self) -> None:
        delta = grok.extract_console_stream_delta({"type": "response.output_text.delta", "delta": "hello"})

        self.assertEqual(delta.content, "hello")
        self.assertEqual(delta.reasoning_content, "")

    def test_extract_console_stream_delta_from_reasoning_delta(self) -> None:
        delta = grok.extract_console_stream_delta({"type": "response.reasoning_summary_text.delta", "delta": "think"})

        self.assertEqual(delta.content, "")
        self.assertEqual(delta.reasoning_content, "think")

    def test_extract_console_stream_delta_ignores_completed_snapshot(self) -> None:
        delta = grok.extract_console_stream_delta({"type": "response.completed", "output_text": "complete text"})

        self.assertEqual(delta.content, "")
        self.assertEqual(delta.reasoning_content, "")

    def test_app_chat_headers_use_grok_app_shape_with_plain_token(self) -> None:
        with (
            mock.patch.object(grok, "_grok_app_chat_profile", return_value=types.SimpleNamespace(
                user_agent="Test UA",
                cf_clearance="",
                cf_cookies="",
                sec_ch_ua="test sec ua",
                sec_ch_ua_mobile="?0",
                sec_ch_ua_platform='"Windows"',
                statsig_id="statsig-test",
            )),
            mock.patch.object(grok.uuid, "uuid4", return_value="request-id"),
        ):
            headers = grok.app_chat_headers("plain-token")

        self.assertEqual(headers["Accept"], "*/*")
        self.assertEqual(headers["Accept-Encoding"], "gzip, deflate, br, zstd")
        self.assertEqual(headers["Accept-Language"], "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Origin"], "https://grok.com")
        self.assertEqual(headers["Referer"], "https://grok.com/")
        self.assertEqual(headers["Priority"], "u=1, i")
        self.assertEqual(headers["Sec-Fetch-Dest"], "empty")
        self.assertEqual(headers["Sec-Fetch-Mode"], "cors")
        self.assertEqual(headers["Sec-Fetch-Site"], "same-origin")
        self.assertEqual(headers["Sec-Ch-Ua"], "test sec ua")
        self.assertEqual(headers["Sec-Ch-Ua-Mobile"], "?0")
        self.assertEqual(headers["Sec-Ch-Ua-Platform"], '"Windows"')
        self.assertEqual(headers["User-Agent"], "Test UA")
        self.assertEqual(headers["x-statsig-id"], "statsig-test")
        self.assertEqual(headers["x-xai-request-id"], "request-id")
        self.assertEqual(headers["Cookie"], "sso=plain-token; sso-rw=plain-token")
        self.assertNotIn("cf_clearance", headers["Cookie"])
        self.assertNotIn("Authorization", headers)

    def test_app_chat_headers_normalize_simple_sso_cookie_token(self) -> None:
        with mock.patch.object(grok, "_grok_app_chat_profile", return_value=types.SimpleNamespace(
            user_agent="Test UA",
            cf_clearance="",
            cf_cookies="",
            sec_ch_ua="",
            sec_ch_ua_mobile="",
            sec_ch_ua_platform="",
            statsig_id=grok.GROK_APP_CHAT_STATSIG_ID,
        )):
            headers = grok.app_chat_headers(" sso=plain-token ")

        self.assertEqual(headers["Cookie"], "sso=plain-token; sso-rw=plain-token")
        self.assertNotIn("cf_clearance", headers["Cookie"])
        self.assertNotIn("Authorization", headers)

    def test_app_chat_headers_append_optional_cloudflare_profile_cookies(self) -> None:
        with mock.patch.object(grok, "_grok_app_chat_profile", return_value=types.SimpleNamespace(
            user_agent="Test UA",
            cf_clearance="profile-clearance",
            cf_cookies="cf_bm=profile-bm",
            sec_ch_ua="",
            sec_ch_ua_mobile="",
            sec_ch_ua_platform="",
            statsig_id=grok.GROK_APP_CHAT_STATSIG_ID,
        )):
            headers = grok.app_chat_headers("plain-token")

        self.assertEqual(headers["Cookie"], "sso=plain-token; sso-rw=plain-token; cf_bm=profile-bm; cf_clearance=profile-clearance")
        self.assertNotIn("Authorization", headers)

    def test_app_chat_cookie_merges_cloudflare_cookies_and_replaces_clearance(self) -> None:
        cookie = grok._app_chat_cookie(
            " sso=stored ; sso-rw=old ; cf_clearance=stored-clearance ; other=value ",
            " profile-clearance ",
            " cf_bm=profile-bm ; cf_clearance=cf-cookie-clearance ",
        )

        self.assertEqual(cookie, "sso=stored; sso-rw=stored; cf_clearance=profile-clearance; other=value; cf_bm=profile-bm")

    def test_app_chat_cookie_merges_solver_cookies_without_overriding_sso(self) -> None:
        cookie = grok._app_chat_cookie(
            " sso=stored ; sso-rw=old ",
            " solved-clearance ",
            " sso=solver ; sso-rw=solver-rw ; x-challenge=challenge ; x-signature=signature ; cf_clearance=solver-clearance ",
        )

        self.assertEqual(cookie, "sso=stored; sso-rw=stored; x-challenge=challenge; x-signature=signature; cf_clearance=solved-clearance")

    def test_app_chat_headers_normalize_cookie_token_without_overriding_clearance(self) -> None:
        with mock.patch.object(grok, "_grok_app_chat_profile", return_value=types.SimpleNamespace(
            user_agent="Test UA",
            cf_clearance="profile-clearance",
            cf_cookies="",
            sec_ch_ua="",
            sec_ch_ua_mobile="",
            sec_ch_ua_platform="",
            statsig_id=grok.GROK_APP_CHAT_STATSIG_ID,
        )):
            headers = grok.app_chat_headers(" sso=stored ; cf_clearance=stored-clearance ")

        self.assertEqual(headers["Cookie"], "sso=stored; sso-rw=stored; cf_clearance=profile-clearance")
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
        self.assertEqual(payload["collectionIds"], [])
        self.assertEqual(payload["connectors"], [])
        self.assertEqual(payload["deviceEnvInfo"], {
            "darkModeEnabled": False,
            "devicePixelRatio": 2,
            "screenHeight": 1329,
            "screenWidth": 2056,
            "viewportHeight": 1083,
            "viewportWidth": 2056,
        })
        self.assertTrue(payload["disableMemory"])
        self.assertFalse(payload["disableSearch"])
        self.assertFalse(payload["disableSelfHarmShortCircuit"])
        self.assertFalse(payload["disableTextFollowUps"])
        self.assertTrue(payload["enableImageGeneration"])
        self.assertTrue(payload["enableImageStreaming"])
        self.assertTrue(payload["enableSideBySide"])
        self.assertEqual(payload["fileAttachments"], [])
        self.assertFalse(payload["forceConcise"])
        self.assertFalse(payload["forceSideBySide"])
        self.assertEqual(payload["imageAttachments"], [])
        self.assertEqual(payload["imageGenerationCount"], 2)
        self.assertFalse(payload["isAsyncChat"])
        self.assertEqual(payload["responseMetadata"], {})
        self.assertFalse(payload["returnImageBytes"])
        self.assertFalse(payload["returnRawGrokInXaiRequest"])
        self.assertFalse(payload["searchAllConnectors"])
        self.assertTrue(payload["sendFinalMetadata"])
        self.assertTrue(payload["temporary"])
        self.assertEqual(payload["toolOverrides"], {
            "imageGen": False,
            "webSearch": False,
            "xSearch": False,
            "xMediaSearch": False,
            "trendsSearch": False,
            "xPostAnalyze": False,
        })

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
        events = [
            {"type": "response.output_text.delta", "delta": "Hi"},
            {"type": "response.output_text.delta", "delta": " there"},
            {"type": "response.completed"},
        ]
        with (
            mock.patch.object(grok, "console_chat_completion_events", return_value=iter(events)) as patched_stream,
            mock.patch.object(grok, "console_chat_completion") as patched_blocking,
        ):
            chunks = list(openai_v1_chat_complete.handle(body))

        patched_stream.assert_called_once()
        patched_blocking.assert_not_called()
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0]["object"], "chat.completion.chunk")
        self.assertEqual(chunks[0]["model"], "grok-4.20-multi-agent")
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant", "content": "Hi"})
        self.assertIsNone(chunks[0]["choices"][0]["finish_reason"])
        self.assertEqual(chunks[1]["choices"][0]["delta"], {"content": " there"})
        self.assertEqual(chunks[2]["choices"][0]["delta"], {})
        self.assertEqual(chunks[2]["choices"][0]["finish_reason"], "stop")

    def test_console_grok_reasoning_model_uses_console_path(self) -> None:
        spec = resolve_model("grok-4.20-reasoning")

        self.assertFalse(openai_v1_chat_complete.is_grok_app_chat_model(spec))

    def test_streaming_grok_console_completion_emits_reasoning_content(self) -> None:
        body = {
            "model": "grok-4.20-reasoning",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        events = [
            {"type": "response.reasoning_summary_text.delta", "delta": "think"},
            {"type": "response.output_text.delta", "delta": "Hi"},
        ]
        with (
            mock.patch.object(grok, "console_chat_completion_events", return_value=iter(events)) as patched_console,
            mock.patch.object(grok, "app_chat_completion_events") as patched_app_chat,
            mock.patch.object(grok, "console_chat_completion") as patched_blocking,
        ):
            chunks = list(openai_v1_chat_complete.handle(body))

        patched_console.assert_called_once()
        patched_app_chat.assert_not_called()
        patched_blocking.assert_not_called()
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

    def test_responses_grok_console_routes_to_console_completion(self) -> None:
        body = {
            "model": "grok-4.3",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "Hello"}]}],
            "tools": [{"type": "web_search"}],
        }
        with mock.patch.object(
            grok,
            "console_chat_completion",
            return_value=grok.GrokConsoleCompletion(content="Hi from Grok"),
        ) as patched_console:
            response = openai_v1_response.handle(body)

        patched_console.assert_called_once()
        self.assertEqual(patched_console.call_args.args[0]["tools"], [{"type": "web_search"}])
        self.assertEqual(response["object"], "response")
        self.assertEqual(response["status"], "completed")
        content = response["output"][0]["content"][0]
        self.assertEqual(content["type"], "output_text")
        self.assertEqual(content["text"], "Hi from Grok")

    def test_streaming_responses_grok_console_emits_response_events(self) -> None:
        body = {
            "model": "grok-4.3",
            "input": "Hello",
            "stream": True,
        }
        with mock.patch.object(
            grok,
            "console_chat_completion",
            return_value=grok.GrokConsoleCompletion(content="Hi"),
        ) as patched_console:
            events = list(openai_v1_response.handle(body))

        patched_console.assert_called_once()
        event_types = [event.get("type") for event in events]
        self.assertEqual(event_types[0], "response.created")
        self.assertIn("response.output_text.delta", event_types)
        self.assertEqual(event_types[-1], "response.completed")

    def test_responses_unknown_non_grok_model_uses_text_backend(self) -> None:
        body = {
            "model": "custom-text-model",
            "input": "Hello",
        }
        with (
            mock.patch.object(openai_v1_response, "ConversationRequest", lambda **kwargs: kwargs),
            mock.patch.object(openai_v1_response, "stream_text_deltas", return_value=iter(["generic"])) as patched_stream,
            mock.patch.object(grok, "console_chat_completion") as patched_console,
        ):
            response = openai_v1_response.handle(body)

        patched_stream.assert_called_once()
        patched_console.assert_not_called()
        self.assertEqual(response["output"][0]["content"][0]["text"], "generic")

    def test_responses_grok_app_chat_returns_explicit_error(self) -> None:
        body = {
            "model": "grok-4.20-heavy",
            "input": "Hello",
        }
        with self.assertRaises(HTTPException) as ctx:
            list(openai_v1_response.handle(body))

        self.assertEqual(getattr(ctx.exception, "status_code", None), 501)
        self.assertIn("Grok app-chat is not supported", str(getattr(ctx.exception, "detail", "")))

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
        spec = resolve_model("grok-imagine-image-edit")
        with self.assertRaises(FakeImageGenerationError) as context:
            list(grok.app_chat_image_outputs({"prompt": "Draw"}, spec, "Draw"))

        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(context.exception.code, "unsupported_model")
        self.assertEqual(context.exception.param, "model")

    def test_app_chat_error_classification_is_specific(self) -> None:
        cases = {
            401: (401, "authentication failed"),
            403: (403, "forbidden"),
            429: (429, "rate limited"),
        }
        for upstream_status, (openai_status, message) in cases.items():
            with self.subTest(upstream_status=upstream_status):
                error = grok.classify_app_chat_upstream_error(upstream_status)

                self.assertEqual(error.status_code, openai_status)
                self.assertEqual(error.upstream_status, upstream_status)
                self.assertIn(message, str(error))

    def test_app_chat_403_classification_does_not_say_unsupported_model(self) -> None:
        error = grok.classify_app_chat_upstream_error(403)

        self.assertNotIn("unsupported", str(error).lower())
        self.assertNotIn("secret", str(error).lower())
        self.assertNotIn("browser", str(error).lower())
        self.assertNotIn("cf_clearance", str(error).lower())

    def test_app_chat_completion_uses_model_aware_account_selection(self) -> None:
        account_service = types.SimpleNamespace(
            get_grok_app_chat_access_token=mock.Mock(return_value="selected-token"),
            get_account=mock.Mock(return_value={"access_token": "selected-token", "cf_cookies": "cf_bm=account-bm"}),
            mark_text_used=mock.Mock(),
        )
        sys.modules["services.account_service"] = types.SimpleNamespace(account_service=account_service)
        spec = resolve_model("grok-4.20-heavy")

        with mock.patch.object(grok, "GrokAppChatClient") as client_class:
            client = client_class.return_value.__enter__.return_value
            client.stream_events.return_value = iter([{"result": {"response": {"token": "Hi", "messageTag": "final"}}}])
            events = list(grok.app_chat_completion_events({}, spec, [{"role": "user", "content": "Hello"}]))

        account_service.get_grok_app_chat_access_token.assert_called_once_with(spec)
        account_service.get_account.assert_called_once_with("selected-token")
        client_class.assert_called_once_with("selected-token", {"access_token": "selected-token", "cf_cookies": "cf_bm=account-bm"})
        self.assertEqual(events[0]["result"]["response"]["token"], "Hi")
        account_service.mark_text_used.assert_called_once_with("selected-token")

    def test_app_chat_headers_use_account_metadata_over_global_profile(self) -> None:
        with mock.patch.object(grok, "_grok_app_chat_profile", return_value=types.SimpleNamespace(
            user_agent="Global UA",
            cf_clearance="global-clearance",
            cf_cookies="cf_bm=global-bm",
            sec_ch_ua="global sec ua",
            sec_ch_ua_mobile="?0",
            sec_ch_ua_platform='"Windows"',
            statsig_id="global-statsig",
        )):
            headers = grok.app_chat_headers("selected-token", {
                "user_agent": "Account UA",
                "cf_cookies": "cf_bm=account-bm; cf_clearance=account-cookie-clearance",
                "cf_clearance": "account-clearance",
                "sec_ch_ua": "account sec ua",
                "sec_ch_ua_mobile": "?1",
                "sec_ch_ua_platform": '"Linux"',
            })

        self.assertEqual(headers["User-Agent"], "Account UA")
        self.assertEqual(headers["Sec-Ch-Ua"], "account sec ua")
        self.assertEqual(headers["Sec-Ch-Ua-Mobile"], "?1")
        self.assertEqual(headers["Sec-Ch-Ua-Platform"], '"Linux"')
        self.assertEqual(headers["x-statsig-id"], "global-statsig")
        self.assertEqual(headers["Cookie"], "sso=selected-token; sso-rw=selected-token; cf_bm=account-bm; cf_clearance=account-clearance")

    def test_grok_app_chat_client_uses_account_impersonate_without_leaking_to_console_headers(self) -> None:
        settings = {
            "network_profiles": {
                "grok_console": {"user-agent": "Console UA", "cf_clearance": "console-clearance"},
                "grok_app_chat": {"impersonate": "global-browser", "user-agent": "Global UA"},
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
            client = grok.GrokAppChatClient("selected-token", {
                "browser": "account-browser",
                "user_agent": "Account UA",
                "cf_cookies": "cf_bm=account-bm",
            })
            app_headers = grok.app_chat_headers("selected-token", client.account)
            console_headers = grok._headers("selected-token")

        self.assertEqual(created, [{"impersonate": "account-browser", "verify": True}])
        self.assertEqual(app_headers["User-Agent"], "Account UA")
        self.assertIn("cf_bm=account-bm", app_headers["Cookie"])
        self.assertEqual(console_headers["User-Agent"], "Console UA")
        self.assertEqual(console_headers["Cookie"], "sso=selected-token; cf_clearance=console-clearance")

    def test_grok_console_default_network_profile_matches_existing_behavior(self) -> None:
        with mock.patch.object(grok.config, "data", {}):
            headers = grok._headers("token-value")

        self.assertEqual(headers["User-Agent"], "Mozilla/5.0 (webchat2api grok console)")
        self.assertEqual(headers["Cookie"], "sso=token-value")
        self.assertEqual(headers["Authorization"], "Bearer token-value")

    def test_grok_console_default_session_uses_network_profile(self) -> None:
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

    def test_grok_console_stream_response_parses_sse_lines(self) -> None:
        calls: list[dict[str, object]] = []
        closed: list[bool] = []

        class FakeResponse:
            status_code = 200

            def iter_lines(self):
                return iter([
                    b": keepalive",
                    b"event: response.output_text.delta",
                    b'data: {"type":"response.output_text.delta","delta":"Hi"}',
                    b"data: [DONE]",
                    b'data: {"type":"response.output_text.delta","delta":" ignored"}',
                ])

            def close(self) -> None:
                closed.append(True)

        class FakeSession:
            headers: dict[str, str] = {}

            def __init__(self, **kwargs: object) -> None:
                pass

            def post(self, url: str, **kwargs: object) -> FakeResponse:
                calls.append({"url": url, **kwargs})
                return FakeResponse()

            def close(self) -> None:
                pass

        with mock.patch.object(grok.config, "data", {}), mock.patch("curl_cffi.requests.Session", FakeSession):
            client = grok.GrokConsoleClient("token-value")
            events = list(client.stream_response({"model": "grok-4.3", "input": []}))

        self.assertEqual(events, [{"type": "response.output_text.delta", "delta": "Hi"}])
        self.assertEqual(calls[0]["url"], grok.CONSOLE_RESPONSES_URL)
        self.assertTrue(calls[0]["stream"])
        self.assertEqual(calls[0]["json"]["stream"], True)
        self.assertEqual(closed, [True])

    def test_grok_console_stream_response_uses_sse_event_name_when_data_has_no_type(self) -> None:
        class FakeResponse:
            status_code = 200

            def iter_lines(self):
                return iter([
                    b"event: response.reasoning_summary_text.delta",
                    b'data: {"delta":"think"}',
                    b"event: response.output_text.delta",
                    b'data: {"delta":"Hi"}',
                    b"data: [DONE]",
                ])

        class FakeSession:
            headers: dict[str, str] = {}

            def __init__(self, **kwargs: object) -> None:
                pass

            def post(self, url: str, **kwargs: object) -> FakeResponse:
                return FakeResponse()

            def close(self) -> None:
                pass

        with mock.patch.object(grok.config, "data", {}), mock.patch("curl_cffi.requests.Session", FakeSession):
            client = grok.GrokConsoleClient("token-value")
            events = list(client.stream_response({"model": "grok-4.3", "input": []}))

        self.assertEqual(
            events,
            [
                {"type": "response.reasoning_summary_text.delta", "delta": "think"},
                {"type": "response.output_text.delta", "delta": "Hi"},
            ],
        )
        self.assertEqual(grok.extract_console_stream_delta(events[0]).reasoning_content, "think")
        self.assertEqual(grok.extract_console_stream_delta(events[1]).content, "Hi")

    def test_grok_console_stream_response_aggregates_multiline_sse_data(self) -> None:
        class FakeResponse:
            status_code = 200

            def iter_lines(self):
                return iter([
                    b"event: response.output_text.delta",
                    b'data: {"delta":',
                    b'data: "Hi"}',
                    b"",
                    b"data:",
                    b"",
                    b"data: [DONE]",
                ])

        class FakeSession:
            headers: dict[str, str] = {}

            def __init__(self, **kwargs: object) -> None:
                pass

            def post(self, url: str, **kwargs: object) -> FakeResponse:
                return FakeResponse()

            def close(self) -> None:
                pass

        with mock.patch.object(grok.config, "data", {}), mock.patch("curl_cffi.requests.Session", FakeSession):
            client = grok.GrokConsoleClient("token-value")
            events = list(client.stream_response({"model": "grok-4.3", "input": []}))

        self.assertEqual(events, [{"type": "response.output_text.delta", "delta": "Hi"}])
        self.assertEqual(grok.extract_console_stream_delta(events[0]).content, "Hi")

    def test_grok_console_stream_response_resets_sse_event_after_dispatch(self) -> None:
        class FakeResponse:
            status_code = 200

            def iter_lines(self):
                return iter([
                    b"event: response.reasoning_summary_text.delta",
                    b'data: {"delta":"think"}',
                    b"",
                    b'data: {"delta":"plain"}',
                    b"",
                    b"data: [DONE]",
                ])

        class FakeSession:
            headers: dict[str, str] = {}

            def __init__(self, **kwargs: object) -> None:
                pass

            def post(self, url: str, **kwargs: object) -> FakeResponse:
                return FakeResponse()

            def close(self) -> None:
                pass

        with mock.patch.object(grok.config, "data", {}), mock.patch("curl_cffi.requests.Session", FakeSession):
            client = grok.GrokConsoleClient("token-value")
            events = list(client.stream_response({"model": "grok-4.3", "input": []}))

        self.assertEqual(events[0], {"type": "response.reasoning_summary_text.delta", "delta": "think"})
        self.assertEqual(events[1], {"delta": "plain"})
        self.assertEqual(grok.extract_console_stream_delta(events[0]).reasoning_content, "think")
        self.assertEqual(grok.extract_console_stream_delta(events[1]).content, "plain")

    def test_grok_console_stream_marks_account_used_when_generator_is_closed(self) -> None:
        account_service = types.SimpleNamespace(
            get_text_access_token=mock.Mock(return_value="selected-token"),
            mark_text_used=mock.Mock(),
        )
        spec = resolve_model("grok-4.3")

        class FakeClient:
            def __init__(self, access_token: str) -> None:
                self.access_token = access_token

            def __enter__(self) -> "FakeClient":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                pass

            def stream_response(self, payload):
                yield {"type": "response.output_text.delta", "delta": "Hi"}
                yield {"type": "response.output_text.delta", "delta": " later"}

        with (
            mock.patch.dict(sys.modules, {"services.account_service": types.SimpleNamespace(account_service=account_service)}),
            mock.patch.object(grok, "GrokConsoleClient", FakeClient),
        ):
            events = grok.console_chat_completion_events(
                {"model": "grok-4.3"},
                spec,
                [{"role": "user", "content": "Hello"}],
            )
            self.assertEqual(next(events), {"type": "response.output_text.delta", "delta": "Hi"})
            events.close()

        account_service.mark_text_used.assert_called_once_with("selected-token")

    def test_grok_console_stream_marks_account_used_when_stream_completes_without_events(self) -> None:
        account_service = types.SimpleNamespace(
            get_text_access_token=mock.Mock(return_value="selected-token"),
            mark_text_used=mock.Mock(),
        )
        spec = resolve_model("grok-4.3")

        class FakeClient:
            def __init__(self, access_token: str) -> None:
                self.access_token = access_token

            def __enter__(self) -> "FakeClient":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                pass

            def stream_response(self, payload):
                return iter(())

        with (
            mock.patch.dict(sys.modules, {"services.account_service": types.SimpleNamespace(account_service=account_service)}),
            mock.patch.object(grok, "GrokConsoleClient", FakeClient),
        ):
            events = list(grok.console_chat_completion_events(
                {"model": "grok-4.3"},
                spec,
                [{"role": "user", "content": "Hello"}],
            ))

        self.assertEqual(events, [])
        account_service.mark_text_used.assert_called_once_with("selected-token")

    def test_grok_console_stream_marks_account_used_after_partial_stream_error(self) -> None:
        account_service = types.SimpleNamespace(
            get_text_access_token=mock.Mock(return_value="selected-token"),
            mark_text_used=mock.Mock(),
        )
        spec = resolve_model("grok-4.3")

        class FakeClient:
            def __init__(self, access_token: str) -> None:
                self.access_token = access_token

            def __enter__(self) -> "FakeClient":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                pass

            def stream_response(self, payload):
                yield {"type": "response.output_text.delta", "delta": "Hi"}
                raise grok.GrokConsoleError("stream failed", 502)

        with (
            mock.patch.dict(sys.modules, {"services.account_service": types.SimpleNamespace(account_service=account_service)}),
            mock.patch.object(grok, "GrokConsoleClient", FakeClient),
        ):
            events = grok.console_chat_completion_events(
                {"model": "grok-4.3"},
                spec,
                [{"role": "user", "content": "Hello"}],
            )
            self.assertEqual(next(events), {"type": "response.output_text.delta", "delta": "Hi"})
            with self.assertRaises(grok.HTTPException):
                next(events)

        account_service.mark_text_used.assert_called_once_with("selected-token")

    def test_grok_console_stream_response_raises_stream_errors(self) -> None:
        class FakeResponse:
            status_code = 200

            def iter_lines(self):
                return iter([
                    b'data: {"type":"response.failed","error":{"message":"upstream failed"}}',
                ])

        class FakeSession:
            headers: dict[str, str] = {}

            def __init__(self, **kwargs: object) -> None:
                pass

            def post(self, url: str, **kwargs: object) -> FakeResponse:
                return FakeResponse()

            def close(self) -> None:
                pass

        with mock.patch.object(grok.config, "data", {}), mock.patch("curl_cffi.requests.Session", FakeSession):
            client = grok.GrokConsoleClient("token-value")
            with self.assertRaises(grok.GrokConsoleError) as ctx:
                list(client.stream_response({"model": "grok-4.3", "input": []}))

        self.assertIn("upstream failed", str(ctx.exception))

    def test_grok_console_stream_response_includes_upstream_error_detail(self) -> None:
        account_service = types.SimpleNamespace(update_account=mock.Mock())

        class FakeResponse:
            status_code = 402

            def json(self):
                return {"error": {"message": "quota exhausted"}}

        class FakeSession:
            headers: dict[str, str] = {}

            def __init__(self, **kwargs: object) -> None:
                pass

            def post(self, url: str, **kwargs: object) -> FakeResponse:
                return FakeResponse()

            def close(self) -> None:
                pass

        with (
            mock.patch.dict(sys.modules, {"services.account_service": types.SimpleNamespace(account_service=account_service)}),
            mock.patch.object(grok.config, "data", {}),
            mock.patch("curl_cffi.requests.Session", FakeSession),
        ):
            client = grok.GrokConsoleClient("token-value")
            with self.assertRaises(grok.GrokConsoleError) as ctx:
                list(client.stream_response({"model": "grok-4.3", "input": []}))

        self.assertIn("quota exhausted", str(ctx.exception))
        account_service.update_account.assert_called_once_with("token-value", {"status": "限流"})

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

    def test_grok_app_chat_uses_dedicated_network_profile(self) -> None:
        settings = {
            "network_profiles": {
                "grok_console": {
                    "impersonate": "console-browser",
                    "user-agent": "Console UA",
                    "timeout": 11,
                },
                "grok_app_chat": {
                    "impersonate": "app-browser",
                    "user-agent": "App UA",
                    "verify": False,
                    "timeout": 7,
                    "cf_clearance": "app-clearance",
                },
            },
        }
        created: list[dict[str, object]] = []

        class FakeSession:
            headers: dict[str, str] = {}

            def __init__(self, **kwargs: object) -> None:
                created.append(kwargs)

            def close(self) -> None:
                pass

        with mock.patch.object(grok.config, "data", settings), mock.patch("curl_cffi.requests.Session", FakeSession):
            client = grok.GrokAppChatClient("token-value")
            headers = grok.app_chat_headers("token-value")

        self.assertEqual(created, [{"impersonate": "app-browser", "verify": False}])
        self.assertEqual(client.network_profile.timeout, 7)
        self.assertEqual(headers["User-Agent"], "App UA")
        self.assertEqual(headers["Cookie"], "sso=token-value; sso-rw=token-value; cf_clearance=app-clearance")

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

    def test_app_chat_status_feedback_updates_account(self) -> None:
        account_service = types.SimpleNamespace(update_account=mock.Mock())
        with mock.patch.dict(sys.modules, {"services.account_service": types.SimpleNamespace(account_service=account_service)}):
            for upstream_status in (401, 403, 429):
                with self.subTest(upstream_status=upstream_status):
                    error = grok.classify_app_chat_upstream_error(upstream_status, "token-value")
                    self.assertEqual(error.status_code, upstream_status)

        self.assertEqual(account_service.update_account.mock_calls, [
            mock.call("token-value", {"status": "异常"}),
            mock.call("token-value", {"status": "异常"}),
            mock.call("token-value", {"status": "限流"}),
        ])

    def test_grok_app_chat_uses_flaresolverr_on_403_then_retries(self) -> None:
        class FakeResponse:
            def __init__(self, status_code: int, lines: list[bytes] | None = None) -> None:
                self.status_code = status_code
                self._lines = lines or []

            def iter_lines(self):
                return iter(self._lines)

        class FakeSession:
            def __init__(self, **kwargs: object) -> None:
                self.headers: dict[str, str] = {}
                self.calls: list[dict[str, object]] = []
                self.responses = [
                    FakeResponse(403),
                    FakeResponse(200, [b'data: {"result":{"response":{"token":"ok","messageTag":"final"}}}']),
                ]

            def post(self, url: str, **kwargs: object) -> FakeResponse:
                self.calls.append({"url": url, **kwargs})
                return self.responses.pop(0)

            def close(self) -> None:
                pass

        settings = {
            "flaresolverr_url": "http://solver.local",
            "network_profiles": {"grok_app_chat": {"user-agent": "Old UA", "cf_clearance": "old-clearance"}},
        }
        clearance = types.SimpleNamespace(
            user_agent="Solved UA",
            cf_clearance="solved-clearance",
            cf_cookies="cf_clearance=solved-clearance; __cf_bm=solved-bm; x-challenge=solved-challenge",
        )
        updates: list[dict[str, object]] = []

        def fake_update(data: dict[str, object]) -> dict[str, object]:
            updates.append(data)
            settings.update(data)
            return settings

        with (
            mock.patch.object(grok.config, "data", settings),
            mock.patch.object(grok.config, "update", side_effect=fake_update) as update,
            mock.patch.object(grok.FlareSolverrClearanceProvider, "solve", return_value=clearance) as solve,
            mock.patch.object(grok, "create_session", return_value=FakeSession()),
        ):
            client = grok.GrokAppChatClient("token-value")
            events = list(client.stream_events({"message": "hi"}))

        solve.assert_called_once_with()
        update.assert_called_once()
        self.assertEqual(len(client.session.calls), 2)
        retry_headers = client.session.calls[1]["headers"]
        self.assertEqual(retry_headers["User-Agent"], "Solved UA")
        self.assertIn("cf_clearance=solved-clearance", retry_headers["Cookie"])
        self.assertEqual(client.session.headers["User-Agent"], "Solved UA")
        self.assertEqual(events[0]["result"]["response"]["token"], "ok")
        saved_profile = updates[0]["network_profiles"]["grok_app_chat"]
        self.assertEqual(saved_profile["user-agent"], "Solved UA")
        self.assertEqual(saved_profile["cf_clearance"], "solved-clearance")
        self.assertEqual(saved_profile["cf_cookies"], "cf_clearance=solved-clearance; __cf_bm=solved-bm; x-challenge=solved-challenge")

    def test_grok_app_chat_refresh_derives_coherent_browser_and_headers(self) -> None:
        class FakeSession:
            headers: dict[str, str] = {}

            def close(self) -> None:
                pass

        settings = {
            "flaresolverr_url": "http://solver.local",
            "network_profiles": {"grok_app_chat": {"user-agent": "Old UA", "browser": "chrome136"}},
        }
        clearance = types.SimpleNamespace(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
            cf_clearance="solved-clearance",
            cf_cookies="cf_clearance=solved-clearance; __cf_bm=solved-bm",
        )
        updates: list[dict[str, object]] = []

        def fake_update(data: dict[str, object]) -> dict[str, object]:
            updates.append(data)
            settings.update(data)
            return settings

        with (
            mock.patch.object(grok.config, "data", settings),
            mock.patch.object(grok.config, "update", side_effect=fake_update),
            mock.patch.object(grok.FlareSolverrClearanceProvider, "solve", return_value=clearance),
            mock.patch.object(grok, "create_session", return_value=FakeSession()),
        ):
            client = grok.GrokAppChatClient("token-value")
            self.assertTrue(client._refresh_clearance())
            headers = grok.app_chat_headers("token-value")

        saved_profile = updates[0]["network_profiles"]["grok_app_chat"]
        self.assertEqual(saved_profile["browser"], "chrome141")
        self.assertEqual(saved_profile["impersonate"], "chrome141")
        self.assertEqual(headers["User-Agent"], clearance.user_agent)
        self.assertEqual(headers["Sec-Ch-Ua"], '"Chromium";v="141", "Google Chrome";v="141", "Not.A/Brand";v="99"')
        self.assertEqual(headers["Sec-Ch-Ua-Mobile"], "?0")
        self.assertEqual(headers["Sec-Ch-Ua-Platform"], '"Windows"')

    def test_flaresolverr_provider_posts_solution_request_with_proxy(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, object]:
                return {
                    "solution": {
                        "userAgent": "Solved UA",
                        "cookies": [
                            {"name": "cf_clearance", "value": "clearance-value"},
                            {"name": "__cf_bm", "value": "bm-value"},
                            {"name": "session", "value": "kept"},
                            {"name": "x-challenge", "value": "challenge-value"},
                            {"name": "x-signature", "value": "signature-value"},
                            {"name": "empty", "value": ""},
                            {"name": "bad;name", "value": "bad-value"},
                            {"name": "bad-value", "value": "line\nbreak"},
                        ],
                    }
                }

        with (
            mock.patch.object(flaresolverr.config, "data", {"flaresolverr_url": "http://solver.local", "flaresolverr_timeout_sec": 12}),
            mock.patch.object(flaresolverr.config, "get_proxy_settings", return_value="http://proxy.local:8080"),
            mock.patch.object(flaresolverr.requests, "post", return_value=FakeResponse(), create=True) as post,
        ):
            clearance = flaresolverr.FlareSolverrClearanceProvider().solve()

        post.assert_called_once_with(
            "http://solver.local/v1",
            json={
                "cmd": "request.get",
                "url": "https://grok.com",
                "maxTimeout": 12000,
                "proxy": {"url": "http://proxy.local:8080"},
            },
            timeout=17,
        )
        self.assertIsNotNone(clearance)
        assert clearance is not None
        self.assertEqual(clearance.user_agent, "Solved UA")
        self.assertEqual(clearance.cf_clearance, "clearance-value")
        self.assertEqual(
            clearance.cf_cookies,
            "cf_clearance=clearance-value; __cf_bm=bm-value; session=kept; x-challenge=challenge-value; x-signature=signature-value",
        )


class TestBrowserBridge(unittest.TestCase):
    def test_extract_raw_sso_plain_token(self):
        from services.providers.grok import _extract_raw_sso
        sso = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.test"
        self.assertEqual(_extract_raw_sso(sso), sso)

    def test_extract_raw_sso_with_prefix(self):
        from services.providers.grok import _extract_raw_sso
        self.assertEqual(_extract_raw_sso("sso=abc123"), "abc123")

    def test_extract_raw_sso_from_cookie_header(self):
        from services.providers.grok import _extract_raw_sso
        self.assertEqual(_extract_raw_sso("sso=abc123; cf_clearance=xyz; other=val"), "abc123")

    def test_extract_raw_sso_empty(self):
        from services.providers.grok import _extract_raw_sso
        self.assertEqual(_extract_raw_sso(""), "")
        self.assertEqual(_extract_raw_sso(None), "")

    @mock.patch("services.providers.grok._detect_bridge_url", return_value="")
    def test_try_browser_bridge_returns_none_when_no_bridge(self, _):
        from services.providers.grok import GrokAppChatClient
        client = GrokAppChatClient.__new__(GrokAppChatClient)
        client.access_token = "test_sso"
        self.assertIsNone(client._try_browser_bridge({"message": "hi"}))

    @mock.patch("services.providers.grok._detect_bridge_url", return_value="http://127.0.0.1:3080")
    def test_try_browser_bridge_calls_bridge(self, _):
        from services.providers.grok import GrokAppChatClient
        resp = mock.MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"result":{"response":{"token":"hi"}}}\n'
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        client = GrokAppChatClient.__new__(GrokAppChatClient)
        client.access_token = "test_sso_token"
        with mock.patch("urllib.request.urlopen", return_value=resp):
            result = client._try_browser_bridge({"message": "test"})
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 0)

    @mock.patch("services.providers.grok.config")
    def test_detect_bridge_url_uses_config_first(self, mock_config):
        import services.providers.grok as grok_mod
        mock_config.browser_bridge_url = "http://custom:9999"
        grok_mod._bridge_probed = False
        self.assertEqual(grok_mod._detect_bridge_url(), "http://custom:9999")

    @mock.patch("services.providers.grok._detect_bridge_url", return_value="http://127.0.0.1:3080")
    def test_try_browser_bridge_403_reports_tier_hint(self, _):
        import urllib.error
        from services.providers.grok import GrokAppChatClient, GrokConsoleError
        client = GrokAppChatClient.__new__(GrokAppChatClient)
        client.access_token = "test_sso_token"
        err = urllib.error.HTTPError(
            url="http://127.0.0.1:3080/api/chat",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(GrokConsoleError) as ctx:
                client._try_browser_bridge({"message": "test"})
        self.assertIn("account may lack required tier", str(ctx.exception))

    @mock.patch("services.providers.grok.config")
    def test_app_chat_prefers_direct_when_bridge_is_auto_detected(self, mock_config):
        from services.providers.grok import GrokAppChatClient
        mock_config.browser_bridge_url = ""
        client = GrokAppChatClient.__new__(GrokAppChatClient)
        direct_event = {"result": {"response": {"token": "hi"}}}
        with (
            mock.patch.object(client, "_stream_direct_events", return_value=iter([direct_event])) as direct,
            mock.patch.object(client, "_try_browser_bridge") as bridge,
        ):
            self.assertEqual(list(client.stream_events({"message": "test"})), [direct_event])
        direct.assert_called_once()
        bridge.assert_not_called()

    @mock.patch("services.providers.grok.config")
    def test_app_chat_uses_explicit_bridge_before_direct(self, mock_config):
        from services.providers.grok import GrokAppChatClient
        mock_config.browser_bridge_url = "http://bridge.local"
        client = GrokAppChatClient.__new__(GrokAppChatClient)
        with (
            mock.patch.object(client, "_try_browser_bridge", return_value=['{"result":{"response":{"token":"hi"}}}']) as bridge,
            mock.patch.object(client, "_stream_direct_events") as direct,
        ):
            events = list(client.stream_events({"message": "test"}))
        self.assertEqual(events, [{"result": {"response": {"token": "hi"}}}])
        bridge.assert_called_once()
        direct.assert_not_called()

    @mock.patch("services.providers.grok.config")
    def test_app_chat_falls_back_to_bridge_after_direct_403(self, mock_config):
        from services.providers.grok import GrokAppChatClient, GrokConsoleError
        mock_config.browser_bridge_url = ""
        client = GrokAppChatClient.__new__(GrokAppChatClient)
        with (
            mock.patch.object(client, "_stream_direct_events", side_effect=GrokConsoleError("forbidden", 403, 403)) as direct,
            mock.patch.object(client, "_try_browser_bridge", return_value=['{"result":{"response":{"token":"hi"}}}']) as bridge,
        ):
            events = list(client.stream_events({"message": "test"}))
        self.assertEqual(events, [{"result": {"response": {"token": "hi"}}}])
        direct.assert_called_once()
        bridge.assert_called_once()

    @mock.patch("services.providers.grok.config")
    def test_detect_bridge_url_auto_probes(self, mock_config):
        import services.providers.grok as grok_mod
        mock_config.browser_bridge_url = ""
        grok_mod._bridge_probed = False
        grok_mod._bridge_detected_url = None
        resp = mock.MagicMock()
        resp.status = 200
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        with mock.patch("urllib.request.urlopen", return_value=resp):
            result = grok_mod._detect_bridge_url()
            self.assertEqual(result, "http://127.0.0.1:3080")


if __name__ == "__main__":
    unittest.main()

