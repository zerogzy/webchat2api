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
from services.openai_backend_api import ChatRequirements, DEFAULT_CLIENT_BUILD_NUMBER, DEFAULT_CLIENT_VERSION, ImageStreamHardTimeoutError, OpenAIBackendAPI
from services.providers.gpt.chat import normalize_thinking_effort, thinking_effort_from_body
from services.providers.gpt.models import gpt_effective_thinking_effort, gpt_upstream_model_id


class FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None, lines: list[bytes] | None = None) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self.text = ""
        self.payload = payload or {}
        self.lines = lines or []
        self.closed = False

    def json(self) -> dict[str, Any]:
        return self.payload

    def iter_lines(self):
        return iter(self.lines)

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse] | None = None) -> None:
        self.headers: dict[str, str] = {}
        self.responses = list(responses or [])
        self.posts: list[dict[str, Any]] = []
        self.gets: list[dict[str, Any]] = []
        self.closed = False
        self.cookies = mock.Mock()

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.posts.append({"url": url, **kwargs})
        return self.responses.pop(0) if self.responses else FakeResponse()

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.gets.append({"url": url, **kwargs})
        return self.responses.pop(0) if self.responses else FakeResponse()

    def close(self) -> None:
        self.closed = True


class ChatGPTUpstreamParityTests(unittest.TestCase):
    def test_client_version_matches_pinned_chatgpt2api_upstream(self) -> None:
        self.assertEqual(DEFAULT_CLIENT_VERSION, "prod-a194cd50d4416d3c0b47c740f206b12ce60f5887")
        self.assertEqual(DEFAULT_CLIENT_BUILD_NUMBER, "6708908")

    def test_image_stream_hard_timeout_closes_response(self) -> None:
        response = FakeResponse(lines=[b"data: first"])

        with self.assertRaises(ImageStreamHardTimeoutError):
            list(OpenAIBackendAPI._iter_sse_payloads_capped(response, 0))

        self.assertTrue(response.closed)

    def test_bootstrap_retries_with_flaresolverr_clearance_on_403(self) -> None:
        blocked = FakeResponse()
        blocked.status_code = 403
        solved = FakeResponse()
        solved.text = ""
        session = FakeSession([blocked, solved])
        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        client.base_url = "https://chatgpt.com"
        client.session = session
        client.pow_script_sources = []
        client.pow_data_build = ""
        client._call_with_retry = lambda fn, policy=None, context="": fn()
        client._bootstrap_headers = lambda: {}
        client._refresh_cloudflare_clearance = mock.Mock(return_value=True)

        client._bootstrap()

        self.assertEqual(len(session.gets), 2)
        self.assertTrue(blocked.closed)
        client._refresh_cloudflare_clearance.assert_called_once_with()

    def test_refresh_cloudflare_clearance_updates_user_agent_and_cookies(self) -> None:
        session = FakeSession()
        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        client.base_url = "https://chatgpt.com"
        client.session = session
        client.user_agent = "Old UA"
        clearance = mock.Mock(
            user_agent="Solved UA",
            cf_cookies="cf_clearance=clearance; __cf_bm=browser-management",
        )

        with (
            mock.patch.object(config, "data", {"flaresolverr_url": "http://solver.local"}),
            mock.patch("services.openai_backend_api.FlareSolverrClearanceProvider.solve", return_value=clearance) as solve,
        ):
            refreshed = client._refresh_cloudflare_clearance()

        self.assertTrue(refreshed)
        self.assertEqual(client.user_agent, "Solved UA")
        self.assertEqual(session.headers["User-Agent"], "Solved UA")
        self.assertEqual(session.cookies.set.call_count, 2)
        solve.assert_called_once_with("https://chatgpt.com/auth/login")

    def test_thinking_effort_accepts_openai_chat_and_responses_shapes(self) -> None:
        self.assertEqual(thinking_effort_from_body({"reasoning_effort": "high"}), "extended")
        self.assertEqual(thinking_effort_from_body({"thinking_effort": "xhigh"}), "extended")
        self.assertEqual(thinking_effort_from_body({"reasoning": {"effort": "medium"}}), "standard")
        self.assertEqual(normalize_thinking_effort("standard"), "standard")
        self.assertEqual(normalize_thinking_effort("low"), "standard")
        self.assertEqual(normalize_thinking_effort("unsupported"), "")

    def test_upstream_effort_suffixes_are_removed_from_model_id(self) -> None:
        self.assertEqual(gpt_upstream_model_id("gpt-5-6-thinking-max"), "gpt-5-6-thinking")
        self.assertEqual(gpt_effective_thinking_effort("gpt-5-6-thinking-max"), "max")

    def test_conversation_payload_only_emits_normalized_thinking_effort(self) -> None:
        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        messages = [{"role": "user", "content": "solve this"}]

        payload = client._conversation_payload(messages, "auto", "Asia/Shanghai", "xhigh")
        default_payload = client._conversation_payload(messages, "auto", "Asia/Shanghai", "")

        self.assertEqual(payload["thinking_effort"], "extended")
        self.assertNotIn("thinking_effort", default_payload)

    def test_prepared_payload_reuses_converted_messages_and_current_fields(self) -> None:
        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        messages = [{"role": "user", "content": "solve this"}]
        converted = [{
            "id": "message-1",
            "author": {"role": "user"},
            "content": {"content_type": "text", "parts": ["solve this"]},
        }]

        with mock.patch.object(client, "_api_messages_to_conversation_messages", side_effect=AssertionError("converted twice")):
            payload = client._conversation_payload(
                messages,
                "gpt-5-6-thinking",
                "Asia/Shanghai",
                "medium",
                conversation_messages=converted,
                parent_message_id="parent-1",
                client_prepare_state="success",
            )

        self.assertIs(payload["messages"], converted)
        self.assertEqual(payload["parent_message_id"], "parent-1")
        self.assertEqual(payload["thinking_effort"], "standard")
        self.assertEqual(payload["supported_encodings"], ["v1"])
        self.assertTrue(payload["supports_buffering"])
        self.assertEqual(payload["force_parallel_switch"], "auto")
        self.assertNotIn("force_paragen", payload)
        self.assertNotIn("websocket_request_id", payload)

    def test_prepare_conversation_uses_last_user_message_and_conduit_header(self) -> None:
        response = FakeResponse({"conduit_token": "conduit-1"})
        session = FakeSession([response])
        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        client.base_url = "https://chatgpt.com"
        client.session = session
        client._call_with_retry = lambda fn, policy=None, context="": fn()
        conversation_messages = [
            {"id": "u1", "author": {"role": "user"}, "content": {"content_type": "text", "parts": ["old"]}},
            {"id": "a1", "author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["answer"]}},
            {"id": "u2", "author": {"role": "user"}, "content": {"content_type": "text", "parts": ["current"]}},
        ]

        token = client._prepare_text_conversation(
            conversation_messages,
            "gpt-5-6-thinking",
            "Asia/Shanghai",
            "high",
            "parent-1",
            [],
        )

        request = session.posts[0]
        self.assertEqual(token, "conduit-1")
        self.assertTrue(request["url"].endswith("/backend-api/f/conversation/prepare"))
        self.assertEqual(request["headers"]["X-Conduit-Token"], "no-token")
        self.assertEqual(request["json"]["partial_query"]["id"], "u2")
        self.assertEqual(request["json"]["thinking_effort"], "extended")
        self.assertTrue(request["json"]["history_and_training_disabled"])

    def test_conversation_init_requests_selected_model(self) -> None:
        response = FakeResponse({"default_model_slug": "gpt-5-6-thinking"})
        session = FakeSession([response])
        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        client.base_url = "https://chatgpt.com"
        client.session = session
        client._call_with_retry = lambda fn, policy=None, context="": fn()

        client._get_conversation_init("gpt-5-6-thinking", ["retrieval"])

        request = session.posts[0]
        self.assertTrue(request["url"].endswith("/backend-api/conversation/init"))
        self.assertEqual(request["json"]["requested_default_model"], "gpt-5-6-thinking")
        self.assertEqual(request["json"]["system_hints"], ["retrieval"])

    def test_authenticated_stream_uses_conduit_flow_with_auto(self) -> None:
        response = FakeResponse(lines=[b'data: {"message":"ok"}', b"data: [DONE]"])
        session = FakeSession([response])
        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        client.base_url = "https://chatgpt.com"
        client.access_token = "token"
        client.session_id = "session-1"
        client.session = session
        client._bootstrap = mock.Mock()
        client._call_with_retry = lambda fn, policy=None, context="": fn()
        converted = [{
            "id": "u1",
            "author": {"role": "user"},
            "content": {"content_type": "text", "parts": ["hello"]},
        }]
        client._api_messages_to_conversation_messages = mock.Mock(return_value=converted)
        client._prepare_text_request = mock.Mock(return_value=(ChatRequirements("sentinel-1"), "conduit-1"))

        chunks = list(client.stream_conversation(messages=[{"role": "user", "content": "hello"}], model="auto"))

        self.assertEqual(chunks, ['{"message":"ok"}', "[DONE]"])
        client._api_messages_to_conversation_messages.assert_called_once()
        prepare_args = client._prepare_text_request.call_args.args
        self.assertEqual(prepare_args[1], "auto")
        request = session.posts[0]
        self.assertTrue(request["url"].endswith("/backend-api/f/conversation"))
        self.assertEqual(request["headers"]["X-Conduit-Token"], "conduit-1")
        self.assertEqual(request["json"]["model"], "auto")
        self.assertEqual(request["json"]["client_prepare_state"], "success")
        self.assertEqual(gpt_upstream_model_id("auto"), "auto")
        self.assertTrue(response.closed)

    def test_authenticated_stream_maps_upstream_extended_suffix(self) -> None:
        response = FakeResponse(lines=[b'data: {"message":"ok"}', b"data: [DONE]"])
        session = FakeSession([response])
        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        client.base_url = "https://chatgpt.com"
        client.access_token = "token"
        client.session_id = "session-1"
        client.session = session
        client._bootstrap = mock.Mock()
        client._call_with_retry = lambda fn, policy=None, context="": fn()
        converted = [{
            "id": "u1",
            "author": {"role": "user"},
            "content": {"content_type": "text", "parts": ["hello"]},
        }]
        client._api_messages_to_conversation_messages = mock.Mock(return_value=converted)
        client._prepare_text_request = mock.Mock(return_value=(ChatRequirements("sentinel-1"), "conduit-1"))

        chunks = list(client.stream_conversation(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-5-6-thinking-extended",
        ))

        self.assertEqual(chunks, ['{"message":"ok"}', "[DONE]"])
        prepare_args = client._prepare_text_request.call_args.args
        self.assertEqual(prepare_args[1], "gpt-5-6-thinking")
        self.assertEqual(prepare_args[3], "extended")
        request = session.posts[0]
        self.assertEqual(request["json"]["model"], "gpt-5-6-thinking")
        self.assertEqual(request["json"]["thinking_effort"], "extended")
        self.assertEqual(gpt_upstream_model_id("gpt-5-6-thinking-extended"), "gpt-5-6-thinking")
        self.assertTrue(response.closed)

    def test_authenticated_model_list_uses_picker_categories(self) -> None:
        response = FakeResponse({
            "models": [
                {"slug": "gpt-5-5", "created": 1},
                {"slug": "gpt-5-6-thinking", "created": 2},
                {"slug": "gpt-5-5-mini", "created": 3},
                {"slug": "gpt-5.6-sol-wm", "is_work_mode_model": True},
                {"slug": "research"},
            ],
            "categories": [
                {"default_model": "gpt-5-5"},
                {"default_model": {"slug": "gpt-5-6-thinking"}},
                {"default_model": "gpt-5.6-sol-wm"},
                {"default_model": "research"},
                {"default_model": "gpt-5-5-mini", "disabled_by_admin": True},
            ],
        })
        session = FakeSession([response])
        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        client.base_url = "https://chatgpt.com"
        client.access_token = "token"
        client.session = session
        client._bootstrap = mock.Mock()
        client._call_with_retry = lambda fn, policy=None, context="": fn()

        result = client.list_models()

        self.assertEqual([item["id"] for item in result["data"]], ["gpt-5-5", "gpt-5-6-thinking"])
        self.assertIn("history_and_training_disabled=true", session.gets[0]["url"])
        self.assertNotIn("gpt-5-5-mini", {item["id"] for item in result["data"]})

    def test_anonymous_model_list_also_uses_picker_categories(self) -> None:
        response = FakeResponse({
            "models": [
                {"slug": "gpt-5-5"},
                {"slug": "gpt-5-5-mini"},
            ],
            "categories": [
                {"default_model": "auto"},
                {"default_model": "gpt-5-5"},
            ],
        })
        session = FakeSession([response])
        client = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        client.base_url = "https://chatgpt.com"
        client.access_token = ""
        client.session = session
        client._bootstrap = mock.Mock()
        client._call_with_retry = lambda fn, policy=None, context="": fn()

        result = client.list_models()

        self.assertEqual([item["id"] for item in result["data"]], ["auto", "gpt-5-5"])
        self.assertNotIn("gpt-5-5-mini", {item["id"] for item in result["data"]})

    def test_generated_fingerprint_is_persisted_and_reused(self) -> None:
        stored_account: dict[str, Any] = {"access_token": "token", "provider": "gpt"}
        updates: list[dict[str, Any]] = []

        def get_account(access_token: str, provider: str | None = None) -> dict[str, Any]:
            return dict(stored_account)

        def update_account(access_token: str, values: dict[str, Any], provider: str | None = None) -> dict[str, Any]:
            updates.append(values)
            stored_account.update(values)
            return dict(stored_account)

        with mock.patch.object(config, "data", {**config.data, "chatgpt_fingerprint": {}}), \
             mock.patch("services.openai_backend_api.account_service.get_account", side_effect=get_account), \
             mock.patch("services.openai_backend_api.account_service.update_account", side_effect=update_account), \
             mock.patch("services.openai_backend_api.create_session", side_effect=lambda **kwargs: FakeSession()):
            first = OpenAIBackendAPI("token")
            first_ids = (first.device_id, first.session_id)
            first.close()
            second = OpenAIBackendAPI("token")
            second_ids = (second.device_id, second.session_id)
            second.close()

        self.assertEqual(first_ids, second_ids)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["fp"]["oai-device-id"], first_ids[0])
        self.assertEqual(updates[0]["fp"]["oai-session-id"], first_ids[1])


if __name__ == "__main__":
    unittest.main()
