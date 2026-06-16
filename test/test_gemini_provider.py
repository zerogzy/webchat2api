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
from services.providers.gemini import models as gemini_models
from services.providers.gemini import client as gemini_client


class GeminiProviderTests(unittest.TestCase):
    def test_stream_gemini_chat_completion_sends_initial_chunk_before_upstream(self) -> None:
        def fake_deltas(body: dict[str, Any], spec: Any, messages: list[dict[str, Any]]):
            yield "识别结果"

        messages = [{"role": "user", "content": [{"type": "text", "text": "描述图片"}]}]
        with mock.patch.object(openai_v1_chat_complete.gemini_chat, "chat_completion_deltas", side_effect=fake_deltas):
            chunks = list(openai_v1_chat_complete.stream_gemini_chat_completion({}, resolve_model("gemini-3-flash"), messages, "gemini-3-flash"))

        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant", "content": ""})
        self.assertEqual(chunks[1]["choices"][0]["delta"], {"content": "识别结果"})
        self.assertEqual(chunks[2]["choices"][0]["finish_reason"], "stop")

    def test_stream_gemini_chat_completion_sends_non_empty_initial_chunk_for_images(self) -> None:
        def fake_deltas(body: dict[str, Any], spec: Any, messages: list[dict[str, Any]]):
            yield "红色"

        messages = [{"role": "user", "content": [{"type": "image", "data": b"image-bytes", "mime": "image/png"}]}]
        with mock.patch.object(openai_v1_chat_complete.gemini_chat, "chat_completion_deltas", side_effect=fake_deltas):
            chunks = list(openai_v1_chat_complete.stream_gemini_chat_completion({}, resolve_model("gemini-3-flash"), messages, "gemini-3-flash"))

        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant", "content": "正在识别图片，请稍候…\n\n"})
        self.assertEqual(chunks[1]["choices"][0]["delta"], {"content": "红色"})
        self.assertEqual(chunks[2]["choices"][0]["finish_reason"], "stop")

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

    def test_account_cookie_header_unquotes_stored_cookie_fields(self) -> None:
        header = gemini.account_cookie_header({
            "cookies": {"__Secure-1PSID": "'psid'", "__Secure-1PSIDTS": '"psidts"', "NID": '"nid"'},
        })

        self.assertEqual(header, "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts; NID=nid")

    def test_cookie_header_helpers_preserve_extra_cookies_and_redact_secrets(self) -> None:
        header = 'NID="nid"; empty=; quoted="value"; __Secure-1PSID="psid"; SNlM0e=token; __Secure-1PSIDTS=psidts;'

        parsed = gemini.parse_cookie_header(header)
        self.assertEqual(parsed["NID"], "nid")
        self.assertEqual(parsed["__Secure-1PSID"], "psid")
        self.assertEqual(parsed["empty"], "")
        self.assertEqual(parsed["quoted"], "value")
        self.assertEqual(
            gemini.cookie_header_from_mapping(parsed),
            "NID=nid; quoted=value; __Secure-1PSID=psid; SNlM0e=token; __Secure-1PSIDTS=psidts",
        )
        self.assertEqual(
            gemini.sanitize_cookie_header(header),
            "NID=nid; quoted=value; __Secure-1PSID=[redacted]; SNlM0e=[redacted]; __Secure-1PSIDTS=[redacted]",
        )

    def test_merge_cookie_headers_deduplicates_and_redacts_debug_output(self) -> None:
        merged = gemini.merge_cookie_headers(
            "NID=old; __Secure-1PSID=psid; __Secure-1PSIDTS=old-ts",
            "NID=new; SID=sid; __Secure-1PSIDTS=new-ts",
        )

        self.assertEqual(merged, "NID=new; __Secure-1PSID=psid; __Secure-1PSIDTS=new-ts; SID=sid")
        self.assertEqual(
            gemini.sanitize_cookie_header(merged),
            "NID=new; __Secure-1PSID=[redacted]; __Secure-1PSIDTS=[redacted]; SID=sid",
        )

    def test_cookie_header_from_response_reads_set_cookie_headers(self) -> None:
        class FakeResponse:
            headers = {"Set-Cookie": "NID=nid; Path=/, __Secure-1PSIDTS=new-ts; Path=/; Secure"}
            cookies = None

        self.assertEqual(gemini.cookie_header_from_response(FakeResponse()), "NID=nid; __Secure-1PSIDTS=new-ts")

    def test_account_cookie_header_strips_session_token_fields(self) -> None:
        header = gemini.account_cookie_header({
            "access_token": "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts; SNlM0e=token; at=token",
        })

        self.assertEqual(header, "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts")

    def test_gemini_cookie_state_normalizes_account_without_session_token_fields(self) -> None:
        state = gemini.gemini_cookie_state({
            "access_token": "__Secure-1PSID=old; __Secure-1PSIDTS=old-ts; SNlM0e=token",
            "cookies": {"__Secure-1PSID": "stored", "NID": "nid", "at": "at-token"},
            "__Secure-1PSIDTS": "field-ts",
        })

        self.assertEqual(state.psid, "stored")
        self.assertEqual(state.psidts, "old-ts")
        self.assertEqual(state.cookies, {"__Secure-1PSID": "stored", "__Secure-1PSIDTS": "old-ts", "NID": "nid"})
        self.assertEqual(state.cookie_header, "__Secure-1PSID=stored; __Secure-1PSIDTS=old-ts; NID=nid")

    def test_gemini_cookie_state_reports_missing_required_cookie(self) -> None:
        with self.assertRaises(ValueError) as raised:
            gemini.gemini_cookie_state({"access_token": "__Secure-1PSID=psid"}, require_session_cookies=True)

        self.assertIn("__Secure-1PSIDTS", str(raised.exception))

    def test_account_session_token_precedence_prefers_direct_then_snlm0e_then_at(self) -> None:
        self.assertEqual(
            gemini.account_session_token({
                "session_token": "direct",
                "SNlM0e": "top-snlm0e",
                "at": "top-at",
                "access_token": "SNlM0e=access-snlm0e; at=access-at",
                "cookies": {"SNlM0e": "cookie-snlm0e", "at": "cookie-at"},
            }),
            "direct",
        )
        self.assertEqual(
            gemini.account_session_token({
                "access_token": "SNlM0e=access-snlm0e; at=access-at",
                "cookies": {"SNlM0e": "cookie-snlm0e"},
            }),
            "access-snlm0e",
        )
        self.assertEqual(
            gemini.account_session_token({"access_token": "at=access-at", "cookies": {"SNlM0e": "cookie-snlm0e"}}),
            "cookie-snlm0e",
        )

    def test_account_session_token_reads_account_cookie_and_access_token_shapes(self) -> None:
        self.assertEqual(gemini.account_session_token({"session_token": "direct"}), "direct")
        self.assertEqual(gemini.account_session_token({"access_token": "__Secure-1PSID=psid; SNlM0e=token"}), "token")
        self.assertEqual(gemini.account_session_token({"cookies": {"at": "cookie-token"}}), "cookie-token")
        self.assertEqual(gemini.account_session_token({"cookies": {"SNlM0e": '"quoted-token"'}}), "quoted-token")
