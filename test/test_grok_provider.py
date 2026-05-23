from __future__ import annotations

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
conversation.ImageOutput = object
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
    def test_streaming_grok_chat_completion_returns_openai_chunks(self) -> None:
        body = {
            "model": "grok-4.20-multi-agent",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with mock.patch.object(grok, "chat_completion", return_value="Hi there"):
            chunks = list(openai_v1_chat_complete.handle(body))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["object"], "chat.completion.chunk")
        self.assertEqual(chunks[0]["model"], "grok-4.20-multi-agent")
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant", "content": "Hi there"})
        self.assertIsNone(chunks[0]["choices"][0]["finish_reason"])
        self.assertEqual(chunks[1]["choices"][0]["delta"], {})
        self.assertEqual(chunks[1]["choices"][0]["finish_reason"], "stop")

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
