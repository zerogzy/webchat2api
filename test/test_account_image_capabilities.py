from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs

install_curl_cffi_stub()
install_fastapi_stubs()

os.environ.setdefault("WEBCHAT2API_AUTH_KEY", "test-auth")

from services.account_service import AccountService
import services.account_service as account_service_module
from services.auth_service import AuthService
from services.storage.json_storage import JSONStorageBackend
from utils.helper import anonymize_token


class AccountCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_patcher = mock.patch.object(account_service_module.log_service, "add")
        self.log_patcher.start()
        self.addCleanup(self.log_patcher.stop)

    def test_unknown_quota_accounts_are_available_only_when_not_throttled(self) -> None:
        self.assertFalse(
            AccountService._is_image_account_available(
                {"status": "限流", "image_quota_unknown": True, "quota": 0}
            )
        )
        self.assertTrue(
            AccountService._is_image_account_available(
                {"status": "正常", "image_quota_unknown": True, "quota": 0}
            )
        )

    def test_prolite_variants_are_normalized_from_account_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items([
                {"access_token": "token-1", "account": {"plan_type": "pro_lite"}},
                {"access_token": "token-2", "type": "plus", "account": {"plan_type": "pro_lite"}},
            ])

            accounts = {account["access_token"]: account for account in service.list_accounts()}
            self.assertEqual(accounts["token-1"]["type"], "ProLite")
            self.assertEqual(accounts["token-2"]["type"], "plus")

    def test_search_account_type_ignores_unrelated_scalar_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items([
                {
                    "access_token": "token-1",
                    "metadata": {
                        "amr": ["pwd", "otp", "mfa"],
                        "chatgpt_compute_residency": "no_constraint",
                        "chatgpt_data_residency": "no_constraint",
                        "user_id": "user-I52GFfLGFM0dokFk2dBiKEBn",
                    },
                }
            ])

            [account] = service.list_accounts()
            self.assertEqual(account["type"], "free")

    def test_mark_image_result_does_not_consume_unknown_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts(["token-1"])
            service.update_account(
                "token-1",
                {
                    "status": "正常",
                    "quota": 0,
                    "image_quota_unknown": True,
                },
            )

            updated = service.mark_image_result("token-1", success=True)

            assert updated is not None
            self.assertEqual(updated["quota"], 0)
            self.assertEqual(updated["status"], "正常")
            self.assertTrue(updated["image_quota_unknown"])

    def test_get_available_access_token_respects_excluded_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts(["token-1", "token-2"])
            service.update_account("token-1", {"status": "正常", "quota": 1})
            service.update_account("token-2", {"status": "正常", "quota": 1})

            def fake_fetch(access_token: str, event: str = "fetch_remote_info") -> dict:
                account = service.get_account(access_token)
                self.assertIsNotNone(account)
                return account or {}

            service.fetch_remote_info = fake_fetch

            selected = service.get_available_access_token({"token-1"})

            self.assertEqual(selected, "token-2")
            service.release_image_slot(selected)


class TokenLogTests(unittest.TestCase):
    def test_anonymize_token_hides_raw_value(self) -> None:
        token = "super-secret-token"
        token_ref = anonymize_token(token)

        self.assertTrue(token_ref.startswith("token:"))
        self.assertNotIn(token, token_ref)


class AuthServiceTests(unittest.TestCase):
    def test_create_authenticate_disable_and_delete_user_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))

            item, raw_key = service.create_key(role="user", name="Alice")

            self.assertEqual(item["role"], "user")
            self.assertEqual(item["name"], "Alice")
            self.assertTrue(item["enabled"])
            self.assertTrue(raw_key.startswith("sk-"))

            authed = service.authenticate(raw_key)
            assert authed is not None
            key_id = cast(str, item["id"])
            self.assertEqual(authed["id"], key_id)
            self.assertEqual(authed["role"], "user")
            self.assertIsNotNone(authed["last_used_at"])

            updated = service.update_key(key_id, {"enabled": False}, role="user")
            assert updated is not None
            self.assertFalse(updated["enabled"])
            self.assertIsNone(service.authenticate(raw_key))

            self.assertTrue(service.delete_key(key_id, role="user"))
            self.assertFalse(service.delete_key(key_id, role="user"))
            self.assertEqual(service.list_keys(role="user"), [])

    def test_authenticate_ignores_last_used_save_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            item, raw_key = service.create_key(role="user", name="Alice")

            def fail_save() -> None:
                raise OSError("disk unavailable")

            service._save = fail_save

            authed = service.authenticate(raw_key)

            assert authed is not None
            self.assertEqual(authed["id"], item["id"])
            self.assertIsNotNone(authed["last_used_at"])

    def test_update_user_key_replaces_raw_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            item, raw_key = service.create_key(role="user", name="Alice")

            updated = service.update_key(cast(str, item["id"]), {"key": "sk-user-custom-key"}, role="user")

            self.assertIsNotNone(updated)
            self.assertIsNone(service.authenticate(raw_key))

            authed = service.authenticate("sk-user-custom-key")
            assert authed is not None
            self.assertEqual(authed["id"], item["id"])

    def test_user_key_name_must_be_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            first, _ = service.create_key(role="user", name="Alice")
            second, _ = service.create_key(role="user", name="Bob")

            with self.assertRaisesRegex(ValueError, "这个名称已经在使用中了"):
                service.create_key(role="user", name="Alice")

            with self.assertRaisesRegex(ValueError, "这个名称已经在使用中了"):
                service.update_key(cast(str, second["id"]), {"name": "Alice"}, role="user")

            updated = service.update_key(cast(str, first["id"]), {"name": "Alice"}, role="user")
            assert updated is not None
            self.assertEqual(updated["name"], "Alice")


if __name__ == "__main__":
    unittest.main()
