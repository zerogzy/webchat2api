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

    def test_build_web_payload_rejects_image_url_part(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            gemini.build_web_payload(
                resolve_model("gemini-2.5-pro"),
                {},
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "What is in this image?"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                        ],
                    }
                ],
            )

        self.assertEqual(getattr(raised.exception, "status_code"), 400)
        detail = cast(dict[str, Any], getattr(raised.exception, "detail"))
        self.assertEqual(detail["error"], "Gemini Web image input is not supported by this upstream adapter")

    def test_build_web_payload_rejects_responses_input_image_part(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            gemini.build_web_payload(
                resolve_model("gemini-2.5-pro"),
                {},
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Describe it"},
                            {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
                        ],
                    }
                ],
            )

        self.assertEqual(getattr(raised.exception, "status_code"), 400)
        self.assertIn("Gemini Web image input", str(getattr(raised.exception, "detail")))

    def test_build_web_payload_rejects_native_inline_data_part(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            gemini.build_web_payload(
                resolve_model("gemini-2.5-pro"),
                {},
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe it"},
                            {"inlineData": {"mimeType": "image/png", "data": "AA=="}},
                        ],
                    }
                ],
            )

        self.assertEqual(getattr(raised.exception, "status_code"), 400)
        self.assertIn("Gemini Web image input", str(getattr(raised.exception, "detail")))

    def test_build_web_payload_rejects_openai_compatible_nested_image_payload_key(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            gemini.build_web_payload(
                resolve_model("gemini-2.5-pro"),
                {},
                [{"role": "user", "content": {"text": "Describe it", "image_url": "data:image/png;base64,AA=="}}],
            )

        self.assertEqual(getattr(raised.exception, "status_code"), 400)
        self.assertIn("Gemini Web image input", str(getattr(raised.exception, "detail")))

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

    def test_stream_generate_payload_uses_web_rpc_envelope_and_session_token(self) -> None:
        data = gemini.build_stream_generate_form_payload("hello", "gemini-2.5-pro", "at-token")

        outer = json.loads(data["f.req"])
        inner = json.loads(outer[1])
        self.assertEqual(outer[0], None)
        self.assertEqual(inner, [["hello"], None, None, "gemini-2.5-pro"])
        self.assertEqual(data["at"], "at-token")
        self.assertEqual(
            gemini.stream_generate_url(),
            "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
        )
        self.assertEqual(
            gemini.stream_generate_url("a b"),
            "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?at=a%20b",
        )

    def test_session_token_from_response_reads_primary_fallback_and_nonce_shapes(self) -> None:
        self.assertEqual(gemini.session_token_from_response('{"SNlM0e":"primary"}'), "primary")
        self.assertEqual(gemini.session_token_from_response('["SNlM0e","fallback"]'), "fallback")
        self.assertEqual(gemini.session_token_from_response('["at","at-token"]'), "at-token")
        self.assertEqual(gemini.session_token_from_response('<script>var at="assignment-token";</script>'), "assignment-token")
        self.assertEqual(gemini.session_token_from_response('<html><body nonce="nonce-token"></body></html>'), "nonce-token")

    def test_rotate_psidts_cookie_sends_accounts_origin_and_keeps_existing_psidts(self) -> None:
        class FakeResponse:
            status_code = 200
            text = ""
            headers: dict[str, str] = {}
            cookies: dict[str, str] = {}

        class FakeSession:
            captured_headers: dict[str, str] = {}
            captured_data = ""

            def post(self, url: str, headers: dict[str, str], data: str, timeout: int) -> FakeResponse:
                self.captured_headers = headers
                self.captured_data = data
                return FakeResponse()

        session = FakeSession()
        header = gemini.rotate_psidts_cookie(session, "__Secure-1PSID=psid; __Secure-1PSIDTS=old-ts")

        self.assertEqual(header, "__Secure-1PSID=psid; __Secure-1PSIDTS=old-ts")
        self.assertEqual(session.captured_headers["origin"], "https://accounts.google.com")
        self.assertEqual(session.captured_data, '[000,"-0000000000000000000"]')

    def test_rotate_psidts_cookie_requires_retained_psidts(self) -> None:
        class FakeResponse:
            status_code = 200
            text = ""
            headers: dict[str, str] = {}
            cookies: dict[str, str] = {}

        class FakeSession:
            def post(self, url: str, headers: dict[str, str], data: str, timeout: int) -> FakeResponse:
                return FakeResponse()

        with self.assertRaises(gemini.GeminiWebError) as raised:
            gemini.rotate_psidts_cookie(FakeSession(), "__Secure-1PSID=psid")

        self.assertEqual(raised.exception.code, "gemini_session_cookie_missing")

    def test_bootstrap_session_token_retries_after_rotation(self) -> None:
        with mock.patch("services.providers.gemini.client.create_session", return_value=mock.Mock()):
            client = gemini.GeminiWebClient("__Secure-1PSID=psid; __Secure-1PSIDTS=old-ts")
        with mock.patch.object(client, "fetch_init_body", side_effect=["<html></html>", '{"SNlM0e":"new-at"}']), \
             mock.patch.object(client, "rotate_psidts", return_value="__Secure-1PSID=psid; __Secure-1PSIDTS=new-ts") as rotate:
            self.assertEqual(client.bootstrap_session_token(), "new-at")

        rotate.assert_called_once()
        self.assertEqual(client.session_token, "new-at")

    def test_fetch_init_body_uses_gemini_app_bootstrap_headers(self) -> None:
        class FakeResponse:
            status_code = 200
            text = '{"SNlM0e":"at"}'
            headers: dict[str, str] = {}
            cookies: dict[str, str] = {}

        class FakeSession:
            calls: list[tuple[str, dict[str, str]]] = []

            def get(self, url: str, headers: dict[str, str], timeout: int) -> FakeResponse:
                self.calls.append((url, headers))
                return FakeResponse()

            def close(self) -> None:
                return None

        with mock.patch("services.providers.gemini.client.create_session", return_value=FakeSession()):
            client = gemini.GeminiWebClient("__Secure-1PSID=psid; __Secure-1PSIDTS=ts")
        fake_session = client.session
        self.assertEqual(client.fetch_init_body(), '{"SNlM0e":"at"}')

        self.assertEqual(fake_session.calls[1][0], "https://gemini.google.com/app?hl=en")
        headers = fake_session.calls[1][1]
        self.assertEqual(headers["origin"], "https://gemini.google.com")
        self.assertEqual(headers["referer"], "https://gemini.google.com/")
        self.assertEqual(headers["x-same-domain"], "1")

    def setUp(self) -> None:
        gemini_models.clear_gemini_dynamic_model_cache()

    def tearDown(self) -> None:
        gemini_models.clear_gemini_dynamic_model_cache()

    def test_extract_gemini_model_ids_filters_internal_and_duplicates(self) -> None:
        body = " ".join([
            "gemini-2.5-pro",
            "gemini-2.5-pro.",
            "gemini-2.5-flash-preview-05-20,",
            "gemini-advanced)",
            "gemini-u-foo",
            "gemini-apps-bar",
            "gemini-pro",
            "other-model-2.0",
        ])

        self.assertEqual(
            gemini_models.extract_gemini_model_ids(body),
            ["gemini-2.5-pro", "gemini-2.5-flash-preview-05-20", "gemini-advanced"],
        )

    def test_list_model_metadata_returns_safe_static_models(self) -> None:
        with mock.patch("services.providers.gemini.client.fetch_authenticated_init_body", side_effect=RuntimeError("network")):
            metadata = gemini.list_model_metadata()

        models = {item["id"]: item for item in metadata}
        self.assertEqual(models["gemini-2.5-pro"]["provider"], "gemini")
        self.assertEqual(models["gemini-2.5-flash"]["owned_by"], "google")
        self.assertEqual(models["gemini-pro"]["root"], "gemini-pro")
        metadata[0]["id"] = "mutated"
        with mock.patch("services.providers.gemini.client.fetch_authenticated_init_body", side_effect=RuntimeError("network")):
            self.assertEqual(gemini.list_model_metadata()[0]["id"], "gemini-2.5-pro")

    def test_gemini_model_metadata_injects_dynamic_models_and_uses_cache(self) -> None:
        calls = 0

        def fetcher() -> str:
            nonlocal calls
            calls += 1
            return "gemini-2.5-pro gemini-2.5-ultra gemini-u-hidden gemini-apps-docs"

        first = gemini_models.gemini_model_metadata(fetcher, now=10.0)
        second = gemini_models.gemini_model_metadata(lambda: "gemini-2.5-ignored", now=20.0)

        first_models = {item["id"]: item for item in first}
        second_models = {item["id"] for item in second}
        self.assertEqual(calls, 1)
        self.assertEqual(first_models["gemini-2.5-ultra"]["provider"], "gemini")
        self.assertIn("gemini-2.5-ultra", second_models)
        self.assertNotIn("gemini-u-hidden", second_models)

    def test_gemini_model_metadata_refreshes_after_ttl(self) -> None:
        first = gemini_models.gemini_model_metadata(lambda: "gemini-2.5-first", now=10.0)
        second = gemini_models.gemini_model_metadata(lambda: "gemini-2.5-second", now=400.0)

        self.assertIn("gemini-2.5-first", {item["id"] for item in first})
        self.assertIn("gemini-2.5-second", {item["id"] for item in second})

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

    def test_parse_web_response_text_reads_canonical_wrb_stream_generate_candidate(self) -> None:
        stream_payload = json.dumps([
            None,
            ["https://gemini.google.com/app/metadata-fragment"],
            "7287830021314931269",
            ["rc_abc123", "gemini-2.5-pro"],
            [["candidate-id", [["你好！有什么可以帮你的吗？"]]]],
        ])
        frame = json.dumps([
            ["wrb.fr", "assistant.lamda.BardFrontendService.StreamGenerate", stream_payload, None, None, None, "generic"],
        ])
        parsed = gemini.parse_web_response_text(")]}'\n\n123\n" + frame)

        self.assertEqual(gemini.extract_completion(parsed).content, "你好！有什么可以帮你的吗？")

    def test_parse_web_response_text_reads_full_answer_from_live_null_slot_wrb_frames(self) -> None:
        partial_payload = json.dumps([
            None,
            ["https://gemini.google.com/app/metadata-fragment"],
            "7287830021314931269",
            ["rc_abc123", "gemini-2.5-pro"],
            [["candidate-id", [["你好！"]]]],
        ])
        full_answer = "你好！很高兴和你交流。今天有什么我可以帮你的吗？"
        full_payload = json.dumps([
            None,
            ["https://gemini.google.com/app/metadata-fragment"],
            "7287830021314931269",
            ["rc_abc123", "gemini-2.5-pro"],
            [["candidate-id", [[full_answer]]]],
        ])
        frame = json.dumps([
            ["wrb.fr", None, partial_payload, None, None, None, "generic"],
            ["wrb.fr", None, full_payload, None, None, None, "generic"],
        ])
        parsed = gemini.parse_web_response_text(")]}'\n\n123\n" + frame)

        self.assertEqual(gemini.extract_completion(parsed).content, full_answer)

    def test_parse_web_response_text_reads_current_wrb_direct_candidate_text(self) -> None:
        stream_payload = json.dumps([
            None,
            ["conversation-id", "response-id"],
            None,
            None,
            [["rc_abc123", ["Hi there"], None]],
        ])
        parsed = [["wrb.fr", None, stream_payload, None]]

        completion = gemini.extract_completion(parsed)

        self.assertEqual(completion.content, "Hi there")
        self.assertEqual(completion.metadata, {"cid": "conversation-id", "rid": "response-id", "rcid": "rc_abc123"})

    def test_extract_completion_ignores_numeric_only_null_slot_wrb_metadata(self) -> None:
        stream_payload = json.dumps([
            None,
            ["https://gemini.google.com/app/metadata-fragment"],
            "7287830021314931269",
            ["rc_abc123", "gemini-2.5-pro"],
        ])
        parsed = [["wrb.fr", None, stream_payload, None]]

        self.assertEqual(gemini.extract_completion(parsed).content, "")

    def test_extract_completion_ignores_numeric_only_stream_metadata(self) -> None:
        stream_payload = json.dumps([
            None,
            ["https://gemini.google.com/app/metadata-fragment"],
            "7287830021314931269",
            ["rc_abc123", "gemini-2.5-pro"],
        ])
        parsed = [["wrb.fr", "assistant.lamda.BardFrontendService.StreamGenerate", stream_payload, None]]

        self.assertEqual(gemini.extract_completion(parsed).content, "")

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

    def test_extract_completion_exposes_stream_generate_metadata(self) -> None:
        stream_payload = json.dumps([
            "rpc-id",
            "conversation-id",
            "response-id",
            None,
            [["rc_abc123", [["Gemini response with metadata"]]]],
        ])
        parsed = [["wrb.fr", "assistant.lamda.BardFrontendService.StreamGenerate", stream_payload, None]]

        completion = gemini.extract_completion(parsed)

        self.assertEqual(completion.content, "Gemini response with metadata")
        self.assertEqual(completion.metadata, {"cid": "conversation-id", "rid": "response-id", "rcid": "rc_abc123"})

    def test_classify_upstream_error_maps_auth_rate_limit_and_server_failures(self) -> None:
        auth = gemini.classify_upstream_error(403, "SNlM0e not found")
        stale = gemini.classify_upstream_error(500, "SNlM0e expired")
        rate = gemini.classify_upstream_error(429, "quota")
        server = gemini.classify_upstream_error(503, "unavailable")

        self.assertEqual(auth.status_code, 401)
        self.assertEqual(auth.code, "gemini_auth_failed")
        self.assertEqual(stale.status_code, 401)
        self.assertEqual(stale.code, "gemini_auth_failed")
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

            def get(self, url: str, headers: dict[str, str], timeout: int) -> FakeResponse:
                raise AssertionError("explicit session token should not bootstrap")

            def post(self, url: str, headers: dict[str, str], data: dict[str, str], timeout: int) -> FakeResponse:
                self.calls.append({"url": url, "headers": headers, "data": data, "timeout": timeout})
                return FakeResponse()

        fake_session = FakeSession()
        with mock.patch("services.providers.gemini.client.create_session", return_value=fake_session):
            client = gemini.GeminiWebClient("__Secure-1PSID=psid; __Secure-1PSIDTS=psidts")
            parsed = client.generate({"prompt": "hello", "model": "gemini-2.5-pro", "session_token": "at-token"})

        self.assertEqual(gemini.extract_completion(parsed).content, "ok")
        call = fake_session.calls[0]
        self.assertEqual(
            call["url"],
            "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?at=at-token",
        )
        self.assertEqual(call["headers"]["x-same-domain"], "1")
        self.assertEqual(call["headers"]["cookie"], "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts")
        self.assertEqual(call["data"]["at"], "at-token")
        inner = json.loads(json.loads(call["data"]["f.req"])[1])
        self.assertEqual(inner, [["hello"], None, None, "gemini-2.5-pro"])

    def test_gemini_web_client_bootstraps_missing_session_token_before_post(self) -> None:
        class FakeGetResponse:
            status_code = 200
            text = '<html><script nonce="boot-token"></script></html>'
            headers = {"Set-Cookie": "INIT=init; Path=/"}
            cookies = None

        class FakeGoogleResponse:
            status_code = 200
            text = ""
            headers = {"Set-Cookie": "NID=nid; Path=/"}
            cookies = None

        class FakePostResponse:
            status_code = 200
            text = ")]}'\n\n[[\"content\", \"ok\"]]"
            headers: dict[str, str] = {}
            cookies = None

        class FakeSession:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def get(self, url: str, headers: dict[str, str], timeout: int) -> FakeGetResponse | FakeGoogleResponse:
                self.calls.append({"method": "GET", "url": url, "headers": headers, "timeout": timeout})
                if url == "https://www.google.com/":
                    return FakeGoogleResponse()
                return FakeGetResponse()

            def post(self, url: str, headers: dict[str, str], data: dict[str, str], timeout: int) -> FakePostResponse:
                self.calls.append({"method": "POST", "url": url, "headers": headers, "data": data, "timeout": timeout})
                return FakePostResponse()

        fake_session = FakeSession()
        with mock.patch("services.providers.gemini.client.create_session", return_value=fake_session):
            client = gemini.GeminiWebClient("__Secure-1PSID=psid; __Secure-1PSIDTS=psidts", "Test-UA")
            parsed = client.generate({"prompt": "hello", "model": "gemini-2.5-pro"})

        self.assertEqual(gemini.extract_completion(parsed).content, "ok")
        self.assertEqual([call["method"] for call in fake_session.calls], ["GET", "GET", "POST"])
        google_call = fake_session.calls[0]
        self.assertEqual(google_call["url"], "https://www.google.com/")
        self.assertNotIn("cookie", google_call["headers"])
        get_call = fake_session.calls[1]
        self.assertEqual(get_call["url"], "https://gemini.google.com/app?hl=en")
        self.assertEqual(get_call["headers"]["referer"], "https://gemini.google.com/")
        self.assertEqual(get_call["headers"]["cookie"], "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts; NID=nid")
        self.assertEqual(get_call["headers"]["user-agent"], "Test-UA")
        post_call = fake_session.calls[2]
        self.assertEqual(
            post_call["url"],
            "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?at=boot-token",
        )
        self.assertEqual(post_call["headers"]["cookie"], "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts; NID=nid; INIT=init")
        self.assertEqual(post_call["data"]["at"], "boot-token")
        self.assertEqual(post_call["headers"]["user-agent"], "Test-UA")
        self.assertEqual(post_call["headers"]["referer"], "https://gemini.google.com/")

    def test_gemini_web_client_bootstrap_rejects_login_page(self) -> None:
        class FakeGoogleResponse:
            status_code = 200
            text = ""
            headers: dict[str, str] = {}
            cookies = None

        class FakeGetResponse:
            status_code = 200
            text = '<html><a href="https://accounts.google.com/ServiceLogin">Sign in</a></html>'
            headers: dict[str, str] = {}
            cookies = None

        class FakeSession:
            def get(self, url: str, headers: dict[str, str], timeout: int) -> FakeGetResponse | FakeGoogleResponse:
                if url == "https://www.google.com/":
                    return FakeGoogleResponse()
                return FakeGetResponse()

        with mock.patch("services.providers.gemini.client.create_session", return_value=FakeSession()):
            client = gemini.GeminiWebClient("__Secure-1PSID=psid; __Secure-1PSIDTS=psidts")
            with self.assertRaises(gemini.GeminiWebError) as raised:
                client.bootstrap_session_token()

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.code, "gemini_auth_failed")

    def test_gemini_web_client_bootstrap_prefers_session_token_over_incidental_accounts_link(self) -> None:
        class FakeGoogleResponse:
            status_code = 200
            text = ""
            headers: dict[str, str] = {}
            cookies = None

        class FakeGetResponse:
            status_code = 200
            text = '<html><script>["SNlM0e","boot-token"]</script><a href="https://accounts.google.com/ManageAccount">account</a></html>'
            headers: dict[str, str] = {}
            cookies = None

        class FakeSession:
            def get(self, url: str, headers: dict[str, str], timeout: int) -> FakeGetResponse | FakeGoogleResponse:
                if url == "https://www.google.com/":
                    return FakeGoogleResponse()
                return FakeGetResponse()

        with mock.patch("services.providers.gemini.client.create_session", return_value=FakeSession()):
            client = gemini.GeminiWebClient("__Secure-1PSID=psid; __Secure-1PSIDTS=psidts")
            token = client.bootstrap_session_token()

        self.assertEqual(token, "boot-token")
        self.assertEqual(client.session_token, "boot-token")

    def test_rotate_psidts_cookie_posts_exact_payload_and_merges_new_cookie(self) -> None:
        class FakeResponse:
            status_code = 200
            text = ""
            headers = {"Set-Cookie": "__Secure-1PSIDTS=new-ts; Path=/; Secure, NID=nid; Path=/"}
            cookies = None

        class FakeSession:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def post(self, url: str, headers: dict[str, str], data: str, timeout: int) -> FakeResponse:
                self.calls.append({"url": url, "headers": headers, "data": data, "timeout": timeout})
                return FakeResponse()

        fake_session = FakeSession()
        merged = gemini.rotate_psidts_cookie(fake_session, "__Secure-1PSID=psid; __Secure-1PSIDTS=old-ts", "Test-UA")

        self.assertEqual(merged, "__Secure-1PSID=psid; __Secure-1PSIDTS=new-ts; NID=nid")
        call = fake_session.calls[0]
        self.assertEqual(call["url"], "https://accounts.google.com/RotateCookies")
        self.assertEqual(call["data"], '[000,"-0000000000000000000"]')
        self.assertEqual(call["headers"]["cookie"], "__Secure-1PSID=psid; __Secure-1PSIDTS=old-ts")
        self.assertEqual(call["headers"]["user-agent"], "Test-UA")

    def test_rotate_cookies_result_exposes_refreshed_psidts_metadata(self) -> None:
        class FakeResponse:
            headers = {"Set-Cookie": "__Secure-1PSIDTS=new-ts; Path=/; Secure, NID=nid; Path=/"}
            cookies = None

        result = gemini.gemini_rotate_cookies_result("__Secure-1PSID=psid; __Secure-1PSIDTS=old-ts", FakeResponse())

        self.assertEqual(result.cookie_header, "__Secure-1PSID=psid; __Secure-1PSIDTS=new-ts; NID=nid")
        self.assertEqual(result.psidts, "new-ts")
        self.assertEqual(result.refreshed_psidts, "new-ts")
        self.assertEqual(result.cookies["__Secure-1PSIDTS"], "new-ts")

    def test_rotate_cookies_result_keeps_empty_refresh_metadata_when_psidts_unchanged(self) -> None:
        class FakeResponse:
            headers: dict[str, str] = {}
            cookies = None

        result = gemini.gemini_rotate_cookies_result("__Secure-1PSID=psid; __Secure-1PSIDTS=old-ts", FakeResponse())

        self.assertEqual(result.cookie_header, "__Secure-1PSID=psid; __Secure-1PSIDTS=old-ts")
        self.assertEqual(result.psidts, "old-ts")
        self.assertEqual(result.refreshed_psidts, "")

    def test_rotate_psidts_cookie_allows_200_without_new_cookie_when_existing_psidts_present(self) -> None:
        class FakeResponse:
            status_code = 200
            text = ""
            headers: dict[str, str] = {}
            cookies = None

        class FakeSession:
            def post(self, url: str, headers: dict[str, str], data: str, timeout: int) -> FakeResponse:
                return FakeResponse()

        merged = gemini.rotate_psidts_cookie(FakeSession(), "__Secure-1PSID=psid; __Secure-1PSIDTS=old-ts")

        self.assertEqual(merged, "__Secure-1PSID=psid; __Secure-1PSIDTS=old-ts")

    def test_rotate_psidts_cookie_treats_forbidden_as_auth_failure(self) -> None:
        class FakeResponse:
            status_code = 403
            text = "forbidden"
            headers: dict[str, str] = {}
            cookies = None

        class FakeSession:
            def post(self, url: str, headers: dict[str, str], data: str, timeout: int) -> FakeResponse:
                return FakeResponse()

        with self.assertRaises(gemini.GeminiWebError) as raised:
            gemini.rotate_psidts_cookie(FakeSession(), "__Secure-1PSID=psid")

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.code, "gemini_auth_failed")

    def test_gemini_web_client_retries_transient_server_and_empty_parse_responses(self) -> None:
        class FakeResponse:
            def __init__(self, status_code: int, text: str) -> None:
                self.status_code = status_code
                self.text = text
                self.headers: dict[str, str] = {}
                self.cookies = None

        class FakeSession:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []
                self.responses = [
                    FakeResponse(503, "unavailable"),
                    FakeResponse(200, "   "),
                    FakeResponse(200, ")]}'\n\n[[\"content\", \"ok after retry\"]]"),
                ]

            def get(self, url: str, headers: dict[str, str], timeout: int) -> FakeResponse:
                raise AssertionError("explicit session token should not bootstrap")

            def post(self, url: str, headers: dict[str, str], data: dict[str, str], timeout: int) -> FakeResponse:
                self.calls.append({"url": url, "headers": headers, "data": data, "timeout": timeout})
                return self.responses.pop(0)

        fake_session = FakeSession()
        with mock.patch("services.providers.gemini.client.create_session", return_value=fake_session), \
             mock.patch("services.providers.gemini.client.time.sleep") as sleep:
            client = gemini.GeminiWebClient("__Secure-1PSID=psid; __Secure-1PSIDTS=psidts")
            parsed = client.generate({"prompt": "hello", "model": "gemini-2.5-pro", "session_token": "at-token"})

        self.assertEqual(gemini.extract_completion(parsed).content, "ok after retry")
        self.assertEqual(len(fake_session.calls), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_gemini_web_client_does_not_retry_rate_limit(self) -> None:
        class FakeResponse:
            status_code = 429
            text = "quota"
            headers: dict[str, str] = {}
            cookies = None

        class FakeSession:
            def __init__(self) -> None:
                self.calls = 0

            def post(self, url: str, headers: dict[str, str], data: dict[str, str], timeout: int) -> FakeResponse:
                self.calls += 1
                return FakeResponse()

        fake_session = FakeSession()
        with mock.patch("services.providers.gemini.client.create_session", return_value=fake_session):
            client = gemini.GeminiWebClient("__Secure-1PSID=psid; __Secure-1PSIDTS=psidts")
            with self.assertRaises(gemini.GeminiWebError) as raised:
                client.generate({"prompt": "hello", "model": "gemini-2.5-pro", "session_token": "at-token"})

        self.assertEqual(raised.exception.code, "gemini_rate_limited")
        self.assertEqual(fake_session.calls, 1)

    def test_chat_completion_session_token_missing_does_not_mark_account_used_or_remove_token(self) -> None:
        account_service = mock.Mock()
        account_service.get_text_access_token.return_value = "gemini-token"
        account_service.get_account.return_value = {
            "access_token": "gemini-token",
            "provider": "gemini",
            "cookies": {"__Secure-1PSID": "psid", "__Secure-1PSIDTS": "psidts"},
        }
        client = mock.Mock()
        client.generate.side_effect = gemini.GeminiWebError(
            "Gemini session token bootstrap failed",
            status_code=401,
            code="gemini_session_token_missing",
        )
        client_context = mock.Mock()
        client_context.__enter__ = mock.Mock(return_value=client)
        client_context.__exit__ = mock.Mock(return_value=None)

        with mock.patch.dict(sys.modules, {"services.account_service": mock.Mock(account_service=account_service)}), \
             mock.patch("services.providers.gemini.client.GeminiWebClient", return_value=client_context):
            with self.assertRaises(HTTPException) as raised:
                gemini.chat_completion(
                    {},
                    resolve_model("gemini-2.5-pro"),
                    [{"role": "user", "content": "Hello"}],
                )

        self.assertEqual(getattr(raised.exception, "status_code"), 401)
        self.assertEqual(getattr(raised.exception, "detail")["code"], "gemini_session_token_missing")
        account_service.mark_text_used.assert_not_called()
        account_service.remove_invalid_token.assert_not_called()

    def test_chat_completion_auth_failure_does_not_mark_account_used_on_first_failure(self) -> None:
        account_service = mock.Mock()
        account_service.get_text_access_token.return_value = "gemini-token"
        account_service.get_account.return_value = {
            "access_token": "gemini-token",
            "provider": "gemini",
            "cookies": {"__Secure-1PSID": "psid", "__Secure-1PSIDTS": "psidts"},
        }
        client = mock.Mock()
        client.generate.side_effect = gemini.GeminiWebError(
            "Gemini upstream authentication failed",
            status_code=401,
            code="gemini_auth_failed",
        )
        client_context = mock.Mock()
        client_context.__enter__ = mock.Mock(return_value=client)
        client_context.__exit__ = mock.Mock(return_value=None)

        with mock.patch.dict(sys.modules, {"services.account_service": mock.Mock(account_service=account_service)}), \
             mock.patch("services.providers.gemini.client.GeminiWebClient", return_value=client_context):
            with self.assertRaises(HTTPException) as raised:
                gemini.chat_completion(
                    {},
                    resolve_model("gemini-2.5-pro"),
                    [{"role": "user", "content": "Hello"}],
                )

        self.assertEqual(getattr(raised.exception, "status_code"), 401)
        self.assertEqual(getattr(raised.exception, "detail")["code"], "gemini_auth_failed")
        account_service.mark_text_used.assert_not_called()
        account_service.remove_invalid_token.assert_not_called()

    def test_chat_completion_success_marks_account_used_once(self) -> None:
        account_service = mock.Mock()
        account_service.get_text_access_token.return_value = "gemini-token"
        account_service.get_account.return_value = {
            "access_token": "gemini-token",
            "provider": "gemini",
            "cookies": {"__Secure-1PSID": "psid", "__Secure-1PSIDTS": "psidts"},
        }
        client = mock.Mock()
        client.cookie_header = "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts"
        client.session_token = ""
        client.generate.return_value = [["content", "Gemini success"]]
        client_context = mock.Mock()
        client_context.__enter__ = mock.Mock(return_value=client)
        client_context.__exit__ = mock.Mock(return_value=None)

        with mock.patch.dict(sys.modules, {"services.account_service": mock.Mock(account_service=account_service)}), \
             mock.patch("services.providers.gemini.client.GeminiWebClient", return_value=client_context):
            completion = gemini.chat_completion(
                {},
                resolve_model("gemini-2.5-pro"),
                [{"role": "user", "content": "Hello"}],
            )

        self.assertEqual(completion.content, "Gemini success")
        account_service.mark_text_used.assert_called_once_with("gemini-token")
        account_service.remove_invalid_token.assert_not_called()

    def test_chat_completion_success_persists_updated_gemini_session(self) -> None:
        account_service = mock.Mock()
        account_service.get_text_access_token.return_value = "gemini-token"
        account_service.get_account.return_value = {
            "access_token": "gemini-token",
            "provider": "gemini",
            "cookies": {"__Secure-1PSID": "old-psid", "__Secure-1PSIDTS": "old-ts"},
            "session_token": "stored-at",
        }
        client = mock.Mock()
        client.cookie_header = "__Secure-1PSID=new-psid; __Secure-1PSIDTS=new-ts; NID=nid"
        client.session_token = "stored-at"
        client.generate.return_value = [["content", "Gemini success"]]
        client_context = mock.Mock()
        client_context.__enter__ = mock.Mock(return_value=client)
        client_context.__exit__ = mock.Mock(return_value=None)

        with mock.patch.dict(sys.modules, {"services.account_service": mock.Mock(account_service=account_service)}), \
             mock.patch("services.providers.gemini.client.GeminiWebClient", return_value=client_context):
            completion = gemini.chat_completion(
                {},
                resolve_model("gemini-2.5-pro"),
                [{"role": "user", "content": "Hello"}],
            )

        self.assertEqual(completion.content, "Gemini success")
        account_service.update_account.assert_called_once_with(
            "gemini-token",
            {
                "cookies": {"__Secure-1PSID": "new-psid", "__Secure-1PSIDTS": "new-ts", "NID": "nid"},
                "__Secure-1PSID": "new-psid",
                "__Secure-1PSIDTS": "new-ts",
                "session_token": "stored-at",
                "SNlM0e": "stored-at",
                "at": "stored-at",
            },
            provider="gemini",
        )
        account_service.mark_text_used.assert_called_once_with("gemini-token")
        account_service.remove_invalid_token.assert_not_called()

    def test_chat_completion_success_persists_bootstrapped_gemini_session_token(self) -> None:
        account_service = mock.Mock()
        account_service.get_text_access_token.return_value = "gemini-token"
        account_service.get_account.return_value = {
            "access_token": "gemini-token",
            "provider": "gemini",
            "cookies": {"__Secure-1PSID": "old-psid", "__Secure-1PSIDTS": "old-ts"},
        }
        client = mock.Mock()
        client.cookie_header = "__Secure-1PSID=old-psid; __Secure-1PSIDTS=new-ts"
        client.session_token = "bootstrapped-at"
        client.generate.return_value = [["content", "Gemini success"]]
        client_context = mock.Mock()
        client_context.__enter__ = mock.Mock(return_value=client)
        client_context.__exit__ = mock.Mock(return_value=None)

        with mock.patch.dict(sys.modules, {"services.account_service": mock.Mock(account_service=account_service)}), \
             mock.patch("services.providers.gemini.client.GeminiWebClient", return_value=client_context):
            gemini.chat_completion(
                {},
                resolve_model("gemini-2.5-pro"),
                [{"role": "user", "content": "Hello"}],
            )

        account_service.update_account.assert_called_once_with(
            "gemini-token",
            {
                "cookies": {"__Secure-1PSID": "old-psid", "__Secure-1PSIDTS": "new-ts"},
                "__Secure-1PSID": "old-psid",
                "__Secure-1PSIDTS": "new-ts",
                "session_token": "bootstrapped-at",
                "SNlM0e": "bootstrapped-at",
                "at": "bootstrapped-at",
            },
            provider="gemini",
        )
        account_service.remove_invalid_token.assert_not_called()

    def test_fetch_authenticated_init_body_persists_updated_gemini_session(self) -> None:
        account_service = mock.Mock()
        account_service.get_text_access_token.return_value = "gemini-token"
        account_service.get_account.return_value = {
            "access_token": "gemini-token",
            "provider": "gemini",
            "cookies": {"__Secure-1PSID": "old-psid", "__Secure-1PSIDTS": "old-ts"},
        }
        client = mock.Mock()
        client.cookie_header = "__Secure-1PSID=old-psid; __Secure-1PSIDTS=new-ts; NID=nid"
        client.fetch_init_body.return_value = "gemini init body"
        client_context = mock.Mock()
        client_context.__enter__ = mock.Mock(return_value=client)
        client_context.__exit__ = mock.Mock(return_value=None)

        with mock.patch.dict(sys.modules, {"services.account_service": mock.Mock(account_service=account_service)}), \
             mock.patch("services.providers.gemini.client.GeminiWebClient", return_value=client_context):
            body = gemini.fetch_authenticated_init_body()

        self.assertEqual(body, "gemini init body")
        account_service.update_account.assert_called_once_with(
            "gemini-token",
            {
                "cookies": {"__Secure-1PSID": "old-psid", "__Secure-1PSIDTS": "new-ts", "NID": "nid"},
                "__Secure-1PSID": "old-psid",
                "__Secure-1PSIDTS": "new-ts",
            },
            provider="gemini",
        )

    def test_gemini_session_writeback_skips_empty_updates(self) -> None:
        account_service = mock.Mock()

        gemini_client.persist_gemini_session(account_service, "gemini-token", {}, "")

        account_service.update_account.assert_not_called()

    def test_gemini_web_client_merges_successful_generate_response_cookies(self) -> None:
        class FakeResponse:
            status_code = 200
            text = ")]}'\n\n[[\"content\", \"ok\"]]"
            headers = {"Set-Cookie": "__Secure-1PSIDTS=new-ts; Path=/; Secure, NID=nid; Path=/"}
            cookies = None

        class FakeSession:
            def get(self, url: str, headers: dict[str, str], timeout: int) -> FakeResponse:
                raise AssertionError("explicit session token should not bootstrap")

            def post(self, url: str, headers: dict[str, str], data: dict[str, str], timeout: int) -> FakeResponse:
                return FakeResponse()

        with mock.patch("services.providers.gemini.client.create_session", return_value=FakeSession()):
            client = gemini.GeminiWebClient("__Secure-1PSID=psid; __Secure-1PSIDTS=old-ts")
            parsed = client.generate({"prompt": "hello", "model": "gemini-2.5-pro", "session_token": "at-token"})

        self.assertEqual(gemini.extract_completion(parsed).content, "ok")
        self.assertEqual(client.cookie_header, "__Secure-1PSID=psid; __Secure-1PSIDTS=new-ts; NID=nid")

    def test_chat_completion_success_persists_generate_response_cookies(self) -> None:
        class FakeResponse:
            status_code = 200
            text = ")]}'\n\n[[\"content\", \"Gemini success\"]]"
            headers = {"Set-Cookie": "__Secure-1PSIDTS=new-ts; Path=/; Secure, NID=nid; Path=/"}
            cookies = None

        class FakeSession:
            def get(self, url: str, headers: dict[str, str], timeout: int) -> FakeResponse:
                raise AssertionError("explicit session token should not bootstrap")

            def post(self, url: str, headers: dict[str, str], data: dict[str, str], timeout: int) -> FakeResponse:
                return FakeResponse()

        account_service = mock.Mock()
        account_service.get_text_access_token.return_value = "gemini-token"
        account_service.get_account.return_value = {
            "access_token": "gemini-token",
            "provider": "gemini",
            "cookies": {"__Secure-1PSID": "psid", "__Secure-1PSIDTS": "old-ts"},
            "session_token": "stored-at",
        }

        with mock.patch.dict(sys.modules, {"services.account_service": mock.Mock(account_service=account_service)}), \
             mock.patch("services.providers.gemini.client.create_session", return_value=FakeSession()):
            completion = gemini.chat_completion(
                {},
                resolve_model("gemini-2.5-pro"),
                [{"role": "user", "content": "Hello"}],
            )

        self.assertEqual(completion.content, "Gemini success")
        account_service.update_account.assert_called_once_with(
            "gemini-token",
            {
                "cookies": {"__Secure-1PSID": "psid", "__Secure-1PSIDTS": "new-ts", "NID": "nid"},
                "__Secure-1PSID": "psid",
                "__Secure-1PSIDTS": "new-ts",
                "session_token": "stored-at",
                "SNlM0e": "stored-at",
                "at": "stored-at",
            },
            provider="gemini",
        )

    def test_gemini_chat_image_without_modalities_is_not_silently_stripped(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            openai_v1_chat_complete.handle({
                "model": "gemini-2.5-pro",
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image_url", "image_url": {"url": "https://example.test/cat.png"}},
                ]}],
            })

        detail = cast(dict[str, Any], getattr(raised.exception, "detail"))
        self.assertEqual(detail["error"], "Gemini Web image input is not supported by this upstream adapter")

    def test_gemini_stream_chat_image_without_modalities_is_not_silently_stripped(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            result = openai_v1_chat_complete.handle({
                "model": "gemini-2.5-pro",
                "stream": True,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image_url", "image_url": {"url": "https://example.test/cat.png"}},
                ]}],
            })
            list(cast(Any, result))

        detail = cast(dict[str, Any], getattr(raised.exception, "detail"))
        self.assertEqual(detail["error"], "Gemini Web image input is not supported by this upstream adapter")

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

    def test_gemini_image_input_chat_routes_to_text_provider_error(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            openai_v1_chat_complete.handle({
                "model": "gemini-2.5-pro",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                    ],
                }],
                "modalities": ["image"],
            })

        detail = cast(dict[str, Any], getattr(raised.exception, "detail"))
        self.assertEqual(getattr(raised.exception, "status_code"), 400)
        self.assertEqual(detail["error"], "Gemini Web image input is not supported by this upstream adapter")

    def test_gemini_streaming_image_input_chat_routes_to_text_provider_error(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            result = openai_v1_chat_complete.handle({
                "model": "gemini-2.5-pro",
                "stream": True,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                    ],
                }],
                "modalities": ["image"],
            })
            list(cast(Any, result))

        detail = cast(dict[str, Any], getattr(raised.exception, "detail"))
        self.assertEqual(getattr(raised.exception, "status_code"), 400)
        self.assertEqual(detail["error"], "Gemini Web image input is not supported by this upstream adapter")

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
