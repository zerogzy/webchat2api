from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from services.account_service import AccountService
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
        "status": "正常",
    }


class CatpawQuotaTests(unittest.TestCase):
    def test_refresh_catpaw_quota_stores_remote_limit_without_auto_apply_when_remaining_is_enough(self) -> None:
        service = AccountService(MemoryStorage([catpaw_account()]), now=lambda: 1000.0)
        limit_payload = {"data": {"modelRequestCount": 1500, "modelRequestLimit": 2000, "modelRemaingCount": 500}}

        with (
            mock.patch("services.providers.catpaw.client.get_user_limit", return_value=limit_payload) as get_limit,
            mock.patch("services.providers.catpaw.client.apply_quota") as apply_quota,
        ):
            result = service.refresh_catpaw_quota(auto_apply=True)

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["items"][0]["catpaw_quota"]["remaining"], 500)
        self.assertEqual(result["items"][0]["catpaw_quota"]["limit"], 2000)
        self.assertEqual(result["items"][0]["catpaw_quota"]["used"], 1500)
        get_limit.assert_called_once_with("catpaw-access-token", "catpaw-mis")
        apply_quota.assert_not_called()

    def test_refresh_catpaw_quota_auto_applies_below_threshold_and_refreshes_limit(self) -> None:
        service = AccountService(MemoryStorage([catpaw_account()]), now=lambda: 1000.0)
        low_payload = {"data": {"modelRequestCount": 1960, "modelRequestLimit": 2000, "modelRemaingCount": 40}}
        raised_payload = {"data": {"modelRequestCount": 1960, "modelRequestLimit": 3000, "modelRemaingCount": 1040}}

        with (
            mock.patch("services.providers.catpaw.client.get_user_limit", side_effect=[low_payload, raised_payload]),
            mock.patch("services.providers.catpaw.client.apply_quota", return_value={"code": 0, "msg": "成功", "data": {"modelRequestLimit": 3000}}) as apply_quota,
        ):
            result = service.refresh_catpaw_quota(auto_apply=True)

        item = result["items"][0]
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(item["catpaw_quota"]["remaining"], 1040)
        self.assertEqual(item["catpaw_quota"]["limit"], 3000)
        self.assertEqual(item["catpaw_quota"]["auto_apply_status"], "success")
        self.assertEqual(item["catpaw_quota"]["auto_apply_message"], "成功")
        apply_quota.assert_called_once_with("catpaw-access-token", "catpaw-mis")

    def test_sanitize_catpaw_account_exposes_quota_without_secrets(self) -> None:
        from services.providers.catpaw.accounts import sanitize_account

        account = catpaw_account()
        account["catpaw_quota"] = {"remaining": 12, "limit": 2000, "used": 1988}

        sanitized = sanitize_account(account)

        self.assertEqual(sanitized["catpaw_quota"]["remaining"], 12)
        self.assertNotIn("catpaw_access_token", sanitized)
        self.assertNotIn("refresh_token", sanitized)


if __name__ == "__main__":
    unittest.main()
