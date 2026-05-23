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

if "pybase64" not in sys.modules:
    pybase64 = types.ModuleType("pybase64")
    pybase64.b64encode = lambda value: value
    pybase64.b64decode = lambda value: value
    sys.modules["pybase64"] = pybase64

if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: object = None) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi.HTTPException = HTTPException
    concurrency = types.ModuleType("fastapi.concurrency")
    concurrency.run_in_threadpool = lambda func, *args, **kwargs: func(*args, **kwargs)
    responses = types.ModuleType("fastapi.responses")

    class JSONResponse:
        pass

    class StreamingResponse:
        pass

    responses.JSONResponse = JSONResponse
    responses.StreamingResponse = StreamingResponse
    fastapi.concurrency = concurrency
    fastapi.responses = responses
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.concurrency"] = concurrency
    sys.modules["fastapi.responses"] = responses

if "PIL" not in sys.modules:
    pil = types.ModuleType("PIL")
    pil.Image = object
    sys.modules["PIL"] = pil

if "tiktoken" not in sys.modules:
    tiktoken = types.ModuleType("tiktoken")
    tiktoken.get_encoding = lambda name: types.SimpleNamespace(encode=lambda text: list(text))
    sys.modules["tiktoken"] = tiktoken

from services.openai_backend_api import RetryableTurnstileError
from services.protocol import conversation


class TurnstileRetryTests(unittest.TestCase):
    def test_retryable_turnstile_error_rotates_to_next_account_without_removing_old_token(self) -> None:
        created_tokens: list[str] = []

        class FakeBackend:
            def __init__(self, access_token: str = "") -> None:
                self.access_token = access_token
                created_tokens.append(access_token)

            def close(self) -> None:
                pass

        def fake_conversation_events(backend: FakeBackend, **kwargs: object):
            if backend.access_token == "first-token":
                raise RetryableTurnstileError("turnstile token generation returned None")
            yield {"type": "conversation.delta", "delta": "second account response"}

        requested_attempts: list[set[str]] = []
        account_service = mock.Mock()

        def get_text_access_token(attempted_tokens: set[str]) -> str:
            requested_attempts.append(set(attempted_tokens))
            return "second-token"

        account_service.get_text_access_token.side_effect = get_text_access_token
        request = conversation.ConversationRequest(
            model="auto",
            messages=[{"role": "user", "content": "hello"}],
        )
        initial_backend = types.SimpleNamespace(access_token="first-token")

        with (
            mock.patch.object(conversation, "OpenAIBackendAPI", FakeBackend),
            mock.patch.object(conversation, "conversation_events", fake_conversation_events),
            mock.patch.object(conversation, "account_service", account_service),
        ):
            deltas = list(conversation.stream_text_deltas(initial_backend, request))

        self.assertEqual(deltas, ["second account response"])
        self.assertEqual(created_tokens, ["first-token", "second-token"])
        self.assertEqual(requested_attempts, [{"first-token"}])
        account_service.get_text_access_token.assert_called_once()
        account_service.remove_invalid_token.assert_not_called()
        account_service.mark_text_used.assert_called_once_with("second-token")


if __name__ == "__main__":
    unittest.main()
