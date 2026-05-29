from __future__ import annotations

import importlib
import importlib.util
import json
import time
import unittest
from unittest import mock
from typing import Any, cast

requests: Any = None
if importlib.util.find_spec("requests") is not None:
    requests = importlib.import_module("requests")

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_tiktoken_stub
from test.utils import save_image

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_tiktoken_stub()

from services.providers.base import ModelSpec
from services.providers.gemini.client import GeminiCompletion
from services.protocol import openai_v1_response

AUTH_KEY = "webchat2api"
BASE_URL = "http://localhost:83"
TEXT_MODEL = "auto"
IMAGE_MODEL = "gpt-image-2"
CODEX_IMAGE_MODEL = "codex-gpt-image-2"


class ResponseRoutingUnitTests(unittest.TestCase):
    def test_gemini_text_response_dispatches_to_gemini_adapter(self) -> None:
        calls: list[tuple[dict[str, Any], ModelSpec, list[dict[str, Any]]]] = []

        def fake_chat_completion(body: dict[str, Any], spec: ModelSpec, messages: list[dict[str, Any]]) -> GeminiCompletion:
            calls.append((body, spec, messages))
            return GeminiCompletion("Gemini routed text")

        with mock.patch.object(openai_v1_response.gemini_chat, "chat_completion", fake_chat_completion):
            result = cast(dict[str, Any], openai_v1_response.handle({"model": "gemini-2.5-pro", "input": "hello"}))

        self.assertEqual(result["object"], "response")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model"], "gemini-2.5-pro")
        self.assertEqual(result["output"][0]["content"][0]["text"], "Gemini routed text")
        self.assertEqual(calls[0][1].provider, "gemini")
        self.assertEqual(calls[0][2], [{"role": "user", "content": "hello"}])

    def test_gemini_text_response_stream_preserves_response_events(self) -> None:
        with mock.patch.object(openai_v1_response.gemini_chat, "chat_completion", return_value=GeminiCompletion("stream text")):
            events = list(cast(Any, openai_v1_response.handle({"model": "gemini-2.5-flash", "input": "hello", "stream": True})))

        event_types = [event.get("type") for event in events]
        self.assertEqual(event_types[0], "response.created")
        self.assertIn("response.output_item.added", event_types)
        self.assertIn("response.output_text.delta", event_types)
        self.assertEqual(event_types[-1], "response.completed")
        self.assertEqual(events[-1]["response"]["output"][0]["content"][0]["text"], "stream text")


@unittest.skipIf(requests is None, "requests is not installed")
class ResponsesTests(unittest.TestCase):
    @staticmethod
    def _iter_sse_payloads(response: Any):
        for line in response.iter_lines():
            if not line:
                continue
            text = line.decode("utf-8", errors="replace")
            yield text

    def test_text_response_http(self):
        """测试 Responses 文本的非流式 HTTP 调用。"""
        response = requests.post(
            f"{BASE_URL}/v1/responses",
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            json={
                "model": TEXT_MODEL,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "你好，请简单介绍一下你自己。"},
                        ],
                    }
                ],
            },
            timeout=300,
        )
        self.assertEqual(response.status_code, 200, response.text)
        print("responses text non-stream status:")
        print(response.status_code)
        print("responses text non-stream result:")
        try:
            payload = response.json()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            self.assertEqual(payload.get("object"), "response")
            self.assertEqual(payload.get("status"), "completed")
            self.assertTrue(isinstance(payload.get("output"), list) and payload.get("output"))
        except Exception:
            print(response.text)
            raise

    def test_text_response_stream_http(self):
        """测试 Responses 文本的流式 HTTP 调用。"""
        response = requests.post(
            f"{BASE_URL}/v1/responses",
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            json={
                "model": TEXT_MODEL,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "你好，请简单介绍一下你自己。"},
                        ],
                    }
                ],
                "stream": True,
            },
            stream=True,
            timeout=300,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(
            response.headers.get("content-type", "").startswith("text/event-stream"),
            response.headers.get("content-type", ""),
        )
        started_at = time.time()
        print("responses text stream status:")
        print(response.status_code)
        print("responses text stream chunks:")
        event_types = []
        for text in self._iter_sse_payloads(response):
            print(f"{time.time() - started_at:6.2f}s {text}")
            if not text.startswith("data:"):
                continue
            payload_text = text[5:].strip()
            if payload_text == "[DONE]":
                break
            try:
                payload = json.loads(payload_text)
            except Exception:
                continue
            event_type = str(payload.get("type") or "")
            if event_type:
                event_types.append(event_type)
        self.assertIn("response.created", event_types)
        self.assertIn("response.completed", event_types)

    def test_image_response_http(self):
        """测试 Responses 画图的非流式 HTTP 调用。"""
        response = requests.post(
            f"{BASE_URL}/v1/responses",
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            json={
                "model": IMAGE_MODEL,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "我想做一张南京城市宣传海报图。"},
                        ],
                    }
                ],
                "tools": [{"type": "image_generation"}],
            },
            timeout=300,
        )
        self.assertEqual(response.status_code, 200, response.text)
        saved_paths = []
        try:
            payload = response.json()
        except Exception:
            payload = {}
        for index, item in enumerate(payload.get("output") or [], start=1):
            if not isinstance(item, dict):
                continue
            image_b64 = str(item.get("result") or "")
            if image_b64:
                saved_paths.append(save_image(image_b64, f"responses_image_non_stream_{index}"))
        print("responses image non-stream status:")
        print(response.status_code)
        print("responses image non-stream result:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("responses image non-stream saved files:")
        for path in saved_paths:
            print(path)

    def test_image_response_stream_http(self):
        """测试 Responses 画图的流式 HTTP 调用。"""
        response = requests.post(
            f"{BASE_URL}/v1/responses",
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            json={
                "model": IMAGE_MODEL,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "我想做一张南京城市宣传海报图。"},
                        ],
                    }
                ],
                "tools": [{"type": "image_generation"}],
                "stream": True,
            },
            stream=True,
            timeout=300,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(
            response.headers.get("content-type", "").startswith("text/event-stream"),
            response.headers.get("content-type", ""),
        )
        started_at = time.time()
        saved_paths = []
        print("responses image stream status:")
        print(response.status_code)
        print("responses image stream chunks:")
        for text in self._iter_sse_payloads(response):
            print(f"{time.time() - started_at:6.2f}s {text}")
            if not text.startswith("data:"):
                continue
            payload_text = text[5:].strip()
            if payload_text == "[DONE]":
                break
            try:
                payload = json.loads(payload_text)
            except Exception:
                continue
            if payload.get("type") != "response.output_item.done":
                continue
            item = payload.get("item") or {}
            if str(item.get("type") or "") != "image_generation_call":
                continue
            image_b64 = str(item.get("result") or "")
            if image_b64:
                saved_paths.append(save_image(image_b64, f"responses_image_stream_{len(saved_paths) + 1}"))
        print("responses image stream saved files:")
        for path in saved_paths:
            print(path)

    def test_codex_image_response_http(self):
        """测试 Responses 的 codex 画图非流式 HTTP 调用。"""
        response = requests.post(
            f"{BASE_URL}/v1/responses",
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            json={
                "model": CODEX_IMAGE_MODEL,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "我想做一张南京城市宣传海报图。"},
                        ],
                    }
                ],
                "tools": [{"type": "image_generation"}],
            },
            timeout=300,
        )
        self.assertEqual(response.status_code, 200, response.text)
        saved_paths = []
        try:
            payload = response.json()
        except Exception:
            payload = {}
        for index, item in enumerate(payload.get("output") or [], start=1):
            if not isinstance(item, dict):
                continue
            image_b64 = str(item.get("result") or "")
            if image_b64:
                saved_paths.append(save_image(image_b64, f"responses_codex_image_non_stream_{index}"))
        print("responses codex image non-stream status:")
        print(response.status_code)
        print("responses codex image non-stream result:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("responses codex image non-stream saved files:")
        for path in saved_paths:
            print(path)

    def test_codex_image_response_stream_http(self):
        """测试 Responses 的 codex 画图流式 HTTP 调用。"""
        response = requests.post(
            f"{BASE_URL}/v1/responses",
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            json={
                "model": CODEX_IMAGE_MODEL,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "我想做一张南京城市宣传海报图。"},
                        ],
                    }
                ],
                "tools": [{"type": "image_generation"}],
                "stream": True,
            },
            stream=True,
            timeout=300,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(
            response.headers.get("content-type", "").startswith("text/event-stream"),
            response.headers.get("content-type", ""),
        )
        started_at = time.time()
        saved_paths = []
        print("responses codex image stream status:")
        print(response.status_code)
        print("responses codex image stream chunks:")
        for text in self._iter_sse_payloads(response):
            print(f"{time.time() - started_at:6.2f}s {text}")
            if not text.startswith("data:"):
                continue
            payload_text = text[5:].strip()
            if payload_text == "[DONE]":
                break
            try:
                payload = json.loads(payload_text)
            except Exception:
                continue
            if payload.get("type") != "response.output_item.done":
                continue
            item = payload.get("item") or {}
            if str(item.get("type") or "") != "image_generation_call":
                continue
            image_b64 = str(item.get("result") or "")
            if image_b64:
                saved_paths.append(save_image(image_b64, f"responses_codex_image_stream_{len(saved_paths) + 1}"))
        print("responses codex image stream saved files:")
        for path in saved_paths:
            print(path)


if __name__ == "__main__":
    unittest.main()
