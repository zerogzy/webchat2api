from __future__ import annotations

import json
import sys
import unittest
from typing import Any, cast
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_tiktoken_stub()

HTTPException = cast(type[Exception], getattr(sys.modules["fastapi"], "HTTPException"))

from services.models import resolve_model
from services.protocol import openai_v1_chat_complete
from services.providers import gemini


class GeminiProviderTests(unittest.TestCase):
    def test_build_web_payload_converts_messages_to_prompt(self) -> None:
        payload = gemini.build_web_payload(
            resolve_model("gemini-2.5-pro"),
            {"temperature": 0.3, "max_tokens": 64},
            [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                {"role": "assistant", "content": "Hi"},
            ],
        )

        self.assertEqual(payload["model"], "gemini-2.5-pro")
        self.assertEqual(payload["temperature"], 0.3)
        self.assertEqual(payload["max_tokens"], 64)
        self.assertEqual(payload["prompt"], "System: Be brief.\n\nUser: Hello\n\nAssistant: Hi")

    def test_account_cookie_header_requires_both_session_cookies(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            gemini.account_cookie_header({"access_token": "__Secure-1PSID=psid"})

        self.assertEqual(getattr(raised.exception, "status_code"), 400)
        self.assertIn("__Secure-1PSIDTS", str(getattr(raised.exception, "detail")))

    def test_account_cookie_header_accepts_stored_cookie_fields(self) -> None:
        header = gemini.account_cookie_header({
            "cookies": {"__Secure-1PSID": "psid"},
            "__Secure-1PSIDTS": "psidts",
        })

        self.assertEqual(header, "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts")

    def test_cookie_header_helpers_preserve_extra_cookies_and_redact_secrets(self) -> None:
        header = "NID=nid; __Secure-1PSID=psid; SNlM0e=token; __Secure-1PSIDTS=psidts"

        parsed = gemini.parse_cookie_header(header)
        self.assertEqual(parsed["NID"], "nid")
        self.assertEqual(gemini.cookie_header_from_mapping(parsed), header)
        self.assertEqual(
            gemini.sanitize_cookie_header(header),
            "NID=nid; __Secure-1PSID=[redacted]; SNlM0e=[redacted]; __Secure-1PSIDTS=[redacted]",
        )

    def test_account_cookie_header_strips_session_token_fields(self) -> None:
        header = gemini.account_cookie_header({
            "access_token": "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts; SNlM0e=token; at=token",
        })

        self.assertEqual(header, "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts")

    def test_account_session_token_reads_account_cookie_and_access_token_shapes(self) -> None:
        self.assertEqual(gemini.account_session_token({"session_token": "direct"}), "direct")
        self.assertEqual(gemini.account_session_token({"access_token": "__Secure-1PSID=psid; SNlM0e=token"}), "token")
        self.assertEqual(gemini.account_session_token({"cookies": {"at": "cookie-token"}}), "cookie-token")

    def test_stream_generate_payload_uses_web_rpc_envelope_and_session_token(self) -> None:
        data = gemini.build_stream_generate_form_payload("hello", "gemini-2.5-pro", "at-token")

        outer = json.loads(data["f.req"])
        inner = json.loads(outer[1])
        self.assertEqual(outer[0], None)
        self.assertEqual(inner, [["hello"], None, None, "gemini-2.5-pro"])
        self.assertEqual(data["at"], "at-token")
        self.assertTrue(gemini.stream_generate_url("a b").endswith("?at=a%20b"))

    def test_session_token_from_response_reads_primary_and_fallback_shapes(self) -> None:
        self.assertEqual(gemini.session_token_from_response('{"SNlM0e":"primary"}'), "primary")
        self.assertEqual(gemini.session_token_from_response('["SNlM0e","fallback"]'), "fallback")

    def test_list_model_metadata_returns_safe_static_models(self) -> None:
        metadata = gemini.list_model_metadata()

        models = {item["id"]: item for item in metadata}
        self.assertEqual(models["gemini-2.5-pro"]["provider"], "gemini")
        self.assertEqual(models["gemini-2.5-flash"]["owned_by"], "google")
        self.assertEqual(models["gemini-pro"]["root"], "gemini-pro")
        metadata[0]["id"] = "mutated"
        self.assertEqual(gemini.list_model_metadata()[0]["id"], "gemini-2.5-pro")

    def test_extract_text_from_nested_gemini_payload(self) -> None:
        payload = [["unused"], [{"candidate": {"content": "Gemini response"}}]]

        self.assertEqual(gemini.extract_completion(payload).content, "Gemini response")

    def test_parse_web_response_text_reads_json_line(self) -> None:
        parsed = gemini.parse_web_response_text(")]}'\n\n[{\"content\": \"Gemini response\"}]")

        self.assertEqual(gemini.extract_completion(parsed).content, "Gemini response")

    def test_parse_web_response_text_targets_realistic_wrb_stream_generate_payload(self) -> None:
        stream_payload = json.dumps([
            ["conversation-id"],
            ["response-id", [["Gemini realistic response"]]],
            ["rc_abc123", "gemini-2.5-pro"],
        ])
        frame = json.dumps([
            ["wrb.fr", "not-the-stream-rpc", "[\"incidental metadata\"]", None, None, None, "generic"],
            ["wrb.fr", "assistant.lamda.BardFrontendService.StreamGenerate", stream_payload, None, None, None, "generic"],
        ])
        parsed = gemini.parse_web_response_text(")]}'\n\n123\n" + frame)

        self.assertEqual(gemini.extract_completion(parsed).content, "Gemini realistic response")

    def test_parse_web_response_text_prefers_wrb_candidate_over_incidental_strings(self) -> None:
        stream_payload = json.dumps(["rpc-id", "request-id", [["Candidate answer wins"]]])
        parsed = [
            ["wrb.fr", "assistant.lamda.BardFrontendService.StreamGenerate", stream_payload, None],
            ["metadata", "gemini-2.5-pro", "request-id"],
        ]

        self.assertEqual(gemini.extract_completion(parsed).content, "Candidate answer wins")

    def test_extract_completion_ignores_session_tokens_as_text(self) -> None:
        parsed = [["wrb.fr", "assistant.lamda.BardFrontendService.StreamGenerate", json.dumps(["SNlM0e", "Gemini answer"]), None]]

        self.assertEqual(gemini.extract_completion(parsed).content, "Gemini answer")

    def test_classify_upstream_error_maps_auth_rate_limit_and_server_failures(self) -> None:
        auth = gemini.classify_upstream_error(403, "SNlM0e not found")
        rate = gemini.classify_upstream_error(429, "quota")
        server = gemini.classify_upstream_error(503, "unavailable")

        self.assertEqual(auth.status_code, 401)
        self.assertEqual(auth.code, "gemini_auth_failed")
        self.assertEqual(rate.status_code, 429)
        self.assertEqual(rate.code, "gemini_rate_limited")
        self.assertEqual(server.status_code, 502)
        self.assertEqual(server.code, "gemini_upstream_unavailable")

    def test_gemini_web_client_posts_hardened_headers_and_payload_without_logging_cookie(self) -> None:
        class FakeResponse:
            status_code = 200
            text = ")]}\'\n\n[[\"content\", \"ok\"]]"

        class FakeSession:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def post(self, url: str, headers: dict[str, str], data: dict[str, str], timeout: int) -> FakeResponse:
                self.calls.append({"url": url, "headers": headers, "data": data, "timeout": timeout})
                return FakeResponse()

        fake_session = FakeSession()
        with mock.patch("services.providers.gemini.client.create_session", return_value=fake_session):
            client = gemini.GeminiWebClient("__Secure-1PSID=psid; __Secure-1PSIDTS=psidts")
            parsed = client.generate({"prompt": "hello", "model": "gemini-2.5-pro", "session_token": "at-token"})

        self.assertEqual(gemini.extract_completion(parsed).content, "ok")
        call = fake_session.calls[0]
        self.assertIn("?at=at-token", call["url"])
        self.assertEqual(call["headers"]["x-same-domain"], "1")
        self.assertEqual(call["headers"]["cookie"], "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts")
        self.assertEqual(call["data"]["at"], "at-token")
        inner = json.loads(json.loads(call["data"]["f.req"])[1])
        self.assertEqual(inner, [["hello"], None, None, "gemini-2.5-pro"])

    def test_gemini_image_chat_request_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            openai_v1_chat_complete.handle({
                "model": "gemini-2.5-pro",
                "messages": [{"role": "user", "content": "draw a cat"}],
                "modalities": ["image"],
            })

        self.assertEqual(getattr(raised.exception, "status_code"), 400)
        detail = cast(dict[str, Any], getattr(raised.exception, "detail"))
        self.assertEqual(detail["code"], "unsupported_model")
        self.assertIn("Gemini image chat", detail["error"])

    def test_gemini_streaming_image_chat_request_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            result = openai_v1_chat_complete.handle({
                "model": "gemini-2.5-pro",
                "stream": True,
                "messages": [{"role": "user", "content": "draw a cat"}],
                "modalities": ["image"],
            })
            list(cast(Any, result))

        self.assertEqual(getattr(raised.exception, "status_code"), 400)

    def test_non_streaming_chat_dispatch_uses_gemini_provider(self) -> None:
        with mock.patch.object(gemini, "chat_completion", return_value=gemini.GeminiCompletion("Hello from Gemini")) as chat_completion:
            response = openai_v1_chat_complete.handle({
                "model": "gemini-2.5-pro",
                "messages": [{"role": "user", "content": "Hello"}],
            })

        result = cast(dict[str, Any], response)
        chat_completion.assert_called_once()
        self.assertEqual(result["choices"][0]["message"]["content"], "Hello from Gemini")
        self.assertEqual(result["model"], "gemini-2.5-pro")

    def test_streaming_chat_dispatch_synthetically_chunks_gemini_response(self) -> None:
        with mock.patch.object(gemini, "chat_completion", return_value=gemini.GeminiCompletion("abcdef")), \
             mock.patch.object(gemini, "synthetic_stream_content", return_value=iter(["abc", "def"])):
            chunks = list(cast(Any, openai_v1_chat_complete.handle({
                "model": "gemini-2.5-pro",
                "stream": True,
                "messages": [{"role": "user", "content": "Hello"}],
            })))

        deltas = [choice["delta"] for chunk in chunks for choice in chunk["choices"]]
        self.assertEqual(deltas[0], {"role": "assistant", "content": "abc"})
        self.assertEqual(deltas[1], {"content": "def"})
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")


if __name__ == "__main__":
    unittest.main()
