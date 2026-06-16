from __future__ import annotations

import threading
import unittest
from typing import Any, cast
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_tiktoken_stub()

from services.config import config
from services.protocol import openai_v1_chat_complete
from services.protocol.chat_completion_cache import cache_key, chat_completion_cache, normalize_text_messages
from services.protocol.conversation import count_message_tokens


class ChatCompletionCacheProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_data = dict(config.data)
        chat_completion_cache.clear()

    def tearDown(self) -> None:
        config.data = self.original_data
        chat_completion_cache.clear()

    def enable_cache(self, **overrides: object) -> None:
        settings: dict[str, object] = {
            "enabled": True,
            "ttl_seconds": 60,
            "max_entries": 8,
            "cache_stream": False,
            "cache_tool_calls": False,
            "dedupe_inflight": True,
        }
        settings.update(overrides)
        config.data["chat_completion_cache"] = settings

    def test_non_streaming_text_completion_reuses_cached_response_copy(self) -> None:
        self.enable_cache()
        calls = 0

        def fake_chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: object = None) -> str:
            nonlocal calls
            calls += 1
            return "cached text"

        body = {"model": "auto", "temperature": 0, "messages": [{"role": "user", "content": "Hello"}]}
        with (
            mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_chat_complete.gpt_chat, "chat_completion", side_effect=fake_chat_completion),
        ):
            first = cast(dict[str, Any], openai_v1_chat_complete.handle(dict(body)))
            first["choices"][0]["message"]["content"] = "mutated"
            second = cast(dict[str, Any], openai_v1_chat_complete.handle(dict(body)))

        self.assertEqual(calls, 1)
        self.assertEqual(second["choices"][0]["message"]["content"], "cached text")

    def test_cache_key_changes_for_temperature(self) -> None:
        messages = [{"role": "user", "content": "Hello"}]
        cold = cache_key({"model": "auto", "temperature": 0, "messages": messages}, messages, stream=False)
        warm = cache_key({"model": "auto", "temperature": 1, "messages": messages}, messages, stream=False)

        self.assertNotEqual(cold, warm)

    def test_cache_key_changes_for_extra_output_affecting_fields(self) -> None:
        messages = [{"role": "user", "content": "Hello"}]
        fast = cache_key({"model": "auto", "temperature": 0, "reasoning_effort": "low", "messages": messages}, messages, stream=False)
        deep = cache_key({"model": "auto", "temperature": 0, "reasoning_effort": "high", "messages": messages}, messages, stream=False)

        self.assertNotEqual(fast, deep)

    def test_cache_key_ignores_body_messages_in_favor_of_normalized_messages(self) -> None:
        raw_messages = [{"role": "user", "content": "Hello"}, {"role": "user", "content": "Hello"}]
        normalized_messages = [{"role": "user", "content": "Hello"}]
        first = cache_key({"model": "auto", "temperature": 0, "messages": raw_messages}, normalized_messages, stream=False)
        second = cache_key({"model": "auto", "temperature": 0, "messages": normalized_messages}, normalized_messages, stream=False)

        self.assertEqual(first, second)

    def test_cache_requires_deterministic_sampling(self) -> None:
        self.enable_cache()
        deterministic_body = {"model": "auto", "temperature": 0, "messages": [{"role": "user", "content": "Hello"}]}
        default_sampling_body = {"model": "auto", "messages": [{"role": "user", "content": "Hello"}]}
        warm_body = {"model": "auto", "temperature": 0.2, "messages": [{"role": "user", "content": "Hello"}]}

        self.assertTrue(openai_v1_chat_complete.is_cacheable_text_request(deterministic_body, stream=False))
        self.assertFalse(openai_v1_chat_complete.is_cacheable_text_request(default_sampling_body, stream=False))
        self.assertFalse(openai_v1_chat_complete.is_cacheable_text_request(warm_body, stream=False))

    def test_streaming_and_tool_requests_bypass_cache(self) -> None:
        self.enable_cache()

        stream_body = {"model": "auto", "temperature": 0, "stream": True, "messages": [{"role": "user", "content": "Hello"}]}
        tool_body = {
            "model": "auto",
            "temperature": 0,
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
        }
        with (
            mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_chat_complete, "stream_text_deltas", return_value=iter(["one"])),
            mock.patch.object(openai_v1_chat_complete, "stream_tool_text_chat_completion", return_value=iter([])) as stream_tool,
            mock.patch.object(openai_v1_chat_complete.gpt_chat, "chat_completion", side_effect=["first", "second"]) as chat_completion,
        ):
            list(cast(Any, openai_v1_chat_complete.handle(dict(stream_body))))
            cast(dict[str, Any], openai_v1_chat_complete.handle(dict(tool_body)))
            cast(dict[str, Any], openai_v1_chat_complete.handle(dict(tool_body)))

        stream_tool.assert_not_called()
        self.assertEqual(chat_completion.call_count, 2)

    def test_exceptions_are_not_cached(self) -> None:
        self.enable_cache()
        body = {"model": "auto", "temperature": 0, "messages": [{"role": "user", "content": "Hello"}]}
        failing_then_ok = mock.Mock(side_effect=[RuntimeError("boom"), "ok"])

        with (
            mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_chat_complete.gpt_chat, "chat_completion", side_effect=failing_then_ok),
        ):
            with self.assertRaises(RuntimeError):
                openai_v1_chat_complete.handle(dict(body))
            response = cast(dict[str, Any], openai_v1_chat_complete.handle(dict(body)))

        self.assertEqual(response["choices"][0]["message"]["content"], "ok")
        self.assertEqual(failing_then_ok.call_count, 2)

    def test_inflight_identical_requests_share_single_result(self) -> None:
        self.enable_cache()
        started = threading.Event()
        release = threading.Event()
        results: list[dict[str, Any]] = []
        errors: list[BaseException] = []
        body = {"model": "auto", "temperature": 0, "messages": [{"role": "user", "content": "Hello"}]}
        calls = 0

        def fake_chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: object = None) -> str:
            nonlocal calls
            calls += 1
            started.set()
            self.assertTrue(release.wait(5))
            return "shared"

        def run_request() -> None:
            try:
                results.append(cast(dict[str, Any], openai_v1_chat_complete.handle(dict(body))))
            except BaseException as exc:
                errors.append(exc)

        with (
            mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_chat_complete.gpt_chat, "chat_completion", side_effect=fake_chat_completion),
        ):
            first = threading.Thread(target=run_request)
            second = threading.Thread(target=run_request)
            first.start()
            self.assertTrue(started.wait(5))
            second.start()
            release.set()
            first.join(5)
            second.join(5)

        self.assertFalse(errors)
        self.assertEqual(calls, 1)
        self.assertEqual([item["choices"][0]["message"]["content"] for item in results], ["shared", "shared"])

    def test_adjacent_duplicate_normalization_is_opt_in_and_preserves_tools(self) -> None:
        duplicate = {"role": "user", "content": "Hello"}
        tool = {"role": "tool", "tool_call_id": "call_1", "content": "Hello"}
        messages = [duplicate, dict(duplicate), {"role": "assistant", "content": "ok"}, dict(duplicate), tool, dict(tool)]

        self.assertEqual(normalize_text_messages(messages), messages)
        config.data["chat_completion_message_normalization"] = {"enabled": True, "drop_adjacent_duplicates": True}

        normalized = normalize_text_messages(messages)

        self.assertEqual(normalized, [duplicate, {"role": "assistant", "content": "ok"}, duplicate, tool, tool])

    def test_image_content_adds_prompt_token_estimate_without_changing_text_only_count(self) -> None:
        text_messages = [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]
        image_messages = [{"role": "user", "content": [{"type": "text", "text": "Hello"}, {"type": "image", "data": b"abc", "mime": "image/png"}]}]

        text_tokens = count_message_tokens(text_messages, "auto")
        image_tokens = count_message_tokens(image_messages, "auto")

        self.assertEqual(image_tokens, text_tokens + 85)

    def test_long_text_attachment_usage_uses_original_messages(self) -> None:
        config.data["chatgpt_text_attachments"] = {
            "enabled": True,
            "threshold_tokens": 10,
            "threshold_chars": 40,
            "max_attachment_bytes": 10000,
        }
        body = {"model": "gpt-5", "messages": [{"role": "user", "content": "x" * 100}]}
        seen_messages: list[list[dict[str, Any]]] = []

        def fake_chat_completion(_body: dict[str, Any], messages: list[dict[str, Any]], _model: str, backend: object = None) -> str:
            seen_messages.append(messages)
            return "ok"

        with (
            mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_chat_complete.gpt_chat, "chat_completion", side_effect=fake_chat_completion),
            mock.patch.object(openai_v1_chat_complete, "resolve_model", return_value=type("Spec", (), {"provider": "gpt"})()),
        ):
            response = cast(dict[str, Any], openai_v1_chat_complete.handle(dict(body)))

        self.assertIsInstance(seen_messages[0][0]["content"], list)
        self.assertEqual(response["usage"]["prompt_tokens"], count_message_tokens(body["messages"], "gpt-5"))


if __name__ == "__main__":
    unittest.main()
