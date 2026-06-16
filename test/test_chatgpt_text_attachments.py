from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_tiktoken_stub()

from services.config import config
from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import openai_v1_chat_complete
from services.providers.gpt.runtime import maybe_attach_long_text_messages


class ChatGPTTextAttachmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_data = dict(config.data)

    def tearDown(self) -> None:
        config.data = self.original_data

    def enable_attachments(self, **overrides: object) -> None:
        settings: dict[str, object] = {
            "enabled": True,
            "threshold_tokens": 10,
            "threshold_chars": 40,
            "max_attachment_bytes": 0,
            "mime_type": "text/markdown",
            "file_extension": "md",
            "mode": "largest_user_message",
        }
        settings.update(overrides)
        config.data["chatgpt_text_attachments"] = settings

    def test_disabled_keeps_messages_unchanged(self) -> None:
        messages = [{"role": "user", "content": "x" * 100}]

        self.assertIs(maybe_attach_long_text_messages(messages, "auto"), messages)

    def test_short_text_keeps_messages_unchanged(self) -> None:
        self.enable_attachments(threshold_tokens=1000, threshold_chars=1000)
        messages = [{"role": "user", "content": "short"}]

        self.assertEqual(maybe_attach_long_text_messages(messages, "auto"), messages)

    def test_long_user_text_becomes_file_part(self) -> None:
        self.enable_attachments()
        text = "marker-123 " + "x" * 80
        messages = [{"role": "user", "content": text}]

        updated = maybe_attach_long_text_messages(messages, "auto")

        content = updated[0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "file")
        self.assertEqual(content[1]["data"], text.encode("utf-8"))
        self.assertEqual(content[1]["mime"], "text/markdown")
        self.assertEqual(content[1]["name"], "long_context_1.md")

    def test_only_user_text_is_attached(self) -> None:
        self.enable_attachments()
        messages = [
            {"role": "system", "content": "s" * 100},
            {"role": "assistant", "content": "a" * 100},
            {"role": "tool", "content": "t" * 100},
            {"role": "user", "content": "u" * 100},
        ]

        updated = maybe_attach_long_text_messages(messages, "auto")

        self.assertEqual(updated[0]["content"], messages[0]["content"])
        self.assertEqual(updated[1]["content"], messages[1]["content"])
        self.assertEqual(updated[2]["content"], messages[2]["content"])
        self.assertIsInstance(updated[3]["content"], list)

    def test_long_text_with_image_preserves_image_part(self) -> None:
        self.enable_attachments()
        messages = [{"role": "user", "content": [{"type": "text", "text": "x" * 100}, {"type": "image", "data": b"abc", "mime": "image/png"}]}]

        updated = maybe_attach_long_text_messages(messages, "auto")

        content = updated[0]["content"]
        self.assertEqual(content[1]["type"], "file")
        self.assertEqual(content[2]["type"], "image")

    def test_payload_supports_text_file_attachment(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.access_token = "token"
        uploaded = {"file_id": "file-abc", "file_name": "context.md", "file_size": 9, "mime_type": "text/markdown", "file_token_size": 3, "non_library_my_files_injest_upload": True}
        with mock.patch.object(backend, "_upload_file", return_value=uploaded) as upload_file:
            messages = backend._api_messages_to_conversation_messages([{
                "role": "user",
                "content": [
                    {"type": "text", "text": "请总结附件"},
                    {"type": "file", "data": b"long text", "mime": "text/markdown", "name": "context.md"},
                ],
            }])

        upload_file.assert_called_once_with(b"long text", "context.md", "text/markdown", use_case="my_files")
        message = messages[0]
        self.assertEqual(message["content"]["content_type"], "text")
        self.assertEqual(message["content"]["parts"], ["请总结附件"])
        self.assertEqual(message["metadata"]["attachments"], [{"id": "file-abc", "mimeType": "text/markdown", "mime_type": "text/markdown", "name": "context.md", "size": 9, "file_token_size": 3, "source": "my_files", "non_library_my_files_injest_upload": True, "is_big_paste": True}])

    def test_text_chat_parts_only_attaches_for_gpt_provider(self) -> None:
        self.enable_attachments()
        body = {"model": "gpt-5", "messages": [{"role": "user", "content": "x" * 100}]}
        with mock.patch.object(openai_v1_chat_complete, "resolve_model", return_value=type("Spec", (), {"provider": "gpt"})()):
            _, messages, original = openai_v1_chat_complete.text_chat_parts(body)

        self.assertIsInstance(messages[0]["content"], list)
        self.assertEqual(original[0]["content"], "x" * 100)

    def test_text_chat_parts_skips_non_gpt_provider(self) -> None:
        self.enable_attachments()
        body = {"model": "gemini-3-flash", "messages": [{"role": "user", "content": "x" * 100}]}
        with mock.patch.object(openai_v1_chat_complete, "resolve_model", return_value=type("Spec", (), {"provider": "gemini"})()):
            _, messages, _ = openai_v1_chat_complete.text_chat_parts(body)

        self.assertEqual(messages[0]["content"], "x" * 100)


if __name__ == "__main__":
    unittest.main()
