from __future__ import annotations

import unittest

from test.optional_stubs import install_fastapi_stubs

install_fastapi_stubs()

from services.protocol.error_response import (
    MAX_PUBLIC_ERROR_MESSAGE_LENGTH,
    openai_error_payload,
    sanitize_openai_error_payload,
    sanitize_public_error_message,
)


class PublicErrorSanitizationTests(unittest.TestCase):
    def test_preserves_clear_validation_messages(self) -> None:
        self.assertEqual(
            sanitize_public_error_message("image_url must be a data URL or http URL"),
            "image_url must be a data URL or http URL",
        )
        self.assertEqual(
            openai_error_payload("file_id image references are not supported", 400)["error"]["message"],
            "file_id image references are not supported",
        )
        validation_payload = openai_error_payload(
            [{"loc": ["body", "prompt"], "msg": "Field required"}, {"loc": ["body", "n"], "msg": "Input should be greater than or equal to 1"}],
            422,
        )
        self.assertIn("prompt: Field required", validation_payload["error"]["message"])
        self.assertIn("n: Input should be greater than or equal to 1", validation_payload["error"]["message"])

    def test_replaces_tracebacks_and_python_exception_reprs(self) -> None:
        traceback_message = 'Traceback (most recent call last): File "/srv/app/provider.py", line 42, in call RuntimeError("boom")'
        self.assertEqual(sanitize_public_error_message(traceback_message), "request failed")
        self.assertEqual(sanitize_public_error_message('UpstreamHTTPError(status=500, body="secret")'), "request failed")

    def test_replaces_network_browser_and_raw_upstream_details(self) -> None:
        messages = [
            "curl: (35) TLS connect error: OPENSSL_internal:WRONG_VERSION_NUMBER",
            "HTTPSConnectionPool(host='chatgpt.com', port=443): Max retries exceeded with url: /backend-api/conversation",
            "status=500 body={'error': 'internal'}",
        ]
        for message in messages:
            with self.subTest(message=message):
                self.assertEqual(sanitize_public_error_message(message), "request failed")

    def test_replaces_token_cookie_auth_email_and_inline_image_details(self) -> None:
        messages = [
            "authorization: Bearer sk-secret",
            "Set-Cookie: session=secret",
            "refresh_token=rt-secret",
            "account user@example.com failed upstream",
            "bad payload data:image/png;base64,AA==",
            "raw " + ("A" * 160),
        ]
        for message in messages:
            with self.subTest(message=message):
                self.assertEqual(sanitize_public_error_message(message), "request failed")

    def test_truncates_oversized_safe_messages(self) -> None:
        message = "safe detail " * 100
        sanitized = sanitize_public_error_message(message)
        self.assertEqual(len(sanitized), MAX_PUBLIC_ERROR_MESSAGE_LENGTH)
        self.assertTrue(sanitized.endswith("..."))
        self.assertIn("safe detail", sanitized)

    def test_sanitizes_openai_payload_message_param_and_code(self) -> None:
        payload = sanitize_openai_error_payload(
            {
                "error": {
                    "message": "Traceback with access_token=secret",
                    "type": "server_error",
                    "param": "authorization",
                    "code": "cookie=secret",
                }
            }
        )

        self.assertEqual(payload["error"]["message"], "request failed")
        self.assertEqual(payload["error"]["param"], "request failed")
        self.assertEqual(payload["error"]["code"], "upstream_error")
        self.assertEqual(payload["error"]["type"], "server_error")


if __name__ == "__main__":
    unittest.main()
