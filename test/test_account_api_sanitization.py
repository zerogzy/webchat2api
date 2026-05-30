from __future__ import annotations

import sys
import unittest
from typing import Any, cast
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_pydantic_stub, install_starlette_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_pydantic_stub()
install_starlette_stub()
install_tiktoken_stub()

FastAPI = cast(Any, getattr(sys.modules["fastapi"], "FastAPI"))
TestClient = cast(Any, getattr(sys.modules["fastapi.testclient"], "TestClient"))

import api.accounts as accounts_module

AUTH_HEADERS = {"Authorization": "Bearer webchat2api"}
GEMINI_TOKEN = "__Secure-1PSID=psid; __Secure-1PSIDTS=psidts"
SECRET_KEYS = ("access_token", "cookies", "__Secure-1PSID", "__Secure-1PSIDTS")
GPT_TOKEN = "gpt-token-for-admin-operations"
GROK_TOKEN = "grok-token-for-admin-operations"


class FakeAccountService:
    def __init__(self) -> None:
        self.account = {
            "access_token": GEMINI_TOKEN,
            "provider": "gemini",
            "status": "正常",
            "type": "free",
            "account_id": "gemini-account-1",
            "row_id": "gemini-row-1",
            "cookies": {"__Secure-1PSID": "psid", "__Secure-1PSIDTS": "psidts"},
            "__Secure-1PSID": "psid",
            "__Secure-1PSIDTS": "psidts",
        }
        self.deleted: tuple[list[str], str | None, list[dict[str, str]] | None] | None = None

    def list_accounts(self, provider: str | None = None) -> list[dict[str, Any]]:
        if provider and provider != self.account.get("provider"):
            return []
        return [dict(self.account)]

    def list_tokens(self, provider: str | None = None) -> list[str]:
        if provider and provider != self.account.get("provider"):
            return []
        return [GEMINI_TOKEN]

    def add_account_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {"added": 1, "skipped": 0, "items": self.list_accounts()}

    def add_accounts(self, tokens: list[str]) -> dict[str, Any]:
        return {"added": 1, "skipped": 0, "items": self.list_accounts()}

    def refresh_accounts(self, access_tokens: list[str], provider: str | None = None) -> dict[str, Any]:
        return {"refreshed": 0, "errors": [], "items": self.list_accounts(provider=provider)}

    def update_account(self, access_token: str, updates: dict[str, Any], provider: str | None = None) -> dict[str, Any] | None:
        if provider and provider != self.account.get("provider"):
            return None
        self.account.update(updates)
        return dict(self.account)

    def delete_accounts(
        self,
        tokens: list[str],
        provider: str | None = None,
        identifiers: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        self.deleted = (tokens, provider, identifiers)
        return {"removed": 0, "items": self.list_accounts(provider=provider)}

    def delete_limited_accounts(self, provider: str | None = None) -> dict[str, Any]:
        return {"removed": 0, "items": self.list_accounts(provider=provider)}

    def build_export_items(self, access_tokens: list[str] | None = None, provider: str | None = None) -> list[dict[str, str]]:
        return [{"access_token": GEMINI_TOKEN, "sso": ""}]

    @staticmethod
    def build_export_text(items: list[dict[str, str]]) -> str:
        return "\n".join(item["access_token"] for item in items) + "\n"


class GeminiAccountApiSanitizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.account_service = FakeAccountService()
        self.patchers = [
            mock.patch.object(accounts_module, "account_service", self.account_service),
            mock.patch.object(accounts_module, "require_admin", lambda authorization: {"role": "admin"}),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(accounts_module.create_router())
        self.client = TestClient(app)

    def assert_no_gemini_secrets(self, payload: object) -> None:
        text = str(payload)
        self.assertNotIn("psid", text)
        self.assertNotIn(GEMINI_TOKEN, text)
        if isinstance(payload, dict):
            for key in SECRET_KEYS:
                self.assertNotIn(key, payload)
            self.assertTrue(payload.get("has_gemini_session"))

    def test_get_accounts_sanitizes_gemini_credentials(self) -> None:
        response = self.client.get("/api/accounts", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200)
        body = cast(dict[str, Any], response.json())
        self.assert_no_gemini_secrets(body["items"][0])

    def test_gpt_and_grok_tokens_remain_available_as_current_admin_identifiers(self) -> None:
        for provider, token in (("gpt", GPT_TOKEN), ("grok", GROK_TOKEN)):
            sanitized = accounts_module.sanitize_account({
                "provider": provider,
                "access_token": token,
                "status": "正常",
                "type": "free",
            })

            self.assertEqual(sanitized["access_token"], token)

    def test_create_accounts_sanitizes_gemini_credentials(self) -> None:
        response = self.client.post(
            "/api/accounts",
            headers=AUTH_HEADERS,
            json={"tokens": [GEMINI_TOKEN], "accounts": [{"provider": "gemini", "access_token": GEMINI_TOKEN}]},
        )

        self.assertEqual(response.status_code, 200)
        body = cast(dict[str, Any], response.json())
        self.assert_no_gemini_secrets(body["items"][0])

    def test_refresh_accounts_sanitizes_gemini_credentials(self) -> None:
        response = self.client.post("/api/accounts/refresh", headers=AUTH_HEADERS, json={"access_tokens": [GEMINI_TOKEN]})

        self.assertEqual(response.status_code, 200)
        body = cast(dict[str, Any], response.json())
        self.assert_no_gemini_secrets(body["items"][0])

    def test_update_account_sanitizes_gemini_credentials(self) -> None:
        response = self.client.post(
            "/api/accounts/update",
            headers=AUTH_HEADERS,
            json={"access_token": GEMINI_TOKEN, "status": "正常"},
        )

        self.assertEqual(response.status_code, 200)
        body = cast(dict[str, Any], response.json())
        self.assert_no_gemini_secrets(body["item"])
        self.assert_no_gemini_secrets(body["items"][0])

    def test_provider_filter_is_forwarded_to_get_accounts(self) -> None:
        response = self.client.get("/api/accounts?provider=gpt", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])

    def test_update_account_target_provider_does_not_match_other_provider(self) -> None:
        response = self.client.post(
            "/api/accounts/update",
            headers=AUTH_HEADERS,
            json={"access_token": GEMINI_TOKEN, "target_provider": "gpt", "status": "禁用"},
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_accounts_forwards_provider_and_sanitizes_gemini_credentials(self) -> None:
        response = self.client._request(
            "DELETE",
            "/api/accounts",
            headers=AUTH_HEADERS,
            json_data={"provider": "gemini", "tokens": [GEMINI_TOKEN]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.account_service.deleted, ([GEMINI_TOKEN], "gemini", []))
        body = cast(dict[str, Any], response.json())
        self.assert_no_gemini_secrets(body["items"][0])

    def test_delete_accounts_accepts_sanitized_gemini_account_identifier(self) -> None:
        response = self.client._request(
            "DELETE",
            "/api/accounts",
            headers=AUTH_HEADERS,
            json_data={"provider": "gemini", "identifiers": [{"account_id": "gemini-account-1"}]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.account_service.deleted, ([], "gemini", [{"account_id": "gemini-account-1"}]))
        body = cast(dict[str, Any], response.json())
        self.assert_no_gemini_secrets(body["items"][0])
        self.assertEqual(body["items"][0]["account_id"], "gemini-account-1")

    def test_delete_accounts_accepts_sanitized_gemini_row_identifier(self) -> None:
        response = self.client._request(
            "DELETE",
            "/api/accounts",
            headers=AUTH_HEADERS,
            json_data={"provider": "gemini", "identifiers": [{"row_id": "gemini-row-1"}]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.account_service.deleted, ([], "gemini", [{"row_id": "gemini-row-1"}]))
        body = cast(dict[str, Any], response.json())
        self.assert_no_gemini_secrets(body["items"][0])

    def test_export_accounts_keeps_gemini_cookie_header(self) -> None:
        items = self.account_service.build_export_items(provider="gemini")
        text = self.account_service.build_export_text(items)

        self.assertIn(GEMINI_TOKEN, text)


if __name__ == "__main__":
    unittest.main()
