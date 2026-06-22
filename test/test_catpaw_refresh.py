from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from services.account_service import AccountService
from services.providers.account_ops import account_row_id_for_provider
from services.providers.base import CATPAW_PROVIDER
from services.storage.base import StorageBackend


class MemoryStorage(StorageBackend):
    def __init__(self, accounts: list[dict[str, Any]] | None = None) -> None:
        self.accounts = list(accounts or [])

    def load_accounts(self) -> list[dict[str, Any]]:
        return list(self.accounts)

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        self.accounts = list(accounts)

    def load_auth_keys(self) -> list[dict[str, Any]]:
        return []

    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        pass

    def load_settings(self) -> dict[str, Any]:
        return {}

    def save_settings(self, settings: dict[str, Any]) -> None:
        pass

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def get_backend_info(self) -> dict[str, Any]:
        return {"type": "memory"}


def catpaw_account() -> dict[str, Any]:
    return {
        "provider": CATPAW_PROVIDER,
        "catpaw_id": "catpaw-user-1",
        "access_token": "catpaw-user-1",
        "catpaw_access_token": "catpaw-access-token",
        "refresh_token": "catpaw-refresh-token",
        "mis_id": "catpaw-mis",
        "status": "normal",
    }


class CatpawRefreshTests(unittest.TestCase):
    def test_refresh_token_value_uses_access_token_header_and_refresh_token_body(self) -> None:
        from services.providers.catpaw import client as catpaw_client

        with (
            mock.patch.object(catpaw_client, "_common_headers", return_value={"Content-Type": "application/json"}),
            mock.patch.object(
                catpaw_client,
                "_http_post_json",
                return_value=(
                    200,
                    '{"code":0,"msg":"success","data":{"accessToken":"new-access","refreshToken":"new-refresh","expires":123,"refreshExpires":456}}',
                ),
            ) as post_json,
        ):
            data = catpaw_client.refresh_token_value("old-access", "old-refresh")

        post_json.assert_called_once_with(
            "/api/login/refreshToken",
            {"Content-Type": "application/json", "Catpaw-Auth": "old-access"},
            {"refreshToken": "old-refresh"},
        )
        self.assertEqual(data["accessToken"], "new-access")
        self.assertEqual(data["refreshToken"], "new-refresh")

    def test_refresh_token_value_raises_readable_error_from_upstream_payload(self) -> None:
        from services.providers.catpaw import client as catpaw_client

        with mock.patch.object(
            catpaw_client,
            "_http_post_json",
            return_value=(401, '{"data":{"message":"auth failed"}}'),
        ):
            with self.assertRaises(catpaw_client.CatpawError) as ctx:
                catpaw_client.refresh_token_value("old-access", "old-refresh")

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("auth failed", str(ctx.exception))

    def test_token_manager_refresh_uses_access_and_refresh_token_pair(self) -> None:
        from services.providers.catpaw import client as catpaw_client

        manager = catpaw_client._TokenManager()
        token = {"accessToken": "old-access", "refreshToken": "old-refresh"}

        with (
            mock.patch.object(
                catpaw_client,
                "refresh_token_value",
                return_value={
                    "accessToken": "new-access",
                    "refreshToken": "new-refresh",
                    "expires": 123,
                    "refreshExpires": 456,
                },
            ) as refresh_token_value,
            mock.patch.object(manager, "_save") as save,
        ):
            refreshed = manager._refresh(token)

        self.assertTrue(refreshed)
        refresh_token_value.assert_called_once_with("old-access", "old-refresh")
        self.assertEqual(token["accessToken"], "new-access")
        self.assertEqual(token["refreshToken"], "new-refresh")
        save.assert_called_once_with(token)

    def test_validate_remote_info_requires_access_and_refresh_tokens(self) -> None:
        from services.providers.catpaw import accounts

        with self.assertRaises(RuntimeError) as missing_access:
            accounts.validate_remote_info("catpaw-user-1", {"refresh_token": "refresh"})
        self.assertIn("access token", str(missing_access.exception))

        with self.assertRaises(RuntimeError) as missing_refresh:
            accounts.validate_remote_info("catpaw-user-1", {"catpaw_access_token": "access"})
        self.assertIn("refresh token", str(missing_refresh.exception))

    def test_refresh_accounts_updates_catpaw_rotating_tokens(self) -> None:
        service = AccountService(MemoryStorage([catpaw_account()]), now=lambda: 1000.0)

        with mock.patch(
            "services.providers.catpaw.client.refresh_token_value",
            return_value={
                "accessToken": "new-access-token",
                "refreshToken": "new-refresh-token",
                "expires": 123,
                "refreshExpires": 456,
            },
        ) as refresh_token_value:
            result = service.refresh_accounts(["catpaw-user-1"], provider=CATPAW_PROVIDER)

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["refreshed"], 1)
        self.assertEqual(result["failed"], 0)
        refresh_token_value.assert_called_once_with("catpaw-access-token", "catpaw-refresh-token")
        saved = service.get_account("catpaw-user-1", provider=CATPAW_PROVIDER)
        self.assertEqual(saved["catpaw_access_token"], "new-access-token")
        self.assertEqual(saved["refresh_token"], "new-refresh-token")
        self.assertEqual(saved["expires"], 123)
        self.assertEqual(saved["refresh_expires"], 456)

    def test_refresh_accounts_resolves_catpaw_row_identifier(self) -> None:
        account = catpaw_account()
        service = AccountService(MemoryStorage([account]), now=lambda: 1000.0)
        row_id = account_row_id_for_provider(account, CATPAW_PROVIDER)

        with mock.patch(
            "services.providers.catpaw.client.refresh_token_value",
            return_value={
                "accessToken": "new-access-token",
                "refreshToken": "new-refresh-token",
                "expires": 123,
                "refreshExpires": 456,
            },
        ):
            result = service.refresh_accounts([], provider=CATPAW_PROVIDER, identifiers=[{"row_id": row_id}])

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["refreshed"], 1)

    def test_refresh_accounts_resolves_catpaw_identifier_without_provider_filter(self) -> None:
        account = catpaw_account()
        service = AccountService(MemoryStorage([account]), now=lambda: 1000.0)
        row_id = account_row_id_for_provider(account, CATPAW_PROVIDER)

        with mock.patch(
            "services.providers.catpaw.client.refresh_token_value",
            return_value={
                "accessToken": "new-access-token",
                "refreshToken": "new-refresh-token",
                "expires": 123,
                "refreshExpires": 456,
            },
        ):
            result = service.refresh_accounts([], identifiers=[{"row_id": row_id}])

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["refreshed"], 1)


if __name__ == "__main__":
    unittest.main()
