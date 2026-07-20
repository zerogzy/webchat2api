from __future__ import annotations

import unittest

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_tiktoken_stub()

from services.openai_backend_api import DEFAULT_CLIENT_BUILD_NUMBER, DEFAULT_CLIENT_VERSION, OpenAIBackendAPI
from services.providers.gpt.chat import normalize_thinking_effort, thinking_effort_from_body


class ChatGPTUpstreamParityTests(unittest.TestCase):
    def test_client_version_matches_pinned_chatgpt2api_upstream(self) -> None:
        self.assertEqual(DEFAULT_CLIENT_VERSION, "prod-a194cd50d4416d3c0b47c740f206b12ce60f5887")
        self.assertEqual(DEFAULT_CLIENT_BUILD_NUMBER, "6708908")

    def test_thinking_effort_accepts_openai_chat_and_responses_shapes(self) -> None:
        self.assertEqual(thinking_effort_from_body({"reasoning_effort": "high"}), "high")
        self.assertEqual(thinking_effort_from_body({"thinking_effort": "xhigh"}), "extended")
        self.assertEqual(thinking_effort_from_body({"reasoning": {"effort": "medium"}}), "medium")
        self.assertEqual(normalize_thinking_effort("unsupported"), "")

    def test_conversation_payload_only_emits_normalized_thinking_effort(self) -> None:
        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        messages = [{"role": "user", "content": "solve this"}]

        payload = client._conversation_payload(messages, "auto", "Asia/Shanghai", "xhigh")
        default_payload = client._conversation_payload(messages, "auto", "Asia/Shanghai", "")

        self.assertEqual(payload["thinking_effort"], "extended")
        self.assertNotIn("thinking_effort", default_payload)


if __name__ == "__main__":
    unittest.main()
